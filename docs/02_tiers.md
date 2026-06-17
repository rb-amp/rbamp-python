# 02 · Module Tiers

![rbAmp product tiers: BASIC / STANDARD / PRO capability ladder](images/tier-ladder.png)

## What a Tier Is

**rbAmp** ships in three tiers: **BASIC**, **STANDARD**, **PRO**.
A tier is a complete bundle of a **hardware revision and firmware**, not a
software flag. Moving between tiers requires a physical SKU change,
not a firmware update.

From the package and user-code perspective: **the `RbAmp` class API is
identical across all tiers**. The differences show up in which
values the module returns and in how the firmware behavior interprets the data
(export to the grid in particular).

## Current State

In the current rbAmp firmware, **only the BASIC** tier is implemented and shipped.
STANDARD and PRO are on the roadmap and not implemented.

| Tier | Shipping |
|---|---|
| **BASIC** | ✅ yes |
| **STANDARD** | ❌ planned |
| **PRO** | ❌ planned |

## BASIC — Entry-Level Consumption-Metering Tier

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
| `dev.power[ch]` / `dev.read_power(ch)` | instantaneous RT power, ~200 ms update | **signed** — a negative value is visible in real time (export) |
| `snap.avg_p[ch]` (from `dev.read_period_snapshot()`) | average power over the period | each 200 ms average-P window is **clamped** to `max(P, 0)` before being added to the period accumulator |
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
        # Sample RT power at 5 Hz — matches the firmware-commit
        # cadence (200 ms per channel). Faster yields empty repeated
        # reads; slower loses resolution on sharp load transitions.
        # ±2 % accuracy is achievable for typical mixed loads with
        # an inverter.
        time.sleep(0.2)

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
platforms) and `time.sleep(0.2)` with `time.sleep_ms(200)`. On
CPython, no changes.

A complete working example of the pattern is in [06_examples.md](06_examples.md),
the "Master-side bidirectional metering" scenario, as well as the
source files [`examples_upy/07_bidirectional_energy.py`](https://github.com/rb-amp/rbamp-python)
and [`examples_cpython/05_bidirectional_energy.py`](https://github.com/rb-amp/rbamp-python).

## STANDARD — Bidirectional Tier *(planned)*

> This section describes **planned** functionality. It is not
> implemented in the current rbAmp firmware.

### What Will Be Added

- **Hardware**: an extended analog stack for accurate
  measurement of both consumption and reverse flow
- **Firmware**: bidirectional accounting — two separate per-period
  accumulators (consumption and export), with separate exposure through
  additional registers (details after the STANDARD tier is released)
- **Package API**: `dev.energy.wh(ch)` will begin returning a signed
  net value (consumption − export) automatically, with no
  master-side tricks

### Typical Applications (Once Released)

- Homes with rooftop solar generation
- Homes with wind turbines
- Storage systems (batteries)
- Regenerative loads
- V2G (vehicle-to-grid) electric-vehicle charging

## PRO — Premium Tier *(planned)*

> This section describes **planned** functionality. It is not
> implemented in the current rbAmp firmware.

### What PRO Will Add

- **Hardware**: a PRO-grade analog front-end (lower
  noise, tighter linearity), premium factory calibration,
  optional extended channel sets
- **Firmware**: bidirectional metering (as in STANDARD) plus
  additional diagnostic features — details after the PRO tier
  is released
- **Package API**: extended diagnostic accessors
  (exact set after the firmware implementation)

### PRO Applications (Once Released)

- Submetering of commercial tenants
- Billing-grade accuracy installations
- Test-and-measurement laboratories
- Energy-intensive industrial loads

## How to Detect the Tier at Runtime

In the current firmware there is no explicit "tier" register. Indirect
indicators are available through handle properties:

```python
# Firmware version — opaque byte
fw = dev.firmware_version

# Presence of a voltage sensor (UI* variants vs I*-only)
voltage_hw = dev.has_voltage_hw

# Number of current channels (1 / 2 / 3)
channels = dev.channels

# Full SKU — variant byte from REG_HW_VARIANT (0x55)
# 1=UI1, 2=UI2, 3=UI3 (roadmap), 4=I1, 5=I2, 6=I3
variant = dev.hw_variant
```

> **Note**: the indirect indicators above give different signals (the
> firmware version, channel count, presence of a voltage sensor, SKU), but
> they do **NOT** give the tier — the current firmware is implicitly always
> **BASIC**. Use the SKU label on the module enclosure as the source of truth
> for the tier. An explicit tier register will appear once STANDARD starts
> shipping.

## Where Tier-Dependent Places Are Marked in the Documentation

In the package text and canonical documentation, tier-dependent
features are marked explicitly, for example:

> **STANDARD / PRO only** — this register is not available on a BASIC module.

Or, conversely, BASIC-specific behavior:

> **BASIC**: the `dev.energy.wh(ch)` counter is monotonic — export to the grid
> is not counted. For bidirectional metering, see the
> "Master-side bidirectional metering" scenario in
> [06_examples.md](06_examples.md), or use a STANDARD/PRO
> module.

## What's Next

- [03 · Current Sensor Selection](03_sensor_selection.md) — which SCT-013
  for which task
- [04 · Wiring](04_hardware.md) — hardware specifics for each
  host (RPi / ESP32 / RP2040 / STM32 / Pyboard)
- [06 · Examples](06_examples.md) — scenarios for BASIC, including
  master-side bidirectional metering
- [09 · API Reference](09_api_reference.md) — the full public API
