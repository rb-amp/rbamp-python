# 03 · Current sensor selection

This chapter answers two questions:

1. Which current sensor to choose for a given load.
2. How to tell the rbAmp module about your choice, so that the
   factory calibration for that combination loads automatically.

The physical sensor connection (clamp orientation, L/N polarity
check) is covered in [04_hardware.md](04_hardware.md). This chapter
is about **model selection** and the **API calls**.

## Sensor class

rbAmp modules work with CT clamps of the **SCT-013** family. The
sensor class is determined by the module's hardware revision and is
set at the factory — the user reports their choice via
`dev.set_sensor_class()` before selecting a specific CT-clamp
model.

## Choosing the SCT-013 model

The SCT-013 family has five models, differing in the maximum
primary-circuit current:

| `code` | Model | Current range | Typical use |
|:---:|---|---|---|
| **1** | SCT-013-005 | 0…5 A | Small loads — lamps, low-power electronics, a single switch |
| **2** | SCT-013-010 | 0…10 A | One medium-power appliance — refrigerator, washing machine, AC unit up to 2 kW |
| **3** | SCT-013-030 | 0…30 A | A mid-size household service entrance — up to ~7 kW |
| **4** | SCT-013-050 | 0…50 A | A large service entrance — electric heating, EV charger, a house with peak loads |
| **5** | SCT-013-100 | 0…100 A | A house or small-office mains feed — up to ~23 kW |

### How to pick the right model

The basic rule:

1. **Determine the maximum current** that can flow in the circuit
   (the largest load + 30% headroom).
2. Choose the model whose range that value fits into.
3. **Don't over-size by more than 5×.** An SCT-013-100 clamp on a
   circuit with a 5 A maximum will work, but it gives low
   resolution and high error at typical values.

### Headroom

An SCT-013 clamp operates without saturation within its rated
range. Brief peaks (compressor inrush, an inductive load) may
exceed the rating by 5–7× — this is **normal**; the clamp
physically withstands it, but the measurement becomes nonlinear
above the rating.

If your load has a peak current above the clamp's rating, choose
the next size up. For example, for a washing machine with a 12 A
inrush current (but a 2–3 A rating while running), SCT-013-030 is
better than SCT-013-005.

## How to tell the module about your choice

**Two calls are required, in the correct order** — first
`dev.set_sensor_class()`, then `dev.set_ct_model()`. If you call
`dev.set_ct_model()` before `dev.set_sensor_class()` on v1.2+
firmware, the package raises `RbAmpModeError`:

```text
REG_SENSOR_CLASS is UNSET on v1.2+ firmware;
call dev.set_sensor_class(RbAmpSensorClass.SCT_013) first
```

```python
from rbamp import RbAmp, RbAmpSensorClass, RbAmpModeError

with RbAmp(bus, 0x50) as dev:
    # Step 1: the sensor class. REQUIRED before set_ct_model().
    try:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    except RbAmpModeError as e:
        # Possible causes: communication failure (see 10 · Troubleshooting),
        # invalid argument (cls outside the known values)
        print("set_sensor_class failed:", e)
        return

    # Step 2: the model within the family.
    # 1 = SCT-013-005, 2 = SCT-013-010, 3 = SCT-013-030,
    # 4 = SCT-013-050, 5 = SCT-013-100.
    try:
        dev.set_ct_model(3)   # e.g., SCT-013-030
    except RbAmpModeError:
        print("Sensor class must be set first (v1.2+ firmware guard)")
    except ValueError:
        print("Code out of range (must be 1..5)")
```

`set_sensor_class()` accepts **either an enum or a plain int** —
both forms are equivalent:

```python
dev.set_sensor_class(RbAmpSensorClass.SCT_013)  # explicit enum form
dev.set_sensor_class(1)                          # numeric (equivalent)
```

> **The package deliberately does NOT call `set_sensor_class()`
> for you.** If this step is skipped on v1.2+ firmware,
> `set_ct_model()` raises `RbAmpModeError` without writing to
> flash. This is done so the behavior is predictable and explicit —
> no "magic" in the public API.
>
> On v1.0 / v1.1 firmware the guard is skipped (backward compat —
> `set_ct_model()` writes to `REG_CT_MODEL` directly, as before).

After these two calls:

- The module stores both values in flash — the setting survives a
  reset, a power cycle, and a firmware re-flash.
- The calibration coefficients for that specific combination
  (sensor class + model) load from the factory preset table. You
  don't need to touch any manual calibration registers.
- The next read of `dev.current[0]` (or `dev.read_current(0)`)
  already returns a value in amperes with the correct scaling.

**Total time for both calls** is about **1.4 seconds** (two
flash-write operations × ~700 ms each, limited by the flash page
erase time). It is done **once** at first installation; the
setting persists in flash and is not repeated.

> If you already selected a sensor on first run and are simply
> restarting the script, there's no need to repeat the
> `set_sensor_class()` and `set_ct_model()` calls — the module
> remembers the previous choice. But it does no harm either —
> calling again with the same value just rewrites the same byte.

### Verifying the setup

A simple sanity check after `set_ct_model()`:

```python
import time
from rbamp import RbAmp, RbAmpSensorClass

def sanity_check(dev):
    print("Ready. Connect a purely resistive load",
          "(e.g., an incandescent lamp).")
    print("Expect a stable PF ~= 1.0 and positive P.")

    while True:
        u  = dev.voltage              # property — one I²C transaction
        i  = dev.read_current(0)
        p  = dev.read_power(0)
        pf = dev.read_power_factor(0)
        print(f"U={u:.1f} V  I={i:.2f} A  P={p:.1f} W  PF={pf:.2f}")
        time.sleep(2)

with RbAmp(bus, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)
    sanity_check(dev)
```

On MicroPython, replace `time.sleep(2)` with `time.sleep_ms(2000)`.

On a purely resistive load (incandescent lamp, electric kettle,
heating element), expect:

- `U` ≈ 220–240 V (for 230 V grids)
- `I` ≈ corresponds to the load's power (P / U)
- `P` > 0 and stable
- `PF` ≈ 1.0 (strictly positive)

If something doesn't add up, see
[10_troubleshooting.md](10_troubleshooting.md).

## Modules with multiple current channels

On the `UI2`, `UI3`, `I2`, `I3` modules, each current channel has
an **independent** SCT-013 model selection. You can connect, for
example, SCT-013-005 on channel 0 (a single outlet), SCT-013-030
on channel 1 (a stove line), and SCT-013-100 on channel 2 (the
mains feed).

The API for per-channel selection is
`dev.set_ct_model_ch(channel, code)` (added in v1.1.0):

```python
dev.set_sensor_class(RbAmpSensorClass.SCT_013)   # once for all channels

# IMPORTANT: assign channels from highest to lowest (descending order).
# First channel 2, then 1, then 0. See below for why.
dev.set_ct_model_ch(2, 5)   # channel 2: SCT-013-100
dev.set_ct_model_ch(1, 3)   # channel 1: SCT-013-030
dev.set_ct_model_ch(0, 1)   # channel 0: SCT-013-005
```

> ⚠ **Order matters.** Each `dev.set_ct_model_ch(channel, code)`
> call also applies the same `code` to **channel 0** as a side
> effect — this is the legacy compatibility path with v1.1 firmware
> (the single-arg `set_ct_model(code)` always wrote to channel 0
> directly). If you assign channels in forward order
> `(0,1) → (1,3) → (2,5)`, the last call overwrites ch0 with the
> value 5, and the final state is `ch0=5, ch1=3, ch2=5` —
> incorrect.
>
> **The correct order is from the highest channel to the lowest**:
> the last call, with `channel=0`, pins the final ch0 value.

If all channels get the **same** model, order doesn't matter (the
side effect is idempotent):

```python
dev.set_sensor_class(RbAmpSensorClass.SCT_013)
dev.set_ct_model_ch(0, 1)
dev.set_ct_model_ch(1, 1)   # ch0 is overwritten with the same value
dev.set_ct_model_ch(2, 1)
```

The single-parameter `dev.set_ct_model(code)` remains for backward
compat with UI1 modules (it applies to channel 0). On
multi-channel modules it is equivalent to
`dev.set_ct_model_ch(0, code)`.

> On v1.0/v1.1 firmware (REG_VERSION < 0x03) the per-channel
> opcodes `CMD_SET_CT_MODEL_CHn` do not exist —
> `dev.set_ct_model_ch(channel, code)` raises `RbAmpVersionError`
> without writing. Use the single-parameter
> `dev.set_ct_model(code)`, which writes to channel 0 via the
> legacy path.

## Advanced setup: two clamps of different ratings on one wire

> ⚙ **An advanced pattern, not a basic one.** This section
> describes an optional technique for improving resolution at low
> currents. For most installations, a single clamp matched to the
> load range is sufficient. Use dual-CT only if you have a specific
> accuracy requirement at currents < 1 A.

### When it applies

- Multi-channel **UI2 / UI3** modules on v1.2+ firmware.
- The same wire needs to be measured both for small loads (≤ 1 A)
  and for peak events (≥ 5 A) with equal quality.
- A typical example: an apartment service entrance with 50–100 W
  standby during the day, and a kettle or electric stove drawing
  3+ kW in the evening.

### The idea

**Two** SCT-013 clamps of different ratings are installed on the
same wire:

- Channel 0 — the small clamp (e.g., SCT-013-005, 5 A): sees small
  currents with better resolution and a lower noise floor.
- Channel 1 — the large clamp (e.g., SCT-013-030 or higher):
  handles currents above the small clamp's overload point without
  saturation.

The master decides which channel to use based on the current
value — while the small clamp is in its linear range, its reading
is more accurate; above that, it switches to the large one.

### Configuration (descending order — mandatory)

The sensor class first, **once**, then the models **in order from
the highest channel to the lowest** — otherwise the legacy
`REG_CT_MODEL` side effect overwrites channel 0 with the value of
the last call (see the warning in the previous section, "Modules
with multiple current channels").

```python
dev.set_sensor_class(RbAmpSensorClass.SCT_013)   # once

# First channel 1 (the large clamp), then channel 0 (the small one) —
# so the final ch0 value is correct.
dev.set_ct_model_ch(1, 3)   # ch1 = SCT-013-030 (0..30 A)
dev.set_ct_model_ch(0, 1)   # ch0 = SCT-013-005 (0..5 A)
```

Final state: `ch0 = SCT-013-005`, `ch1 = SCT-013-030`. ✓

### Aggregation logic on the master side

The simplest pattern is to switch on a threshold:

```python
import math

def read_combined_current(dev):
    """Read the combined current — picks low-CT when in its linear range,
    otherwise falls back to the high-CT.
    """
    i_low  = dev.read_current(0)   # the small clamp
    i_high = dev.read_current(1)   # the large clamp

    # While the small clamp is far from saturation, it gives better
    # accuracy at low currents. Switch to the large one as it
    # approaches its overload point.
    #
    # The 4.5 A threshold for SCT-013-005 is PROVISIONAL; the exact
    # value will be determined by bench validation (see below, re IP-010).
    # Behavior near the threshold is a matter of measurement, not estimate.
    if not math.isnan(i_low) and i_low < 4.5:
        return i_low
    return i_high
```

> 📷 **An installation diagram is expected.** Two clamps on one
> wire are physically possible on most household-gauge cables, but
> they need a little room in the panel. A detailed diagram —
> including the arrow orientation of both clamps and the allowable
> distances between them — will appear here as it is prepared.


> ⚙ **Bench validation.** The exact figures for the dual-CT pattern
> (behavior near the threshold, temperature drift, the divergence
> of the two clamps in the overlapping range) are established by the
> IP-010 measurement program (the successor to IP-001). Until it is
> complete, treat dual-CT as a pilot pattern; for critical
> applications, a single clamp matched to the upper end of the load
> range is preferable.

### Approaches to improving low-current sensitivity

If your load has a wide dynamic range (for example, 1 W router
standby vs. a 2000 W water heater on the same outlet), a single
clamp sized for the upper limit loses the lower currents in the
noise.

Three strategies, in increasing order of complexity:

1. **Size the CT to the maximum, not "with margin".** The most
   common mistake is putting an SCT-013-100 (100 A) on a household
   outlet with typical consumption of 0.5–10 A. The signal sits in
   the bottom 1–10% of the ADC — where noise becomes comparable to
   the signal. For a household scenario (a 16 A outlet) SCT-013-030
   is optimal; for connecting a single device (≤ 5 A), SCT-013-005.
2. **Dual-CT topology** (requires a UI2/UI3 SKU): a small clamp for
   the low range + a large one for the high range, with the master
   choosing by threshold. See the "Dual-CT topology" section above —
   the pattern is a pilot, and the numbers are being refined by the
   IP-010 program.
3. **Bench calibration of the noise floor** (factory-side): IP-001
   characterizes the noise floor on a test bench; the results are
   baked into the firmware's calibration array. On the user side,
   nothing needs to be done beyond `set_sensor_class()` +
   `set_ct_model()`. Until the program is complete, the specific
   low-current accuracy numbers are not published.

## What's next

- [04 · Hardware connection](04_hardware.md) — physical connection
  of the clamp, arrow orientation, L/N polarity
- [05 · Quickstart](05_quickstart.md) — a full first-light script
  for both backends
- [06 · Examples](06_examples.md) — working scenarios for different
  loads
- [10 · Troubleshooting](10_troubleshooting.md) — what to do if the
  readings are strange (negative PF, unstable I, etc.)


---

[← Tier Support](02_tiers.md) | [Contents](README.md) | [Hardware Setup →](04_hardware.md)
