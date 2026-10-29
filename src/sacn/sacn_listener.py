import time
import sacn
import sys
import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if sys.platform == "win32":
    import RPi.GPIO as GPIO
    print("[HAL] Running on Windows: Loaded local ANSI GPIO Emulator.")
else:
    try:
        import RPi.GPIO as GPIO
        print("[HAL] Running on Linux/Raspberry Pi: Loaded native RPI.GPIO hardware driver.")
    except ImportError:
        import RPi.GPIO as GPIO
        print("[HAL] Linux host without native GPIO driver: Loaded local GPIO Emulator fallback.")

def parse_patch_arg(patch_str: str, default_nondim: bool) -> dict[int, dict]:
    """Parse a raw JSON string or JSON file path into a {dmx_addr: {"pin": int, "nondim": bool}} map."""
    data = None

    patch_path = Path(patch_str)
    if patch_path.is_file():
        with open(patch_path, "r") as f:
            data = json.load(f)
    else:
        try:
            data = json.loads(patch_str)
        except json.JSONDecodeError:
            print(f"[Error] Failed to parse patch argument: '{patch_str}'. Must be vald JSON string or file path.")
            sys.exit(1)

    channel_map = {}
    for raw_addr, config in data.items():
        try:
            dmx_addr = int(raw_addr)
        except ValueError:
            print(f"[Error] Invalid patch entry '{raw_addr}'. Must be an integer.")
            sys.exit(1)

        if not (1 <= dmx_addr <= 512):
            print(f"[Error] DMX address {dmx_addr} out of valid range (1-512).")
            sys.exit(1)

        if isinstance(config, dict):
            bcm_pin = int(config.get("pin"))
            is_nondim = bool(config.get("nondim", default_nondim))
        else:
            bcm_pin = int(config)
            is_nondim = default_nondim

        channel_map[dmx_addr] = {"pin": bcm_pin, "nondim": is_nondim}

    return channel_map

def parse_args():
    """Parse command line arguments for Universe and Patch."""
    parser = argparse.ArgumentParser(
        description="sACN GPIO Receiver Node for relays/dimmers."
    )
    parser.add_argument(
        "-u",
        "--universe",
        type=int,
        default=1,
        help="sACN Universe to listen on (default: 1)"
    )
    parser.add_argument(
        "-p",
        "--patch",
        type=str,
        default=None,
        help='JSON string or path to JSON file'
    )
    parser.add_argument(
        "--nondim",
        action="store_true",
        help="Global flag: Set all channels to Non-Dim mode by default"
    )
    return parser.parse_args()

args = parse_args()
UNIVERSE = args.universe
GLOBAL_NONDIM = args.nondim

DEFAULT_CHANNEL_MAP = {
    1: {"pin": 4, "nondim": GLOBAL_NONDIM},
    2: {"pin": 17, "nondim": GLOBAL_NONDIM},
    3: {"pin": 18, "nondim": GLOBAL_NONDIM},
    4: {"pin": 22, "nondim": GLOBAL_NONDIM},
    5: {"pin": 23, "nondim": GLOBAL_NONDIM},
    6: {"pin": 24, "nondim": GLOBAL_NONDIM},
    7: {"pin": 25, "nondim": GLOBAL_NONDIM},
    8: {"pin": 27, "nondim": GLOBAL_NONDIM}
}

if args.patch:
    CHANNEL_MAP = parse_patch_arg(args.patch, default_nondim=GLOBAL_NONDIM)
else:
    CHANNEL_MAP = DEFAULT_CHANNEL_MAP

GPIO.setmode(GPIO.BCM)
drivers = {}

for dmx_addr, cfg in CHANNEL_MAP.items():
    bcm_pin = cfg["pin"]
    is_nondim = cfg["nondim"]
    GPIO.setup(bcm_pin, GPIO.OUT)

    if is_nondim:
        GPIO.output(bcm_pin, GPIO.LOW)
        drivers[dmx_addr] = {"type": "nondim", "pin": bcm_pin}
    else:
        p = GPIO.PWM(bcm_pin, 100)
        p.start(0.0)
        drivers[dmx_addr] = {"type": "pwm", "driver": p, "pin": bcm_pin}

# Initialize sACN receiver engine
receiver = sacn.sACNreceiver()


@receiver.listen_on("universe", universe=UNIVERSE)
def callback(packet):
    """Callback function triggered when sACN multicast packets arrive on Universe 1."""

    # DMX payload is a tuple of 512 integers (0-255)
    dmx_data = packet.dmxData
        
    # Update channels 1-8
    for dmx_addr, drv in drivers.items():
        raw_dmx = dmx_data[dmx_addr - 1]

        if drv["type"] == "nondim":
            state = GPIO.HIGH if raw_dmx >= 128 else GPIO.LOW
            GPIO.output(drv["pin"], state)
        else:
            duty_cycle = (raw_dmx / 255.0) * 100.0
            drv["driver"].ChangeDutyCycle(duty_cycle)

@receiver.listen_on("availability")
def availability_callback(universe, changed):
    """
    Native sACN callback for stream availability updates.
    'changed' can be: 'timeout', 'stream_terminated', or 'available'
    """
    if universe == UNIVERSE:
        if changed in ("timeout", "stream_terminated"):
            for dmx_addr, drv in drivers.items():
                if drv["type"] == "nondim":
                    GPIO.output(drv["pin"], GPIO.LOW)
                else:
                    drv["driver"].ChangeDutyCycle(0.0)

def start_listener():
    receiver.start()
    receiver.join_multicast(UNIVERSE)
    print(f"sACN Listener initialized on Universe {UNIVERSE}. Listening for packets...")
    for addr, cfg in sorted(CHANNEL_MAP.items()):
        mode = "NON-DIM (Relay)" if cfg["nondim"] else "PWM (Dimmer)"
        print(f"  - DMX Address {addr:3d} -> BCM Pin {cfg['pin']:2d} [{mode}]")


def stop_listener():
    receiver.leave_multicast(UNIVERSE)
    receiver.stop()
    GPIO.cleanup()
    print("sACN Listener stopped.")


if __name__ == "__main__":
    start_listener()
    try:
        # Keep main thread alive while background UDP listener processes packets
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down listener...")
        stop_listener()