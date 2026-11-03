#!/usr/bin/env python3
"""Receive sACN data and drive GPIO devices from every universe in patch.json."""

import argparse
import json
import sys
import time
from pathlib import Path

import sacn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Keep the project's existing GPIO/HAL import convention.
if sys.platform == "win32":
    import RPi.GPIO as GPIO
    print("[HAL] Running on Windows: Loaded local ANSI GPIO Emulator.")
else:
    try:
        import RPi.GPIO as GPIO
        print("[HAL] Running on Linux/Raspberry Pi: Loaded native RPi.GPIO driver.")
    except ImportError:
        import RPi.GPIO as GPIO
        print("[HAL] Linux host without native GPIO driver: Loaded local GPIO emulator fallback.")


def read_patch(patch_arg: str) -> dict:
    """Load a patch file or JSON string."""
    patch_path = Path(patch_arg)
    try:
        if patch_path.is_file():
            with patch_path.open("r", encoding="utf-8") as patch_file:
                data = json.load(patch_file)
        else:
            data = json.loads(patch_arg)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load patch '{patch_arg}': {exc}") from exc

    try:
        universes = data["patch"]
    except (TypeError, KeyError) as exc:
        raise ValueError("Patch must contain a top-level 'patch' object.") from exc
    if not isinstance(universes, dict) or not universes:
        raise ValueError("The patch contains no universes.")
    return universes


def parse_patch(patch_arg: str, default_nondim: bool = False) -> dict:
    """Return {universe: {DMX address: GPIO config}} for GPIO devices only."""
    raw_universes = read_patch(patch_arg)
    result = {}

    for raw_universe, universe_config in raw_universes.items():
        try:
            universe = int(raw_universe)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid sACN universe ID {raw_universe!r}.") from exc
        if universe < 1:
            raise ValueError(f"Invalid sACN universe {universe}; universe IDs must be positive.")

        if not isinstance(universe_config, dict):
            raise ValueError(f"Configuration for universe {universe} must be an object.")
        devices = universe_config.get("devices", {})
        if not isinstance(devices, dict):
            raise ValueError(f"'devices' for universe {universe} must be an object.")

        channels = {}
        for device_id, device in devices.items():
            # This receiver intentionally ignores every non-GPIO device.
            if not isinstance(device, dict) or str(device.get("type", "")).upper() != "GPIO":
                continue

            if "pin" not in device:
                raise ValueError(
                    f"GPIO device {device_id!r} in universe {universe} is missing its BCM pin."
                )
            try:
                pin = int(device["pin"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"GPIO device {device_id!r} in universe {universe} has invalid pin "
                    f"{device.get('pin')!r}."
                ) from exc

            parameters = device.get("parameters", [])
            if not isinstance(parameters, list):
                raise ValueError(
                    f"GPIO device {device_id!r} in universe {universe}: "
                    "'parameters' must be a list."
                )

            for parameter in parameters:
                if not isinstance(parameter, dict) or "address" not in parameter:
                    raise ValueError(
                        f"GPIO device {device_id!r} in universe {universe} has a "
                        "parameter without a DMX address."
                    )
                try:
                    address = int(parameter["address"])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Invalid DMX address {parameter.get('address')!r} on "
                        f"GPIO device {device_id!r} in universe {universe}."
                    ) from exc
                if not 1 <= address <= 512:
                    raise ValueError(
                        f"DMX address {address} on GPIO device {device_id!r} "
                        "is outside the valid range 1-512."
                    )
                if address in channels:
                    previous = channels[address]["device_id"]
                    raise ValueError(
                        f"DMX address {address} is assigned more than once in universe "
                        f"{universe} (devices {previous!r} and {device_id!r})."
                    )

                channels[address] = {
                    "pin": pin,
                    "nondim": bool(parameter.get("nondim", default_nondim)),
                    "device_id": str(device_id),
                    "device_label": device.get("label", str(device_id)),
                    "parameter_label": parameter.get("label", f"DMX {address}"),
                }

        if channels:
            result[universe] = channels

    if not result:
        raise ValueError("No GPIO parameters were found in any patched universe.")

    # A physical GPIO pin cannot independently represent multiple patched channels.
    pin_owners = {}
    for universe, channels in result.items():
        for address, config in channels.items():
            pin = config["pin"]
            owner = (universe, address)
            if pin in pin_owners:
                old_universe, old_address = pin_owners[pin]
                raise ValueError(
                    f"BCM pin {pin} is assigned to multiple channels: "
                    f"universe {old_universe} DMX {old_address} and "
                    f"universe {universe} DMX {address}."
                )
            pin_owners[pin] = owner

    return result


def parse_args():
    parser = argparse.ArgumentParser(
        description="sACN GPIO receiver driven by every GPIO device in patch.json."
    )
    parser.add_argument(
        "-p", "--patch", required=True,
        help="Path to patch.json, or a JSON string using the project patch schema.",
    )
    parser.add_argument(
        "--nondim", action="store_true",
        help="Use non-dim mode only when a GPIO parameter omits its 'nondim' setting.",
    )
    return parser.parse_args()


def create_drivers(patch_map: dict) -> dict:
    """Configure each GPIO once; index drivers by universe and DMX address."""
    GPIO.setmode(GPIO.BCM)
    drivers = {}
    try:
        for universe, channels in patch_map.items():
            drivers[universe] = {}
            for address, config in channels.items():
                pin = config["pin"]
                GPIO.setup(pin, GPIO.OUT)

                if config["nondim"]:
                    GPIO.output(pin, GPIO.LOW)
                    driver = {"type": "nondim", "pin": pin}
                else:
                    pwm = GPIO.PWM(pin, 100)
                    pwm.start(0.0)
                    driver = {"type": "pwm", "pin": pin, "driver": pwm}

                drivers[universe][address] = driver
    except Exception:
        for universe_drivers in drivers.values():
            for driver in universe_drivers.values():
                if driver["type"] == "pwm":
                    driver["driver"].stop()
        GPIO.cleanup()
        raise
    return drivers


def set_universe_off(universe_drivers: dict):
    """Set all GPIO outputs belonging to one unavailable universe to zero."""
    for driver in universe_drivers.values():
        if driver["type"] == "nondim":
            GPIO.output(driver["pin"], GPIO.LOW)
        else:
            driver["driver"].ChangeDutyCycle(0.0)


def make_universe_callback(universe: int, universe_drivers: dict):
    """Create a callback bound to one universe's configured GPIO channels."""
    def callback(packet):
        dmx_data = packet.dmxData
        for address, driver in universe_drivers.items():
            index = address - 1
            if index >= len(dmx_data):
                continue

            level = dmx_data[index]
            if driver["type"] == "nondim":
                GPIO.output(driver["pin"], GPIO.HIGH if level >= 128 else GPIO.LOW)
            else:
                driver["driver"].ChangeDutyCycle((level / 255.0) * 100.0)

    return callback


def main():
    args = parse_args()
    try:
        patch_map = parse_patch(args.patch, args.nondim)
    except ValueError as exc:
        raise SystemExit(f"[Error] {exc}") from exc

    drivers = create_drivers(patch_map)
    receiver = sacn.sACNreceiver()
    joined_universes = []

    # Register one packet callback for every universe containing GPIO channels.
    for universe, universe_drivers in drivers.items():
        callback = make_universe_callback(universe, universe_drivers)
        receiver.listen_on("universe", universe=universe)(callback)

    @receiver.listen_on("availability")
    def availability_callback(universe, changed):
        if changed in ("timeout", "stream_terminated") and universe in drivers:
            print(f"[sACN] Universe {universe} {changed}; setting its GPIO outputs off.")
            set_universe_off(drivers[universe])

    try:
        receiver.start()
        for universe in sorted(drivers):
            receiver.join_multicast(universe)
            joined_universes.append(universe)

        print("sACN GPIO listener active on patched universes:")
        for universe in sorted(patch_map):
            channels = patch_map[universe]
            print(f"  Universe {universe}:")
            for address, config in sorted(channels.items()):
                mode = "NON-DIM" if config["nondim"] else "PWM"
                print(
                    f"    DMX {address:3d} -> BCM {config['pin']:2d} "
                    f"[{mode}] {config['device_label']}"
                )

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down sACN GPIO listener...")
    finally:
        try:
            for universe in joined_universes:
                receiver.leave_multicast(universe)
            receiver.stop()
        finally:
            for universe_drivers in drivers.values():
                for driver in universe_drivers.values():
                    if driver["type"] == "pwm":
                        driver["driver"].stop()
            GPIO.cleanup()
        print("sACN GPIO listener stopped.")


if __name__ == "__main__":
    main()
