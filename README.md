# rollerli

Control of an Interroll MultiControl roller conveyor (4 sections) over EtherNet/IP.

| File | Purpose |
|---|---|
| `multicontrol.py` | driver library (talks to the device) |
| `zones.py` | section-wise moving / shaking used by the scripts |
| `shuttle_in.py`, `shuttle_out.py` | move the box forward / backward to a position (`-s` speed, `-g` goal 0-3) |
| `shake_zone.py` | shake the box where it is |
| `loop.py` | N x (in, wait, back to middle, shake), then out |
| `manual.py` | drive with the arrow keys (`-s` speed, up/down changes it live, `--latch` for tap-to-start) |
| `multicontrol_server.py`, `multicontrol_client.py` | ZeroMQ bridge so scripts can run on another device |

## Install (Raspberry Pi OS)

```sh
python3 -m venv ~/rollerli-venv
~/rollerli-venv/bin/pip install pycomm3 pyyaml pyzmq
source ~/rollerli-venv/bin/activate     # in every new terminal
```

A device that only runs scripts with `--remote` needs `pyyaml pyzmq` (no pycomm3).

## Running directly on the Pi

```sh
./shuttle_in.py -s 50
```

## Running through ZeroMQ

1. Start the server on the Pi (it owns the connection to the MultiControl):

   ```sh
   ./multicontrol_server.py
   ```

2. Run any script with `--remote`:

   ```sh
   ./loop.py --remote                            # address from config.yaml (remote.connect)
   ./loop.py --remote tcp://192.168.0.10:5555    # explicit address
   ```

By default (`config.yaml` → `remote`) the server only listens on `127.0.0.1`, i.e. the Pi
talks to itself. For another device set `bind: "tcp://*:5555"` on the Pi and
`connect: "tcp://<pi-ip>:5555"` on the other device. There is **no authentication** -
only do this on a trusted network.

If the server hears nothing from any client for `remote.watchdog` seconds while motors
run (script crashed, network gone), it stops all motors.

In your own code, `RemoteMultiControl` is a drop-in replacement for `MultiControl`:

```python
from multicontrol_client import RemoteMultiControl
with RemoteMultiControl("tcp://192.168.0.10:5555") as mc:
    mc.forward("motor1", 30)
    print(mc.sensors())
```
