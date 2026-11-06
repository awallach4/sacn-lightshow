from logging import Logger
import os
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from logger_setup import get_node_logger

# --- PATHS & CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent.parent
MAIN_CONFIG_PATH = BASE_DIR / "config" / "main_config.json"
INIT_NODE_URL = "http://127.0.0.1:5001"


def query_initializer_api(endpoint: str, payload: dict, log: Logger) -> bool:
    """Sends POST command payloads to the Initializer Node IPC server."""
    url = f"{INIT_NODE_URL}{endpoint}"
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception as e:
        log.error(f"[SCHEDULER] Failed to send IPC command to {endpoint}: {e}")
        return False


def get_system_status() -> dict:
    """Queries Initializer Node for system state."""
    url = f"{INIT_NODE_URL}/api/status"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


class SchedulerNode:
    def __init__(self, main_config_path: Path, log: Logger):
        self.main_config_path = main_config_path
        self.main_config = self._load_json(self.main_config_path)
        self.log = log

        # Force lookup of config/schedule.json
        self.schedule_path = BASE_DIR / "config" / "schedule.json"
        paths_cfg = self.main_config.get("system", {}).get("paths", {})
        if "schedule_config" in paths_cfg:
            self.schedule_path = BASE_DIR / paths_cfg["schedule_config"]

        self.running = True
        self.last_triggered_event = None

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception as e:
                self.log.warning(f"[SCHEDULER] Failed to read {path}: {e}")
        else:
            self.log.warning(f"[SCHEDULER] File not found: {path}")
        return {}

    def run(self):
        show_name = self.main_config.get("system", {}).get("show_name", "Holiday Lightshow")

        self.log.info("==================================================")
        self.log.info(f"   {show_name.upper()} - SCHEDULER DAEMON   ")
        self.log.info("==================================================")
        self.log.info(f"[SCHEDULER] Main config: {self.main_config_path}")
        self.log.info(f"[SCHEDULER] Schedule matrix source: {self.schedule_path}")
        self.log.info(f"[SCHEDULER] API Target: {INIT_NODE_URL}")

        while self.running:
            try:
                now = datetime.now()
                day_name = now.strftime("%A").lower()  # e.g. "wednesday"
                time_key = now.strftime("%H%M")         # e.g. "2003"
                event_id = f"{day_name}_{time_key}"

                # Automatically clear latch if minute has changed
                if self.last_triggered_event and not self.last_triggered_event.endswith(time_key):
                    self.last_triggered_event = None

                # Query Initializer status
                status = get_system_status()
                current_state = status.get("system_state", "UNKNOWN")

                # Do not execute scheduled items during emergency PANIC
                if current_state == "PANIC":
                    time.sleep(5.0)
                    continue

                # Load latest schedule matrix
                schedule_data = self._load_json(self.schedule_path)
                day_schedule = schedule_data.get("schedule", {}).get(day_name, {})

                # Check if current minute key exists in schedule
                if time_key in day_schedule:
                    action_target = str(day_schedule[time_key]).strip()

                    if self.last_triggered_event != event_id:
                        self.log.info(f"\n[SCHEDULER] Match found! [{day_name.upper()} {time_key}] -> Target: '{action_target}'")

                        success = False
                        if action_target.upper() in ("ON", "ALL_ON"):
                            success = query_initializer_api("/api/macro", {"action": "ON"}, self.log)
                        elif action_target.upper() in ("OFF", "BLACKOUT"):
                            success = query_initializer_api("/api/macro", {"action": "OFF"}, self.log)
                        elif action_target.upper() == "PANIC":
                            success = query_initializer_api("/api/macro", {"action": "PANIC"}, self.log)
                        else:
                            # Treat any other string as a playlist name.
                            # /api/show/start kills whatever is running before starting, so never fire it
                            # while a show is in progress: that would restart the show mid-song. (It also
                            # makes a retry safe if an earlier request timed out after the show had started.)
                            if current_state == "SHOW_RUNNING":
                                now_playing = status.get("now_playing") or "unknown track"
                                self.log.info(f"[SCHEDULER] Skipping '{action_target}' for {event_id}: a show is already "
                                      f"running (now playing: {now_playing}).")
                                self.last_triggered_event = event_id
                                time.sleep(5.0)
                                continue
                            success = query_initializer_api("/api/show/start", {"playlist": action_target}, self.log)

                        if success:
                            self.last_triggered_event = event_id
                            self.log.info(f"[SCHEDULER] Command successfully dispatched for {event_id}.")

            except Exception as e:
                self.log.error(f"[SCHEDULER] Exception in main loop: {e}")

            # Sleep 5 seconds to ensure we hit the 1-minute window reliably
            time.sleep(5.0)

    def stop(self):
        self.running = False


def main():
    def load_log_level() -> str:
        try:
            if Path(MAIN_CONFIG_PATH).exists():
                with open(Path(MAIN_CONFIG_PATH), "r", encoding="utf-8") as f:
                    return json.load(f).get("log_level", "INFO")
        except Exception:
            pass
        return "INFO"

    log = get_node_logger("scheduler", load_log_level())
    scheduler = SchedulerNode(MAIN_CONFIG_PATH, log)
    try:
        scheduler.run()
    except (KeyboardInterrupt, SystemExit):
        log.info("\n[SCHEDULER] Scheduler shutting down cleanly.")


if __name__ == "__main__":
    main()