import sys
import time
import math

# Direct RPi imports to our local emulator module
import RPi.GPIO as GPIO

# Configure 8-Channel Layout (Standard LightShowPi BCM mapping)
CHANNELS = [4, 17, 18, 22, 23, 24, 25, 27]

GPIO.setmode(GPIO.BCM)
pwms = []

for pin in CHANNELS:
    GPIO.setup(pin, GPIO.OUT)
    p = GPIO.PWM(pin, 100) # 100Hz software PWM
    p.start(0)
    pwms.append(p)

print("Starting GPIO Emulator Test Stream...")
time.sleep(1)

try:
    # Simulate a 10-second sinusoidal sweep across 8 channels
    for t in range(200):
        for i, pwm in enumerate(pwms):
            # Calculate sine wave offset per channel
            phase = (t / 10.0) + (i * 0.5)
            level = (math.sin(phase) + 1.0) / 2.0 * 100.0
            pwm.ChangeDutyCycle(level)
        time.sleep(0.05)

finally:
    GPIO.cleanup()
    print("Test Complete. GPIO Cleaned up.")