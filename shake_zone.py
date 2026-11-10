#!/usr/bin/env python3
"""Shake the box in the section where the sensors see it (stays in place).

    ./shake_zone.py                 # default intensity
    ./shake_zone.py -s 100 -c 20    # harder and longer
"""
import argparse

import zones
from multicontrol import MultiControl


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-s", "--speed", type=zones.speed_arg, default=zones.SHAKE_SPEED,
                   help=f"shake speed in %% (default {zones.SHAKE_SPEED})")
    p.add_argument("-c", "--cycles", type=int, default=zones.SHAKE_CYCLES,
                   help=f"number of forward+backward jolts (default {zones.SHAKE_CYCLES})")
    p.add_argument("-i", "--interval", type=float, default=zones.SHAKE_INTERVAL,
                   help=f"seconds per jolt, min 0.1 (default {zones.SHAKE_INTERVAL})")
    args = p.parse_args()
    if args.interval < 0.1:
        p.error("interval must be at least 0.1 s")

    with MultiControl(zones.CONFIG) as mc:
        zones.shake(mc, args.speed, args.interval, args.cycles)
        print("done")


if __name__ == "__main__":
    zones.run(main)
