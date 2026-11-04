#!/usr/bin/env python3
"""
Audio FM Broadcast Node
-----------------------
Handles audio file playback and manages the FM transmitter output frequency.
Supports dual-output playback (FM broadcast + local speaker monitoring).

Sync protocol (with show_control.py):
  1. This node finishes all slow setup (pygame load / FM process start).
  2. It prints READY_TOKEN on stdout.
  3. The controller replies on stdin with a shared start time T0 (time.monotonic() clock).
  4. Audio is started at T0 + offset, on every platform.
"""

import sys
import os
import argparse
import signal
import subprocess
import time

READY_TOKEN = "@@READY@@"
FIFO_PATH = "/tmp/fm_audio.fifo"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Audio Playback and FM Transmitter Controller Node"
    )
    parser.add_argument(
        "audio_file",
        type=str,
        help="Path to the audio file (.wav, .mp3, .flac)"
    )
    parser.add_argument(
        "-f", "--freq",
        type=float,
        default=90.3,
        help="FM frequency to transmit on in MHz (default: 90.3)"
    )
    parser.add_argument(
        "-s", "--speakers",
        action="store_true",
        help="Enable local speaker playback concurrently with FM broadcast"
    )
    parser.add_argument(
        "--ps-name",
        type=str,
        default="HOLIDAY",
        help="RDS Program Service station name (max 8 chars, default: HOLIDAY)"
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Seconds to shift the main (FM / pygame) audio relative to T0. Positive = later, "
             "negative = earlier. Use to calibrate out fixed latency (default: 0.0)"
    )
    parser.add_argument(
        "--speaker-offset",
        type=float,
        default=0.0,
        help="Same as --offset, but for the local monitor speakers (default: 0.0)"
    )
    return parser.parse_args()


def validate_environment(audio_file, freq):
    if not os.path.isfile(audio_file):
        print(f"[ERROR] Audio file not found: {audio_file}", file=sys.stderr)
        sys.exit(1)

    if not (87.5 <= freq <= 108.0):
        print(f"[ERROR] Frequency {freq} MHz is outside the standard FM broadcast band (87.5 - 108.0 MHz)", file=sys.stderr)
        sys.exit(1)


def sleep_until(deadline):
    """Sleep until a time.monotonic() deadline (coarse sleep, then a short spin)."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if remaining > 0.004:
            time.sleep(remaining - 0.003)


def audio_duration_s(path):
    """Audio length in seconds via soxi, or None if unavailable."""
    try:
        out = subprocess.run(["soxi", "-D", path], capture_output=True, text=True, timeout=10)
        return float(out.stdout.strip()) if out.returncode == 0 else None
    except Exception:
        return None


def report_fm_timing(duration, started, finished):
    """sox can only finish once the FM modulator has consumed (almost) all the audio, so how long
    it took tells us how fast the FM chain really plays the stream compared with the wall clock."""
    if not duration:
        return
    elapsed = finished - started
    pipe_s = 65536 / 44100.0  # default Linux pipe (64 KiB) holds ~1.5 s of 22050 Hz mono s16 audio
    ratio = (duration - pipe_s) / elapsed if elapsed > 0 else 0
    print(f"[TIMING] file length {duration:.2f}s; FM chain took {elapsed:.2f}s to accept it "
          f"(expect ~{duration - pipe_s:.2f}s). Playback speed vs real time: {ratio:.4f}x", flush=True)
    if abs(ratio - 1.0) > 0.003:
        print(f"[TIMING] WARNING: FM audio is {'FASTER' if ratio > 1 else 'SLOWER'} than real time by "
              f"{abs(ratio - 1) * 100:.2f}% (~{abs(duration * (1 - 1 / ratio)):.1f}s over this track). "
              f"Lights will drift against it; set system.sync.light_speed to ~{ratio:.4f}.", flush=True)


def signal_ready_and_get_t0():
    """Tell the controller we're ready, then block until it sends the shared T0."""
    print(READY_TOKEN, flush=True)
    line = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline().strip()
    return float(line) if line else time.monotonic()  # standalone run: start immediately


def play_windows(abs_audio_path, offset):
    import pygame  # only needed here, so the Pi doesn't require pygame

    print("[INFO] Windows detected: Skipping pi_fm_rds RF hardware generation.")
    os.environ['SDL_AUDIODRIVER'] = 'directsound'
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    pygame.mixer.music.load(abs_audio_path)

    t0 = signal_ready_and_get_t0()
    sleep_until(t0 + offset)

    pygame.mixer.music.play()
    clock = pygame.time.Clock()
    while pygame.mixer.music.get_busy():
        clock.tick(30)


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def play_linux(abs_audio_path, freq, enable_speakers, ps_name, offset, speaker_offset):
    signal.signal(signal.SIGTERM, _raise_interrupt)  # let `finally` reap sox/play/pi_fm_rds
    processes = []
    try:
        fm_proc = None
        if os.path.exists(FIFO_PATH):
            print(f"[INFO] Streaming to active FM carrier pipe ({FIFO_PATH})...")
            sink = FIFO_PATH
        else:
            print(f"[WARN] No FIFO found.  Launching standalone transmitter instance on {freq:.1f} MHz...")
            sink = "-"
            # Start the modulator NOW (it's slow to initialise); sox is attached to it at T0.
            fm_proc = subprocess.Popen(
                ["pi_fm_rds", "-freq", f"{freq:.1f}", "-audio", "-", "-ps", ps_name],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            processes.append(fm_proc)

        sox_cmd = [
            "sox", "-q",
            "--buffer", "1024",
            abs_audio_path,
            "-r", "22050", "-c", "1", "-b", "16", "-e", "signed-integer", "-t", "raw", sink,
            "remix", "1,2",
            "gain", "-1"
        ]

        duration = audio_duration_s(abs_audio_path)
        t0 = signal_ready_and_get_t0()

        # Launch each output at its own (T0-relative) moment, earliest first.
        launches = [(t0 + offset, "fm")]
        if enable_speakers:
            launches.append((t0 + speaker_offset, "speaker"))
        launches.sort()

        sox_proc = speaker_proc = None
        for when, name in launches:
            sleep_until(when)
            if name == "fm":
                sox_started = time.monotonic()
                sox_proc = subprocess.Popen(sox_cmd, stdout=fm_proc.stdin if fm_proc else None)
                processes.append(sox_proc)
                if fm_proc:
                    fm_proc.stdin.close()  # so pi_fm_rds sees EOF when sox finishes
                print(f"[INFO] Broadcasting started on {freq:.1f} MHz...")
            else:
                speaker_proc = subprocess.Popen(["play", "-q", abs_audio_path],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(speaker_proc)

        # Wait for everything to finish.
        sox_proc.wait()
        report_fm_timing(duration, sox_started, time.monotonic())
        if fm_proc:
            fm_proc.wait()
        if speaker_proc:
            speaker_proc.wait()
        print("[INFO] Playback and broadcast completed cleanly.")

    except KeyboardInterrupt:
        print("\n[WARN] Playback interrupted by user. Terminating broadcast processes...")
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()


def play_audio(audio_file, freq, enable_speakers, ps_name, offset, speaker_offset):
    abs_audio_path = os.path.abspath(audio_file)
    if not os.path.isfile(abs_audio_path):
        print(f"[ERROR] Audio file not found: {abs_audio_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Initializing Audio FM Node")
    print(f"[INFO] File: {abs_audio_path}")
    print(f"[INFO] Frequency: {freq:.1f} MHz")
    print(f"[INFO] Speaker Monitoring: {'ENABLED' if enable_speakers else 'DISABLED'}")
    print(f"[INFO] Start offset vs T0: FM {offset:+.3f}s, speakers {speaker_offset:+.3f}s", flush=True)

    if os.name == 'nt':
        play_windows(abs_audio_path, offset)
    else:
        play_linux(abs_audio_path, freq, enable_speakers, ps_name, offset, speaker_offset)


def main():
    args = parse_args()
    validate_environment(args.audio_file, args.freq)
    play_audio(
        audio_file=args.audio_file,
        freq=args.freq,
        enable_speakers=args.speakers,
        ps_name=args.ps_name,
        offset=args.offset,
        speaker_offset=args.speaker_offset,
    )


if __name__ == "__main__":
    main()