#!/usr/bin/env python3
"""Shuttle: wait for a box at the start sensor, move it forward to the end
sensor, wait, then move it backwards to the start sensor again.
Ctrl+C or any error stops all motors."""
import logging
import sys
import time

from multicontrol import MultiControl

# ------------------------------------------------------------------ settings
MOTORS = ["motor1", "motor2", "motor3", "motor4"]   # motors that move the box
START_SENSOR = "sensor1"
END_SENSOR = "sensor4"
SPEED = 40              # [%] 5..100
WAIT_AT_END = 5.0       # [s]
MOVE_TIMEOUT = 20.0     # [s] abort if a sensor is not reached in time
EJECT = False           # True: keep going backwards until the box has left the start sensor
EJECT_EXTRA = 1.0       # [s] extra run time after the box left the start sensor
# ---------------------------------------------------------------------------


def drive(mc, speed):
    """All box motors at the same signed speed (0 = stop)."""
    for m in MOTORS:
        mc.set_speed(m, speed)


def drive_until(mc, speed, sensor, blocked=True, timeout=MOVE_TIMEOUT):
    """Drive until the sensor reaches the given state, then stop."""
    drive(mc, speed)
    reached = mc.wait_for_sensor(sensor, blocked=blocked, timeout=timeout)
    drive(mc, 0)
    if not reached:
        state = "blocked" if blocked else "clear"
        raise TimeoutError(f"{sensor} not {state} within {timeout:.0f} s")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with MultiControl("config.yaml") as mc:
        print(f"waiting for a box at {START_SENSOR} ...")
        mc.wait_for_sensor(START_SENSOR, blocked=True)

        print(f"forward to {END_SENSOR}")
        drive_until(mc, +SPEED, END_SENSOR)

        print(f"at the end - waiting {WAIT_AT_END:.0f} s")
        time.sleep(WAIT_AT_END)

        print(f"backward to {START_SENSOR}")
        drive_until(mc, -SPEED, START_SENSOR)

        if EJECT:
            print("ejecting")
            drive_until(mc, -SPEED, START_SENSOR, blocked=False)
            drive(mc, -SPEED)
            time.sleep(EJECT_EXTRA)
            drive(mc, 0)
        print("done")


if __name__ == "__main__":
    try:
        main()
    except TimeoutError as e:
        sys.exit(f"aborted: {e}")
    except KeyboardInterrupt:
        sys.exit("aborted by user")
