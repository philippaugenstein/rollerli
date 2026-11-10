"""zones.py - section-wise box handling for the 4-section roller conveyor.

The conveyor is split into sections (zones). Position n (0..3) is section n,
made of one motor and the sensor that tells whether the box is there:

    position:   0          1          2          3
                beginning                        end
                ----------- forward ----------->
                <---------- backward -----------

Only the sections the box is currently on (sensor blocked), the one ahead and
the one behind it are driven; a section keeps running for RUN_ON seconds after
the box left its sensor. All other rollers stand still.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from multicontrol import MIN_SPEED, MAX_SPEED, MultiControl

# ------------------------------------------------------------------ settings
CONFIG = "config.yaml"
ZONES = [                     # (motor, sensor) per position, beginning -> end
    ("motor1", "sensor1"),
    ("motor2", "sensor2"),
    ("motor3", "sensor3"),
    ("motor4", "sensor4"),
]
FIRST, LAST = 0, len(ZONES) - 1
STEP_TIMEOUT = 10.0           # [s] abort if the sensors do not change for this long while moving
RUN_ON = 1.5                  # [s] keep a section running after the box left its sensor
                              #     (the box still rests partly on those rollers)
POLL = 0.02                   # [s] control loop interval

SHAKE_SPEED = 90              # [%] shaking speed
SHAKE_INTERVAL = 0.2          # [s] duration of each forward / backward jolt
SHAKE_CYCLES = 15             # number of forward+backward pairs
SHAKE_NEIGHBORS = True        # also shake the sections next to the box (box may overhang)
# ---------------------------------------------------------------------------

FORWARD, BACKWARD = +1, -1


class BoxNotFound(RuntimeError):
    """No sensor sees the box."""


def occupied(mc: MultiControl) -> list[int]:
    """Positions whose sensor is blocked, ascending."""
    s = mc.sensors()
    return [i for i, (_, sensor) in enumerate(ZONES) if s[sensor]]


def wait_for_box(mc: MultiControl) -> list[int]:
    """Block until any sensor sees a box, return the occupied positions."""
    while not (occ := occupied(mc)):
        time.sleep(POLL)
    return occ


def stop(mc: MultiControl) -> None:
    for motor, _ in ZONES:
        mc.set_speed(motor, 0)


def move_to(mc: MultiControl, goal: int, speed: int, direction: int,
            start: int | None = None, step_timeout: float = STEP_TIMEOUT) -> None:
    """Move the box in `direction` until its leading edge blocks the sensor of `goal`.

    `start` is a fallback position used when no sensor sees the box at the
    moment (e.g. it drifted between two sensors while shaking).
    """
    if not FIRST <= goal <= LAST:
        raise ValueError(f"goal must be {FIRST}..{LAST}")
    occ = occupied(mc)
    if not occ:
        if start is None:
            raise BoxNotFound("no sensor sees a box")
        occ_ref = [start]
    else:
        occ_ref = occ
    lead = max(occ_ref) if direction == FORWARD else min(occ_ref)
    if goal in occ:
        print(f"box already at position {goal}")
        return
    word = "forward" if direction == FORWARD else "backward"
    if (goal - lead) * direction < 0:
        raise ValueError(f"box is at position {lead}, cannot go {word} to position {goal}")

    print(f"box at position {lead} -> {word} to position {goal}")

    def zones_for(positions):
        # section of the box, the next one ahead and the one behind (the box body
        # extends behind its sensor and may rest mostly on those rollers)
        lo, hi = (FIRST, goal) if direction == FORWARD else (goal, LAST)
        return {i for p in positions for i in (p - direction, p, p + direction) if lo <= i <= hi}

    active = zones_for(occ_ref)
    last_occ, last_change, last_lead = occ, time.monotonic(), lead
    seen = {p: last_change for p in occ_ref}   # position -> last time its sensor was blocked
    try:
        while True:
            occ = occupied(mc)
            now = time.monotonic()
            if goal in occ:
                break
            if occ != last_occ:
                last_occ, last_change = occ, now
            elif now - last_change > step_timeout:
                raise TimeoutError(f"box did not move on within {step_timeout:.0f} s "
                                   f"(sensors blocked at {occ or 'none'})")
            for p in occ:
                seen[p] = now
            recent = [p for p, t in seen.items() if now - t <= RUN_ON]
            if recent:
                active = zones_for(recent)
            if occ:
                cur = max(occ) if direction == FORWARD else min(occ)
                if cur != last_lead:
                    print(f"  position {cur}")
                    last_lead = cur
            for i, (motor, _) in enumerate(ZONES):
                mc.set_speed(motor, direction * speed if i in active else 0)
            time.sleep(POLL)
    finally:
        stop(mc)
    print(f"  position {goal} reached")


def shake(mc: MultiControl, speed: int = SHAKE_SPEED, interval: float = SHAKE_INTERVAL,
          cycles: int = SHAKE_CYCLES, at: int | None = None) -> int:
    """Shake the box where it currently is. Returns the position it was shaken at."""
    occ = occupied(mc) or ([at] if at is not None else [])
    if not occ:
        raise BoxNotFound("no sensor sees a box")
    zones = set(occ)
    if SHAKE_NEIGHBORS:
        zones |= {i for p in occ for i in (p - 1, p + 1) if FIRST <= i <= LAST}
    motors = [ZONES[i][0] for i in sorted(zones)]
    pos = occ[0]
    print(f"shaking at position {pos} (sections {sorted(zones)})")
    try:
        for k in range(cycles):
            print(f"\r  shake {k + 1}/{cycles}", end="", flush=True)
            for m in motors:
                mc.set_speed(m, +speed)
            time.sleep(interval)
            for m in motors:
                mc.set_speed(m, -speed)
            time.sleep(interval)
    finally:
        stop(mc)
        print()
    if not occupied(mc):
        print(f"  note: box drifted off the sensors while shaking")
    return pos


# ------------------------------------------------------------- CLI helpers
def speed_arg(text: str) -> int:
    v = int(text)
    if not MIN_SPEED <= v <= MAX_SPEED:
        raise argparse.ArgumentTypeError(f"speed must be {MIN_SPEED}..{MAX_SPEED}")
    return v


def position_arg(text: str) -> int:
    v = int(text)
    if not FIRST <= v <= LAST:
        raise argparse.ArgumentTypeError(f"position must be {FIRST}..{LAST}")
    return v


def run(main) -> None:
    """Run a script's main() with logging and readable error exits."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        main()
    except (TimeoutError, BoxNotFound, ValueError) as e:
        sys.exit(f"aborted: {e}")
    except KeyboardInterrupt:
        sys.exit("aborted by user")
