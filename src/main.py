from logging import Logger
import os
import sys
import time
import json
import signal
import threading
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

from logger_setup import get_node_logger

# --- CONFIGURATION & PATHS ---
CONFIG_PATH = Path("config/main_config.json")
PATCH_PATH = Path("config/patch.json")
IPC_PORT = 5001  # Internal API port for IPC communication from app.py / CLI

class State:
    OFF = "OFF"
    STATIC_ON = "STATIC_ON"
    SHOW_RUNNING = "SHOW_RUNNING"
    PANIC = "PANIC"

class QuietHTTPServer(HTTPServer):
    """Custom HTTPServer that suppresses socket reset errors caused by killed client processes."""
    def handle_error(self, request, client_address):
        # Inspect exception type without printing stack traces for ungraceful client disconnections
        ex_type, ex_val, _ = sys.exc_info()
        if issubclass(ex_type, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            # Client process was terminated mid-request; safe to ignore cleanly
            return
        # Pass non-network/real server exceptions up to standard handler
        super().handle_error(request, client_address)

def kill_tree(proc):
    """SIGKILL a child and its process group, but never our own group.

    Children started without start_new_session share the initializer's process group, so a plain
    killpg() on them would kill the initializer itself half-way through its own teardown.
    """
    try:
        if os.name == 'nt':
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.getpgid(proc.pid) != os.getpgid(0):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass

class InitializerNode:
    def __init__(self, config_path: Path, log: Logger):
        self.config_path = config_path
        self.config = self._load_config()
        self.current_state = State.OFF
        self.running = True

        # Track active playback titles
        self.now_playing = ""
        self.next_up = ""

        # Process Registry
        self.processes = {
            "web_server": None,
            "scheduler": None,
            "sacn_listener": None,
            "show_control": None
        }
        self.active_worker_pids = []

        # Background threads
        self.macro_thread = None
        self.macro_active = False
        self.monitor_thread = None
        self.http_server = None
        self.log = log

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                self.log.warning(f"[INIT] Failed to read {self.config_path}: {e}")

        # Fallback schema matching main_config.json structure
        return {
            "system": {
                "show_name": "Holiday Lightshow",
                "playlist_name": "Winter 2026",
                "schedule_name": "",
                "log_level": "INFO",
                "services": {
                    "sacn_listener": True,
                    "scheduler": True
                },
                "network": {
                    "sacn_universe": 11,
                    "priority_normal": 100,
                    "priority_panic": 200,
                    "multicast": True
                },
                "paths": {
                    "playlist_config": "config/playlist.json",
                    "patch_config": "config/patch.json"
                }
            }
        }

    # --- PROCESS SUPERVISION ---
    def start_core_nodes(self):
        sys_cfg = self.config.get("system", {})
        show_name = sys_cfg.get("show_name", "Holiday Lightshow")
        self.log.info(f"==================================================")
        self.log.info(f"   {show_name.upper()} - INITIALIZER NODE   ")
        self.log.info(f"==================================================")
        self.log.info(f"[INIT] Loaded main configuration from: {self.config_path.resolve()}")

        py_exe = sys.executable

        # 1. Web Backend & Frontend (src/web/app.py)
        web_script = "src/web/app.py"
        if os.path.exists(web_script):
            self.log.info(f"[INIT] Launching Web Server ({web_script})...")
            self.processes["web_server"] = subprocess.Popen([py_exe, web_script])

        # 2. Scheduler Node (scheduler_node.py)
        if sys_cfg.get("services", {}).get("scheduler", True) == True:
            sched_script = "src/scheduler.py"
            if os.path.exists(sched_script):
                self.log.info(f"[INIT] Launching Scheduler Node ({sched_script})...")
                self.processes["scheduler"] = subprocess.Popen([py_exe, sched_script])
        else:
            self.log.info("Scheduler node disabled, skipping...")

        # 3. sACN Listener Node (sacn_listener.py)
        if sys_cfg.get("services", {}).get("sacn_listener", True) == True:
            listener_script = "src/sacn/sacn_listener.py"
            if os.path.exists(listener_script):
                self.log.info(f"[INIT] Launching sACN Listener ({listener_script})...")
                self.processes["sacn_listener"] = subprocess.Popen([py_exe, listener_script, "-p", "config/patch.json"])
        else:
            self.log.info("sACN Listener node disabled, skipping...")

        # Start Process Health Monitor
        self.monitor_thread = threading.Thread(target=self._monitor_processes, daemon=True)
        self.monitor_thread.start()

    def _monitor_processes(self):
        """Continuously checks child processes and re-spawns core nodes if they crash."""
        sys_cfg = self.config.get("system", {})
        script_map = {
            "web_server": "src/web/app.py"
        }
        if sys_cfg.get("services", {}).get("scheduler", True) == True:
            script_map["scheduler"] = "src/scheduler.py"
        if sys_cfg.get("services", {}).get("sacn_listener", True) == True:
            script_map["sacn_listener"] = "src/sacn/sacn_listener.py"
        while self.running:
            py_exe = sys.executable
            for name, script_path in script_map.items():
                proc = self.processes[name]
                if proc and proc.poll() is not None:
                    self.log.warning(f"[INIT] Process '{name}' exited unexpectedly with code {proc.returncode}. Restarting...")
                    if os.path.exists(script_path):
                        if name == "sacn_listener":
                            self.processes[name] = subprocess.Popen([py_exe, script_path, "-p", "config/patch.json"])
                        else:
                            self.processes[name] = subprocess.Popen([py_exe, script_path])

            # Check if active show control completed naturally
            show_proc = self.processes["show_control"]
            if show_proc and show_proc.poll() is not None:
                self.log.info(f"[INIT] Show Control process finished (code {show_proc.returncode}). Returning to OFF state.")
                self.processes["show_control"] = None
                # Reap any leftovers and forget the PIDs: stale PIDs can be reused by unrelated
                # processes, and stop_active_output() would SIGKILL them later.
                for pid in self.active_worker_pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
                self.active_worker_pids.clear()
                self.now_playing = ""
                self.next_up = ""
                if self.current_state == State.SHOW_RUNNING:
                    self.current_state = State.OFF

            time.sleep(2.0)

    # --- CONFIG RESTART ACTIONS ---
    def restart_node(self, name: str):
        """Safely restarts a single managed child node process."""
        py_exe = sys.executable
        sys_cfg = self.config.get("system", {})
        script_map = {
            "web_server": "src/web/app.py"
        }
        if sys_cfg.get("services", {}).get("scheduler", True) == True:
            script_map["scheduler"] = "src/scheduler.py"
        if sys_cfg.get("services", {}).get("sacn_listener", True) == True:
            script_map["sacn_listener"] = "src/sacn/sacn_listener.py"

        if name not in script_map:
            self.log.warning(f"[INIT] Cannot restart unknown node: {name}")
            return

        proc = self.processes.get(name)
        if proc and proc.poll() is None:
            self.log.info(f"[INIT] Terminating '{name}' (PID {proc.pid}) for config update restart...")
            kill_tree(proc)

        script_path = script_map[name]
        if os.path.exists(script_path):
            self.log.info(f"[INIT] Respawning '{name}' ({script_path})...")
            if name == "sacn_listener":
                self.processes[name] = subprocess.Popen([py_exe, script_path, "-p", "config/patch.json"])
            else:
                self.processes[name] = subprocess.Popen([py_exe, script_path])

    def restart_full_system(self):
        """Executes a full stack teardown and process restart."""
        self.log.warning("[INIT] Executing full system stack reload due to main_config change...")
        self.cleanup()
        # On Linux/systemd, exiting allows systemd to automatically restart main.py.
        # On Windows/Dev, re-exec the main process wrapper.
        if os.name != 'nt':
            sys.exit(0)
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def _get_all_patched_universes(self) -> dict[int, list[int]]:
        """
        Reads config/patch.json and returns a dictionary mapping:
        { universe_id: [512-slot DMX list with 255 for patched addresses, 0 for unpatched] }
        """
        universe_frames = {}
        patch_path = PATCH_PATH

        if not patch_path.exists():
            fallback_universe = self.config.get("system", {}).get("network", {}).get("sacn_universe", 11)
            self.log.warning(f"[INIT] Patch config not found at {patch_path}. Defaulting Universe {fallback_universe} to all 512 slots.")
            return {fallback_universe: [255] * 512}

        try:
            with open(patch_path, "r") as f:
                raw_data = json.load(f)

            patch_map = raw_data.get("patch", {})

            for univ_str, univ_data in patch_map.items():
                try:
                    univ_id = int(univ_str)
                except ValueError:
                    continue

                dmx_frame = [0] * 512
                devices = univ_data.get("devices", {})
                patched_count = 0

                for device_id, device in devices.items():
                    parameters = device.get("parameters", [])
                    for param in parameters:
                        address = param.get("address")
                        if address is not None and 1 <= address <= 512:
                            dmx_frame[address - 1] = 255
                            patched_count += 1

                if patched_count > 0:
                    universe_frames[univ_id] = dmx_frame
                    self.log.info(f"[INIT] Loaded patch for Universe {univ_id}: {patched_count} active channels.")

            if not universe_frames:
                fallback_universe = self.config.get("system", {}).get("network", {}).get("sacn_universe", 11)
                universe_frames[fallback_universe] = [0] * 512

            return universe_frames

        except Exception as e:
            fallback_universe = self.config.get("system", {}).get("network", {}).get("sacn_universe", 11)
            self.log.error(f"[INIT] Failed to parse {patch_path}: {e}. Defaulting Universe {fallback_universe} to all 512 slots.")
            return {fallback_universe: [255] * 512}

    def get_process_status(self) -> dict:
        """Returns process status map and track info for web dashboard telemetry."""
        status = {
            "system_state": self.current_state,
            "now_playing": self.now_playing if self.current_state == State.SHOW_RUNNING else "",
            "next_up": self.next_up if self.current_state == State.SHOW_RUNNING else "",
            "nodes": {}
        }
        for name, proc in self.processes.items():
            if proc is None:
                state_str = "STOPPED"
            elif proc.poll() is None:
                state_str = "RUNNING"
            else:
                state_str = f"EXITED ({proc.returncode})"
            status["nodes"][name] = {"pid": proc.pid if proc else None, "status": state_str}
        return status

    # --- STATE ACTIONS & MACROS ---
    def stop_active_output(self):
        """Halts active show execution, stops macro threads, and forcefully purges worker processes."""

        self.macro_active = False

        if self.macro_thread and self.macro_thread.is_alive():
            self.macro_thread.join(timeout=2.0)

        show_proc = self.processes["show_control"]

        # 1. Kill registered worker child PIDs directly
        for pid in self.active_worker_pids:
            try:
                if os.name != 'nt':
                    os.kill(pid, signal.SIGKILL)
                else:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        self.active_worker_pids.clear()

        # 2. Kill show_control and its entire process group
        if show_proc and show_proc.poll() is None:
            self.log.info("[INIT] Terminating active Show Control process tree...")
            kill_tree(show_proc)

        self.processes["show_control"] = None

    def release_all_universes(self, priority: int = None):
        """Sends zero-value frames to ALL patched universes and deactivates outputs."""
        if self.current_state == State.PANIC:
            self.log.error("Cannot send macro 'All ON' while system is in panic.")
            return
        net_cfg = self.config.get("system", {}).get("network", {})
        priority = priority or net_cfg.get("priority_normal", 100)
        is_multicast = net_cfg.get("multicast", True)

        all_universe_frames = self._get_all_patched_universes()
        target_universes = list(all_universe_frames.keys())

        self.log.info(f"[INIT] Releasing sACN Universes {target_universes} (Priority {priority}, Blackout)...")
        try:
            import sacn
            sender = sacn.sACNsender(source_name="Initializer_Blackout")
            sender.start()

            for u_id in target_universes:
                sender.activate_output(u_id)
                sender[u_id].priority = priority
                sender[u_id].multicast = is_multicast
                sender[u_id].dmx_data = (0,) * 512

            time.sleep(0.1)

            for u_id in target_universes:
                sender[u_id].dmx_data = (0,) * 512
                time.sleep(0.02)
                sender.deactivate_output(u_id)
            sender.stop()

        except ImportError:
            self.log.warning("[INIT] sACN package not installed. Skipping DMX blackout frame.")
        except Exception as e:
            self.log.error(f"[INIT] Failed to send blackout sACN: {e}")

    def send_panic(self):
        """Sends zero-value frames to ALL patched universes and deactivates outputs."""
        net_cfg = self.config.get("system", {}).get("network", {})
        is_multicast = net_cfg.get("multicast", True)

        all_universe_frames = self._get_all_patched_universes()
        self.stop_active_output()

        self.current_state = State.PANIC
        self.log.warning(f"[PANIC] Latching all universes to 0")
        def _panic_loop():
            try:
                import sacn
                sender = sacn.sACNsender(source_name="PANIC")
                sender.start()

                # Activate output and assign scaled DMX payload for each patched universe
                for u_id, mask in all_universe_frames.items():
                    sender.activate_output(u_id)
                    sender[u_id].priority = 200
                    sender[u_id].multicast = is_multicast
                    sender[u_id].dmx_data = (0,) * 512

                active_universes = list(all_universe_frames.keys())

                while self.current_state == State.PANIC:
                    time.sleep(0.5)

                # Blackout and deactivate outputs across all active universes
                for u_id in active_universes:
                    sender[u_id].dmx_data = (0,) * 512
                    time.sleep(0.02)
                    sender.deactivate_output(u_id)
                sender.stop()

            except Exception as err:
                self.log.error(f"[INIT] Failed to send panic blackout: {err}")

        self.macro_thread = threading.Thread(target=_panic_loop, daemon=True)
        self.macro_thread.start()

    def trigger_all_on(self, level: int = 255):
        """Drives ONLY patched channels on ALL universes defined in patch.json to full level."""
        if self.current_state == State.PANIC:
            self.log.error("Cannot send macro 'All ON' while system is in panic.")
            return
        self.stop_active_output()
        self.current_state = State.STATIC_ON
        self.macro_active = True

        net_cfg = self.config.get("system", {}).get("network", {})
        priority = net_cfg.get("priority_normal", 100)
        is_multicast = net_cfg.get("multicast", True)

        # Fetch patch frames for every universe in patch.json
        all_universe_frames = self._get_all_patched_universes()

        def _on_loop():
            try:
                import sacn
                sender = sacn.sACNsender(source_name="Initializer_AllON")
                sender.start()

                # Activate output and assign scaled DMX payload for each patched universe
                for u_id, mask in all_universe_frames.items():
                    sender.activate_output(u_id)
                    sender[u_id].priority = priority
                    sender[u_id].multicast = is_multicast
                    frame = tuple(level if val > 0 else 0 for val in mask)
                    sender[u_id].dmx_data = frame

                active_universes = list(all_universe_frames.keys())
                self.log.info(f"[INIT] ALL ON macro active across Universes {active_universes} @ level {level}.")

                while self.macro_active and self.running:
                    time.sleep(0.5)

                # Blackout and deactivate outputs across all active universes
                for u_id in active_universes:
                    sender[u_id].dmx_data = (0,) * 512
                    time.sleep(0.02)
                    sender.deactivate_output(u_id)
                sender.stop()

            except Exception as err:
                self.log.error(f"[INIT] All ON macro exception: {err}")

        self.macro_thread = threading.Thread(target=_on_loop, daemon=True)
        self.macro_thread.start()

    def start_show(self, playlist_name: str = None):
        """Stops static output and launches show_control.py with the requested playlist."""
        if self.current_state == State.PANIC:
            self.log.error("Cannot start show while system is in panic.")
            return
        self.stop_active_output()
        self.current_state = State.SHOW_RUNNING

        target_playlist = playlist_name or self.config.get("system", {}).get("playlist_name", "Winter 2026")
        py_exe = sys.executable
        cmd = [py_exe, "src/show_control.py", "--config", str(self.config_path), "--playlist", target_playlist]

        self.log.info(f"[INIT] Starting show control with playlist '{target_playlist}'...")
        self.processes["show_control"] = subprocess.Popen(cmd, start_new_session=True)

    def execute_host_command(self, action: str):
        """Executes system-level host operations."""
        self.cleanup()
        if action == "reboot":
            self.log.info("[INIT] Executing system reboot...")
            if os.name != 'nt':
                subprocess.run(["systemctl", "reboot"], capture_output=True, text=True, check=True)
        elif action == "shutdown":
            self.log.info("[INIT] Executing system shutdown...")
            if os.name != 'nt':
                subprocess.run(["systemctl", "poweroff"], capture_output=True, text=True, check=True)

    # --- CLEAN TEARDOWN ---
    def cleanup(self):
        """Gracefully terminates all child processes, zeros all channels, and exits."""
        if not self.running:
            return
        self.running = False
        self.log.info("\n[INIT] Commencing graceful shutdown of Initializer Node...")

        self.stop_active_output()

        # Terminate core child processes
        for name, proc in self.processes.items():
            if proc and proc.poll() is None:
                self.log.info(f"[INIT] Stopping {name} (PID {proc.pid})...")
                kill_tree(proc)

        self.release_all_universes()
        self.log.info("[INIT] Teardown complete. All processes terminated and universes released.")


# --- INTERNAL HTTP IPC SERVER ---
def make_ipc_handler(node: InitializerNode):
    class IPCHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, payload: dict):
            try:
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))
            except (ConnectionResetError, BrokenPipeError, OSError):
                # Socket closed mid-response due to process termination
                pass

        def do_GET(self):
            if self.path == "/api/status":
                self._send_json(200, node.get_process_status())
            else:
                self._send_json(404, {"error": "Endpoint not found"})

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            if self.path == "/api/macro":
                action = str(data.get("action", "")).upper()
                if action == "ON":
                    node.trigger_all_on(data.get("level", 255))
                    self._send_json(200, {"status": "ok", "state": node.current_state})
                elif action in ("OFF", "BLACKOUT"):
                    node.stop_active_output()
                    node.release_all_universes()
                    node.current_state = State.OFF
                    self._send_json(200, {"status": "ok", "state": node.current_state})
                elif action == "PANIC":
                    node.send_panic()
                    node.current_state = State.PANIC
                    self._send_json(200, {"status": "ok", "state": node.current_state})
                elif action == "RESET":
                    node.current_state = State.OFF
                    self._send_json(200, {"status": "ok", "state": node.current_state})
                else:
                    self._send_json(400, {"error": f"Unknown macro action '{action}'"})

            elif self.path == "/api/show/register_workers":
                pids = data.get("pids", [])
                node.active_worker_pids = pids
                self._send_json(200, {"status": "ok"})

            elif self.path == "/api/show/start":
                playlist = data.get("playlist")
                node.start_show(playlist)
                self._send_json(200, {"status": "ok", "state": node.current_state, "playlist": playlist})

            elif self.path == "/api/show/progress":
                node.now_playing = data.get("now_playing", "")
                node.next_up = data.get("next_up", "")
                self._send_json(200, {"status": "ok"})

            elif self.path == "/api/host":
                action = str(data.get("action", "")).lower()
                if action in ("reboot", "shutdown"):
                    self._send_json(200, {"status": "ok", "message": f"Host command '{action}' accepted."})
                    threading.Thread(target=node.execute_host_command, args=(action,), daemon=True).start()
                else:
                    self._send_json(400, {"error": f"Invalid host command '{action}'"})

            elif self.path == "/api/config/reload":
                config_type = str(data.get("config_type", "")).lower()

                # Guard: Block configuration restarts if a show is running
                if node.current_state == State.SHOW_RUNNING:
                    self._send_json(409, {
                        "error": f"Cannot reload {config_type}.json while show is running. Stop show first."
                    })
                    return

                if config_type == "playlist":
                    # No process restart needed; show_control reads playlist dynamically on run
                    self._send_json(200, {
                        "status": "ok",
                        "message": "Playlist config updated. Will take effect on next play cycle."
                    })

                elif config_type == "schedule":
                    node.restart_node("scheduler")
                    self._send_json(200, {
                        "status": "ok",
                        "message": "Scheduler node process restarted with new schedule."
                    })

                elif config_type == "patch":
                    node.restart_node("sacn_listener")
                    self._send_json(200, {
                        "status": "ok",
                        "message": "sACN listener process restarted with new channel patch."
                    })

                elif config_type == "main_config":
                    self._send_json(200, {
                        "status": "ok",
                        "message": "Full system process stack reloading..."
                    })
                    node.restart_full_system()

                else:
                    self._send_json(400, {"error": f"Unknown config_type '{config_type}'"})
            else:
                self._send_json(404, {"error": "Endpoint not found"})

        def log_message(self, format, *args):
            pass

    return IPCHandler


def main():
    def load_log_level() -> str:
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f).get("log_level", "INFO")
        except Exception:
            pass
        return "INFO"

    log = get_node_logger("main", load_log_level())

    node = InitializerNode(CONFIG_PATH, log)

    def _sig_handler(signum, frame):
        log.info(f"\n[INIT] Received signal {signum}.")
        node.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # Launch managed subprocesses
    node.start_core_nodes()

    # Launch HTTP IPC Command Server
    handler_cls = make_ipc_handler(node)
    httpd = QuietHTTPServer(("127.0.0.1", IPC_PORT), handler_cls)
    node.http_server = httpd
    log.info(f"[INIT] IPC Command Server listening on http://127.0.0.1:{IPC_PORT}")

    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.cleanup()

if __name__ == "__main__":
    main()