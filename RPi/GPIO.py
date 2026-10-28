import sys
import time
import os

# Constants matching RPi.GPIO
BCM = 11
BOARD = 10
OUT = 0
IN = 1
HIGH = 1
LOW = 0

_mode = None
_pins = {}
_initialized = False

ACTIVE_LOW = False
NON_DIM_THRESHOLD = 50.0

def setmode(mode):
    global _mode
    _mode = mode

def setwarnings(flag):
    pass

def setup(pin, mode, initial=HIGH if ACTIVE_LOW else LOW):
    """Sets up a pin, initial level defaults to HIGH when set to ACTIVE_LOW."""
    is_on = (initial == LOW) if ACTIVE_LOW else (initial == HIGH)
    _pins[pin] = {
        "mode": mode,
        "is_on": is_on,
        "raw_level": 100.0 if is_on else 0.0
    }
    render_initial_table()

def output(pin, state):
    if pin in _pins:
        if isinstance(state, (float, int)) and state > 1:
            raw_percent = (state / 255.0) * 100.0
        else:
            raw_percent = 100.0 if state else 0.0

        is_on = raw_percent >= NON_DIM_THRESHOLD

        _pins[pin]["is_on"] = is_on
        _pins[pin]["raw_level"] = raw_percent
        _render_console()

class PWM:
    def __init__(self, pin, frequency):
        self.pin = pin
        self.frequency = frequency
        self.duty_cycle = 0.0

    def start(self, duty_cycle):
        self.ChangeDutyCycle(duty_cycle)

    def ChangeDutyCycle(self, duty_cycle):
        self.duty_cycle = max(0.0, min(100.0, float(duty_cycle)))
        if self.pin in _pins:
            is_on = self.duty_cycle >= NON_DIM_THRESHOLD
            _pins[self.pin]["is_on"] = is_on
            _pins[self.pin]["raw_level"] = self.duty_cycle
        _render_console()

    def stop(self):
        self.duty_cycle = 0.0
        if self.pin in _pins:
            _pins[self.pin]["is_on"] = False
            _pins[self.pin]["raw_level"] = 0.0
        _render_console()

def render_initial_table():
    global _initialized
    _initialized = True
    _render_console(first_run=True)

def cleanup():
    _pins.clear()

# Enable native ANSI color/escape sequence processing in Windows Console
if os.name == 'nt':
    os.system('')

def _render_console(first_run=False):
    """Renders real-time ASCII channel output smoothly using ANSI cursor repositioning."""
    if not _pins or not _initialized:
        return

    lines = [
        "==================================================",
        "            RPi GPIO EMULATOR (WINDOWS)           ",
        "=================================================="
    ]
    
    for pin in sorted(_pins.keys()):
        is_on = _pins[pin]["is_on"]
        raw_lvl = _pins[pin]["raw_level"]

        if is_on:
            bar = "█" * 20
            state_str = "ON [ACTIVE-LOW: 0V]"
        else:
            bar = "-" * 20
            state_str = "OFF [ACTIVE-LOW: 3.3V]"
        
        lines.append(f"Channel Pin {pin:02d} | [{bar}] {state_str} (Level: {raw_lvl:3.0f}%)")
    
    lines.append("==================================================")

    prefix = "\n" if first_run else "\033[H"
    sys.stdout.write(prefix + "\n".join(lines) + "\n")
    sys.stdout.flush()
