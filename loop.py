#!/usr/bin/env python3
"""Loop: repeat N rounds of [in to the end, wait, back to the middle, shake],
then take the box out to the beginning.

    ./loop.py                       # 5 rounds, defaults
    ./loop.py -n 3 -s 50 -w 2 -m 2  # 3 rounds, speed 50 %, wait 2 s, shake at position 2
"""
import argparse
import time

import zones

DEFAULT_SPEED = 40    # [%]
DEFAULT_ROUNDS = 5
DEFAULT_WAIT = 5.0    # [s] pause at the end
DEFAULT_MIDDLE = 1    # position where the box is shaken


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-s", "--speed", type=zones.speed_arg, default=DEFAULT_SPEED,
                   help=f"transport speed in %% (default {DEFAULT_SPEED})")
    p.add_argument("-n", "--rounds", type=int, default=DEFAULT_ROUNDS,
                   help=f"number of in/shake rounds (default {DEFAULT_ROUNDS})")
    p.add_argument("-w", "--wait", type=float, default=DEFAULT_WAIT,
                   help=f"seconds to wait at the end (default {DEFAULT_WAIT:g})")
    p.add_argument("-m", "--middle", type=zones.position_arg, default=DEFAULT_MIDDLE,
                   help=f"position to shake at (default {DEFAULT_MIDDLE})")
    zones.add_remote_arg(p)
    args = p.parse_args()
    if not zones.FIRST < args.middle < zones.LAST:
        p.error(f"middle must be between {zones.FIRST} and {zones.LAST} (exclusive)")

    with zones.open_conveyor(args) as mc:
        if not zones.occupied(mc):
            print("waiting for a box ...")
            zones.wait_for_box(mc)

        hint = None   # last known position, in case the box drifts off the sensors
        for r in range(1, args.rounds + 1):
            print(f"=== round {r}/{args.rounds}")
            zones.move_to(mc, zones.LAST, args.speed, zones.FORWARD, start=hint)
            print(f"waiting {args.wait:g} s")
            time.sleep(args.wait)
            zones.move_to(mc, args.middle, args.speed, zones.BACKWARD)
            hint = zones.shake(mc, at=args.middle)

        print("=== out")
        zones.move_to(mc, zones.FIRST, args.speed, zones.BACKWARD, start=hint)
        print("done")


if __name__ == "__main__":
    zones.run(main)
