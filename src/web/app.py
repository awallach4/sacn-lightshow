import sys
from pathlib import Path
import ssl

IMPORT_ROOT = Path(__file__).resolve().parent.parent

if str(IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(IMPORT_ROOT))

from logger_setup import get_node_logger

CONFIG_FILE = Path("config/main_config.json")
CERT_PATH = Path("config/jobber.tail7de9f9.ts.net.crt")
KEY_PATH = Path("config/jobber.tail7de9f9.ts.net.key")

def load_log_level() -> str:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("log_level", "INFO")
    except Exception:
        pass
    return "INFO"

# Initialize node logger
log = get_node_logger("web", load_log_level())

def _fatal_import(exc):
    """Explain a missing/broken dependency in terms of the exact interpreter that failed."""
    log.error(f"[Flask] FATAL: could not import a required package: {exc}")
    log.error(f"[Flask]   Python in use : {sys.executable} ({sys.version.split()[0]})")
    log.error(f"[Flask]   Fix           : {sys.executable} -m pip install -r requirements.txt")
    log.error("[Flask]   (Ubuntu 23.04+ blocks system-wide pip installs: use a venv, and start main.py with that "
              "venv's python, also under sudo.)")
    sys.exit(1)


# gevent must patch the standard library BEFORE anything else is imported. Without it, every blocking
# call in a request handler (e.g. the HTTP call to the Initializer in /api/telemetry) freezes the whole
# server, including the panic button, until it returns.
try:
    from gevent import monkey
    monkey.patch_all()
except ImportError as _exc:
    _fatal_import(_exc)

import os
import json
import time
import platform
import urllib.request
import urllib.error

try:
    import gevent
    from gevent.pywsgi import WSGIServer
    from geventwebsocket.handler import WebSocketHandler
    import psutil
    from flask import Flask, jsonify, request, send_from_directory
    from flask_cors import CORS
    from werkzeug.utils import secure_filename
    import sacn
except ImportError as _exc:
    _fatal_import(_exc)

# Resolve base directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR = PROJECT_ROOT / "frontend" / "dist"

# Windows IPC fallback path vs Linux /dev/shm shared memory
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    IPC_DIR = PROJECT_ROOT / ".tmp"
    IPC_DIR.mkdir(exist_ok=True)
else:
    IPC_DIR = Path("/dev/shm")

# Initializer Node IPC Endpoint
INIT_NODE_URL = "http://127.0.0.1:5001"

# static_folder=None: Flask's built-in static route would otherwise capture every path ahead of the
# routes below, so SPA deep links (e.g. /playlist) 404 and /api typos return HTML instead of JSON.
app = Flask(__name__, static_folder=None)
CORS(app)  # Allows Vite dev server (http://localhost:5173) to query API directly
app.config['MAX_CONTENT_LENGTH'] = 150 * 1024 * 1024

def get_cpu_temp() -> float:
    """Cross-platform CPU temperature reader."""
    if IS_WINDOWS:
        try:
            temps = getattr(psutil, "sensors_temperatures", lambda: {})()
            if temps:
                for sensor_name, entries in temps.items():
                    if entries:
                        return round(entries[0].current, 1)
        except Exception:
            pass
        return 0.0
    else:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return round(float(f.read().strip()) / 1000.0, 1)
        except Exception:
            temps = getattr(psutil, "sensors_temperatures", lambda: {})()
            if "cpu_thermal" in temps and temps["cpu_thermal"]:
                return round(temps["cpu_thermal"][0].current, 1)
            return 0.0


def query_initializer_node(endpoint: str, method: str = "GET", payload: dict = None) -> tuple[dict, int]:
    """Helper function to proxy HTTP IPC requests to the Initializer Node on port 5001."""
    url = f"{INIT_NODE_URL}{endpoint}"
    try:
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method=method
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body, resp.status
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {"error": "IPC call failed"}
        return err_body, e.code
    except Exception as e:
        return {"error": f"Initializer Node unreachable: {str(e)}"}, 503


def is_show_active() -> bool:
    """Query Initializer Node status to check if a show is actively running."""
    init_data, status_code = query_initializer_node("/api/status", method="GET")
    if status_code == 200:
        return init_data.get("system_state") == "SHOW_RUNNING"
    return False


# -------------------------------------------------------------------
# Telemetry Endpoint
# -------------------------------------------------------------------
@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    cpu_usage = psutil.cpu_percent(interval=None)
    ram_usage = psutil.virtual_memory().percent
    cpu_temp = get_cpu_temp()

    init_data, status_code = query_initializer_node("/api/status", method="GET")

    system_state = "UNKNOWN"
    node_status = {
        "sACN Listener": False,
        "Show Control": False,
        "Scheduler": False,
        "Web Backend": True
    }

    if status_code == 200 and "nodes" in init_data:
        system_state = init_data.get("system_state", "OFF")
        nodes = init_data["nodes"]

        node_status["sACN Listener"] = nodes.get("sacn_listener", {}).get("status") == "RUNNING"
        node_status["Show Control"] = nodes.get("show_control", {}).get("status") == "RUNNING"
        node_status["Scheduler"] = nodes.get("scheduler", {}).get("status") == "RUNNING"
        node_status["Web Backend"] = True

    return jsonify({
        "cpu_usage": cpu_usage,
        "ram_usage": ram_usage,
        "cpu_temp": cpu_temp,
        "system_state": system_state,
        "node_status": node_status,
        "now_playing": init_data.get("now_playing", ""),
        "next_up": init_data.get("next_up", "")
    }), 200


# -------------------------------------------------------------------
# Show Macro Controls
# -------------------------------------------------------------------
@app.route("/api/macro", methods=["POST"])
def execute_macro():
    payload = request.get_json() or {}
    action = str(payload.get("action", "")).strip().lower()

    macro_map = {
        "on": "ON",
        "all_on": "ON",
        "off": "OFF",
        "blackout": "OFF",
        "panic": "PANIC",
        "reset": "RESET"
    }

    if action not in macro_map:
        return jsonify({"error": f"Invalid macro action '{action}'"}), 400

    target_action = macro_map[action]
    init_payload = {"action": target_action}
    if target_action == "ON" and "level" in payload:
        init_payload["level"] = payload["level"]

    res_body, status_code = query_initializer_node("/api/macro", method="POST", payload=init_payload)
    return jsonify(res_body), status_code


# -------------------------------------------------------------------
# Show Control Start Endpoint
# -------------------------------------------------------------------
@app.route("/api/show/start", methods=["POST"])
def start_show():
    payload = request.get_json() or {}
    playlist = payload.get("playlist")

    if not playlist:
        return jsonify({"error": "Missing 'playlist' parameter"}), 400

    res_body, status_code = query_initializer_node(
        "/api/show/start",
        method="POST",
        payload={"playlist": playlist}
    )
    return jsonify(res_body), status_code


# -------------------------------------------------------------------
# Host Controls (Reboot & Shutdown)
# -------------------------------------------------------------------
@app.route("/api/host/<command>", methods=["POST"])
def host_control(command):
    cmd = command.lower()
    if cmd not in ["reboot", "shutdown"]:
        return jsonify({"error": "Invalid host command. Options: ['reboot', 'shutdown']"}), 400

    res_body, status_code = query_initializer_node(
        "/api/host",
        method="POST",
        payload={"action": cmd}
    )
    return jsonify(res_body), status_code


###############################################################################
#                          Config & Playlist Backend                          #
###############################################################################

SHOWFILES_DIR = PROJECT_ROOT / "showfiles"
MUSIC_DIR = SHOWFILES_DIR / "music"
SEQUENCES_DIR = SHOWFILES_DIR / "sequences"
CONFIG_DIR = PROJECT_ROOT / "config"

CONFIG_FILES = {
    "main_config": CONFIG_DIR / "main_config.json",
    "patch": CONFIG_DIR / "patch.json",
    "playlist": CONFIG_DIR / "playlist.json",
    "schedule": CONFIG_DIR / "schedule.json"
}

MUSIC_DIR.mkdir(parents=True, exist_ok=True)
SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_playlists_data() -> dict:
    playlist_path = CONFIG_FILES["playlist"]
    if playlist_path.is_file():
        try:
            with open(playlist_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"playlists": {}}


@app.route("/api/config/<config_type>", methods=["GET"])
def get_json_config(config_type):
    """Returns raw text content of requested JSON config file."""
    if config_type not in CONFIG_FILES:
        return jsonify({"error": f"Invalid config type '{config_type}'. Options: {list(CONFIG_FILES.keys())}"}), 404

    target_file = CONFIG_FILES[config_type]

    if not target_file.is_file():
        default_content = {"playlists": {"playlist": [], "playlist 2": []}} if config_type == "playlist" else {}
        try:
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(default_content, f, indent=2)
            return jsonify({"content": json.dumps(default_content, indent=2)}), 200
        except Exception as e:
            return jsonify({"error": f"Failed to initialize default {config_type}.json: {str(e)}"}), 500

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            raw_text = f.read()
        return jsonify({"content": raw_text}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to read {config_type}.json: {str(e)}"}), 500


@app.route("/api/config/<config_type>", methods=["POST"])
def save_json_config(config_type):
    """Saves raw text content to JSON configuration and triggers target node reload via IPC."""
    if config_type not in CONFIG_FILES:
        return jsonify({"error": f"Invalid config type '{config_type}'. Options: {list(CONFIG_FILES.keys())}"}), 404

    # Guard: Block saves while a show is running
    if is_show_active():
        return jsonify({"error": f"Cannot modify {config_type}.json while show is running. Stop the show first."}), 409

    payload = request.get_json(silent=True) or {}
    raw_content = payload.get("content")

    if raw_content is None:
        return jsonify({"error": "Missing 'content' field in request body."}), 400

    try:
        if isinstance(raw_content, str):
            parsed_data = json.loads(raw_content)
        elif isinstance(raw_content, (dict, list)):
            parsed_data = raw_content
        else:
            return jsonify({"error": "Content must be a JSON string or valid object/array."}), 400
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Invalid JSON syntax: {str(e)}"}), 400

    target_file = CONFIG_FILES[config_type]
    temp_file = target_file.with_name(f".{config_type}.json.tmp")

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(parsed_data, f, indent=2)

        os.replace(temp_file, target_file)

        # Full system stack reload must be deferred to prevent app.py process teardown mid-request
        if config_type == "main_config":
            def _async_ipc_reload():
                gevent.sleep(0.2)  # Give app.py time to flush 200 OK to browser
                query_initializer_node(
                    "/api/config/reload",
                    method="POST",
                    payload={"config_type": "main_config"}
                )

            gevent.spawn(_async_ipc_reload)

            return jsonify({
                "status": "success",
                "message": "main_config.json saved. Full system process stack reloading..."
            }), 200

        # Standard synchronous IPC reload for child nodes (scheduler, patch, playlist)
        res_body, status_code = query_initializer_node(
            "/api/config/reload",
            method="POST",
            payload={"config_type": config_type}
        )

        if status_code != 200:
            return jsonify({"warning": f"Saved {config_type}.json, but IPC reload failed: {res_body.get('error')}"}), status_code

        return jsonify({
            "status": "success",
            "message": f"{config_type}.json saved. {res_body.get('message', '')}"
        }), 200

    except Exception as e:
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)
        return jsonify({"error": f"Failed to write {config_type}.json: {str(e)}"}), 500


@app.route("/api/playlist/upload", methods=["POST"])
def upload_show_files():
    """Handles audio + sequence upload and updates playlist entries."""
    if "audio" not in request.files or "lights" not in request.files:
        return jsonify({"error": "Missing audio or sequence file in request"}), 400

    if is_show_active():
        return jsonify({"error": "Cannot upload files while show is running. Stop the show first."}), 409

    audio_file = request.files["audio"]
    lights_file = request.files["lights"]
    selected_playlists_raw = request.form.get("playlists", "[]")

    try:
        target_playlists = json.loads(selected_playlists_raw)
    except Exception:
        target_playlists = []

    if not audio_file.filename or not lights_file.filename:
        return jsonify({"error": "No selected file names"}), 400

    audio_name = secure_filename(audio_file.filename)
    lights_name = secure_filename(lights_file.filename)

    audio_dest = MUSIC_DIR / audio_name
    lights_dest = SEQUENCES_DIR / lights_name

    try:
        audio_file.save(audio_dest)
        lights_file.save(lights_dest)

        data = load_playlists_data()
        if "playlists" not in data or not isinstance(data["playlists"], dict):
            data["playlists"] = {}

        track_entry = {
            "title": Path(audio_name).stem.replace("_", " ").title(),
            "audio": audio_name,
            "sequence": lights_name,
            "button_hold_s": 1.5,
            "post_delay_s": 3.0,
            "audio_offset_s": 0.0,
            "speaker_offset_s": 0.0,
            "light_speed": 1.0,
            "rds": Path(audio_name).stem.replace("_", "").title()
        }

        for pl_name in target_playlists:
            if pl_name not in data["playlists"]:
                data["playlists"][pl_name] = []

            playlist_items = data["playlists"][pl_name]
            updated = False

            for idx, item in enumerate(playlist_items):
                if item.get("audio") == audio_name or item.get("sequence") == lights_name:
                    playlist_items[idx] = track_entry
                    updated = True
                    break

            if not updated:
                playlist_items.append(track_entry)

        with open(CONFIG_FILES["playlist"], "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Notify main.py of playlist change
        query_initializer_node(
            "/api/config/reload",
            method="POST",
            payload={"config_type": "playlist"}
        )

        return jsonify({
            "status": "success",
            "audio_saved": audio_name,
            "sequence_saved": lights_name,
            "playlists_updated": target_playlists,
        }), 200

    except Exception as e:
        return jsonify({"error": f"Failed to complete file upload operation: {str(e)}"}), 500


###############################################################################
#                               Console Backend                               #
###############################################################################

sacn_sender = sacn.sACNsender(source_name="Web Console", fps=30)
sacn_sender.start()

active_universes = set()
last_console_frame = 0.0
CONSOLE_IDLE_RELEASE_S = 600.0


def release_console_outputs() -> list:
    """Zero and deactivate every universe the console is driving."""
    released = sorted(active_universes)
    for universe in released:
        try:
            sacn_sender[universe].dmx_data = (0,) * 512
        except Exception:
            pass
    if released:
        gevent.sleep(0.1)
    for universe in released:
        try:
            sacn_sender.deactivate_output(universe)
        except Exception:
            pass
    active_universes.clear()
    return released


def _console_idle_watchdog():
    while True:
        gevent.sleep(5.0)
        if active_universes and time.monotonic() - last_console_frame > CONSOLE_IDLE_RELEASE_S:
            log.info(f"[Flask] Console idle for {CONSOLE_IDLE_RELEASE_S:.0f}s; releasing universes {sorted(active_universes)}.")
            release_console_outputs()


gevent.spawn(_console_idle_watchdog)


@app.route("/api/sacn/release", methods=["POST"])
def sacn_release_handler():
    return jsonify({"status": "released", "universes": release_console_outputs()}), 200


@app.route("/api/sacn/stream", methods=["POST"])
def sacn_stream_handler():
    global last_console_frame
    last_console_frame = time.monotonic()
    payload = request.get_json() or {}
    priority = payload.get("priority", 50)
    frame = payload.get("frame", {})

    universe_buffers = {}
    for dmx_key, val in frame.items():
        try:
            u_str, a_str = dmx_key.split("/")
            universe = int(u_str)
            address = int(a_str)
            
            if universe not in universe_buffers:
                universe_buffers[universe] = [0] * 512
            
            if 1 <= address <= 512:
                universe_buffers[universe][address - 1] = max(0, min(255, int(val)))
        except Exception:
            continue

    for universe, dmx_data in universe_buffers.items():
        if universe not in active_universes:
            sacn_sender.activate_output(universe)
            sacn_sender[universe].multicast = True
            active_universes.add(universe)
        
        sacn_sender[universe].priority = priority
        sacn_sender[universe].dmx_data = dmx_data

    return jsonify({"status": "transmitting", "priority": priority, "universes": list(universe_buffers.keys())}), 200


# -------------------------------------------------------------------
# SPA Routing
# -------------------------------------------------------------------
@app.route("/")
def serve_index():
    if (DIST_DIR / "index.html").exists():
        return send_from_directory(DIST_DIR, "index.html")
    return jsonify({"status": "API active", "note": "Vue dist build not found. Run Vite dev server."}), 200


@app.route("/<path:path>")
def serve_static(path):
    if path.startswith("api/"):
        return jsonify({"error": f"Unknown API endpoint '/{path}'"}), 404
    if (DIST_DIR / path).exists():
        return send_from_directory(DIST_DIR, path)
    elif (DIST_DIR / "index.html").exists():
        return send_from_directory(DIST_DIR, "index.html")
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    if os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH):
        log.info("[Flask] Starting HTTPS WSGI server on port 5000...")
        ssl_args = {
            "certfile": CERT_PATH,
            "keyfile": KEY_PATH
        }
        http_server = WSGIServer(('0.0.0.0', 5000), app, **ssl_args)
    else:
        log.info("[Flask] Starting gevent WSGI server on port 5000...")
        http_server = WSGIServer(("0.0.0.0", 5000), app, handler_class=WebSocketHandler)
    http_server.serve_forever()
