#!/usr/bin/env python3
"""Play an xLights FSEQ file over sACN (E1.31) using the `sacn` package.

Supports FSEQ v1 uncompressed and FSEQ v2 uncompressed, zlib, and Zstandard.
V2 sparse channel ranges are expanded into their absolute channel positions.

Install:
    python -m pip install sacn zstandard

Example:
    python fseq_sacn_player.py show.fseq --universe 1
    python fseq_sacn_player.py show.fseq --universe 10 --destination 192.168.1.50 --loop
"""
from __future__ import annotations

import argparse
import bisect
import json
import signal
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from logger_setup import get_node_logger, setup_global_exception_handler

# Printed on stdout once decoding/network setup is finished, so the show
# controller knows it can safely hand out the shared T0 start time.
READY_TOKEN = "@@READY@@"
CONFIG_FILE = Path("config/main_config.json")


@dataclass(frozen=True)
class FseqInfo:
    version: int
    data_offset: int
    channel_count: int
    frame_count: int
    step_ms: int
    compression: int = 0  # 0 none, 1 zstd, 2 zlib
    blocks: tuple[tuple[int, int], ...] = ()  # (first_frame, compressed_size)
    ranges: tuple[tuple[int, int], ...] = ()  # (zero-based start, channel length)

def load_log_level() -> str:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("log_level", "INFO")
    except Exception:
        pass
    return "INFO"

# Initialize node logger
log = get_node_logger("sacn_transmitter", load_log_level())
setup_global_exception_handler(log)

def _u24le(data: bytes) -> int:
    return int.from_bytes(data, "little")


def read_header(data: bytes) -> FseqInfo:
    if len(data) < 28 or data[:4] not in (b"PSEQ", b"FSEQ"):
        raise ValueError("Not a recognized FSEQ file (missing PSEQ/FSEQ signature).")

    data_offset = struct.unpack_from("<H", data, 4)[0]
    minor, major = data[6], data[7]
    channel_count = struct.unpack_from("<I", data, 10)[0]
    frame_count = struct.unpack_from("<I", data, 14)[0]
    step_ms = data[18]

    if major == 1:
        if data_offset > len(data):
            raise ValueError("FSEQ data offset points beyond end of file.")
        # v1 has no sparse ranges: channels are one contiguous block starting at channel 0.
        return FseqInfo(1, data_offset, channel_count, frame_count, step_ms,
                        ranges=((0, channel_count),))
    if major != 2:
        raise ValueError(f"Unsupported FSEQ major version: {major} (minor {minor}).")
    if len(data) < 32:
        raise ValueError("Truncated FSEQ v2 header.")

    compression = data[20] & 0x0F
    block_count = (((data[20] & 0xF0) << 4) | data[21])
    sparse_count = data[22]
    pos = 32
    blocks: list[tuple[int, int]] = []
    for _ in range(block_count):
        if pos + 8 > len(data):
            raise ValueError("Truncated FSEQ compression-block table.")
        first_frame, size = struct.unpack_from("<II", data, pos)
        blocks.append((first_frame, size))
        pos += 8

    ranges: list[tuple[int, int]] = []
    for _ in range(sparse_count):
        if pos + 6 > len(data):
            raise ValueError("Truncated FSEQ sparse-range table.")
        start = _u24le(data[pos:pos + 3])
        length = _u24le(data[pos + 3:pos + 6])
        if length:
            ranges.append((start, length))
        pos += 6

    if not ranges:
        ranges = [(0, channel_count)]
    if sum(length for _, length in ranges) != channel_count:
        raise ValueError(
            "FSEQ channel count does not equal the sum of sparse-range lengths; "
            "the file may use an unsupported extension or be malformed."
        )
    if data_offset < pos or data_offset > len(data):
        raise ValueError("Invalid FSEQ channel-data offset.")
    if compression not in (0, 1, 2):
        raise ValueError(f"Unsupported FSEQ compression type: {compression}.")
    if compression and not any(size for _, size in blocks):
        raise ValueError("Compressed FSEQ has no non-empty compression blocks.")

    return FseqInfo(2, data_offset, channel_count, frame_count, step_ms,
                    compression, tuple(blocks), tuple(ranges))


class FrameSource:
    """Random-access sequence of packed frames.

    Random access (instead of a one-shot generator) lets playback *skip* frames
    to catch up with the clock if the CPU ever stalls, instead of slipping
    permanently behind the audio.
    """

    def __init__(self, count: int, frame_bytes: int, raw: bytes | None = None,
                 offset: int = 0, chunks: list[tuple[int, bytes]] | None = None):
        self._count = count
        self._fb = frame_bytes
        self._raw = raw
        self._offset = offset
        self._chunks = chunks or []
        self._firsts = [first for first, _ in self._chunks]

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, i: int) -> bytes:
        if not 0 <= i < self._count:
            raise IndexError(i)
        if self._raw is not None:
            s = self._offset + i * self._fb
            return self._raw[s:s + self._fb]
        k = bisect.bisect_right(self._firsts, i) - 1
        first, chunk = self._chunks[k]
        s = (i - first) * self._fb
        return chunk[s:s + self._fb]


def load_frames(path: Path) -> tuple[FseqInfo, FrameSource]:
    """Read + fully decode the file once; return metadata and a FrameSource."""
    raw = path.read_bytes()
    info = read_header(raw)
    frame_bytes = info.channel_count

    if info.version == 1 or info.compression == 0:
        expected = info.frame_count * frame_bytes
        if info.data_offset + expected > len(raw):
            raise ValueError(f"Uncompressed channel data is truncated: expected {expected} bytes.")
        return info, FrameSource(info.frame_count, frame_bytes, raw=raw, offset=info.data_offset)

    # V2 compressed data is a sequence of independently compressed blocks.
    starts = [(first, size) for first, size in info.blocks if size > 0]
    starts.sort(key=lambda item: item[0])
    if not starts or starts[0][0] != 0:
        raise ValueError("Compressed FSEQ blocks do not start at frame 0.")
    for i, (first, _) in enumerate(starts):
        if first < 0 or first >= info.frame_count or (i and first <= starts[i - 1][0]):
            raise ValueError("Invalid or non-increasing FSEQ compression block frame index.")

    zstandard = None
    if info.compression == 1:
        try:
            import zstandard  # type: ignore
        except ImportError as exc:
            raise RuntimeError("This file uses Zstandard compression. Install it with: python -m pip install zstandard") from exc

    decoded: list[tuple[int, bytes]] = []
    cursor = info.data_offset
    for i, (first, compressed_size) in enumerate(starts):
        next_frame = starts[i + 1][0] if i + 1 < len(starts) else info.frame_count
        expected_size = (next_frame - first) * frame_bytes
        chunk = raw[cursor:cursor + compressed_size]
        if len(chunk) != compressed_size:
            raise ValueError(f"Compressed block at frame {first} extends beyond end of file.")
        cursor += compressed_size
        if info.compression == 1:
            try:
                decoded_chunk = zstandard.ZstdDecompressor().decompress(
                    chunk, max_output_size=expected_size
                )
            except Exception as exc:
                raise ValueError(f"Could not decompress Zstandard block at frame {first}: {exc}") from exc
        else:
            try:
                decoded_chunk = zlib.decompress(chunk)
            except zlib.error as exc:
                # Some older FSEQ files store the first block uncompressed.
                if len(chunk) == expected_size:
                    decoded_chunk = chunk
                else:
                    raise ValueError(f"Could not decompress zlib block at frame {first}: {exc}") from exc
        if len(decoded_chunk) != expected_size:
            raise ValueError(
                f"Block starting at frame {first} decoded to {len(decoded_chunk)} bytes; "
                f"expected {expected_size}."
            )
        decoded.append((first, decoded_chunk))

    return info, FrameSource(info.frame_count, frame_bytes, chunks=decoded)


def frame_to_universes(frame: bytes, ranges: tuple[tuple[int, int], ...],
                       start_universe: int, slots: int = 512) -> dict[int, tuple[int, ...]]:
    """Map packed sparse-range channel bytes to 1-based universe slots."""
    output: dict[int, bytearray] = {}
    packed_offset = 0
    for absolute_start, length in ranges:
        segment = frame[packed_offset:packed_offset + length]
        if len(segment) != length:
            raise ValueError("Frame is shorter than its sparse-range metadata.")
        packed_offset += length
        pos = 0
        while pos < length:  # copy whole runs per universe rather than byte-by-byte
            channel = absolute_start + pos  # zero-based global channel index
            universe = start_universe + channel // slots
            slot = channel % slots
            n = min(slots - slot, length - pos)
            buf = output.get(universe)
            if buf is None:
                buf = output[universe] = bytearray(slots)
            buf[slot:slot + n] = segment[pos:pos + n]
            pos += n
    return {universe: tuple(values) for universe, values in output.items()}


def sleep_until(deadline: float) -> None:
    """Sleep until a time.perf_counter() deadline: coarse sleep, then a short spin."""
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.004:
            time.sleep(remaining - 0.003)


def play(args: argparse.Namespace) -> None:
    try:
        import sacn  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Install the sACN package with: python -m pip install sacn") from exc

    path = Path(args.file)
    info, frames = load_frames(path)  # decode ONCE, before anything is time-critical
    if info.step_ms <= 0:
        raise ValueError(f"Invalid frame interval: {info.step_ms} ms.")
    if args.universe < 1 or args.universe > 63999:
        raise ValueError("Starting universe must be between 1 and 63999.")
    if args.slots < 1 or args.slots > 512:
        raise ValueError("Slots per universe must be between 1 and 512.")
    total = len(frames)
    if total == 0:
        raise ValueError("FSEQ file contains no frames.")

    frame_period = info.step_ms / 1000.0 / args.speed
    # The sACN sender thread only transmits on its own fps tick, so run it faster than
    # the frame rate to keep the data-to-wire latency (and jitter) small.
    sender_fps = min(120, max(30, 2 * round(1000 / info.step_ms)))
    sender = sacn.sACNsender(source_name=args.source_name, fps=sender_fps)
    active_universes: set[int] = set()
    log.info(f"FSEQ v{info.version}: {total} frames, {info.channel_count} packed channels, "
          f"{info.step_ms} ms/frame; sparse ranges: {info.ranges}")
    log.info(f"Starting universe: {args.universe}; playback speed: {args.speed:g}x")

    def activate(universe: int) -> None:
        sender.activate_output(universe)
        sender[universe].multicast = args.multicast
        if args.destination:
            sender[universe].destination = args.destination
        active_universes.add(universe)

    def send_frame(frame: bytes) -> None:
        for universe, values in frame_to_universes(frame, info.ranges, args.universe, args.slots).items():
            if universe not in active_universes:
                activate(universe)
            sender[universe].dmx_data = values

    # A SIGTERM from the show controller must still run the blackout in `finally`.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    skipped = 0
    try:
        sender.start()
        # Pre-activate every universe the show uses so nothing has to be set up at T0.
        for universe in frame_to_universes(frames[0], info.ranges, args.universe, args.slots):
            activate(universe)

        # ---- Handshake: tell the controller we're fully ready, then wait for the shared T0.
        print(READY_TOKEN, flush=True)
        line = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline().strip()
        t0_monotonic = float(line) if line else time.monotonic()
        # Convert the controller's time.monotonic() T0 into our perf_counter() timeline.
        base = time.perf_counter() + (t0_monotonic - time.monotonic())
        sleep_until(base)

        # ---- Clock-driven playback: frame N is due at base + N * period, always.
        # Absolute scheduling means no drift; if we ever fall behind we skip ahead.
        while True:
            index = 0
            while index < total:
                due = int((time.perf_counter() - base) / frame_period)
                if due > index:
                    skipped += min(due, total) - index
                    index = due
                    if index >= total:
                        break
                send_frame(frames[index])
                index += 1
                sleep_until(base + index * frame_period)  # holds the final frame for a full period

            if not args.loop:
                log.info(f"Sequence finished {time.perf_counter() - (base):.3f}s after T0 "
                      f"(expected {total * frame_period:.3f}s; {total} frames x {info.step_ms} ms / {args.speed:g}).")
                time.sleep(args.button_hold)  # optional extra hold of the last cue
                break
            base += total * frame_period

    finally:
        if skipped:
            log.warning(f"Skipped {skipped} frame(s) to stay in sync with the clock.", sys.stderr)
        try:
            # Blackout: push zeros first, then send stream-terminated packets.
            for universe in active_universes:
                sender[universe].dmx_data = (0,) * args.slots
            time.sleep(2.0 / sender_fps + 0.05)
            for universe in list(active_universes):
                sender.deactivate_output(universe)
        except Exception:
            pass
        sender.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to an xLights .fseq file")
    parser.add_argument("--universe", type=int, default=1,
                        help="Universe to which zero-based FSEQ channel 0 maps (default: 1)")
    parser.add_argument("--slots", type=int, default=512,
                        help="Slots per universe (default: 512; normally leave this at 512)")
    parser.add_argument("--destination", help="Optional unicast receiver IP address")
    parser.add_argument("--multicast", action="store_true",
                        help="Enable sACN multicast output (default is unicast mode if destination is supplied)")
    parser.add_argument("--source-name", default="FSEQ Python Player", help="sACN source name")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0)")
    parser.add_argument("--loop", action="store_true", help="Loop playback continuously")
    parser.add_argument("--button-hold", type=float, default=0.0, help="Extra seconds to hold the final frame before blackout")
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    if args.destination and args.multicast:
        parser.error("Choose either --destination (unicast) or --multicast, not both")
    try:
        play(args)
    except (OSError, ValueError, RuntimeError) as exc:
        log.error(f"Error: {exc}", sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())