"""multicontrol_client.py - use a MultiControl over ZeroMQ (see multicontrol_server.py).

RemoteMultiControl has the same API as multicontrol.MultiControl, so code
written for one works with the other:

    from multicontrol_client import RemoteMultiControl
    with RemoteMultiControl("tcp://192.168.0.10:5555") as mc:
        mc.forward("motor1", 30)
        print(mc.sensors())

Needs pyzmq and pyyaml, but not pycomm3. Leaving the `with` block (or the
process dying) stops all motors - the latter via the server watchdog.
"""
from __future__ import annotations

import threading
import time

import zmq

from multicontrol import MotorStatus, MultiControlError, SystemStatus

_ERRORS = {"ValueError": ValueError, "KeyError": KeyError, "TypeError": TypeError}


class RemoteMultiControl:
    def __init__(self, address: str = "tcp://127.0.0.1:5555", timeout: float = 2.0,
                 heartbeat: float = 0.25):
        self.address = address
        self._timeout_ms = int(timeout * 1000)
        self._heartbeat = heartbeat
        self._lock = threading.Lock()
        self._ctx: zmq.Context | None = None
        self._sock = None
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None
        self._lost = False      # set after an unanswered request: fail fast from then on

    # ------------------------------------------------------------ connection
    def connect(self) -> "RemoteMultiControl":
        if self._sock is not None:
            return self
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.REQ)
        self._sock.setsockopt(zmq.LINGER, 0)
        # allow a new request after a timed-out one and drop late replies
        self._sock.setsockopt(zmq.REQ_RELAXED, 1)
        self._sock.setsockopt(zmq.REQ_CORRELATE, 1)
        self._sock.connect(self.address)
        try:
            self._call("ping")
        except MultiControlError:
            self._close_socket()
            raise MultiControlError(f"no answer from server at {self.address} "
                                    "- is multicontrol_server.py running?") from None
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop,
                                           name="multicontrol-heartbeat", daemon=True)
        self._hb_thread.start()
        return self

    def close(self) -> None:
        """Stop all motors and disconnect. Safe to call twice."""
        if self._sock is None:
            return
        self._hb_stop.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=1)
        try:
            self.stop_all()
        except Exception:  # noqa: BLE001 - server watchdog stops the motors anyway
            pass
        self._close_socket()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- motors
    def set_speed(self, motor: str | int, speed: int) -> None:
        self._call("set_speed", motor, int(speed))

    def forward(self, motor: str | int, speed: int) -> None:
        self._call("forward", motor, int(speed))

    def backward(self, motor: str | int, speed: int) -> None:
        self._call("backward", motor, int(speed))

    def stop(self, motor: str | int) -> None:
        self._call("stop", motor)

    def stop_all(self) -> None:
        self._call("stop_all")

    def motor_status(self, motor: str | int) -> MotorStatus:
        return MotorStatus(**self._call("motor_status", motor))

    @property
    def motors(self) -> list[str]:
        return self._call("motors")

    # --------------------------------------------------------------- sensors
    def sensor(self, sensor: str | int) -> bool:
        return self._call("sensor", sensor)

    def sensor_raw(self, port: int) -> int:
        return self._call("sensor_raw", port)

    def sensors(self) -> dict[str, bool]:
        return self._call("sensors")

    def wait_for_sensor(self, sensor: str | int, blocked: bool = True,
                        timeout: float | None = None) -> bool:
        t_end = None if timeout is None else time.monotonic() + timeout
        while self.sensor(sensor) != blocked:
            if t_end is not None and time.monotonic() > t_end:
                return False
            time.sleep(0.025)
        return True

    # ----------------------------------------------------------- digital I/O
    def io_input(self, n: int) -> bool:
        return self._call("io_input", n)

    def set_io_output(self, n: int, value: bool) -> None:
        self._call("set_io_output", n, bool(value))

    # ---------------------------------------------------------------- system
    def system_status(self) -> SystemStatus:
        return SystemStatus(**self._call("system_status"))

    @property
    def data_age(self) -> float:
        return self._call("data_age")

    # -------------------------------------------------------------- internals
    def _call(self, call: str, *args):
        if self._sock is None:
            raise MultiControlError("not connected")
        if self._lost:
            raise MultiControlError(f"connection to server at {self.address} lost")
        with self._lock:
            self._sock.send_json({"call": call, "args": list(args)})
            if not self._sock.poll(self._timeout_ms):
                self._lost = True
                raise MultiControlError(f"server at {self.address} did not answer '{call}'")
            reply = self._sock.recv_json()
        if "error" in reply:
            raise _ERRORS.get(reply["error"], MultiControlError)(reply["msg"])
        return reply["ok"]

    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(self._heartbeat):
            try:
                self._call("ping")
            except Exception:  # noqa: BLE001 - the main thread will see the error itself
                pass

    def _close_socket(self) -> None:
        self._sock.close()
        self._ctx.term()
        self._sock = self._ctx = None
        self._lost = False
