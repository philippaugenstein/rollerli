#!/usr/bin/env python3
"""Manual mode: drive the conveyor with the arrow keys.

    ->  forward          <-  backward
    up / down  speed +/- 5 %
    space  stop          q  quit

Default is hold-to-drive: the rollers run while an arrow key is held and stop
shortly after it is released (detected by the key auto-repeat stopping, so
the first stop can take up to --release seconds). With --latch a tap starts
the movement and space stops it.

Soft limits: forward stops while the end sensor is blocked, backward while
the start sensor is blocked, so the box is not driven off the conveyor.
--no-limits disables that (e.g. to take the box off).

    ./manual.py                     # hold-to-drive, 40 %
    ./manual.py -s 25 --latch       # tap to start, space to stop
    ./manual.py --sections 1 2      # only drive sections 1 and 2
"""
import argparse
import os
import select
import sys
import termios
import time
import tty

import zones
from multicontrol import MAX_SPEED, MIN_SPEED

DEFAULT_SPEED = 40      # [%]
SPEED_STEP = 5          # [%] per up/down key press
RELEASE = 0.6           # [s] hold mode: stop if no key repeat arrives for this long
                        #     (must be longer than the keyboard's auto-repeat delay)

KEYS = {                # escape sequences (normal and application cursor mode)
    b"\x1b[C": "right", b"\x1bOC": "right",
    b"\x1b[D": "left", b"\x1bOD": "left",
    b"\x1b[A": "up", b"\x1bOA": "up",
    b"\x1b[B": "down", b"\x1bOB": "down",
    b" ": "space", b"q": "quit", b"Q": "quit",
}


def parse_keys(data: bytes) -> list[str]:
    keys, i = [], 0
    while i < len(data):
        for seq, name in KEYS.items():
            if data.startswith(seq, i):
                keys.append(name)
                i += len(seq)
                break
        else:
            i += 1      # ignore anything else
    return keys


def status_line(direction, speed, occ, limited, latch):
    arrow = {1: ">>> forward ", -1: "<<< backward", 0: "    stopped "}[direction]
    sensors = " ".join(f"{i}:{'#' if i in occ else '.'}" for i in range(len(zones.ZONES)))
    note = "  (limit)" if limited else ""
    mode = "latch" if latch else "hold"
    return f"\r[{mode}] {arrow} {speed:3d} %   sensors {sensors}{note}\x1b[K"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="\n".join(__doc__.splitlines()[2:]))
    p.add_argument("-s", "--speed", type=zones.speed_arg, default=DEFAULT_SPEED,
                   help=f"start speed in %% (default {DEFAULT_SPEED})")
    p.add_argument("--latch", action="store_true",
                   help="tap an arrow to start, space to stop (instead of hold-to-drive)")
    p.add_argument("--release", type=float, default=RELEASE,
                   help=f"hold mode: seconds without key repeat until stop (default {RELEASE})")
    p.add_argument("--sections", type=zones.position_arg, nargs="+",
                   help="positions whose motors are driven (default: all)")
    p.add_argument("--no-limits", action="store_true",
                   help="allow driving while the start/end sensor is blocked")
    zones.add_remote_arg(p)
    args = p.parse_args()
    if not sys.stdin.isatty():
        p.error("needs an interactive terminal")

    motors = [zones.ZONES[i][0] for i in (args.sections or range(len(zones.ZONES)))]
    speed, direction, last_key = args.speed, 0, 0.0
    fd = sys.stdin.fileno()
    old_tty = termios.tcgetattr(fd)

    with zones.open_conveyor(args) as mc:
        print(__doc__.split("\n\n")[1])    # key help
        print()
        try:
            tty.setcbreak(fd)
            applied, shown = None, None
            while True:
                if select.select([fd], [], [], 0.02)[0]:
                    for key in parse_keys(os.read(fd, 64)):
                        if key == "quit":
                            return
                        if key == "space":
                            direction = 0
                        elif key in ("right", "left"):
                            direction = 1 if key == "right" else -1
                            last_key = time.monotonic()
                        elif key == "up":
                            speed = min(MAX_SPEED, speed + SPEED_STEP)
                        elif key == "down":
                            speed = max(MIN_SPEED, speed - SPEED_STEP)

                if not args.latch and direction and time.monotonic() - last_key > args.release:
                    direction = 0       # key released

                occ = zones.occupied(mc)
                limited = not args.no_limits and (
                    (direction > 0 and zones.LAST in occ) or (direction < 0 and zones.FIRST in occ))
                cmd = 0 if limited else direction * speed
                if cmd != applied:
                    for m in motors:
                        mc.set_speed(m, cmd)
                    applied = cmd

                line = status_line(direction if not limited else 0, speed, occ, limited, args.latch)
                if line != shown:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    shown = line
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)   # first, even if stopping fails
            print()
            zones.stop(mc)   # if this fails, closing the connection / server watchdog stops the motors


if __name__ == "__main__":
    zones.run(main)
