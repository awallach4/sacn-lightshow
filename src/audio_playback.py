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

import json
from pathlib import Path
import sys
import os
import argparse
import signal
import stat
import subprocess
import time

from logger_setup import get_node_logger, setup_global_exception_handler

READY_TOKEN = "@@READY@@"
FIFO_PATH = "/tmp/fm_audio.fifo"
CONFIG_FILE = Path("config/main_config.json")

def load_log_level() -> str:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("log_level", "INFO")
    except Exception:
        pass
    return "INFO"

# Initialize node logger
log = get_node_logger("audio", load_log_level())
setup_global_exception_handler(log)

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
        "--no-fm",
        action="store_true",
        help="Disable FM entirely (no sox/pi_fm_rds); play through the local speakers only"
    )
    parser.add_argument(
        "--ps-name",
        type=str,
        default="HOLIDAY",
        help="RDS Program Service station name (max 8 chars, default: HOLIDAY)"
    )
    parser.add_argument(
        "--rds-text",
        type=str,
        help="RDS description text (max 64 chars)"
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
        log.error(f"[AUDIO] Audio file not found: {audio_file}")
        sys.exit(1)

    if not (87.5 <= freq <= 108.0):
        log.error(f"[AUDIO] Frequency {freq} MHz is outside the standard FM broadcast band (87.5 - 108.0 MHz)")
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


def report_fm_timing(duration, started, finished, sox_returncode=0):
    """sox can only finish once the FM modulator has consumed (almost) all the audio, so how long
    it took tells us how fast the FM chain really plays the stream compared with the wall clock."""
    if sox_returncode:
        log.error(f"[AUDIO] sox exited with code {sox_returncode}; the audio did not play correctly.")
    if not duration:
        return
    elapsed = finished - started
    pipe_s = 65536 / 44100.0  # default Linux pipe (64 KiB) holds ~1.5 s of 22050 Hz mono s16 audio
    expected = max(duration - pipe_s, 0.1)
    ratio = expected / elapsed if elapsed > 0 else float("inf")
    if ratio > 1.5 or ratio < 0.67:
        log.error(f"[TIMING] sox took {elapsed:.2f}s to feed a {duration:.2f}s file to the FM chain "
              f"(expected ~{expected:.2f}s). Nothing was reading the stream at real-time speed, so the audio "
              f"was almost certainly NOT broadcast. Check that the FM carrier is running and that "
              f"{FIFO_PATH} is a FIFO (`ls -l {FIFO_PATH}` should start with 'p').")
        return
    log.debug(f"[TIMING] file length {duration:.2f}s; FM chain took {elapsed:.2f}s to accept it "
          f"(expect ~{expected:.2f}s). Playback speed vs real time: {ratio:.4f}x")
    if abs(ratio - 1.0) > 0.003:
        log.warning(f"[TIMING] FM audio is {'FASTER' if ratio > 1 else 'SLOWER'} than real time by "
              f"{abs(ratio - 1) * 100:.2f}% (~{abs(duration * (1 - 1 / ratio)):.1f}s over this track). "
              f"Lights will drift against it; set system.sync.light_speed to ~{ratio:.4f}.")


def signal_ready_and_get_t0():
    """Tell the controller we're ready, then block until it sends the shared T0."""
    print(READY_TOKEN, flush=True)
    line = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline().strip()
    return float(line) if line else time.monotonic()  # standalone run: start immediately


def play_windows(abs_audio_path, offset):
    import pygame  # only needed here, so the Pi doesn't require pygame

    log.info("[AUDIO] Windows detected: Skipping pi_fm_rds RF hardware generation.")
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


def play_linux_speakers_only(abs_audio_path, speaker_offset):
    """FM disabled (e.g. dev VM): just play through the local sound device, still starting at T0."""
    signal.signal(signal.SIGTERM, _raise_interrupt)
    proc = None
    try:
        log.info("[AUDIO] FM disabled: playing through local speakers only.")
        t0 = signal_ready_and_get_t0()
        sleep_until(t0 + speaker_offset)
        try:
            proc = subprocess.Popen(["play", "-q", abs_audio_path], stdout=subprocess.DEVNULL)
        except FileNotFoundError:
            log.warning("[AUDIO] 'play' (from the sox package) is not installed; no audio will be played.")
            return
        rc = proc.wait()
        if rc:
            log.warning(f"[AUDIO] play exited with code {rc} (no sound device?); audio did not play.")
    except KeyboardInterrupt:
        log.warning("\n[AUDIO] Playback interrupted. Stopping speaker playback...")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


def play_linux(abs_audio_path, freq, enable_speakers, ps_name, rt_text, offset, speaker_offset):
    signal.signal(signal.SIGTERM, _raise_interrupt)  # let `finally` reap sox/play/pi_fm_rds
    processes = []
    try:
        fm_proc = None
        if os.path.exists(FIFO_PATH):
            if not stat.S_ISFIFO(os.stat(FIFO_PATH).st_mode):
                log.error(f"[AUDIO] {FIFO_PATH} exists but is NOT a named pipe (FIFO). sox would just write the "
                      f"audio into a plain file and nothing would be broadcast. Remove it "
                      f"(rm {FIFO_PATH}) and restart so the FM carrier can recreate it.")
                sys.exit(1)
            log.info(f"[AUDIO] Streaming to active FM carrier pipe ({FIFO_PATH})...")
            sink = FIFO_PATH
        else:
            log.warning(f"[AUDIO] No FIFO found.  Launching standalone transmitter instance on {freq:.1f} MHz...")
            sink = "-"
            # Start the modulator NOW (it's slow to initialise); sox is attached to it at T0.
            fm_cmd = ["pi_fm_rds", "-freq", f"{freq:.1f}", "-audio", "-", "-ps", ps_name]
            if rt_text:  # passing None here used to raise TypeError when --rds-text wasn't given
                fm_cmd += ["-rt", rt_text]
            fm_proc = subprocess.Popen(fm_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            processes.append(fm_proc)

        # FIFO mode: the controller already wrote a WAV header into the pipe, so send headerless PCM.
        # Standalone mode: pi_fm_rds reads this stream from scratch and its libsndfile input needs a
        # real header, so send WAV.
        out_type = "wav" if fm_proc else "raw"
        sox_cmd = [
            "sox", "-q",
            "--buffer", "1024",
            abs_audio_path,
            "-r", "22050", "-c", "1", "-b", "16", "-e", "signed-integer", "-t", out_type, sink,
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
                log.info(f"[AUDIO] Broadcasting started on {freq:.1f} MHz...")
            else:
                speaker_proc = subprocess.Popen(["play", "-q", abs_audio_path],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(speaker_proc)

        # Wait for everything to finish.
        sox_proc.wait()
        report_fm_timing(duration, sox_started, time.monotonic(), sox_proc.returncode)
        if fm_proc:
            fm_proc.wait()
        if speaker_proc:
            speaker_proc.wait()
        log.info("[AUDIO] Playback and broadcast completed cleanly.")

    except KeyboardInterrupt:
        log.warning("\n[AUDIO] Playback interrupted by user. Terminating broadcast processes...")
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()


def play_audio(audio_file, freq, enable_speakers, ps_name, rt_text, offset, speaker_offset, no_fm=False):
    abs_audio_path = os.path.abspath(audio_file)
    if not os.path.isfile(abs_audio_path):
        log.error(f"[AUDIO] Audio file not found: {abs_audio_path}")
        sys.exit(1)

    log.info(f"[AUDIO] Initializing Audio FM Node")
    log.info(f"[AUDIO] File: {abs_audio_path}")
    log.info(f"[AUDIO] Frequency: {freq:.1f} MHz")
    log.info(f"[AUDIO] Speaker Monitoring: {'ENABLED' if enable_speakers else 'DISABLED'}")
    log.info(f"[AUDIO] Start offset vs T0: FM {offset:+.3f}s, speakers {speaker_offset:+.3f}s")

    if os.name == 'nt':
        play_windows(abs_audio_path, offset)
    elif no_fm:
        play_linux_speakers_only(abs_audio_path, speaker_offset)
    else:
        play_linux(abs_audio_path, freq, enable_speakers, ps_name, rt_text, offset, speaker_offset)


def main():
    args = parse_args()
    validate_environment(args.audio_file, args.freq)
    play_audio(
        audio_file=args.audio_file,
        freq=args.freq,
        enable_speakers=args.speakers,
        ps_name=args.ps_name,
        rt_text=args.rds_text,
        offset=args.offset,
        speaker_offset=args.speaker_offset,
        no_fm=args.no_fm,
    )


if __name__ == "__main__":
    main()