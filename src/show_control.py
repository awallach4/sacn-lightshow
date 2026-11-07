#!/usr/bin/env python3
"""
Master Show Controller Daemon
-----------------------------
Location: src/show_control.py

Loads central configuration files (main_config.json, playlist.json, patch.json)
to orchestrate audio streaming and sACN lighting sequences.

Usage:
    python3 src/show_control.py [-c config/main_config.json]

Optional main_config.json keys (all have defaults):
    system.sync.lead_time_s       runway between "workers ready" and T0 (default 1.0)
    system.sync.ready_timeout_s   max seconds to wait for workers to finish setup (default 60)
    system.sync.end_grace_s       extra seconds past the track's expected end (sequence/audio length +
                                  button_hold_s) before a stuck worker is killed (default 15)
    system.broadcast.fm_enabled        false = no FM carrier; audio plays on local speakers (default true)
"""

import sys
import os
import time
import json
import signal
import stat
import struct
import threading
import subprocess
import argparse
from pathlib import Path
import urllib.request

from logger_setup import get_node_logger, setup_global_exception_handler

try:
    import psutil
except ImportError:
    psutil = None

# Base Directory Paths
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
DEFAULT_MAIN_CONFIG = PROJECT_ROOT / "config" / "main_config.json"

# Where the web uploader stores files; used to resolve bare filenames in playlist.json
ASSET_DIRS = {"audio": "showfiles/music", "sequence": "showfiles/sequences"}

def get_system_status() -> dict:
    """Queries Initializer Node for system state."""
    url = f"{INIT_NODE_URL}/api/status"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}

# Global Execution State
fm_carrier_proc = None
active_processes = []
is_terminating = False
sys_config = {}

# ==============================================================================
# CONFIG LOADER
# ==============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="Master Show Controller Daemon")
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=DEFAULT_MAIN_CONFIG,
        help="Path to main system configuration JSON (default: config/main_config.json)"
    )
    parser.add_argument("-p", "--playlist", type=str, default="", help="The name of the playlist in playlist.json to use.")
    return parser.parse_args()

def load_log_level() -> str:
    try:
        if Path(DEFAULT_MAIN_CONFIG).exists():
            with open(Path(DEFAULT_MAIN_CONFIG), "r", encoding="utf-8") as f:
                return json.load(f).get("log_level", "INFO")
    except Exception:
        pass
    return "INFO"

log = get_node_logger("show_control", load_log_level())
setup_global_exception_handler(log)

def load_system_configs(args):
    """Loads main_config.json and cleanly resolves linked playlist asset paths against PROJECT_ROOT."""
    abs_main_path = Path(args.config).resolve()

    if not abs_main_path.is_file():
        log.error(f"[SHOW] Main config file not found: {abs_main_path}")
        sys.exit(1)

    with open(abs_main_path, "r") as f:
        config_data = json.load(f)

    sys_settings = config_data.get("system", {})
    paths = sys_settings.get("paths", {})

    # Resolve playlist config path cleanly against PROJECT_ROOT
    raw_playlist_path = paths.get("playlist_config", "config/playlist.json")
    playlist_path = (PROJECT_ROOT / raw_playlist_path.lstrip("/\\")).resolve()

    if not playlist_path.is_file():
        log.error(f"[SHOW] Playlist file not found: {playlist_path}")
        sys.exit(1)

    with open(playlist_path, "r") as f:
        playlist_file_data = json.load(f)

    target_playlist_name = args.playlist if len(args.playlist) > 0 else (sys_settings.get("playlist_name", "playlist") or paths.get("playlist_name", "playlist"))

    if isinstance(playlist_file_data, dict):
        if target_playlist_name not in playlist_file_data['playlists'].keys():
            log.error(f"[SHOW] Playlist key '{target_playlist_name}' not found in {playlist_path}")
            sys.exit(1)
        playlist_data = playlist_file_data['playlists'][target_playlist_name]
    elif isinstance(playlist_file_data, list):
        playlist_data = playlist_file_data
    else:
        log.error(f"[SHOW] Invalid format in playlist file: {playlist_path}")
        sys.exit(1)

    # Resolve relative audio and sequence paths inside playlist items
    for item in playlist_data:
        for key in ["audio", "sequence"]:
            raw_asset_path = item[key]

            p = Path(raw_asset_path)

            if not p.is_absolute():
                resolved_asset = (PROJECT_ROOT / raw_asset_path.lstrip("/\\")).resolve()
                # The web uploader stores bare filenames and saves the files under showfiles/.
                # If the path isn't valid as written, look there too.
                if not resolved_asset.exists():
                    fallback = (PROJECT_ROOT / ASSET_DIRS[key] / raw_asset_path.lstrip("/\\")).resolve()
                    if fallback.exists():
                        resolved_asset = fallback
            else:
                resolved_asset = p.resolve()

            item[key] = str(resolved_asset)

    return sys_settings, playlist_data

# ==============================================================================
# TELEMETRY
# ==============================================================================

def notify_current_track(now_playing: str, next_up: str):
    """Notify Initializer Node of current track state without heavy file I/O or extra threads."""
    try:
        url = "http://127.0.0.1:5001/api/show/progress"
        payload = json.dumps({"now_playing": now_playing, "next_up": next_up}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception:
        pass

# ==============================================================================
# EMERGENCY PANIC & HARDWARE SAFETY
# ==============================================================================

INIT_NODE_URL = "http://127.0.0.1:5001"

# GOOD: Local sACN blackout frame without IPC macro POSTs
def send_sacn_blackout(universe=11, priority=100):
    """Sends a zero-value sACN blackout frame directly to the network."""
    try:
        import sacn
        sender = sacn.sACNsender(source_name="ShowControl_InterTrack")
        sender.start()
        sender.activate_output(universe)
        sender[universe].priority = priority
        sender[universe].dmx_data = (0,) * 512
        time.sleep(0.05)
        sender.deactivate_output(universe)
        sender.stop()
    except Exception as e:
        log.warning(f"[SHOW] Could not send sACN blackout frame: {e}")


def terminate_all_children():
    """
    Instantly mass-kills all registered child processes AND their entire 
    process groups using POSIX SIGKILL to prevent orphaned audio/sACN threads.
    """
    global active_processes, fm_carrier_proc

    log.info("[SHOW CONTROL] Mass-terminating process groups...")

    # 1. Kill active track worker process groups (audio + sACN)
    for proc in active_processes:
        if proc and proc.poll() is None:
            try:
                if os.name != 'nt':
                    # Send SIGKILL (-9) to the ENTIRE process group
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    # Windows fallback: taskkill /F /T terminates process tree
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], 
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                log.error(f"[SHOW CONTROL] Error killing process group {proc.pid}: {e}")

    active_processes.clear()

    # 2. Terminate persistent FM carrier process group
    if fm_carrier_proc and fm_carrier_proc.poll() is None:
        try:
            log.info("[SHOW CONTROL] Killing FM carrier process group...")
            if os.name != 'nt':
                os.killpg(os.getpgid(fm_carrier_proc.pid), signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(fm_carrier_proc.pid)], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.error(f"[SHOW CONTROL] Error killing FM carrier: {e}")
        fm_carrier_proc = None


def cleanup_and_exit(signum=None, frame=None):
    """
    Universal Signal Handler for SIGINT, SIGTERM, and unexpected exits.
    Ensures audio and sACN sub-processes stop instantly when show_control is killed.
    """
    global is_terminating
    if is_terminating:
        return
    is_terminating = True

    sig_name = signal.Signals(signum).name if signum else "INTERNAL_CLEANUP"
    log.info(f"\n[SHOW CONTROL] Signal received ({sig_name}). Executing immediate process group purge...")

    # Nuke all child audio, sACN, and worker processes instantly
    terminate_all_children()

    # Clean up FIFO pipe
    fifo_path = "/tmp/fm_audio.au"
    if os.path.exists(fifo_path):
        try:
            os.remove(fifo_path)
        except OSError:
            pass

    log.info("[SHOW CONTROL] All process groups terminated and hardware interfaces released. Exiting.")
    sys.exit(0)

def emergency_panic_latch(signum=None, frame=None):
    """Theatrical Emergency Panic Latch - triggers panic on Initializer Node then cleans up."""
    log.warning("\n[EMERGENCY] Panic Latch Triggered! Dispatching PANIC to Initializer Node...")
    try:
        url = f"{INIT_NODE_URL}/api/macro"
        payload = json.dumps({"action": "PANIC"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception as e:
        log.error(f"[EMERGENCY] Failed to notify Initializer Node: {e}")

    cleanup_and_exit(signum, frame)

# ==============================================================================
# FM TRANSMITTER CARRIER MANAGEMENT
# ==============================================================================

# Keep module-level variable so Python's GC doesn't close the file descriptor
dummy_fifo_fd = None
fm_carrier_proc = None

def init_persistent_fm_carrier():
    """Replicates the working CLI carrier setup cleanly in Python."""
    global fm_carrier_proc, sys_config, dummy_fifo_fd

    broadcast_cfg = sys_config.get("broadcast", {})
    freq = broadcast_cfg.get("fm_frequency", 90.3)
    ps_name = broadcast_cfg.get("rds_ps_name", "HOLIDAY")
    rt_text = broadcast_cfg.get("rds_rt_default", "Lightshow")
    fifo_path = broadcast_cfg.get("fifo_path", "/tmp/fm_audio.au")

    if not broadcast_cfg.get("fm_enabled", True) or os.name == 'nt':
        return

    # Step 1: rm -f /tmp/fm_audio.*
    if os.path.exists(fifo_path):
        try:
            os.remove(fifo_path)
        except OSError:
            pass

    # Step 2: mkfifo /tmp/fm_audio.au
    os.mkfifo(fifo_path)

    # Step 3: fd = os.open('/tmp/fm_audio.au', os.O_RDWR)
    # Using low-level os.open (O_RDWR | O_NONBLOCK) opens the write end instantly
    # without blocking the main Python thread.
    try:
        dummy_fifo_fd = os.open(fifo_path, os.O_RDWR | os.O_NONBLOCK)
        log.info(f"[INIT] Established persistent O_RDWR handle on {fifo_path} (fd={dummy_fifo_fd})")
    except Exception as e:
        log.error(f"[INIT] Failed to acquire persistent FIFO handle: {e}")
        return

    log.info(f"[INIT] Launching persistent FM carrier on {freq:.1f} MHz...")
    fifo_read_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)

    # Step 4: /usr/local/bin/pi_fm_rds -freq 90.3 -audio /tmp/fm_audio.au ...
    fm_cmd = [
        "/usr/local/bin/pi_fm_rds",
        "-freq", str(freq),
        "-audio", fifo_path,
        "-ps", ps_name,
        "-rt", rt_text
    ]

    fm_log_file = os.open("/tmp/pi_fm_rds.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)

    # Pass close_fds=True to prevent pi_fm_rds from inheriting the dummy_fifo_fd handle
    fm_carrier_proc = subprocess.Popen(
        fm_cmd,
        stdin=fifo_read_fd,
        stdout=subprocess.DEVNULL,
        stderr=fm_log_file,
        close_fds=True,
        start_new_session=True
    )

    os.close(fifo_read_fd)
    os.close(fm_log_file)

    # Give pi_fm_rds time to initialize DMA and open the reader side of the FIFO
    time.sleep(1.0)

    if fm_carrier_proc.poll() is not None:
        log.error("[INIT] pi_fm_rds exited unexpectedly! Check /tmp/pi_fm_rds.log")


# ==============================================================================
# PLAYLIST EXECUTION LOOP
# ==============================================================================

READY_TOKEN = "@@READY@@"


def check_track_lengths(audio_path, sequence_path):
    """Pre-flight: warn if the FSEQ and the audio file are different lengths."""
    try:
        with open(sequence_path, "rb") as f:
            hdr = f.read(32)
        import struct
        frames = struct.unpack_from("<I", hdr, 14)[0]
        seq_s = frames * hdr[18] / 1000.0
        out = subprocess.run(["soxi", "-D", audio_path], capture_output=True, text=True, timeout=10)
        audio_s = float(out.stdout.strip())
    except Exception:
        return None, None  # soxi missing / unreadable header: skip silently
    log.debug(f"[CHECK] audio {audio_s:.2f}s vs sequence {seq_s:.2f}s (difference {seq_s - audio_s:+.2f}s)")
    if abs(seq_s - audio_s) > 0.5:
        log.warning("[CHECK] WARNING: sequence and audio lengths differ by more than 0.5s. If the sequence is "
              "SHORTER, the lights end early by design of the file (re-render/re-export in xLights).")
    return audio_s, seq_s


def _pump_output(proc, name, ready_event):
    """Forward a worker's stdout to our console and watch for its READY token.

    Draining the pipe also guarantees a chatty worker can never block on a full pipe.
    """
    for line in proc.stdout:
        line = line.rstrip()
        if line == READY_TOKEN:
            ready_event.set()
        elif line:
            log.info(f"[{name}] {line}")


def _wait_until_ready(workers, timeout_s):
    """workers: list of (name, proc, ready_event). Raises if one dies or times out."""
    deadline = time.monotonic() + timeout_s
    while True:
        if all(ev.is_set() for _, _, ev in workers):
            return
        for name, proc, ev in workers:
            if not ev.is_set() and proc.poll() is not None:
                raise RuntimeError(f"{name} worker exited (code {proc.returncode}) before becoming ready")
        if time.monotonic() > deadline:
            raise RuntimeError(f"Timed out after {timeout_s:.0f}s waiting for workers to become ready")
        time.sleep(0.02)


def kill_process_group_instantly(proc):
    """Forcefully kills a subprocess and its process group while closing pipes."""
    if not proc or proc.poll() is not None:
        return

    # Close standard pipes to unblock _pump_output daemon threads
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe:
            try:
                pipe.close()
            except Exception:
                pass

    # Issue instant SIGKILL to the entire process group
    try:
        if os.name != 'nt':
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass


def run_track(track_idx, playlist):
    """Executes a track by index from the master playlist with T0 clock sync."""
    global active_processes, sys_config, is_terminating
    total_tracks = len(playlist)

    status = get_system_status()
    current_state = status.get("system_state", "UNKNOWN")
    is_panicked = current_state == "PANIC"
    
    if not (0 <= track_idx < total_tracks):
        log.error(f"[SHOW] Track index {track_idx} out of range.")
        return False

    if is_panicked:
        log.error("[SHOW] Cannot run show while system is in panic.")
        return False

    current_track = playlist[track_idx]
    audio_path = current_track["audio"]
    sequence_path = current_track["sequence"]
    now_playing_title = current_track.get("title", os.path.splitext(os.path.basename(audio_path))[0])

    if track_idx + 1 < total_tracks:
        next_track = playlist[track_idx + 1]
        next_up_title = next_track.get("title", os.path.splitext(os.path.basename(next_track["audio"]))[0])
    else:
        next_up_title = "Show Complete"

    notify_current_track(now_playing=now_playing_title, next_up=next_up_title)

    if not os.path.isfile(audio_path):
        log.error(f"[SHOW] Audio file missing: {audio_path}")
        return False
    if not os.path.isfile(sequence_path):
        log.error(f"[SHOW] Sequence file missing: {sequence_path}")
        return False

    log.info(f"\n==================================================")
    log.info(f"[SHOW] Executing Track [{track_idx + 1}/{total_tracks}]: {now_playing_title}")
    log.info(f"[SHOW] Sequence Target: {os.path.basename(sequence_path)}")
    log.info(f"==================================================")

    processes = []
    try:
        sync_cfg = sys_config.get("sync", {})
        broadcast_cfg = sys_config.get("broadcast", {})
        lead_time_s = sync_cfg.get("lead_time_s", 1.0)          # runway between READY and T0
        ready_timeout_s = sync_cfg.get("ready_timeout_s", 60.0)  # max time for decode/setup
        end_grace_s = sync_cfg.get("end_grace_s", 15.0)          # hang guard once one worker finishes
        fm_offset_s = current_track.get("audio_offset_s", 0.0)   # calibrate FM latency (+ = later)
        speaker_offset_s = current_track.get("speaker_offset_s", 0.0)
        rt_text = current_track.get("rds", broadcast_cfg['rds_rt_default'])

        audio_script = os.path.join(SRC_DIR, "audio_playback.py")
        sacn_script = os.path.join(SRC_DIR, "sacn/sacn_transmitter.py")
        universe = sys_config.get("network", {}).get("sacn_universe", 1)

        button_hold = current_track.get("button_hold_s", 0)

        light_speed = current_track.get("light_speed", 1.0)           # <1 slows lights (audio runs long)
        if (sys_config.get("broadcast", {}).get("fm_enabled", True) and os.name != 'nt'
                and (fm_carrier_proc is None or fm_carrier_proc.poll() is not None)):
            log.error(f"[SHOW] FM carrier (pi_fm_rds) is NOT running; this track will not be broadcast. {_fm_log_tail()}")
        audio_s, seq_s = check_track_lengths(audio_path, sequence_path)

        audio_cmd = [sys.executable, "-u", audio_script, audio_path,
                     "--offset", str(fm_offset_s), "--speaker-offset", str(speaker_offset_s), "--rds-text", rt_text]
        if not broadcast_cfg.get("fm_enabled", True):
            audio_cmd.append("--no-fm")
        sacn_cmd = [sys.executable, "-u", sacn_script, sequence_path, "--universe", str(universe),
                    "--button-hold", str(button_hold), "--multicast",
                    "--speed", str(light_speed)]

        popen_kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1)
        audio_proc = subprocess.Popen(audio_cmd, **popen_kw)
        sacn_proc = subprocess.Popen(sacn_cmd, **popen_kw)

        processes.extend([audio_proc, sacn_proc])
        active_processes = processes

        try:
            url = f"{INIT_NODE_URL}/api/show/register_workers"
            payload = json.dumps({"pids": [audio_proc.pid, sacn_proc.pid]}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=1.0):
                pass
        except Exception:
            pass

        audio_ready, sacn_ready = threading.Event(), threading.Event()
        for name, proc, ev in (("audio", audio_proc, audio_ready), ("sacn", sacn_proc, sacn_ready)):
            threading.Thread(target=_pump_output, args=(proc, name, ev), daemon=True).start()

        # Phase 1: Wait for READY signals
        _wait_until_ready([("audio", audio_proc, audio_ready), ("sacn", sacn_proc, sacn_ready)],
                          ready_timeout_s)

        # Phase 2: Send T0 clock lock
        t0 = time.monotonic() + lead_time_s
        log.info(f"[SYNC] Both workers ready. T0 Clock Lock Engaged: starting in {lead_time_s:.2f}s.")
        trigger_msg = f"{t0!r}\n"
        for proc in (audio_proc, sacn_proc):
            proc.stdin.write(trigger_msg)
            proc.stdin.flush()

        # Phase 3: Wait for completion or early abort signal
        first_exit = None
        # Hang guard: a worker still running after the other exited is only killed once it is past the
        # time the track should have taken (longest of audio/sequence + button hold) plus a grace period,
        # so lights can't be cut off just because the audio side stopped early (e.g. no sound device).
        expected_s = max([x for x in (audio_s, seq_s) if x] or [0.0])
        hard_deadline = t0 + expected_s + float(button_hold) + end_grace_s
        while True:
            audio_done = audio_proc.poll() is not None
            sacn_done = sacn_proc.poll() is not None
            if audio_done and sacn_done:
                break
            if audio_done or sacn_done:
                now = time.monotonic()
                if first_exit is None:
                    first_exit = now
                if now > max(hard_deadline, first_exit + end_grace_s):
                    laggard = "sacn" if audio_done else "audio"
                    log.warning(f"[SHOW] {laggard} worker still running well past the expected end of the track; "
                          f"terminating it.")
                    break
            time.sleep(0.05)

        for name, proc in (("audio", audio_proc), ("sacn", sacn_proc)):
            if proc.poll() not in (None, 0):
                log.warning(f"[SHOW] {name} worker exited with code {proc.returncode}")

        log.info(f"[SHOW] Track '{now_playing_title}' completed cleanly")

    except KeyboardInterrupt:
        log.warning("\n[SHOW] Track execution aborted by signal.")
        raise
    except Exception as e:
        log.error(f"[SHOW] Exception during track playback: {e}")
        return False
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        active_processes = []

    # Check if a macro killed us during the track
    if is_terminating or is_panicked:
        return False

    # Inter-track delay
    try:
        inter_track_delay = current_track.get("post_delay_s", 3.0)
        log.info("[SHOW] Holding inter-track blackout head...")
        time.sleep(inter_track_delay)
    except Exception as e:
        log.warning(f"[SHOW] Non-fatal error during inter-track delay: {e}")

    return True  # Proceed to track idx + 1

def graceful_shutdown():
    """Clean, non-emergency shutdown for normal show completion."""
    global fm_carrier_proc, sys_config
    log.info("\n[SHOW] Performing graceful show completion and blackout...")
    
    # Issue standard Priority 100 blackout frame
    net_cfg = sys_config.get("network", {})
    universe = net_cfg.get("sacn_universe", 1)
    priority = net_cfg.get("priority_normal", 100)
    send_sacn_blackout(universe=universe, priority=priority)

    # Terminate persistent FM process cleanly if running
    if fm_carrier_proc and fm_carrier_proc.poll() is None:
        fm_carrier_proc.terminate()

    log.info("[SHOW] Show engine stopped cleanly. Exiting with status 0.")

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    global sys_config
    args = parse_args()

    # Register OS Signal Handlers
    signal.signal(signal.SIGINT, cleanup_and_exit)   # Ctrl+C
    signal.signal(signal.SIGTERM, cleanup_and_exit)  # systemd stop / kill

    # Load Central Configs
    sys_config, playlist = load_system_configs(args)

    log.info("==================================================")
    log.info(f"   {sys_config.get('show_name', 'RESIDENTIAL LIGHT SHOW DAEMON')}   ")
    log.info("==================================================")
    log.info(f"[INIT] Loaded system configuration from: {os.path.abspath(args.config)}")
    log.info(f"[INIT] Playlist contains {len(playlist)} tracks.")

    # Initialize persistent broadcast carrier
    init_persistent_fm_carrier()

    show_failed = False

    try:
        for idx in range(len(playlist)):
            log.info(f"\n[SHOW CONTROL] Starting track index {idx} of {len(playlist)}...")
            status = get_system_status()
            current_state = status.get("system_state", "UNKNOWN")
            if current_state == "PANIC":
                show_failed = True
                break
            success = run_track(idx, playlist)      
            if not success:
                log.info(f"[SHOW CONTROL] Track index {idx} returned success=False. Aborting playlist loop.")
                break
        else:
            log.info("[SHOW CONTROL] Completed all tracks in playlist successfully.")            

    except Exception as e:
        log.error(f"[SHOW CONTROL] Unexpected exception in main show loop: {e}")
        show_failed = True

    if show_failed or current_state == "PANIC":
        emergency_panic_latch()
    else:
        log.info("\n[SHOW] Playlist execution finished successfully.")
        graceful_shutdown()
        sys.exit(0)

if __name__ == "__main__":
    main()
