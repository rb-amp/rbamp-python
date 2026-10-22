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
pip install rbamp   # pulls smbus2 for the /dev/i2c-N path
```

MicroPython (mpremote + mip):
```bash
mpremote mip install github:rb-amp/rbamp-python
```

## Quick start (CPython)

Before you start: mains voltage can kill. Disconnect and lock out the
circuit before connecting the L/N terminals or fitting the clamp; have a
qualified person do mains wiring. Clamp **one** conductor only (L) — never
the whole cable. The CT sets the current range, so tell the module which
clamp you use (below, once per installation). Wait one full period (60 s
here) before the first `read_period_snapshot()`: a snapshot right after
`begin()` covers only a fraction of a second and Wh reads ~0.

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)   # once per installation
        dev.set_ct_model(3)                              # 3 = SCT-013-030
        print(f"variant = {dev.read_variant()}  label = {dev.read_label()!r}")
        print(f"U = {dev.voltage:.1f} V  P[0] = {dev.power[0]:.2f} W")
        time.sleep(60)                                   # one full period
        snap = dev.read_period_snapshot()
        print(f"Wh = {dev.energy.wh(0):.4f}")
```

On an STD module (check `dev.is_std`) skip the sensor setup and the period/energy path (BASIC only) and use `read_std_energy()`. The Wh counter is kept in memory and restarts from 0 after a script restart.

## Quick start (MicroPython on ESP32)

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass

# Use 50 kHz on ESP32 — the IDF i2c_master driver beneath machine.I2C can NACK
# intermittently at 100 kHz. NACK-retry is built into the MicroPython backend by default.
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)   # once per installation
    dev.set_ct_model(3)                              # 3 = SCT-013-030
    print(f"variant = {dev.read_variant()}  label = {dev.read_label()!r}")
    time.sleep(60)                                   # one full period
    snap = dev.read_period_snapshot()
    print(f"Wh = {dev.energy.wh(0):.4f}")
```

## Examples

The package ships 10 runnable examples for CPython and 10 for MicroPython:

| Example | CPython | MicroPython |
|---|---|---|
| quick read | 01_quick_read.py | 01_quick_read.py |
| period meter | 02_period_meter.py | 02_oled_period.py (OLED) |
| multi-module | 03_multi_module_broadcast.py | 03_multi_module.py |
| MQTT publisher | 04_mqtt_publisher.py | 04_mqtt.py |
| async streaming | no file | 05_async_streaming.py |
| deep sleep | not applicable | 06_deep_sleep.py |
| bidirectional energy | 05_bidirectional_energy.py | 07_bidirectional_energy.py |
| REST gateway | 06_rest_gateway.py | no file |
| home energy balance | 07_home_energy_balance.py | 08_home_energy_balance.py |
| rotating file logger | 08_rotating_file_logger.py | no file |
| event detection logger | no file | 09_event_detection_logger.py |
| Home Assistant MQTT autodiscovery | 09_ha_autodiscovery.py | 10_ha_autodiscovery.py |
| systemd service | 10_systemd_service.py | not applicable |

File names are the real ones; the numbering differs between the two folders.

See [`rbamp/examples_cpython/`](rbamp/examples_cpython/) and [`rbamp/examples_upy/`](rbamp/examples_upy/).

## Several modules: `RbAmpFleet`

One module or many: start with one `RbAmp`; use `RbAmpFleet` for several.

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

## Documentation

Full user documentation: https://www.rbamp.com/docs/modules-basic-standard-python-overview

## License

MIT — see [LICENSE](LICENSE).
