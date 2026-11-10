#!/usr/bin/env python3
"""multicontrol_server.py - ZeroMQ bridge to the MultiControl. Runs on the Pi.

Owns the EtherNet/IP connection and executes requests from clients
(multicontrol_client.RemoteMultiControl, or the scripts started with --remote).

    ./multicontrol_server.py                       # bind address from config.yaml
    ./multicontrol_server.py --bind tcp://*:5555   # reachable from other devices

Protocol: REQ/REP, JSON. Request {"call": name, "args": [...]}, reply
{"ok": result} or {"error": exception class name, "msg": text}.

Safety: if no client message arrives for `watchdog` seconds while motors run
(client crashed, network gone), all motors are stopped. Clients send a
heartbeat in the background, so a living client never trips it.
"""
import argparse
import dataclasses
import logging
import time

import yaml
import zmq

from multicontrol import MultiControl

log = logging.getLogger("multicontrol.server")

# calls a client may make: plain methods and read-only properties
METHODS = {"set_speed", "forward", "backward", "stop", "stop_all", "sensor",
           "sensor_raw", "sensors", "motor_status", "system_status", "io_input",
           "set_io_output"}
PROPERTIES = {"motors", "data_age"}
STOPPING = {"stop", "stop_all"}


def handle(mc: MultiControl, req: dict, running: set):
    call, args = req.get("call"), req.get("args", [])
    if call == "ping":
        return "pong"
    if call in PROPERTIES:
        return getattr(mc, call)
    if call not in METHODS:
        raise ValueError(f"unknown call '{call}'")
    result = getattr(mc, call)(*args)
    # track which motors are running (for the watchdog log message)
    if call == "stop_all":
        running.clear()
    elif call in ("set_speed", "forward", "backward", "stop"):
        motor = args[0]
        speed = 0 if call == "stop" else args[1]
        (running.add if speed else running.discard)(motor)
    if dataclasses.is_dataclass(result):
        result = dataclasses.asdict(result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-b", "--bind", help="ZeroMQ address to listen on (default: config.yaml remote.bind)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    remote = (yaml.safe_load(open(args.config)) or {}).get("remote") or {}
    bind = args.bind or remote.get("bind", "tcp://127.0.0.1:5555")
    watchdog = float(remote.get("watchdog", 1.0))

    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(bind)
    log.info("listening on %s (watchdog %.1f s)", bind, watchdog)

    running: set = set()
    last_msg = time.monotonic()
    try:
        with MultiControl(args.config) as mc:
            while True:
                if sock.poll(50):
                    req = sock.recv_json()
                    last_msg = time.monotonic()
                    try:
                        reply = {"ok": handle(mc, req, running)}
                    except Exception as e:  # noqa: BLE001 - every error goes back to the client
                        reply = {"error": type(e).__name__, "msg": str(e)}
                        if req.get("call") not in STOPPING:
                            log.warning("%s%s -> %s: %s", req.get("call"),
                                        tuple(req.get("args", [])), type(e).__name__, e)
                    sock.send_json(reply)
                elif running and time.monotonic() - last_msg > watchdog:
                    log.warning("no client message for %.1f s - stopping motors %s",
                                watchdog, sorted(map(str, running)))
                    mc.stop_all()
                    running.clear()
    except KeyboardInterrupt:
        log.info("stopped by user")
    finally:
        sock.close()
        ctx.term()


if __name__ == "__main__":
    main()
