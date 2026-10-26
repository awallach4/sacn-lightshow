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
_pwm_instances = {}

def setmode(mode):
    global _mode
    _mode = mode

def setwarnings(flag):
    pass

def setup(pin, mode, initial=LOW):
    _pins[pin] = {"mode": mode, "state": initial, "duty_cycle": 0.0}

def output(pin, state):
    if pin in _pins:
        _pins[pin]["state"] = state
        _pins[pin]["duty_cycle"] = 100.0 if state else 0.0
        _render_console()

class PWM:
    def __init__(self, pin, frequency):
        self.pin = pin
        self.frequency = frequency
        self.duty_cycle = 0.0

    def start(self, duty_cycle):
        self.ChangeDutyCycle(duty_cycle)

    def ChangeDutyCycle(self, duty_cycle):
        self.duty_cycle = max(0.0, min(100.0, duty_cycle))
        if self.pin in _pins:
            _pins[self.pin]["duty_cycle"] = self.duty_cycle
        _render_console()

    def stop(self):
        self.duty_cycle = 0.0

def cleanup():
    _pins.clear()

# Enable native ANSI color/escape sequence processing in Windows Console
if os.name == 'nt':
    import os
    os.system('')

def _render_console():
    """Renders real-time ASCII channel output smoothly using ANSI cursor repositioning."""
    lines = []
    lines.append("==================================================")
    lines.append("      LIGHTSHOWPI / RPU GPIO EMULATOR (WINDOWS)   ")
    lines.append("==================================================")
    
    for pin in sorted(_pins.keys()):
        raw_duty = _pins[pin]["duty_cycle"]
        gamma_corrected = math_gamma(raw_duty / 100.0, gamma=2.8) * 100.0
        
        bar_length = int(gamma_corrected / 5.0)
        bar = "█" * bar_length + "-" * (20 - bar_length)
        
        lines.append(f"Channel Pin {pin:02d} | [{bar}] {gamma_corrected:5.1f}% (Raw: {raw_duty:3.0f}%)")
    
    lines.append("==================================================")
    
    # ANSI Sequence: \033[H moves cursor to row 1, col 1 without clearing the buffer
    output = "\033[H" + "\n".join(lines) + "\n"
    sys.stdout.write(output)
    sys.stdout.flush()

def math_gamma(val, gamma=2.8):
    """Calculates square-law dimming response."""
    return max(0.0, min(1.0, val)) ** gamma