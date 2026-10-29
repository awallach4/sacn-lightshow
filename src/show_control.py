#!/usr/bin/env python3
"""
Master Show Controller Daemon
-----------------------------
Location: src/show_control.py

Loads central configuration files (main_config.json, playlist.json, patch.json)
to orchestrate audio streaming and sACN lighting sequences.

Usage:
    python3 src/show_control.py [-c config/main_config.json]
"""

import sys
import os
import time
import json
import signal
import threading
import subprocess
import argparse
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

# Base Directory Paths
SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
DEFAULT_MAIN_CONFIG = PROJECT_ROOT / "config" / "main_config.json"

# Global Execution State
is_panicked = False
fm_carrier_proc = None
active_processes = []
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
    return parser.parse_args()

def load_system_configs(main_config_path):
    """Loads main_config.json and cleanly resolves linked playlist asset paths against PROJECT_ROOT."""
    abs_main_path = Path(main_config_path).resolve()

    if not abs_main_path.is_file():
        print(f"[ERROR] Main config file not found: {abs_main_path}", file=sys.stderr)
        sys.exit(1)

    with open(abs_main_path, "r") as f:
        config_data = json.load(f)

    sys_settings = config_data.get("system", {})
    paths = sys_settings.get("paths", {})

    # Resolve playlist config path cleanly against PROJECT_ROOT
    raw_playlist_path = paths.get("playlist_config", "config/playlist.json")
    playlist_path = (PROJECT_ROOT / raw_playlist_path.lstrip("/\\")).resolve()

    if not playlist_path.is_file():
        print(f"[ERROR] Playlist file not found: {playlist_path}", file=sys.stderr)
        sys.exit(1)

    with open(playlist_path, "r") as f:
        playlist_file_data = json.load(f)

    target_playlist_name = sys_settings.get("playlist_name", "playlist") or paths.get("playlist_name", "playlist")

    if isinstance(playlist_file_data, dict):
        if target_playlist_name not in playlist_file_data:
            print(f"[ERROR] Playlist key '{target_playlist_name}' not found in {playlist_path}", file=sys.stderr)
            sys.exit(1)
        playlist_data = playlist_file_data[target_playlist_name]
    elif isinstance(playlist_file_data, list):
        playlist_data = playlist_file_data
    else:
        print(f"[ERROR] Invalid format in playlist file: {playlist_path}", file=sys.stderr)
        sys.exit(1)

    # Resolve relative audio and sequence paths inside playlist items
    for item in playlist_data:
        for key in ["audio", "sequence"]:
            raw_asset_path = item[key]

            p = Path(raw_asset_path)

            if not p.is_absolute():
                resolved_asset = (PROJECT_ROOT / raw_asset_path.lstrip("/\\")).resolve()
            else:
                resolved_asset = p.resolve

            item[key] = str(resolved_asset)

    return sys_settings, playlist_data

# ==============================================================================
# TELEMETRY & SYSTEM HEALTH
# ==============================================================================

class ShowTelemetry:
    """Thread-safe background collector writing JSON snapshots to RAM (/dev/shm)."""
    def __init__(self):
        self._lock = threading.Lock()
        self.state = {
            "show_state": "INITIALIZING",
            "playlist": {
                "current_index": 0,
                "total_tracks": 0,
                "now_playing": "None",
                "next_up": "None"
            },
            "system_health": {
                "cpu_usage_pct": 0.0,
                "memory_usage_pct": 0.0,
                "cpu_temp_c": 0.0
            },
            "node_status": {
                "fm_transmitter": "OFFLINE",
                "sacn_engine": "OFFLINE"
            },
            "timestamp": time.time()
        }
        self._running = True
        self._thread = threading.Thread(target=self._health_loop, daemon=True)
        self._thread.start()

    def _health_loop(self):
        while self._running:
            cpu_pct = psutil.cpu_percent(interval=None) if psutil else 0.0
            ram_pct = psutil.virtual_memory().percent if psutil else 0.0
            
            temp_c = 0.0
            if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        temp_c = round(float(f.read().strip()) / 1000.0, 1)
                except Exception:
                    pass

            with self._lock:
                self.state["system_health"]["cpu_usage_pct"] = cpu_pct
                self.state["system_health"]["memory_usage_pct"] = ram_pct
                self.state["system_health"]["cpu_temp_c"] = temp_c
                self.state["timestamp"] = time.time()

            if os.name != 'nt' and os.path.exists("/dev/shm"):
                try:
                    tmp_file = "/dev/shm/show_state.json.tmp"
                    target_file = "/dev/shm/show_state.json"
                    with open(tmp_file, "w") as f:
                        json.dump(self.get_snapshot(), f)
                    os.replace(tmp_file, target_file)
                except Exception:
                    pass

            time.sleep(1.5)

    def update_track_state(self, current_idx, total, now_playing, next_up, show_state="PLAYING"):
        with self._lock:
            self.state["show_state"] = show_state
            self.state["playlist"]["current_index"] = current_idx
            self.state["playlist"]["total_tracks"] = total
            self.state["playlist"]["now_playing"] = now_playing
            self.state["playlist"]["next_up"] = next_up

    def set_show_state(self, status):
        with self._lock:
            self.state["show_state"] = status

    def set_node_status(self, node_key, status):
        with self._lock:
            self.state["node_status"][node_key] = status

    def get_snapshot(self):
        with self._lock:
            return self.state.copy()

    def stop(self):
        self._running = False

# ==============================================================================
# EMERGENCY PANIC & HARDWARE SAFETY
# ==============================================================================

def send_sacn_blackout(universe, priority):
    """Flushes 0% DMX values across all 8 SSR channels."""
    try:
        import sacn
        sender = sacn.sACNsender()
        sender.start()
        sender.activate_output(universe)
        sender[universe].priority = priority
        sender[universe].dmx_data = [0] * 8
        time.sleep(0.05)
        sender.stop()
    except ImportError:
        pass

def emergency_panic_latch(signum=None, frame=None):
    """
    Theatrical Emergency Panic Latch.
    Overrides all control, halts audio immediately, and streams a 
    Priority 200 blackout frame to force all SSR channels OFF.
    """
    global is_panicked, fm_carrier_proc, active_processes, sys_config
    if is_panicked:
        return
    
    is_panicked = True
    print("\n[EMERGENCY] Panic Latch Activated! Overriding show control...")

    # 1. Terminate all active worker subprocesses
    for proc in active_processes:
        if proc.poll() is None:
            proc.terminate()

    # 2. Issue high-priority sACN blackout frame (Priority 200)
    net_cfg = sys_config.get("network", {})
    universe = net_cfg.get("sacn_universe", 1)
    panic_priority = net_cfg.get("priority_panic", 200)

    print(f"[EMERGENCY] Streaming Priority {panic_priority} blackout packet to Universe {universe}...")
    send_sacn_blackout(universe=universe, priority=panic_priority)

    # 3. Clean up persistent FM carrier process & FIFO pipe
    if fm_carrier_proc and fm_carrier_proc.poll() is None:
        print("[EMERGENCY] Terminating FM transmitter carrier...")
        fm_carrier_proc.terminate()

    fifo_path = sys_config.get("broadcast", {}).get("fifo_path", "/tmp/fm_audio.fifo")
    if os.path.exists(fifo_path):
        try:
            os.remove(fifo_path)
        except OSError:
            pass

    print("[EMERGENCY] System locked in safe blackout state. Exiting.")
    sys.exit(0)

# ==============================================================================
# FM TRANSMITTER CARRIER MANAGEMENT
# ==============================================================================

def init_persistent_fm_carrier(telemetry):
    """Creates the FIFO pipe and launches persistent pi_fm_rds."""
    global fm_carrier_proc, sys_config

    broadcast_cfg = sys_config.get("broadcast", {})
    freq = broadcast_cfg.get("fm_frequency", 90.3)
    ps_name = broadcast_cfg.get("rds_ps_name", "HOLIDAY")
    fifo_path = broadcast_cfg.get("fifo_path", "/tmp/fm_audio.fifo")

    if os.name == 'nt':
        print("[INIT] Windows OS detected. FM transmitter initialization skipped.")
        telemetry.set_node_status("fm_transmitter", "MOCKED (Windows)")
        return

    if not os.path.exists(fifo_path):
        os.mkfifo(fifo_path)

    print(f"[INIT] Launching persistent FM carrier on {freq:.1f} MHz...")
    fm_cmd = [
        "pi_fm_rds",
        "-freq", str(freq),
        "-audio", fifo_path,
        "-ps", ps_name
    ]
    
    fm_carrier_proc = subprocess.Popen(
        fm_cmd, 
        stdout=subprocess.DEVNULL, 
        stderr=subprocess.DEVNULL
    )
    time.sleep(0.5)
    telemetry.set_node_status("fm_transmitter", f"ACTIVE ({freq:.1f} MHz)")

# ==============================================================================
# PLAYLIST EXECUTION LOOP
# ==============================================================================

def run_track(track_idx, playlist, telemetry=None):
    """Executes a track by index from the master playlist with T0 clock sync."""
    global active_processes, sys_config
    total_tracks = len(playlist)
    
    if not (0 <= track_idx < total_tracks):
        print(f"[ERROR] Track index {track_idx} out of range.", file=sys.stderr)
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

    if not os.path.isfile(audio_path):
        print(f"[ERROR] Audio file missing: {audio_path}", file=sys.stderr)
        return False
    if not os.path.isfile(sequence_path):
        print(f"[ERROR] Sequence file missing: {sequence_path}", file=sys.stderr)
        return False

    print(f"\n==================================================")
    print(f"[SHOW] Executing Track [{track_idx + 1}/{total_tracks}]: {now_playing_title}")
    print(f"[SHOW] Sequence Target: {os.path.basename(sequence_path)}")
    print(f"==================================================")

    if telemetry:
        telemetry.update_track_state(
            current_idx=track_idx + 1,
            total=total_tracks,
            now_playing=now_playing_title,
            next_up=next_up_title,
            show_state="PLAYING"
        )

    processes = []
    try:
        lead_time_s = 1.50
        t0 = time.monotonic() + lead_time_s

        audio_script = os.path.join(SRC_DIR, "audio_playback.py")
        sacn_script = os.path.join(SRC_DIR, "sacn/sacn_transmitter.py")
        universe = sys_config.get("network", {}).get("sacn_universe", 1)

        button_hold = current_track.get("button_hold_s", 0)

        audio_cmd = [sys.executable, audio_script, audio_path]
        sacn_cmd = [sys.executable, sacn_script, sequence_path, "--universe", str(universe), "--button-hold", str(button_hold)]

        audio_proc = subprocess.Popen(audio_cmd, stdin=subprocess.PIPE, text=True)
        sacn_proc = subprocess.Popen(sacn_cmd, stdin=subprocess.PIPE, text=True)
        
        processes.extend([audio_proc, sacn_proc])
        active_processes = processes

        time.sleep(0.2)

        print(f"[SYNC] T0 Clock Lock Engaged @ {t0:.3f}s. Audio & sACN Active.")
        trigger_msg = f"{t0}\n"

        if audio_proc.stdin:
            audio_proc.stdin.write(trigger_msg)
            audio_proc.stdin.flush()
        if sacn_proc.stdin:
            sacn_proc.stdin.write(trigger_msg)
            sacn_proc.stdin.flush()
        
        audio_proc.wait()

        try:
            sacn_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            sacn_proc.terminate()

        print(f"[SHOW] Track '{now_playing_title}' completed cleanly")

    except KeyboardInterrupt:
        print("\n[WARN] Track execution aborted by signal.", file=sys.stderr)
        raise
    except Exception as e:
        print(f"[ERROR] Exception during track playback: {e}", file=sys.stderr)
        return False
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        active_processes = []

    # Inter-track 3-second blackout head
    inter_track_delay = current_track.get("post_delay_s", 3.0)
    print("[SHOW] Holding inter-track blackout head on FM...")
    if telemetry:
        telemetry.set_show_state("BLACKOUT")

    normal_priority = sys_config.get("network", {}).get("priority_normal", 100)
    send_sacn_blackout(universe=universe, priority=normal_priority)
    time.sleep(inter_track_delay)

    return True

def graceful_shutdown(telemetry=None):
    """Clean, non-emergency shutdown for normal show completion."""
    global fm_carrier_proc, sys_config
    print("\n[SHOW] Performing graceful show completion and blackout...")
    
    # Issue standard Priority 100 blackout frame
    net_cfg = sys_config.get("network", {})
    universe = net_cfg.get("sacn_universe", 1)
    priority = net_cfg.get("priority_normal", 100)
    send_sacn_blackout(universe=universe, priority=priority)

    # Terminate persistent FM process cleanly if running
    if fm_carrier_proc and fm_carrier_proc.poll() is None:
        fm_carrier_proc.terminate()

    if telemetry:
        telemetry.set_show_state("COMPLETED")
        telemetry.stop()

    print("[SHOW] Show engine stopped cleanly. Exiting with status 0.")

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    global sys_config
    args = parse_args()

    # Register OS Signal Handlers
    signal.signal(signal.SIGINT, emergency_panic_latch)   # Ctrl+C
    signal.signal(signal.SIGTERM, emergency_panic_latch)  # systemd stop / kill

    # Load Central Configs
    sys_config, playlist = load_system_configs(args.config)

    print("==================================================")
    print(f"  {sys_config.get('show_name', 'RESIDENTIAL LIGHT SHOW DAEMON')}  ")
    print("==================================================")
    print(f"[INIT] Loaded system configuration from: {os.path.abspath(args.config)}")
    print(f"[INIT] Playlist contains {len(playlist)} tracks.")

    telemetry = ShowTelemetry()

    # Initialize persistent broadcast carrier
    init_persistent_fm_carrier(telemetry)

    show_failed = False

    try:
        telemetry.set_show_state("RUNNING")
        
        for idx in range(len(playlist)):
            if is_panicked:
                show_failed = True
                break
            success = run_track(idx, playlist, telemetry)
            if not success:
                print(f"[WARN] Track index {idx} failed. Terminating show execution.")
                show_failed = True
                break

        print("\n[SHOW] Playlist execution finished.")
        graceful_shutdown(telemetry)

    except Exception as e:
        print(f"[ERROR] Unexpected exception in main show loop: {e}")
        show_failed = True

    if show_failed or is_panicked:
        emergency_panic_latch()
    else:
        print("\n[SHOW] Playlist execution finished successfully.")
        graceful_shutdown(telemetry)
        sys.exit(0)

if __name__ == "__main__":
    main()
