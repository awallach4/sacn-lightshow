import os
import json
import sys
import platform
import subprocess
import psutil
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Resolve base directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR = PROJECT_ROOT / "src" / "web" / "dist"

# Windows IPC fallback path vs Linux /dev/shm shared memory
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    IPC_DIR = PROJECT_ROOT / ".tmp"
    IPC_DIR.mkdir(exist_ok=True)
else:
    IPC_DIR = Path("/dev/shm")

STATE_FILE_PATH = IPC_DIR / "show_state.json"
CMD_FILE_PATH = IPC_DIR / "show_macro_cmd.json"

app = Flask(__name__, static_folder=str(DIST_DIR), static_url_path="")
CORS(app)  # Allows Vite dev server (http://localhost:5173) to query API directly

# Cross-platform CPU temperature reader
def get_cpu_temp() -> float:
    if IS_WINDOWS:
        # Standard Windows hardware monitor check via WMI/psutil if available
        try:
            temps = getattr(psutil, "sensors_temperatures", lambda: {})()
            if temps:
                for sensor_name, entries in temps.items():
                    if entries:
                        return round(entries[0].current, 1)
        except Exception:
            pass
        return 0.0  # Safe fallback for Windows dev machines without thermal sensors exposed
    else:
        try:
            # Raspberry Pi thermal zone
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return round(float(f.read().strip()) / 1000.0, 1)
        except Exception:
            temps = getattr(psutil, "sensors_temperatures", lambda: {})()
            if "cpu_thermal" in temps and temps["cpu_thermal"]:
                return round(temps["cpu_thermal"][0].current, 1)
            return 0.0

# -------------------------------------------------------------------
# SPA Routing (Serves production Vue build)
# -------------------------------------------------------------------
@app.route("/")
def serve_index():
    if (DIST_DIR / "index.html").exists():
        return send_from_directory(DIST_DIR, "index.html")
    return jsonify({"status": "API active", "note": "Vue dist build not found. Run Vite dev server."}), 200

@app.route("/<path:path>")
def serve_static(path):
    if (DIST_DIR / path).exists():
        return send_from_directory(DIST_DIR, path)
    elif (DIST_DIR / "index.html").exists():
        return send_from_directory(DIST_DIR, "index.html")
    return jsonify({"error": "Not found"}), 404

# -------------------------------------------------------------------
# Telemetry Endpoint (Host Status, Node Status & Show State)
# -------------------------------------------------------------------
@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    # 1. Fetch Host Resource Metrics
    cpu_usage = psutil.cpu_percent(interval=None)
    ram_usage = psutil.virtual_memory().percent
    cpu_temp = get_cpu_temp()

    # 2. Defaults for Show & Node Status
    show_state = {
        "current_song": "",
        "next_song": "",
        "node_status": {
            "sACN Transmitter": False,
            "Audio Playback": False,
            "Show Control": False,
            "Scheduler": False,
            "Web Controls": True
        }
    }

    # 3. Read IPC JSON snapshot if present
    if STATE_FILE_PATH.is_file():
        try:
            with open(STATE_FILE_PATH, "r") as f:
                ram_data = json.load(f)
                
                show_state["current_song"] = ram_data.get("current_song", show_state["current_song"])
                show_state["next_song"] = ram_data.get("next_song", show_state["next_song"])
                
                if "node_status" in ram_data:
                    show_state["node_status"].update(ram_data["node_status"])
        except Exception:
            pass

    return jsonify({
        "cpu_usage": cpu_usage,
        "ram_usage": ram_usage,
        "cpu_temp": cpu_temp,
        "node_status": show_state["node_status"],
        "current_song": show_state["current_song"],
        "next_song": show_state["next_song"]
    }), 200

# -------------------------------------------------------------------
# Show Macro Controls
# -------------------------------------------------------------------
@app.route("/api/macro", methods=["POST"])
def execute_macro():
    payload = request.get_json() or {}
    action = payload.get("action")

    valid_actions = ["start_show", "prev_track", "next_track", "all_on", "blackout", "panic"]
    if action not in valid_actions:
        return jsonify({"error": f"Invalid action. Options: {valid_actions}"}), 400

    try:
        with open(CMD_FILE_PATH, "w") as f:
            json.dump({"action": action}, f)
        return jsonify({"status": "success", "executed_action": action}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to send macro command: {str(e)}"}), 500

# -------------------------------------------------------------------
# Host Controls (Reboot & Shutdown)
# -------------------------------------------------------------------
@app.route("/api/host/<command>", methods=["POST"])
def host_control(command):
    if command not in ["reboot", "shutdown"]:
        return jsonify({"error": "Invalid host command"}), 400

    try:
        if IS_WINDOWS:
            # Safe Windows simulated log for local testing
            print(f"[WINDOWS DEV] Host action triggered: {command}")
        else:
            if command == "reboot":
                subprocess.Popen(["sudo", "systemctl", "reboot"])
            elif command == "shutdown":
                subprocess.Popen(["sudo", "systemctl", "poweroff"])
            
        return jsonify({"status": "success", "message": f"Host {command} sequence initiated"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to execute host action: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)