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

###############################################################################
#                           Dashboard Backend                                 #
###############################################################################

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

###############################################################################
#                          Playlist Editor Backend                            #
###############################################################################

from werkzeug.utils import secure_filename

# Resolve base directories for sacn-lightshow layout
SHOWFILES_DIR = PROJECT_ROOT  / "showfiles"
MUSIC_DIR = SHOWFILES_DIR / "music"
SEQUENCES_DIR = SHOWFILES_DIR / "sequences"
CONFIG_DIR = PROJECT_ROOT / "config"
PLAYLIST_JSON_PATH = CONFIG_DIR / "playlist.json"

# Ensure target directories exist on server start
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Helper to load playlist structure safely
def load_playlists_data() -> dict:
    if PLAYLIST_JSON_PATH.is_file():
        try:
            with open(PLAYLIST_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Default fallback structure if file is missing or unparseable
    return {"playlists": {}}

# -------------------------------------------------------------------
# GET / POST: Raw playlist.json management
# -------------------------------------------------------------------
@app.route("/api/playlist/config", methods=["GET"])
def get_playlist_config():
    """Returns the contents of playlist.json for the frontend editor."""
    if not PLAYLIST_JSON_PATH.is_file():
        default_config = {"playlists": {"playlist": [], "playlist 2": []}}
        with open(PLAYLIST_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)
        return jsonify({"content": json.dumps(default_config, indent=2)}), 200

    try:
        with open(PLAYLIST_JSON_PATH, "r", encoding="utf-8") as f:
            raw_text = f.read()
        return jsonify({"content": raw_text}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to read playlist config: {str(e)}"}), 500


@app.route("/api/playlist/config", methods=["POST"])
def save_playlist_config():
    """Saves raw text content back to playlist.json after verifying JSON syntax."""
    payload = request.get_json() or {}
    raw_content = payload.get("content", "")

    # Validate JSON syntax before overwriting
    try:
        parsed_data = json.loads(raw_content)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Invalid JSON syntax: {str(e)}"}), 400

    try:
        with open(PLAYLIST_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(parsed_data, f, indent=2)
        return jsonify({"status": "success", "message": "playlist.json saved successfully"}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to write playlist config: {str(e)}"}), 500


# -------------------------------------------------------------------
# POST: File Upload and Auto-Playlist Assignment
# -------------------------------------------------------------------
@app.route("/api/playlist/upload", methods=["POST"])
def upload_show_files():
    """Handles audio + sequence upload, overwriting files and updating playlist entries."""
    if "audio" not in request.files or "lights" not in request.files:
        return jsonify({"error": "Missing audio or sequence file in request"}), 400

    audio_file = request.files["audio"]
    lights_file = request.files["lights"]
    selected_playlists_raw = request.form.get("playlists", "[]")

    try:
        target_playlists = json.loads(selected_playlists_raw)
    except Exception:
        target_playlists = []

    if not audio_file.filename or not lights_file.filename:
        return jsonify({"error": "No selected file names"}), 400

    # Secure filenames
    audio_name = secure_filename(audio_file.filename)
    lights_name = secure_filename(lights_file.filename)

    audio_dest = MUSIC_DIR / audio_name
    lights_dest = SEQUENCES_DIR / lights_name

    try:
        # Save files directly (overwrites existing files on disk)
        audio_file.save(audio_dest)
        lights_file.save(lights_dest)

        # Load existing sacn-lightshow/config/playlist.json
        data = load_playlists_data()
        if "playlists" not in data or not isinstance(data["playlists"], dict):
            data["playlists"] = {}

        track_entry = {
            "title": Path(audio_name).stem.replace("_", " ").title(),
            "audio": audio_name,
            "sequence": lights_name,
            "button_hold_s": 1.5,
            "post_delay_s": 3.0
        }

        # Update entries in selected playlists
        for pl_name in target_playlists:
            if pl_name not in data["playlists"]:
                data["playlists"][pl_name] = []

            playlist_items = data["playlists"][pl_name]
            updated = False

            # Search for an existing entry matching audio or sequence filename to overwrite in-place
            for idx, item in enumerate(playlist_items):
                if item.get("audio") == audio_name or item.get("sequence") == lights_name:
                    playlist_items[idx] = track_entry
                    updated = True
                    break

            # Append as new track if no matching entry exists
            if not updated:
                playlist_items.append(track_entry)

        with open(PLAYLIST_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return jsonify({
            "status": "success",
            "audio_saved": audio_name,
            "sequence_saved": lights_name,
            "playlists_updated": target_playlists,
        }), 200

    except Exception as e:
        return jsonify({"error": f"Failed to complete file upload operation: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)