# 02 · Module Lines

![The BASIC and STANDARD (STD) module lines and what each provides.](images/module-lines.png)

## What a Module Line Is

**rbAmp** modules come in module lines: **BASIC**, **STANDARD (STD)** and
**PRO** (not implemented). A module line is a complete bundle of a
**hardware revision and firmware**, not a software flag. Moving between
module lines requires a physical SKU change,
not a firmware update.

From the package and user-code perspective: **one `RbAmp` class serves
every module line**, and the common readers (`voltage`, `current`, `power`,
`power_factor`, `frequency`) work on all of them. The module line is detected
automatically at `begin()`. STANDARD (STD) modules additionally expose the
STD-only calls (on-device energy and so on, see below); calling one of those
on a BASIC module raises `RbAmpNotSupportedError`.

## Current State

BASIC and STANDARD modules are supported by this library (STANDARD from 1.6.0).
PRO is not implemented.

| Module line | Shipping |
|---|---|
| **BASIC** | ✅ yes |
| **STANDARD** (STD) | ✅ yes (supported by this library from 1.6.0) |
| **PRO** | ❌ not implemented |

## BASIC — Entry-Level Consumption-Metering Module Line

### Hardware

A cost-optimized analog path suited to typical
household loads. The module includes an isolated analog front-end,
an on-board power regulator, and factory calibration in flash memory.

### Firmware Behavior

The logic of a classic mechanical disc meter — **the count only goes
forward; export to the grid is not subtracted**.

The three "layers" of active-power values behave differently:

| Signal | Meaning | Behavior on BASIC |
|---|---|---|
| `dev.power[ch]` / `dev.read_power(ch)` | instantaneous RT power, ~100 ms update | **signed** — a negative value is visible in real time (export) |
| `snap.avg_p[ch]` (from `dev.read_period_snapshot()`) | average power over the period | each 100 ms average-P window is **clamped** to `max(P, 0)` before being added to the period accumulator |
| `dev.energy.wh(ch)` | accumulated Wh by the package | **monotonic** — consumption only; export is not counted |

In other words: the user **sees** export in the RT reading, but it is
not present in the package's Wh counter on BASIC.

### Typical Applications

- Households without their own generation (no solar panels,
  wind turbines, battery systems)
- Submetering of purely consuming loads (water heaters, motors,
  home appliances)
- Building consumption monitoring where bidirectional metering is
  not required

### Bidirectional Metering on BASIC (Master-Side)

![BASIC counts consumption only on the module with export handled on the master, while STD keeps cumulative counters on the device.](images/energy-basic-vs-std.png)

If you need to track export to the grid separately on a BASIC module,
**do it on the master side**. The RT power `dev.power[0]` is
signed (or the equivalent method `dev.read_power(0)`), so:

```python
import time
from rbamp import RbAmp, RbAmpIOError

def bidirectional_loop(dev):
    consume_wh = 0.0
    export_wh  = 0.0
    t_prev = time.monotonic()

    while True:
        # Sample RT power at 10 Hz — matches the firmware-commit
        # cadence (100 ms per channel). Faster yields empty repeated
        # reads; slower loses resolution on sharp load transitions.
        time.sleep(0.1)

        t_now = time.monotonic()
        dt_s  = t_now - t_prev
        t_prev = t_now

        try:
            p = dev.read_power(0)
        except RbAmpIOError:
            continue

        dwh = p * dt_s / 3600.0
        if p >= 0.0:
            consume_wh += dwh
        else:
            export_wh  += -dwh
```

On MicroPython, replace `time.monotonic()` with `time.ticks_ms()` /
`time.ticks_diff()` (more accurate and cheap on RAM on embedded
platforms) and `time.sleep(0.1)` with `time.sleep_ms(100)`. On
CPython, no changes.

A complete working example of the pattern is in [06_examples.md](06_examples.md),
the "Master-side bidirectional metering" scenario, as well as the
source files [`examples_upy/07_bidirectional_energy.py`](https://github.com/rb-amp/rbamp-python)
and [`examples_cpython/05_bidirectional_energy.py`](https://github.com/rb-amp/rbamp-python).

## STANDARD (STD) — Shipping Module Line

STANDARD modules are a shipping module line that this library supports from
version 1.6.0. They use a different (16-bit) register map from BASIC; the
library hides the difference — the same `RbAmp` class, the same I²C wiring
and address, and the same common readers.

What the library adds on an STD module:

- **On-device energy**: `dev.read_std_energy()` returns the module's own
  life-time counters — active import / export / net and reactive energy, at
  Wh, mWh and µWh resolution, with per-channel figures.
  `RbAmp.std_energy_delta(prev, now)` gives the energy used between two
  readings and is safe across a power cut. You do not integrate power on
  the master for these counters.
- **Multi-phase helpers**: `dev.read_voltage_angle(phase)`,
  `dev.read_phase_sequence()`, `dev.read_reactive(ch)` (STD only; on BASIC, reactive power is not exposed by this library),
  `dev.read_apparent(ch)`.
- **Starting current**: `dev.get_start_current()` /
  `dev.set_start_current(µA)` + `dev.save_config()`.
- **Full blocks**: `dev.read_std_voltage()`, `read_std_channel()`,
  `read_std_totals()`, `read_std_config()`, `read_std_health()`,
  `dev.set_time()`.

Notes:

- Some STD calls need a recent enough module image (micro-unit energy
  counters, voltage angles, starting current). If the module does not
  provide one, the call raises `RbAmpNotSupportedError` ("not available").
- Use `dev.is_std` to branch in application code.
- The BASIC period-snapshot API and the master-side `dev.energy`
  accumulator are for BASIC modules only; on an STD module use
  `read_std_energy()`.
- On-device energy, angle and starting-current features are validated on
  **CPython (`smbus2`)**. The MicroPython 16-bit path is implemented but is
  **not yet hardware-validated** — prefer CPython for these features until a
  release note says otherwise.

Full signatures: [09 · API Reference](09_api_reference.md#std-module-line-160).

## PRO — Not Implemented

PRO is not implemented in this library. There is no PRO-specific API.

## How to Detect the Module Line at Runtime

![How to tell at runtime whether the connected module is a BASIC or a STANDARD (STD) module.](images/module-line-detect.png)

In the current firmware there is no explicit "module line" register. Indirect
indicators are available through handle properties:

```python
# Firmware version — opaque byte
fw = dev.firmware_version

# Presence of a voltage sensor (UI* variants vs I*-only)
voltage_hw = dev.has_voltage_hw

# Number of current channels (1 / 2 / 3 on BASIC; 5 / 7 on UI5 / UI7)
channels = dev.channels

# BASIC variant byte (1=UI1, 2=UI2, 3=UI3, 4=I1, 5=I2, 6=I3, 7=UI5, 8=UI7)
variant = dev.read_variant()

# Module line: True on an STD module (detected in begin())
is_std = dev.is_std
```

> **Note**: `dev.is_std` tells you the module line (STD vs BASIC). The other
> indicators (firmware version, channel count, presence of a voltage sensor,
> variant) describe the module but do not identify the module line. `dev.read_variant()` is
> the BASIC variant byte; use the SKU label on the module enclosure as the
> source of truth for the product.

## Where Module-Line-Dependent Places Are Marked in the Documentation

In the package text and canonical documentation, module-line-dependent
features are marked explicitly, for example:

> **STD only** — this call is not available on a BASIC module.

Or, conversely, BASIC-specific behavior:

> **BASIC**: the `dev.energy.wh(ch)` counter is monotonic — export to the grid
> is not counted. For bidirectional metering, see the
> "Master-side bidirectional metering" scenario in
> [06_examples.md](06_examples.md), or use the on-device energy of a
> STANDARD module (`dev.read_std_energy()`).

## What's Next

- [03 · Current Sensor Selection](03_sensor_selection.md) — which SCT-013
  for which task
- [04 · Wiring](04_hardware.md) — hardware specifics for each
  host (RPi / ESP32 / RP2040 / STM32 / Pyboard)
- [06 · Examples](06_examples.md) — scenarios for BASIC, including
  master-side bidirectional metering
- [09 · API Reference](09_api_reference.md) — the full public API
