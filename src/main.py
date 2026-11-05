import os
import sys
import time
import json
import signal
import threading
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

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
    def __init__(self, config_path: Path):
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

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[INIT] [WARN] Failed to read {self.config_path}: {e}", file=sys.stderr)
        
        # Fallback schema matching main_config.json structure
        return {
            "system": {
                "show_name": "Holiday Lightshow",
                "playlist_name": "Winter 2026",
                "broadcast": {
                    "fm_frequency": 90.3,
                    "rds_ps_name": "HOLIDAY",
                    "fifo_path": "/tmp/fm_audio.fifo"
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
        print(f"==================================================", flush=True)
        print(f"   {show_name.upper()} - INITIALIZER NODE   ", flush=True)
        print(f"==================================================", flush=True)
        print(f"[INIT] Loaded main configuration from: {self.config_path.resolve()}", flush=True)

        py_exe = sys.executable

        # 1. Web Backend & Frontend (src/web/app.py)
        web_script = "src/web/app.py"
        if os.path.exists(web_script):
            print(f"[INIT] Launching Web Server ({web_script})...", flush=True)
            self.processes["web_server"] = subprocess.Popen([py_exe, web_script])

        # 2. Scheduler Node (scheduler_node.py)
        sched_script = "src/scheduler.py"
        if os.path.exists(sched_script):
            print(f"[INIT] Launching Scheduler Node ({sched_script})...", flush=True)
            self.processes["scheduler"] = subprocess.Popen([py_exe, sched_script])

        # 3. sACN Listener Node (sacn_listener.py)
        # listener_script = "src/sacn/sacn_listener.py"
        # if os.path.exists(listener_script):
        #     print(f"[INIT] Launching sACN Listener ({listener_script})...", flush=True)
        #     self.processes["sacn_listener"] = subprocess.Popen([py_exe, listener_script, "-p", "config/patch.json"])

        # Start Process Health Monitor
        self.monitor_thread = threading.Thread(target=self._monitor_processes, daemon=True)
        self.monitor_thread.start()

    def _monitor_processes(self):
        """Continuously checks child processes and re-spawns core nodes if they crash."""
        while self.running:
            py_exe = sys.executable
            script_map = {
                "web_server": "src/web/app.py",
                "scheduler": "src/scheduler.py",
                # "sacn_listener": "src/sacn/sacn_listener.py"
            }
            
            for name, script_path in script_map.items():
                proc = self.processes[name]
                if proc and proc.poll() is not None:
                    print(f"[INIT] [WARN] Process '{name}' exited unexpectedly with code {proc.returncode}. Restarting...", flush=True)
                    if os.path.exists(script_path):
                        if name == "sacn_listener":
                            self.processes[name] = subprocess.Popen([py_exe, script_path])
                        else:
                            self.processes[name] = subprocess.Popen([py_exe, script_path])

            # Check if active show control completed naturally
            show_proc = self.processes["show_control"]
            if show_proc and show_proc.poll() is not None:
                print(f"[INIT] Show Control process finished (code {show_proc.returncode}). Returning to OFF state.", flush=True)
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

    def _get_all_patched_universes(self) -> dict[int, list[int]]:
        """
        Reads config/patch.json and returns a dictionary mapping:
        { universe_id: [512-slot DMX list with 255 for patched addresses, 0 for unpatched] }
        """
        universe_frames = {}
        patch_path = PATCH_PATH

        if not patch_path.exists():
            fallback_universe = self.config.get("system", {}).get("network", {}).get("sacn_universe", 11)
            print(f"[INIT] [WARN] Patch config not found at {patch_path}. Defaulting Universe {fallback_universe} to all 512 slots.", file=sys.stderr)
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
                    print(f"[INIT] Loaded patch for Universe {univ_id}: {patched_count} active channels.")

            if not universe_frames:
                fallback_universe = self.config.get("system", {}).get("network", {}).get("sacn_universe", 11)
                universe_frames[fallback_universe] = [0] * 512

            return universe_frames

        except Exception as e:
            fallback_universe = self.config.get("system", {}).get("network", {}).get("sacn_universe", 11)
            print(f"[INIT] [ERROR] Failed to parse {patch_path}: {e}. Defaulting Universe {fallback_universe} to all 512 slots.", file=sys.stderr)
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
        # Print a stack/caller trace to see what endpoint or method invoked stop_active_output
        import traceback
        print(f"[INIT DEBUG] stop_active_output() called from:\n{''.join(traceback.format_stack())}", flush=True)

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
            print("[INIT] Terminating active Show Control process tree...", flush=True)
            kill_tree(show_proc)  # show_control runs in its own session, so this reaches its workers too
                
        self.processes["show_control"] = None

    def release_all_universes(self, priority: int = None):
        """Sends zero-value frames to ALL patched universes and deactivates outputs."""
        net_cfg = self.config.get("system", {}).get("network", {})
        priority = priority or net_cfg.get("priority_normal", 100)
        is_multicast = net_cfg.get("multicast", True)

        all_universe_frames = self._get_all_patched_universes()
        target_universes = list(all_universe_frames.keys())

        print(f"[INIT] Releasing sACN Universes {target_universes} (Priority {priority}, Blackout)...", flush=True)
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
            print("[INIT] [WARN] sACN package not installed. Skipping DMX blackout frame.", file=sys.stderr)
        except Exception as e:
            print(f"[INIT] [ERROR] Failed to send blackout sACN: {e}", file=sys.stderr)

    def trigger_all_on(self, level: int = 255):
        """Drives ONLY patched channels on ALL universes defined in patch.json to full level."""
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
                print(f"[INIT] ALL ON macro active across Universes {active_universes} @ level {level}.", flush=True)

                while self.macro_active and self.running:
                    time.sleep(0.5)

                # Blackout and deactivate outputs across all active universes
                for u_id in active_universes:
                    sender[u_id].dmx_data = (0,) * 512
                    time.sleep(0.02)
                    sender.deactivate_output(u_id)
                sender.stop()
                
            except Exception as err:
                print(f"[INIT] All ON macro exception: {err}", file=sys.stderr)

        self.macro_thread = threading.Thread(target=_on_loop, daemon=True)
        self.macro_thread.start()

    def start_show(self, playlist_name: str = None):
        """Stops static output and launches show_control.py with the requested playlist."""
        self.stop_active_output()
        self.current_state = State.SHOW_RUNNING
        
        target_playlist = playlist_name or self.config.get("system", {}).get("playlist_name", "Winter 2026")
        py_exe = sys.executable
        cmd = [py_exe, "src/show_control.py", "--config", str(self.config_path), "--playlist", target_playlist]
        
        print(f"[INIT] Starting show control with playlist '{target_playlist}'...", flush=True)
        self.processes["show_control"] = subprocess.Popen(cmd, start_new_session=True)

    def trigger_panic(self):
        """Immediately halts all processes, releases universes at panic priority, and flags PANIC state."""
        print("[INIT] !!! PANIC TRIGGERED !!! Halting all show outputs.", flush=True)
        self.stop_active_output()

        panic_priority = self.config.get("system", {}).get("network", {}).get("priority_panic", 200)
        self.release_all_universes(priority=panic_priority)
        self.current_state = State.PANIC

    def execute_host_command(self, action: str):
        """Executes system-level host operations."""
        self.cleanup()
        if action == "reboot":
            print("[INIT] Executing system reboot...", flush=True)
            if os.name != 'nt':
                os.system("sudo reboot")
        elif action == "shutdown":
            print("[INIT] Executing system shutdown...", flush=True)
            if os.name != 'nt':
                os.system("sudo shutdown -h now")

    # --- CLEAN TEARDOWN ---
    def cleanup(self):
        """Gracefully terminates all child processes, zeros all channels, and exits."""
        if not self.running:
            return
        self.running = False
        print("\n[INIT] Commencing graceful shutdown of Initializer Node...", flush=True)

        self.stop_active_output()

        # Terminate core child processes
        for name, proc in self.processes.items():
            if proc and proc.poll() is None:
                print(f"[INIT] Stopping {name} (PID {proc.pid})...", flush=True)
                kill_tree(proc)

        self.release_all_universes()
        print("[INIT] Teardown complete. All processes terminated and universes released.", flush=True)


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
                    node.trigger_panic()
                    self._send_json(200, {"status": "ok", "state": node.current_state})
                else:
                    self._send_json(400, {"error": f"Unknown macro action '{action}'"})

            # In main.py -> IPCHandler.do_POST()

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
            else:
                self._send_json(404, {"error": "Endpoint not found"})

        def log_message(self, format, *args):
            pass

    return IPCHandler


def main():
    node = InitializerNode(CONFIG_PATH)

    def _sig_handler(signum, frame):
        print(f"\n[INIT] Received signal {signum}.", flush=True)
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
    print(f"[INIT] IPC Command Server listening on http://127.0.0.1:{IPC_PORT}", flush=True)

    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.cleanup()

if __name__ == "__main__":
    main()