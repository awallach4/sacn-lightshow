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
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


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
        return FseqInfo(1, data_offset, channel_count, frame_count, step_ms)
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


def iter_frames(path: Path) -> tuple[FseqInfo, Iterator[bytes]]:
    """Return metadata and an iterator yielding packed channel bytes per frame."""
    raw = path.read_bytes()
    info = read_header(raw)
    frame_bytes = info.channel_count

    if info.version == 1 or info.compression == 0:
        expected = info.frame_count * frame_bytes
        end = info.data_offset + expected
        if end > len(raw):
            raise ValueError(f"Uncompressed channel data is truncated: expected {expected} bytes.")
        def plain_frames() -> Iterator[bytes]:
            for i in range(info.frame_count):
                start = info.data_offset + i * frame_bytes
                yield raw[start:start + frame_bytes]
        return info, plain_frames()

    # V2 compressed data is a sequence of independently compressed blocks.
    starts = [(first, size) for first, size in info.blocks if size > 0]
    starts.sort(key=lambda item: item[0])
    if not starts or starts[0][0] != 0:
        raise ValueError("Compressed FSEQ blocks do not start at frame 0.")
    for i, (first, _) in enumerate(starts):
        if first < 0 or first >= info.frame_count or (i and first <= starts[i - 1][0]):
            raise ValueError("Invalid or non-increasing FSEQ compression block frame index.")

    try:
        if info.compression == 1:
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

    def compressed_frames() -> Iterator[bytes]:
        for first, chunk in decoded:
            for offset in range(0, len(chunk), frame_bytes):
                yield chunk[offset:offset + frame_bytes]
    return info, compressed_frames()


def frame_to_universes(frame: bytes, ranges: tuple[tuple[int, int], ...],
                       start_universe: int, slots: int = 512) -> dict[int, tuple[int, ...]]:
    """Map packed sparse-range channel bytes to 1-based universe slots."""
    output: dict[int, bytearray] = {}
    packed_offset = 0
    for absolute_start, length in ranges:
        segment = frame[packed_offset:packed_offset + length]
        if len(segment) != length:
            raise ValueError("Frame is shorter than its sparse-range metadata.")
        for i, value in enumerate(segment):
            absolute_channel = absolute_start + i  # zero-based global channel index
            universe = start_universe + absolute_channel // slots
            slot = absolute_channel % slots
            if universe not in output:
                output[universe] = bytearray(slots)
            output[universe][slot] = value
        packed_offset += length
    return {universe: tuple(values) for universe, values in output.items()}


def play(args: argparse.Namespace) -> None:
    try:
        import sacn  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Install the sACN package with: python -m pip install sacn") from exc

    path = Path(args.file)
    info, _ = iter_frames(path)  # Validate the file before opening network output.
    if info.step_ms <= 0:
        raise ValueError(f"Invalid frame interval: {info.step_ms} ms.")
    if args.universe < 1 or args.universe > 63999:
        raise ValueError("Starting universe must be between 1 and 63999.")
    if args.slots < 1 or args.slots > 512:
        raise ValueError("Slots per universe must be between 1 and 512.")

    # Read the sequence again to obtain a fresh iterator for actual playback.
    info, frames = iter_frames(path)
    sender = sacn.sACNsender(source_name=args.source_name, fps=max(1, round(1000 / info.step_ms)))
    active_universes: set[int] = set()
    frame_period = info.step_ms / 1000.0 / args.speed
    print(f"FSEQ v{info.version}: {info.frame_count} frames, {info.channel_count} packed channels, "
          f"{info.step_ms} ms/frame; sparse ranges: {info.ranges}")
    print(f"Starting universe: {args.universe}; playback speed: {args.speed:g}x")

    try:
        sender.start()
        raw_input = sys.stdin.readline().strip()
        if raw_input:
            target_t0 = float(raw_input)
            wait_delta = target_t0 - time.monotonic()
            if wait_delta > 0:
                time.sleep(wait_delta)
            next_deadline = time.perf_counter()
        
        while True:
            next_deadline = time.perf_counter()
            for frame in frames:
                universe_data = frame_to_universes(frame, info.ranges, args.universe, args.slots)
                
                for universe in universe_data:
                    if universe not in active_universes:
                        sender.activate_output(universe)
                        active_universes.add(universe)
                    sender[universe].multicast = args.multicast
                    if args.destination:
                        sender[universe].destination = args.destination
                    sender[universe].dmx_data = universe_data[universe]

                next_deadline += frame_period
                delay = next_deadline - time.perf_counter()
                
                if delay > 0:
                    time.sleep(delay)
                elif delay < -frame_period * 4:
                    # Reset deadline if CPU lag or process suspension drops behind significantly
                    next_deadline = time.perf_counter()

            if not args.loop:
                # Wait one full frame period so the async sACN background thread 
                # actually transmits the final frame buffer over the network before exiting
                time.sleep(args.button_hold)
                break
                
            _, frames = iter_frames(path)

    finally:
        # Flush zero values across active channels
        for universe in active_universes:
            try:
                # Allow background thread to send the blackout packet
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
    parser.add_argument("--button-hold", type=float, default=0.0, help="How long to hold the final cue before blackout")
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be greater than zero")
    if args.destination and args.multicast:
        parser.error("Choose either --destination (unicast) or --multicast, not both")
    try:
        play(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
