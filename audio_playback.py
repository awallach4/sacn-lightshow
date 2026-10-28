#!/usr/bin/env python3
"""
Audio FM Broadcast Node
-----------------------
Handles audio file playback and manages the FM transmitter output frequency.
Supports dual-output playback (FM broadcast + local speaker monitoring).
"""

import sys
import os
import argparse
import subprocess
import signal
import time
import shutil

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
    return parser.parse_args()

def validate_environment(audio_file, freq):
    if not os.path.isfile(audio_file):
        print(f"[ERROR] Audio file not found: {audio_file}", file=sys.stderr)
        sys.exit(1)

    if not (87.5 <= freq <= 108.0):
        print(f"[ERROR] Frequency {freq} MHz is outside the standard FM broadcast band (87.5 - 108.0 MHz)", file=sys.stderr)
        sys.exit(1)

def play_audio(audio_file, freq, enable_speakers, ps_name):
    IS_WINDOWS = (os.name == 'nt')
    fifo_path = "/tmp/fm_audio.fifo"
    abs_audio_path = os.path.abspath(audio_file)
    if not os.path.isfile(abs_audio_path):
        print(f"[ERROR] Audio file not found: {abs_audio_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Initializing Audio FM Node")
    print(f"[INFO] File: {abs_audio_path}")
    print(f"[INFO] Frequency: {freq:.1f} MHz")
    print(f"[INFO] Speaker Monitoring: {'ENABLED' if enable_speakers else 'DISABLED'}")

    if IS_WINDOWS:
        print("[INFO] Windows detected: Skipping pi_fm_rds RF hardware generation.")

        sox_path = shutil.which("sox")

        if sox_path:
            cmd = [
                "sox", "-q",
                "--buffer", "1024",
                abs_audio_path,
                "-r", "22050", "-c", "1", "-b", "16", "-e", "signed-integer", "-t", "raw", "default",
                "remix", "1,2",
                "gain", "-1"
            ]
            proc = subprocess.Popen(cmd)
            processes.append(proc)
            proc.wait()
        else:
            ps_script = f"""
            Add-Type -AssemblyName presentationCore;
            $mediaPlayer = New-Object System.Windows.Media.MediaPlayer;
            $mediaPlayer.Open([System.Uri]'{abs_audio_path}');
            $mediaPlayer.Play();
            Start-Sleep -s 1;
            while ($mediaPlayer.NaturalDuration.HasTimeSpan -and ($mediaPlayer.Position -lt $mediaPlayer.NaturalDuration.TimeSpan)) {{
                Start-Sleep -m 250;
            }}
            """
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
            proc = subprocess.Popen(cmd)
            proc.wait()

        if proc.returncode == 0:
            print("[INFO] Verification: Speaker playback completed successfully.")
        else:
            print(f"[WARN] SoX exited with code {proc.returncode}.  Verify SoX is added to Windows PATH.", file=sys.stderr)
    else:
        processes = []
        try:
            if os.path.exists(fifo_path):
                print("[INFO] Streaming to active FM carrier pipe (/tmp/fm_audio.fifo)...")
                sox_cmd = [
                    "sox", "-q",
                    "--buffer", "1024",
                    abs_audio_path,
                    "-r", "22050", "-c", "1", "-b", "16", "-e", "signed-integer", "-t", "raw", fifo_path,
                    "remix", "1,2",
                    "gain", "-1"
                ]

                if enable_speakers:
                    # Local speaker process running simultaneously via SoX/play or aplay
                    speaker_cmd = ["play", "-q", abs_audio_path]
                    speaker_proc = subprocess.Popen(speaker_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    processes.append(speaker_proc)


                # Execute audio decoder/resampler process
                sox_proc = subprocess.Popen(sox_cmd)
                sox_proc.wait()
            else:
                print(f"[WARN] No FIFO found.  Launching standalone transmitter instance on {freq: .1f} MHz...")

                sox_cmd = [
                    "sox", "-q",
                    "--buffer", "1024",
                    abs_audio_path,
                    "-r", "22050", "-c", "1", "-b", "16", "-e", "signed-integer", "-t", "raw", "-",
                    "remix", "1,2",
                    "gain", "-1"
                ]

                # Execute audio decoder/resampler process
                sox_proc = subprocess.Popen(sox_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                processes.append(sox_proc)

                if enable_speakers:
                    # Local speaker process running simultaneously via SoX/play or aplay
                    speaker_cmd = ["play", "-q", abs_audio_path]
                    speaker_proc = subprocess.Popen(speaker_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    processes.append(speaker_proc)

                # FM Modulator process (e.g., PiFmRds or direct hardware transmitter driver)
                # Transmits 22.5kHz mono raw PCM from stdin
                fm_cmd = [
                    "pi_fm_rds",
                    "-freq", f"{freq:.1f}",
                    "-audio", "-",
                    "-ps", ps_name
                ]

                fm_proc = subprocess.Popen(fm_cmd, stdin=sox_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                processes.append(fm_proc)

                # Allow sox_proc to receive SIGPIPE if fm_proc exits
                sox_proc.stdout.close()

                print(f"[INFO] Broadcasting started on {freq:.1f} MHz...")
                
                # Wait for playback completion
                sox_proc.wait()
                fm_proc.wait()
                
                if enable_speakers and 'speaker_proc' in locals():
                    speaker_proc.wait()

                print("[INFO] Playback and broadcast completed cleanly.")

        except KeyboardInterrupt:
            print("\n[WARN] Playback interrupted by user. Terminating broadcast processes...")
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()

def main():
    args = parse_args()
    validate_environment(args.audio_file, args.freq)
    play_audio(
        audio_file=args.audio_file,
        freq=args.freq,
        enable_speakers=args.speakers,
        ps_name=args.ps_name
    )

if __name__ == "__main__":
    main()