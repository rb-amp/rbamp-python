# rbamp — Python client library for the rbAmp module

A cross-backend Python client for the rbAmp I²C sensor / dimmer module.
One source runs on both **MicroPython** (via `machine.I2C` on ESP32,
RP2040, STM32, Pyboard) and **CPython** (via `smbus2` on Linux SBCs —
Raspberry Pi, Orange Pi, Rock Pi, etc.). The backend is selected
automatically from the type of the bus object passed to `RbAmp` — no
platform flags, no import switches.

## Install

### CPython (Linux SBC)

```sh
pip install rbamp smbus2
```

On Raspberry Pi OS / Debian / Ubuntu you may also need `i2c-tools` and
membership in the `i2c` group; see the docs linked below for host setup.

### MicroPython

```sh
mpremote mip install github:rb-amp/rbamp-python
```

Or copy the package folder manually:

```sh
mpremote cp -r rbamp/ :rbamp/
```

## Quick start

Mains voltage can kill. Disconnect and lock out the circuit before
connecting the L/N terminals or fitting the clamp; have a qualified person
do mains wiring. Clamp **one** conductor only (L). On an STD module (check
`dev.is_std`) skip the sensor setup and period/Wh path and use
`read_std_energy()`. The Wh counter is kept in memory and restarts from 0
after a script restart.

CPython (Raspberry Pi):

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)   # once per installation
        dev.set_ct_model(3)                              # 3 = SCT-013-030
        print(dev.voltage, "V")
        print(dev.power[0], "W")
        time.sleep(60)                                   # wait one full period
        snap = dev.read_period_snapshot()
        print(dev.energy.wh(0), "Wh")
```

MicroPython (ESP32, 50 kHz per platform guidance):

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)   # once per installation
    dev.set_ct_model(3)                              # 3 = SCT-013-030
    print(dev.voltage, "V")
    time.sleep(60)                                   # wait one full period
    snap = dev.read_period_snapshot()
    print(dev.energy.wh(0), "Wh")
```

## What you get

- **One `RbAmp` class** with automatic backend detection.
- **Fleet API** (`RbAmpFleet`) for polling multiple modules on one bus
  in a single call, including balance across a mains-plus-loads
  topology.
- **Per-channel sensor configuration** via `dev.set_ct_model_ch(channel,
  code)` and `dev.read_ct_model_ch(channel)` — works on all channels
  of the senior SKUs (UI5, UI7).
- **On-device energy metering** for modules that keep their own
  life-time counters: `dev.read_std_energy()` returns active import /
  export / net and reactive energy, at Wh, mWh and µWh / µvarh
  resolution, plus per-channel figures; `RbAmp.std_energy_delta(prev,
  now)` gives the energy used between two readings (power-cut-safe).
- **Multi-phase helpers** where the module provides them:
  `dev.read_voltage_angle(phase)` (degrees between voltage inputs),
  `dev.read_phase_sequence()` (forward / reverse / abnormal),
  `dev.read_reactive(ch)` / `dev.read_apparent(ch)`.
- **Energy starting current** — `dev.get_start_current()` /
  `dev.set_start_current(µA)`: below this per-channel current the module
  counts no energy (readings are unaffected), so no-load noise never
  creeps into the counters.
- **Per-channel Wh accumulator** updated automatically on each
  successful period snapshot.
- **Pythonic API** — properties (`dev.voltage`, `dev.power[0]`),
  method-based period snapshots, `async for` streaming through
  `asyncio` / `uasyncio`, and an `RbAmpError` exception hierarchy
  instead of return codes.
- **CLI tool** (CPython only): `rbamp scan`, `rbamp read`,
  `rbamp period`, `rbamp info`, `rbamp address`.

## Documentation

Full documentation (installation, hardware setup, walkthroughs for
Home Assistant / Node-RED / OpenHAB / cloud, API reference, and
troubleshooting) lives on the project site — see the repository's
top-level README for the current URL.

## Compatibility

- **Public API is additive**: releases are backward-compatible unless
  noted in the changelog.
- **Supported hosts**: CPython on any Linux SBC with a `/dev/i2c-N`
  bus; MicroPython on ESP32, RP2040, STM32, and Pyboard ports.
  CircuitPython is not supported directly (its `busio.I2C` is a
  different type — wrap it with an adapter or use the raw protocol).
- **On-device energy / angle / starting-current features** are
  detected automatically and are validated on **CPython (`smbus2`)**.
  The same calls work over MicroPython `machine.I2C` but that path is
  not yet hardware-validated — prefer CPython for these on-device
  features until a release note says otherwise. The common readings
  (`dev.voltage`, `dev.current[ch]`, `dev.power[ch]`, period snapshots)
  work on every supported host regardless.

## License

MIT — see the `LICENSE` file in the top-level repository.
