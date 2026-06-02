# rbamp — Python client for rbAmp I²C AC sensor / dimmer modules

[![PyPI version](https://img.shields.io/pypi/v/rbamp.svg)](https://pypi.org/project/rbamp/)
[![protocol: 1.2](https://img.shields.io/badge/protocol-1.2-blue)](https://rbamp.com/docs/modules-basic-standard-api-reference)
[![runtimes: CPython · MicroPython](https://img.shields.io/badge/runtimes-CPython%20%C2%B7%20MicroPython-brightgreen)](#installation)
[![license: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

Unified Python client for the rbAmp I²C AC sensor / dimmer module — runs on
both **MicroPython** (ESP32 / RP2040 / STM32 / Pyboard via `machine.I2C`) and
**CPython** (Linux SBCs — Raspberry Pi / Orange Pi / Rock Pi via `smbus2`)
from a single source. Backend is auto-selected from the bus object you pass
to `RbAmp` — no platform flag, no import switch.

## Installation

### CPython (Linux SBC)
```bash
pip install rbamp
```

### MicroPython (ESP32 / RP2040 / STM32)
```bash
mpremote mip install github:rb-amp/rbamp-python
```

## Quick start

### CPython on Raspberry Pi
```python
from smbus2 import SMBus
from rbamp import RbAmp

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        print(dev.voltage, "V")
        snap = dev.read_period_snapshot()
        print(dev.energy.wh(0), "Wh")
```

### MicroPython on ESP32
```python
from machine import I2C, Pin
from rbamp import RbAmp

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    print(dev.voltage, "V")
    snap = dev.read_period_snapshot()
    print(dev.energy.wh(0), "Wh")
```

## Bus speed on ESP32

ESP32 platforms (MicroPython on ESP32 share the same low-level driver as
ESP-IDF) require **50 kHz** I²C bus speed for reliable operation. The
library applies automatic per-byte retry by default to absorb intermittent
NACK bursts. See examples for typical workloads.

## Examples

- `rbamp/examples_cpython/` — 10 CPython examples (smbus2 / MQTT / REST /
  rotating-file logger / Home Assistant autodiscovery / systemd service)
- `rbamp/examples_upy/` — 10 MicroPython examples (quick-read / OLED /
  multi-module / MQTT / async streaming / deep-sleep / bidirectional energy /
  HA autodiscovery)

## Documentation

- In-repo guide: [docs/](docs/README.md) — overview, hardware, quickstart, API reference, troubleshooting
- Hosted protocol spec & API reference: <https://rbamp.com/docs/modules-basic-standard-api-reference>

## License

MIT. See [LICENSE](LICENSE).
