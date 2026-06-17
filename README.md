# rbamp-python

[![PyPI](https://img.shields.io/pypi/v/rbamp)](https://pypi.org/project/rbamp/)
[![Python](https://img.shields.io/pypi/pyversions/rbamp)](https://pypi.org/project/rbamp/)
[![MicroPython](https://img.shields.io/badge/MicroPython-1.20%2B-blue)](https://docs.micropython.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

Cross-backend Python client for the **rbAmp** I²C AC energy-monitor module.
One package, two backends — runs on **CPython** (Linux SBC via `smbus2`) and
**MicroPython** (ESP32 / RP2040 / STM32 via `machine.I2C`). The backend is selected
automatically based on the bus object you pass to `RbAmp`.

## Install

CPython (PyPI):
```bash
pip install rbamp[bench]   # the bench extra pulls smbus2 for the /dev/i2c-N path
```

MicroPython (mpremote + mip):
```bash
mpremote mip install github:rb-amp/rbamp-python
```

## Quick start (CPython)

```python
from smbus2 import SMBus
from rbamp import RbAmp

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        print(f"variant = {dev.read_variant()}  label = {dev.read_label()!r}")
        print(f"U = {dev.voltage:.1f} V  P[0] = {dev.power[0]:.2f} W")
        snap = dev.read_period_snapshot()
        print(f"Wh = {dev.energy.wh(0):.4f}")
```

## Quick start (MicroPython on ESP32)

```python
from machine import I2C, Pin
from rbamp import RbAmp

# Use 50 kHz on ESP32 — the IDF i2c_master driver beneath machine.I2C can NACK
# intermittently at 100 kHz. NACK-retry is built into the MicroPython backend by default.
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    print(f"variant = {dev.read_variant()}  label = {dev.read_label()!r}")
    snap = dev.read_period_snapshot()
    print(f"Wh = {dev.energy.wh(0):.4f}")
```

## Multi-module fleet (host-side manager)

```python
from rbamp import RbAmpFleet

fleet = RbAmpFleet(bus)
fleet.scan()                          # adopt every rbAmp on the bus
fleet.enable_gc_all(group=0)          # opt every module into GC latch

tick = fleet.gc_latch(group=0)        # broadcast 5-byte GC frame
sync = fleet.check_sync(expected_tick=tick)
for s in sync:
    if not s.in_sync:
        print(f"  WARN: 0x{s.addr:02X} dropped")

print(f"total: {fleet.total_power():.1f} W, {fleet.total_energy_wh():.3f} Wh")
```

## Examples

The package ships 20 runnable examples — 10 each for CPython and MicroPython:

| #  | CPython                             | MicroPython                          |
|----|-------------------------------------|--------------------------------------|
| 01 | quick read                          | quick read                           |
| 02 | period meter                        | OLED period meter                    |
| 03 | multi-module broadcast              | multi-module                         |
| 04 | MQTT publisher                      | MQTT                                 |
| 05 | bidirectional energy                | async streaming                      |
| 06 | REST gateway                        | deep sleep                           |
| 07 | home energy balance                 | bidirectional energy                 |
| 08 | rotating file logger                | home energy balance                  |
| 09 | Home Assistant MQTT autodiscovery   | event detection logger               |
| 10 | systemd service                     | Home Assistant MQTT autodiscovery    |

See [`rbamp/examples_cpython/`](rbamp/examples_cpython/) and [`rbamp/examples_upy/`](rbamp/examples_upy/).

## Documentation

Full user documentation: https://www.rbamp.com/docs/modules-basic-standard-python-overview

## License

MIT — see [LICENSE](LICENSE).
