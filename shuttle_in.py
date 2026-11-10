#!/usr/bin/env python3
"""Shuttle in: move the box forward from wherever the sensors see it to the
goal position (default: the end), running only the sections it is on.

    ./shuttle_in.py                 # to the end at default speed
    ./shuttle_in.py -s 60 -g 2      # speed 60 %, stop at position 2
"""
import argparse

import zones
from multicontrol import MultiControl

DEFAULT_SPEED = 40   # [%]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-s", "--speed", type=zones.speed_arg, default=DEFAULT_SPEED,
                   help=f"speed in %% (default {DEFAULT_SPEED})")
    p.add_argument("-g", "--goal", type=zones.position_arg, default=zones.LAST,
                   help=f"position {zones.FIRST}-{zones.LAST} to stop at (default {zones.LAST} = end)")
    args = p.parse_args()

    with MultiControl(zones.CONFIG) as mc:
        zones.move_to(mc, args.goal, args.speed, zones.FORWARD)
        print("done")


if __name__ == "__main__":
    zones.run(main)
