# 02 · Module Tiers

## What a tier is

**rbAmp** ships in three tiers: **BASIC**, **STANDARD**, **PRO**.
A tier is a complete pairing of **hardware revision and firmware**,
not a software flag. Moving between tiers requires a physical SKU
change, not a firmware update.

From the perspective of the package and your code: **the `RbAmp`
class API is identical across all tiers**. The differences show up
in which values the module returns and how the firmware's behavior
interprets the data (in particular — export to the grid).

## Current firmware state (v1.2)

In firmware v1.2, **only the BASIC** tier is implemented and shipped.
STANDARD and PRO are on the roadmap and are not implemented in the
current firmware.

| Tier | Firmware | Shipping |
|---|---|---|
| **BASIC** | v1.2 | ✅ yes |
| **STANDARD** | planned in v1.3+ | ❌ no |
| **PRO** | planned in v1.3+ | ❌ no |

## BASIC — the entry-level consumer-metering tier

### Hardware

A cost-optimized analog signal path, suitable for typical household
loads. The module includes an isolated analog front-end, an
on-board power regulator, and a factory calibration stored in flash.

### Firmware behavior

The logic of a classic mechanical disc meter — **the count only
moves forward; export to the grid is not subtracted**.

The three "layers" of active-power values behave differently:

| Signal | Meaning | Behavior on BASIC |
|---|---|---|
| `dev.power[ch]` / `dev.read_power(ch)` | instantaneous RT power, updated ~200 ms | **signed** — a negative value is visible in real time (export) |
| `snap.avg_p[ch]` (from `dev.read_period_snapshot()`) | average power over the period | each 200 ms window of average P is **clamped** to `max(P, 0)` before being added to the period accumulator |
| `dev.energy.wh(ch)` | Wh accumulated by the package | **monotonic** — consumption only; export is not counted |

In other words: the user **sees** export in the RT reading, but on
BASIC the package's Wh counter does not include it.

### Typical applications

- Households without their own generation (no solar panels, wind
  turbines, or battery systems)
- Submetering of purely consuming loads (water heaters, motors,
  appliances)
- Building consumption monitoring where bidirectional metering isn't
  required

### Bidirectional metering on BASIC (master-side)

If you need to track export to the grid separately on a BASIC
module, **do it on the master side**. The RT power `dev.power[0]` is
signed (or the equivalent method `dev.read_power(0)`), so:

```python
import time
from rbamp import RbAmp, RbAmpIOError

def bidirectional_loop(dev):
    consume_wh = 0.0
    export_wh  = 0.0
    t_prev = time.monotonic()

    while True:
        # Sample RT power at 5 Hz — matches the firmware commit
        # cadence (200 ms per channel). Faster gives empty repeat
        # reads; slower loses resolution on abrupt load transitions.
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
`time.ticks_diff()` (more precise and cheaper on RAM on embedded
platforms) and `time.sleep(0.2)` with `time.sleep_ms(200)`. On
CPython, no changes are needed.

A full working example of the pattern is in [06_examples.md](06_examples.md),
the "Master-side bidirectional metering" scenario, as well as the
source files [`examples_upy/07_bidirectional_energy.py`](../rbamp/examples_upy/07_bidirectional_energy.py)
and [`examples_cpython/05_bidirectional_energy.py`](../rbamp/examples_cpython/05_bidirectional_energy.py).

## STANDARD — the bidirectional tier *(planned, v1.3+)*

> This section describes **planned** functionality. It is not
> implemented in the current firmware, v1.2.

### What will be added

- **Hardware**: an extended analog stack for accurate measurement of
  both consumption and reverse flow
- **Firmware**: bidirectional metering — two separate period
  accumulators (consumption and export), exposed separately through
  additional registers (details in the spec after the v1.3+ release)
- **Package API**: `dev.energy.wh(ch)` will begin returning a signed
  net value (consumption − export) automatically, without master-side
  tricks

### Typical applications (once available)

- Homes with rooftop solar generation
- Homes with wind turbines
- Storage systems (batteries)
- Regenerative loads
- V2G (vehicle-to-grid) EV charging

## PRO — the premium tier *(planned, v1.3+)*

> This section describes **planned** functionality. It is not
> implemented in the current firmware, v1.2.

### What PRO will add

- **Hardware**: a PRO-grade analog front-end (lower noise, tighter
  linearity), premium factory calibration, optional extended channel
  sets
- **Firmware**: bidirectional metering (like STANDARD) plus
  additional diagnostic features — details in the spec after the
  v1.3+ release
- **Package API**: extended diagnostic accessors (the exact set —
  after it's implemented in firmware)

### PRO applications (once available)

- Commercial tenant submetering
- Billing-grade accuracy installations
- Instrumentation labs
- Energy-intensive industrial loads

## How to determine the tier at runtime

In firmware v1.2 there is no explicit "tier" register. Indirect
indicators are available through handle properties:

```python
# Firmware version — on v1.2, == 0x03
fw = dev.firmware_version

# Presence of a voltage sensor (UI* variants vs. I*-only)
voltage_hw = dev.has_voltage_hw

# Number of current channels (1 / 2 / 3)
channels = dev.channels
```

> **Note (v1.2)**: the indirect indicators above give various
> signals (firmware version, channel count, presence of a voltage
> sensor), but **do not give the tier** — on v1.2 firmware the tier
> is implicitly always **BASIC**. Use the SKU label on the module's
> enclosure as the source of truth. An explicit tier register will
> be added in firmware v1.3+ when STANDARD starts shipping.

## Where tier-dependent items are flagged in the docs

Throughout the package text and the canonical documentation,
tier-dependent features are flagged explicitly, for example:

> **STANDARD / PRO only** — this register is not available on a
> BASIC module.

Or, conversely, BASIC-specific behavior:

> **BASIC**: the `dev.energy.wh(ch)` counter is monotonic — export
> to the grid is not counted. For bidirectional metering, see the
> "Master-side bidirectional metering" scenario in
> [06_examples.md](06_examples.md), or use a STANDARD/PRO module.

## What's next

- [03 · Current sensor selection](03_sensor_selection.md) — which
  SCT-013 for which job
- [04 · Hardware connection](04_hardware.md) — hardware specifics for
  each host (RPi / ESP32 / RP2040 / STM32 / Pyboard)
- [06 · Examples](06_examples.md) — scenarios for BASIC, including
  master-side bidirectional metering
- [09 · API Reference](09_api_reference.md) — the full public API


---

[← Overview](01_overview.md) | [Contents](README.md) | [Sensor Selection →](03_sensor_selection.md)
