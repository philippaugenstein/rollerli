#!/usr/bin/env python3
"""Shake: wait for a box at the start sensor, move it to the middle sensor,
shake it by quickly alternating forward/backward, then deliver it to the
end sensor. Ctrl+C or any error stops all motors."""
import logging
import sys
import time

from multicontrol import MultiControl

# ------------------------------------------------------------------ settings
MOTORS = ["motor1", "motor2", "motor3", "motor4"]   # motors that move the box
SHAKE_MOTORS = MOTORS   # motors used for shaking (the box may span several rollers)
START_SENSOR = "sensor1"
MIDDLE_SENSOR = "sensor2"
END_SENSOR = "sensor4"
SPEED = 40              # [%] transport speed
SHAKE_SPEED = 60        # [%] shaking speed
SHAKE_INTERVAL = 0.3    # [s] duration of each forward / backward jolt
SHAKE_CYCLES = 10       # number of forward+backward pairs
SETTLE = 0.5            # [s] pause before and after shaking
MOVE_TIMEOUT = 20.0     # [s] abort if a sensor is not reached in time
# ---------------------------------------------------------------------------


def drive(mc, speed, motors=MOTORS):
    for m in motors:
        mc.set_speed(m, speed)


def drive_until(mc, speed, sensor, blocked=True, timeout=MOVE_TIMEOUT):
    drive(mc, speed)
    reached = mc.wait_for_sensor(sensor, blocked=blocked, timeout=timeout)
    drive(mc, 0)
    if not reached:
        state = "blocked" if blocked else "clear"
        raise TimeoutError(f"{sensor} not {state} within {timeout:.0f} s")


def shake(mc):
    for i in range(SHAKE_CYCLES):
        print(f"\rshaking {i + 1}/{SHAKE_CYCLES}", end="", flush=True)
        drive(mc, +SHAKE_SPEED, SHAKE_MOTORS)
        time.sleep(SHAKE_INTERVAL)
        drive(mc, -SHAKE_SPEED, SHAKE_MOTORS)
        time.sleep(SHAKE_INTERVAL)
    drive(mc, 0, SHAKE_MOTORS)
    print()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with MultiControl("config.yaml") as mc:
        print(f"waiting for a box at {START_SENSOR} ...")
        mc.wait_for_sensor(START_SENSOR, blocked=True)

        print(f"forward to {MIDDLE_SENSOR}")
        drive_until(mc, +SPEED, MIDDLE_SENSOR)
        time.sleep(SETTLE)

        shake(mc)
        time.sleep(SETTLE)
        if not mc.sensor(MIDDLE_SENSOR):
            print(f"note: box drifted off {MIDDLE_SENSOR} while shaking")

        print(f"forward to {END_SENSOR}")
        drive_until(mc, +SPEED, END_SENSOR)
        print("done")


if __name__ == "__main__":
    try:
        main()
    except TimeoutError as e:
        sys.exit(f"aborted: {e}")
    except KeyboardInterrupt:
        sys.exit("aborted by user")
