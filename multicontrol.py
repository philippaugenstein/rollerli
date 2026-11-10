"""
multicontrol.py - small driver for an Interroll MultiControl (1st generation)
over EtherNet/IP, process image "Universal Full", control program "I/O Device".

A background thread cyclically writes the output assembly and reads the input
assembly. API calls only change the desired output state or read the cached
input state, so they return immediately and never block on the network.

    from multicontrol import MultiControl
    with MultiControl("config.yaml") as mc:
        mc.forward("motor1", 30)
        print(mc.sensors())

Motors and sensors can be addressed by their name from the config file
("motor1") or by their port number (1-4 for motors, 1-8 for sensors).
"""
from __future__ import annotations

import atexit
import logging
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

try:
    from pycomm3 import CIPDriver, Services
except ImportError:  # remote clients (ZeroMQ) only need the constants/dataclasses
    CIPDriver = Services = None

log = logging.getLogger("multicontrol")

# pycomm3 logs every generic_message() call at INFO ("Sending generic message",
# "Generic message ... completed"). With two calls per cycle this floods the
# terminal, so only let its warnings/errors through.
logging.getLogger("pycomm3").setLevel(logging.WARNING)

MIN_SPEED = 5      # device accepts 5..100 % (or 0 = stop)
MAX_SPEED = 100
N_MOTORS = 4
OUT_SIZE = 10


class MultiControlError(RuntimeError):
    """Raised when the connection is down or the device rejects a request."""


@dataclass
class MotorStatus:
    setpoint: int        # [%] speed setpoint reported back by the device
    current_ma: int      # [mA] motor current (coarse, has an offset near 0)
    error: bool          # motor error bit


@dataclass
class SystemStatus:
    motor_voltage_mv: int
    logic_voltage_mv: int
    temperature_c: float
    uptime_s: int


class MultiControl:
    def __init__(self, config: str | Path = "config.yaml"):
        cfg = yaml.safe_load(Path(config).read_text()) or {}
        conn = cfg.get("connection", {})
        self.ip: str = conn["ip"]
        self._out_inst = int(conn.get("output_instance", 100))
        self._in_inst = int(conn.get("input_instance", 101))
        self._cycle = float(conn.get("cycle_time", 0.05))
        self._max_errors = int(conn.get("max_errors", 5))
        self._cmd_timeout = (cfg.get("safety") or {}).get("command_timeout")

        # name -> (port, invert) / name -> (port, blocked_level)
        self._motors: dict[str, tuple[int, bool]] = {}
        for name, m in (cfg.get("motors") or {}).items():
            port = int(m["port"])
            if not 1 <= port <= N_MOTORS:
                raise ValueError(f"motor '{name}': port must be 1..4")
            self._motors[name] = (port, bool(m.get("invert", False)))
        self._sensors: dict[str, tuple[int, int]] = {}
        for name, s in (cfg.get("sensors") or {}).items():
            port = int(s["port"])
            if not 1 <= port <= 8:
                raise ValueError(f"sensor '{name}': port must be 1..8")
            self._sensors[name] = (port, int(s.get("blocked_level", 0)))

        self._lock = threading.Lock()
        self._speeds = [0] * N_MOTORS          # raw device values (after invert)
        self._io_out = 0
        self._in: bytes | None = None
        self._in_time = 0.0
        self._last_cmd = time.monotonic()
        self._fault: Exception | None = None
        self._drv: CIPDriver | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._first_cycle = threading.Event()

    # ------------------------------------------------------------ connection
    def connect(self, timeout: float = 3.0) -> "MultiControl":
        if self._thread and self._thread.is_alive():
            return self
        if CIPDriver is None:
            raise MultiControlError("pycomm3 is not installed (pip install pycomm3)")
        self._drv = CIPDriver(self.ip)
        self._drv.open()
        self._fault = None
        self._stop_evt.clear()
        self._first_cycle.clear()
        self._thread = threading.Thread(target=self._loop, name="multicontrol", daemon=True)
        self._thread.start()
        if not self._first_cycle.wait(timeout) or self._in is None:
            self.close()
            raise MultiControlError(f"no data from {self.ip}: {self._fault}")
        atexit.register(self.close)
        log.info("connected to %s", self.ip)
        return self

    def close(self) -> None:
        """Stop all motors and close the connection. Safe to call twice."""
        if self._thread is None:
            return
        self._stop_evt.set()
        self._thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self._speeds = [0] * N_MOTORS
            self._io_out = 0
        try:
            self._write(self._out_bytes())
        except Exception as e:  # noqa: BLE001
            log.error("could not send final stop: %s", e)
        try:
            self._drv.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("disconnected from %s", self.ip)

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    @property
    def data_age(self) -> float:
        """Seconds since the last successful read of the inputs."""
        return time.monotonic() - self._in_time

    # ---------------------------------------------------------------- motors
    def set_speed(self, motor: str | int, speed: int) -> None:
        """Signed speed in %: +5..+100 forward, -5..-100 backward, 0 stop."""
        speed = int(speed)
        if speed != 0 and not MIN_SPEED <= abs(speed) <= MAX_SPEED:
            raise ValueError(f"speed must be 0 or {MIN_SPEED}..{MAX_SPEED} % (got {speed})")
        port, invert = self._motor(motor)
        self._check()
        with self._lock:
            self._speeds[port - 1] = -speed if invert else speed
            self._last_cmd = time.monotonic()

    def forward(self, motor: str | int, speed: int) -> None:
        if speed < 0:
            raise ValueError("use backward() for negative directions")
        self.set_speed(motor, speed)

    def backward(self, motor: str | int, speed: int) -> None:
        if speed < 0:
            raise ValueError("speed must be positive; backward() sets the direction")
        self.set_speed(motor, -speed)

    def stop(self, motor: str | int) -> None:
        self.set_speed(motor, 0)

    def stop_all(self) -> None:
        with self._lock:
            self._speeds = [0] * N_MOTORS
            self._last_cmd = time.monotonic()

    def motor_status(self, motor: str | int) -> MotorStatus:
        port, invert = self._motor(motor)
        b = self._input()
        setpoint = struct.unpack_from("b", b, 3 + port - 1)[0]
        return MotorStatus(
            setpoint=-setpoint if invert else setpoint,
            current_ma=struct.unpack_from("<h", b, 8 + 2 * (port - 1))[0],
            error=bool(b[2] >> (port - 1) & 1),
        )

    @property
    def motors(self) -> list[str]:
        return list(self._motors)

    # --------------------------------------------------------------- sensors
    def sensor(self, sensor: str | int) -> bool:
        """True if the sensor is blocked (object present)."""
        port, blocked_level = self._sensor(sensor)
        return self.sensor_raw(port) == blocked_level

    def sensor_raw(self, port: int) -> int:
        """Raw bit (0/1) of sensor input 1..8 as delivered by the device."""
        return self._input()[0] >> (port - 1) & 1

    def sensors(self) -> dict[str, bool]:
        """All configured sensors: {name: blocked}."""
        b = self._input()[0]
        return {n: (b >> (p - 1) & 1) == lvl for n, (p, lvl) in self._sensors.items()}

    def wait_for_sensor(self, sensor: str | int, blocked: bool = True,
                        timeout: float | None = None) -> bool:
        """Wait until the sensor reaches the given state. False on timeout."""
        t_end = None if timeout is None else time.monotonic() + timeout
        while self.sensor(sensor) != blocked:
            if t_end is not None and time.monotonic() > t_end:
                return False
            time.sleep(self._cycle / 2)
        return True

    # ----------------------------------------------------------- digital I/O
    def io_input(self, n: int) -> bool:
        """State of I/O 1..4."""
        return bool(self._input()[1] >> (n - 1) & 1)

    def set_io_output(self, n: int, value: bool) -> None:
        """Set I/O 1..4 (only works if configured as "PLC-Output" on the device)."""
        if not 1 <= n <= 4:
            raise ValueError("I/O number must be 1..4")
        self._check()
        with self._lock:
            mask = 1 << (n - 1)
            self._io_out = (self._io_out | mask) if value else (self._io_out & ~mask)

    # ---------------------------------------------------------------- system
    def system_status(self) -> SystemStatus:
        b = self._input()
        mv_motor, mv_logic, temp = struct.unpack_from("<3h", b, 16)
        return SystemStatus(mv_motor, mv_logic, temp / 10, struct.unpack_from("<I", b, 22)[0])

    # -------------------------------------------------------------- internals
    def _motor(self, motor: str | int) -> tuple[int, bool]:
        if isinstance(motor, int):
            if not 1 <= motor <= N_MOTORS:
                raise ValueError("motor port must be 1..4")
            invert = next((inv for p, inv in self._motors.values() if p == motor), False)
            return motor, invert
        try:
            return self._motors[motor]
        except KeyError:
            raise KeyError(f"unknown motor '{motor}', configured: {list(self._motors)}") from None

    def _sensor(self, sensor: str | int) -> tuple[int, int]:
        if isinstance(sensor, int):
            if not 1 <= sensor <= 8:
                raise ValueError("sensor port must be 1..8")
            lvl = next((l for p, l in self._sensors.values() if p == sensor), 0)
            return sensor, lvl
        try:
            return self._sensors[sensor]
        except KeyError:
            raise KeyError(f"unknown sensor '{sensor}', configured: {list(self._sensors)}") from None

    def _check(self) -> None:
        if self._fault is not None:
            raise MultiControlError(f"connection fault: {self._fault}")
        if self._thread is None:
            raise MultiControlError("not connected - call connect() or use 'with'")

    def _input(self) -> bytes:
        self._check()
        with self._lock:
            return self._in

    def _out_bytes(self) -> bytes:
        with self._lock:
            return bytes([self._io_out] + [s & 0xFF for s in self._speeds]
                         + [0] * (OUT_SIZE - 1 - N_MOTORS))

    def _msg(self, service, instance: int, data: bytes = b""):
        r = self._drv.generic_message(
            service=service, class_code=0x04, instance=instance, attribute=3,
            request_data=data, connected=False, unconnected_send=False, route_path=False)
        if r.error:
            raise MultiControlError(r.error)
        return r.value

    def _write(self, data: bytes) -> None:
        self._msg(Services.set_attribute_single, self._out_inst, data)

    def _read(self) -> bytes:
        value = self._msg(Services.get_attribute_single, self._in_inst)
        if not value:
            raise MultiControlError("empty input data")
        return bytes(value)

    def _loop(self) -> None:
        errors = 0
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            if (self._cmd_timeout and any(self._speeds)
                    and t0 - self._last_cmd > self._cmd_timeout):
                log.warning("no motor command for %.1f s - stopping all motors", self._cmd_timeout)
                with self._lock:
                    self._speeds = [0] * N_MOTORS
            try:
                self._write(self._out_bytes())
                data = self._read()
                with self._lock:
                    self._in, self._in_time = data, time.monotonic()
                errors = 0
                self._first_cycle.set()
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.warning("cycle failed (%d/%d): %s", errors, self._max_errors, e)
                if errors >= self._max_errors:
                    self._fault = e
                    log.error("giving up - motors may keep their last speed!")
                    self._first_cycle.set()
                    return
            self._stop_evt.wait(max(0.0, self._cycle - (time.monotonic() - t0)))
