#!/usr/bin/env python3
"""Example: basic use of the MultiControl library. Conveyor must be clear!"""
import logging
import time

from multicontrol import MultiControl

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

with MultiControl("config.yaml") as mc:          # connects, stops all motors on exit
    print("system:", mc.system_status())
    print("sensors (True = blocked):", mc.sensors())

    # single motor, both directions
    mc.forward("motor1", 30)
    time.sleep(2)
    print("motor1:", mc.motor_status("motor1"))
    mc.stop("motor1")
    time.sleep(0.5)
    mc.backward("motor1", 30)
    time.sleep(2)
    mc.stop("motor1")

    # ports work too
    mc.forward(2, 20)
    time.sleep(1)
    mc.stop(2)

    # run motor1 until sensor2 is blocked (max. 10 s)
    mc.forward("motor1", 40)
    if mc.wait_for_sensor("sensor2", blocked=True, timeout=10):
        print("sensor2 blocked - stopping")
    else:
        print("timeout - sensor2 never blocked")
    mc.stop_all()

    # live sensor display for 5 s
    t_end = time.time() + 5
    while time.time() < t_end:
        print("\r", mc.sensors(), end="   ", flush=True)
        time.sleep(0.1)
    print()
