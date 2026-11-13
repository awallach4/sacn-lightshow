#!/usr/bin/env python3
"""
Audio Playback Node
-------------------
Handles synchronized audio playback to the default system audio output
(e.g., USB FM transmitter) on both Windows and Linux.

Sync protocol (with show_control.py):
  1. Pre-loads the audio file into pygame.mixer.
  2. Prints READY_TOKEN on stdout.
  3. Reads shared start time T0 (time.monotonic() clock) from stdin.
  4. Starts playback at T0 + offset.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame

from logger_setup import get_node_logger, setup_global_exception_handler

READY_TOKEN = "@@READY@@"
CONFIG_FILE = Path("config/main_config.json")


def load_log_level() -> str:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("log_level", "INFO")
    except Exception:
        pass
    return "INFO"


log = get_node_logger("audio", load_log_level())
setup_global_exception_handler(log)


def parse_args():
    parser = argparse.ArgumentParser(description="Synchronized Audio Playback Node")
    parser.add_argument("audio_file", type=str, help="Path to the audio file (.wav, .mp3, .ogg, .flac)")
    parser.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Seconds to shift playback relative to T0 (default: 0.0)",
    )
    return parser.parse_args()


def sleep_until(deadline: float):
    """Sleep until a time.monotonic() deadline (coarse sleep, then a short spin)."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if remaining > 0.004:
            time.sleep(remaining - 0.003)


def signal_ready_and_get_t0() -> float:
    """Tell the controller we're ready, then block until it sends the shared T0."""
    print(READY_TOKEN, flush=True)
    line = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline().strip()
    return float(line) if line else time.monotonic()


def audio_duration_s(path: str):
    """Audio length in seconds via pygame, or None if unavailable."""
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        return float(pygame.mixer.Sound(path).get_length())
    except Exception:
        return None


def _handle_sigterm(*_):
    raise KeyboardInterrupt


def play_audio(audio_file: str, offset: float = 0.0):
    abs_path = os.path.abspath(audio_file)
    if not os.path.isfile(abs_path):
        log.error(f"[AUDIO] Audio file not found: {abs_path}")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    pygame.mixer.music.load(abs_path)

    log.info(f"[AUDIO] Ready to play: {abs_path}")
    t0 = signal_ready_and_get_t0()
    sleep_until(t0 + offset)

    pygame.mixer.music.play()
    clock = pygame.time.Clock()
    try:
        while pygame.mixer.music.get_busy():
            clock.tick(30)
    except KeyboardInterrupt:
        log.warning("[AUDIO] Playback interrupted. Stopping...")
    finally:
        pygame.mixer.music.stop()
        pygame.mixer.quit()


def main():
    args = parse_args()
    play_audio(audio_file=args.audio_file, offset=args.offset)


if __name__ == "__main__":
    main()