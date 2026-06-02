# 05 · Quickstart

A five-minute hello-world: install the package, connect the module,
select a sensor, get your first RT reading and your first
per-period snapshot (Wh). This tutorial covers both runtimes in
parallel — **MicroPython** (on ESP32 / RP2040 / STM32 / Pyboard)
and **CPython** (on Raspberry Pi / Orange Pi / Rock Pi / x86 + USB-I²C).

The details (pinout for a specific host, a multi-module bus,
choosing an SCT-013 by range) live in the neighboring chapters:

- [04 · Hardware connection](04_hardware.md) — hardware specifics
  and per-host pin tables
- [03 · Current sensor selection](03_sensor_selection.md) — which
  SCT-013 model to choose and why

## What you'll need

- An rbAmp module (any tier; UI1 for simplicity)
- One of the hosts:
  - **CPython**: Raspberry Pi (any model) / Orange Pi 5 / Rock Pi 5
    / x86 + USB-I²C dongle
  - **MicroPython**: ESP32 (DevKitC / S2 / S3 / C3) / RP2040 (Pico /
    Pico W) / STM32 (Pyboard / Nucleo)
- An SCT-013 CT clamp rated for your maximum current (5A / 10A / 30A / 50A / 100A)
- A 5 V source to power the module (from the host's USB-5V or external)
- An AC circuit to measure (a lamp, a kettle, a household appliance)

## Step 1 — Installing the package

### CPython (on a Linux SBC or x86)

```sh
pip install rbamp smbus2
```

(On a Debian/Ubuntu system you can use the system
`sudo apt install python3-smbus` instead of `smbus2`, but `smbus2`
from pip is recommended — it supports the `with SMBus(...)` context
manager.)

Verify:

```sh
rbamp --version          # rbamp 1.1.0
rbamp --bus 1 scan       # I²C scan, should show 0x50
```

### MicroPython (on ESP32 and others)

```sh
mpremote mip install github:rb-amp/rbamp-python
```

Or copy the package manually:

```sh
mpremote cp -r path/to/rbamp/ :rbamp/
```

The package installs to `/lib/rbamp/` on the device.

## Step 2 — Connection

Four wires plus an optional DRDY:

| rbAmp pin | Host |
|---|---|
| `VCC` | +5 V (range 4.5..5.5 V) |
| `GND` | GND |
| `SDA` | I²C SDA (RPi pin 3, ESP32 GPIO21, RP2040 GPIO0/etc.) |
| `SCL` | I²C SCL (RPi pin 5, ESP32 GPIO22, RP2040 GPIO1/etc.) |

Power **must be 5 V**. The I²C lines run on 3.3 V logic but are
5 V-tolerant. The module board already has built-in 4.7 kΩ pull-up
resistors — no externals needed for a single module.

The full pinout table for each host is in
[04 · Hardware connection](04_hardware.md).

The SCT-013 CT clamp snaps around the **line conductor (L)**, with
the arrow on the clamp body pointing **in the direction of current
flow toward the load**. More detail in
[04 · Hardware connection](04_hardware.md).

## Step 3 — First script (RT reading)

A minimal script — a connectivity check with no sensor configuration.

### CPython version

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpError

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        # the context manager's __enter__ calls dev.begin() automatically
        print("Module ready.")
        while True:
            try:
                print(f"U={dev.voltage:.1f}V  "
                      f"I={dev.read_current(0):.3f}A  "
                      f"P={dev.read_power(0):.1f}W  "
                      f"PF={dev.read_power_factor(0):.3f}")
            except RbAmpError as e:
                print("read failed:", e)
            time.sleep(1)
```

Run:

```sh
python quick_read.py
```

### MicroPython version

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpError

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)   # ESP32 default
with RbAmp(i2c, 0x50) as dev:
    print("Module ready.")
    while True:
        try:
            print("U={:.1f}V  I={:.3f}A  P={:.1f}W  PF={:.3f}".format(
                dev.voltage, dev.read_current(0),
                dev.read_power(0), dev.read_power_factor(0)))
        except RbAmpError as e:
            print("read failed:", e)
        time.sleep(1)
```

(On MicroPython, `.format()` is used instead of f-strings — for
performance and compatibility with older ports; on newer ports
f-strings work too.)

Run:

```sh
mpremote run quick_read.py
```

Expected output (without sensor calibration):

```text
Module ready.
U=230.4V  I=0.000A  P=0.0W  PF=nan
U=230.4V  I=0.000A  P=0.0W  PF=nan
```

> `U` shows roughly the mains voltage (220-240 V for 230 V grids) —
> that means the module is wired correctly. `I=0.000 A` even with a
> load on is normal at this stage: the module doesn't yet know which
> CT clamp is used. `PF` at `I=0` is mathematically undefined
> (depends on the firmware — may be `nan`, `0`, or a placeholder) —
> the exact shape of the value doesn't matter while the current is
> zero. The next step fixes this.

## Step 4 — Current sensor configuration

On v1.2 firmware, the module **must** be told the sensor class and
model. Without it, the calibration coefficients aren't loaded and
the current readings stay at zero.

Add this at the top of the script after `with RbAmp(...) as dev:`.
**The read loop stays the same as in Step 3** — only the setup
changes:

```python
from rbamp import RbAmp, RbAmpSensorClass

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    # Step 1: sensor class. The current rbAmp SKU is SCT-013.
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)

    # Step 2: model. For example, SCT-013-030 for a household feed up to ~7 kW.
    dev.set_ct_model(3)

    print("Ready.")
    # ...same read loop as in Step 3...
```

Model codes:

| `code` | Model | Range | Typical use |
|:---:|---|---|---|
| 1 | SCT-013-005 | 0..5 A | Small consumers, a single outlet |
| 2 | SCT-013-010 | 0..10 A | Refrigerator, washing machine |
| 3 | SCT-013-030 | 0..30 A | Household feed up to ~7 kW |
| 4 | SCT-013-050 | 0..50 A | EV charger, electric heating |
| 5 | SCT-013-100 | 0..100 A | Main house feed |

More on the choice in [03 · Current sensor selection](03_sensor_selection.md).

> These two calls are made **once** at first install — the choice is
> stored in the module's flash and survives a reset. The total time
> is about **1.4 seconds** (two flash writes × ~700 ms each).
>
> On later runs of the script you don't have to call
> `set_sensor_class()` and `set_ct_model()` (the module remembers).
> But it's harmless either way — calling again with the same value
> rewrites the same byte.

After restarting the script, the correct current value should appear:

```text
Ready.
U=230.4V  I=0.523A  P=119.8W  PF=0.987
```

## Step 5 — Energy accounting (Wh)

The module returns only instantaneous quantities plus the average
power over a period. **The Wh are computed by the package itself**,
using the master clock (`time.monotonic()` on CPython,
`time.ticks_ms()` on MicroPython):

```text
E_Wh += avg_P × master_dt_s / 3600
        [W]    [seconds]      →  [Wh]
```

where `master_dt_s` is the seconds between two successful
`dev.read_period_snapshot()` calls.

A minimal periodic-accounting template (once a minute):

### CPython version (period meter)

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError, RbAmpError

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)   # SCT-013-030

    while True:
        time.sleep(60)   # 60-second period

        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            print("snapshot stale — skip")
            continue
        except RbAmpError as e:
            print("snapshot failed:", e)
            continue

        print(f"avg P over period: {snap.avg_p[0]:.2f} W   "
              f"accumulated: {dev.energy.wh(0):.4f} Wh   "
              f"dt={snap.master_dt_ms} ms")
```

### MicroPython version (period meter)

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError, RbAmpError

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)

    while True:
        time.sleep_ms(60_000)

        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            print("snapshot stale — skip")
            continue
        except RbAmpError as e:
            print("snapshot failed:", e)
            continue

        print("avg P over period: {:.2f} W   accumulated: {:.4f} Wh   dt={} ms".format(
            snap.avg_p[0], dev.energy.wh(0), snap.master_dt_ms))
```

> After Step 4, the `set_sensor_class()` and `set_ct_model()` calls
> have already run once and been saved to the module's flash; in the
> template above they're there for re-runs of the script — the
> module ignores a repeat call with the same value.

What `read_period_snapshot()` does under the hood:

1. Sends the module a period-latch command (`CMD_LATCH_PERIOD`).
2. Waits 50 ms while the module prepares the snapshot.
3. Checks the ready flag (`snap.valid`); reads the average/peak power.
4. Updates the internal Wh counter: `dev.energy.wh(ch) += avg_p[ch] × dt / 3600`.
5. On a stale snapshot it raises `RbAmpStaleError` — the package
   still records the master timestamp so the next snapshot doesn't
   double-count the `dt`.

The first call after `dev.begin()` is a primer: the module returns
what it accumulated since power-on (an interval unsuitable for tariff
accounting). The package figures out on its own that this snapshot
should be discarded — user code never sees it.

Expected output:

```text
avg P over period: 120.18 W   accumulated: 2.0036 Wh   dt=60012 ms
avg P over period: 120.21 W   accumulated: 4.0073 Wh   dt=60005 ms
...
```

## Async-streaming variant (optional)

For applications with asyncio (CPython) or uasyncio (MicroPython),
there's the async generator `dev.stream_period(interval_s=...)`:

```python
import asyncio
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

async def main():
    with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        dev.set_ct_model(3)

        # Once a minute, with built-in stale handling (skip_stale=True)
        async for snap in dev.stream_period(interval_s=60.0):
            print(f"{snap.avg_p[0]:.2f} W,  {dev.energy.wh(0):.4f} Wh")

asyncio.run(main())
```

It works the same way on MicroPython (`import uasyncio as asyncio`).

More on async scenarios in [06 · Examples](06_examples.md), the
"Async period streaming" scenario.

## What's next

- [01 · Overview](01_overview.md) — what rbAmp is and what the
  package does
- [02 · Module tiers](02_tiers.md) — which tier for which task
- [06 · Examples](06_examples.md) — working scenarios: local
  display, MQTT, deep-sleep (MicroPython), async-streaming, a
  multi-module bus, event logging
- [07 · DIY integrations](07_diy_integrations.md) — Home Assistant /
  Node-RED / OpenHAB
- [08 · Cloud integrations](08_cloud_integrations.md) — AWS IoT /
  Azure / GCP / InfluxDB
- [09 · API reference](09_api_reference.md) — the full public API
- [10 · Troubleshooting](10_troubleshooting.md) — if something
  doesn't work


---

[← Hardware Setup](04_hardware.md) | [Contents](README.md) | [Examples →](06_examples.md)
