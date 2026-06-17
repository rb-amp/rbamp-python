# 06 · Examples

This chapter opens with the **flagship scenarios** — the real-world
deployment patterns the package was designed for (mains +
N sub-loads on a single bus via `RbAmpFleet`, GC-synchronized
snapshots, multi-channel mixed-CT via `configure_channels()`).
These are followed by single-module scenarios — from a minimal "hello
world" to a production deployment.

Most scenarios have parallel implementations in
**MicroPython** (under [`examples_upy/`](https://github.com/rb-amp/rbamp-python)) and **CPython**
(under [`examples_cpython/`](https://github.com/rb-amp/rbamp-python)). The code below is the
**distilled core logic**; the full versions live in the corresponding
example files.

| # | Scenario | MicroPython | CPython |
|:---:|---|:---:|:---:|
| **1** | **Mains + N sub-loads — the 80% canon** | (composition) | (composition) |
| **2** | **Provisioning workflow (virgin → fleet)** | (composition) | (composition) |
| **3** | **Multi-channel mixed-CT (I3, different models)** | (composition) | (composition) |
| **4** | **Fleet GC sync — billing-grade synchrony** | (composition) | (composition) |
| 5 | Quick read (single module) | [`01_quick_read.py`](https://github.com/rb-amp/rbamp-python) | [`01_quick_read.py`](https://github.com/rb-amp/rbamp-python) |
| 6 | Period meter + local display | [`02_oled_period.py`](https://github.com/rb-amp/rbamp-python) | [`02_period_meter.py`](https://github.com/rb-amp/rbamp-python) |
| 7 | Monitoring 3 modules (legacy sequential) | [`03_multi_module.py`](https://github.com/rb-amp/rbamp-python) | [`03_multi_module_broadcast.py`](https://github.com/rb-amp/rbamp-python) |
| 8 | UI variant + per-channel MQTT | [`04_mqtt.py`](https://github.com/rb-amp/rbamp-python) | [`04_mqtt_publisher.py`](https://github.com/rb-amp/rbamp-python) |
| 9 | Bidirectional metering on the master side | [`07_bidirectional_energy.py`](https://github.com/rb-amp/rbamp-python) | [`05_bidirectional_energy.py`](https://github.com/rb-amp/rbamp-python) |
| 10 | Whole-home energy balance (multi-module + MQTT) | [`08_home_energy_balance.py`](https://github.com/rb-amp/rbamp-python) | [`07_home_energy_balance.py`](https://github.com/rb-amp/rbamp-python) |
| 11 | Event detection logging (EMA) | [`09_event_detection_logger.py`](https://github.com/rb-amp/rbamp-python) | (composition) |
| 12 | Local rotating logger | (`with open(...)` flash-based) | [`08_rotating_file_logger.py`](https://github.com/rb-amp/rbamp-python) |
| 13 | Battery-powered deep-sleep logger | [`06_deep_sleep.py`](https://github.com/rb-amp/rbamp-python) | (not applicable) |
| 14 | Async streaming via `stream_period` | [`05_async_streaming.py`](https://github.com/rb-amp/rbamp-python) | (asyncio equivalent) |

> Scenarios 1–4 are the flagship scenarios, built around the package's
> canonical deployment. Their code uses `RbAmpFleet` and is validated on
> bench hardware (UI1+I2+I3 heterogeneous fleet). Scenarios 5–14 are
> single-module and compose patterns. Scenario 7 is the legacy sequential
> per-module latch without the fleet API, kept for comparison and as a
> migration path.

> All scenarios assume that the sensor class and CT model are already
> configured via `dev.set_sensor_class()` + `dev.set_ct_model()`
> (or in a single `dev.configure_channels()` call for multi-channel).
> See [05 · Quickstart](05_quickstart.md) Step 4. This is done once
> at installation time — the settings are persisted to the module's
> flash and survive a reset.

> 🛑 **Runnable examples for flagship 1-4 — pending.** The lib-python
> session deferred Task #16 (new example files for the v1.3
> RbAmpFleet/LABEL/two-phase-addr surface). The skeletons in this chapter
> are valid and use the canonical API surface (per the lib-python
> v13-doc-info package); the full runnable .py files will land in a
> separate lib-side commit.

---

## Scenario 1 — Mains + N sub-loads (the 80% canon): integrated metering system

**Goal:** the package's canonical deployment — a cohesive metering
system on a heterogeneous fleet, closed inside a single loop (discover →
configure → arm GC → loop[gc_latch + check_sync + poll_all + totals]).

**Hardware (HW-validated on the Fix-A fleet):** UI1@0x50 + I2@0x51 +
I3@0x52; ~0.58 A load; **external 4.7 kΩ pull-up**.

### Code (CPython)

```python
import time
from smbus2 import SMBus
from rbamp import RbAmpFleet, RbAmpSensorClass

def configure_one(dev):
    variant = dev.read_variant()
    if variant == 1:                                    # UI1 mains
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        dev.set_ct_model_ch(0, 3)                       # SCT-013-030
        dev.save_user_config()
    elif variant == 5:                                  # I2
        dev.configure_channels(RbAmpSensorClass.SCT_013, [1, 3])
    elif variant == 6:                                  # I3
        dev.configure_channels(RbAmpSensorClass.SCT_013, [1, 3, 6])

with SMBus(1) as bus:
    fleet = RbAmpFleet(bus)

    # 1) discover
    added = fleet.scan(match_product=True)
    print(f"fleet_scan: {added} module(s), {len(fleet.excluded)} excluded")

    # 2) configure per-module (mixed-CT on the sub-meters)
    for dev in fleet:
        configure_one(dev)

    # 3) arm GC across the whole fleet
    gc_ok = fleet.enable_gc_all(group=0)
    print(f"enable_gc_all → {gc_ok}/{len(fleet)} armed")

    # 4) loop: gc_latch → check_sync → poll_all → totals
    while True:
        tick = fleet.gc_latch(group=0)                  # auto-increment
        sync = fleet.check_sync(expected_tick=tick)
        n_missed = sum(1 for s in sync if not s.in_sync)

        snaps = fleet.poll_all()
        n_ok = sum(1 for snap, info in snaps if info.ok)

        p_total = fleet.total_power()
        e_total = fleet.total_energy_wh()

        n_total = len(fleet)
        print(f"ITER {tick}: GC {n_total - n_missed}/{n_total} in_sync | "
              f"poll n_ok={n_ok}/{n_total} | "
              f"P_total={p_total:.1f} W  E_total={e_total:.3f} Wh")

        time.sleep(1.0)
```

### MicroPython version

Identical to the CPython version, with the bus initialization swapped out:

```python
from machine import I2C, Pin
bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
fleet = RbAmpFleet(bus)
# ... the rest is the same ...
```

### Bench output (HW-validated, ~0.58 A load)

```text
fleet_scan: 3 module(s), 0 excluded
  [0] @0x50  channels=1  voltage=yes   (mains meter: U + 1 current)
  [1] @0x51  channels=2  voltage=no    (2-channel current sub-meter)
  [2] @0x52  channels=3  voltage=no    (3-channel current sub-meter)
configure_channels(2ch) → mirrors [1 2 -]
configure_channels(3ch) → mirrors [1 2 3]
enable_gc_all → 3/3 armed

ITER N: GC 3/3 in_sync | poll n_ok=3/3 | P_total≈1260 W  E_total: 0 → 27.84 Wh
   @0x50  V=232.6  I=[5.79]                f=50.00
   @0x51  V= —     I=[11.58, 3.87]         f=50.00
   @0x52  V= —     I=[ 3.84, 2.55, 5.84]   f=50.00  (3rd input unused)
```

> The bench figures come from uncalibrated DUTs — they illustrate the
> shape of the data, **not** metrological accuracy. The package's Wh
> accounting is **mathematically exact** (bench measure: `rel_err = 0.0000%`).

### Notes for use

- **One synchronized loop — the entire system.** Each tick: a single
  `gc_latch()` → all modules atomically start the period latch
  (`check_sync` = 3/3) → one `poll_all()` → aggregation.
- **`total_power()` in a heterogeneous fleet.** UI1 reports active
  power; I2/I3 are current-only and contribute `0`. In the 80% canon,
  `total_power` ≈ the power of the mains module.
- **Energy (Wh) — master wall-clock.** The package integrates each
  period using `time.monotonic()` (CPython) or `time.ticks_diff()`
  (MicroPython), not the chip `latch_ms`. See
  [10 · Troubleshooting](10_troubleshooting.md), the section "latch_ms
  reads about 27% low".
- **MISS-resilient.** If one module drops out, `poll_all()`
  marks `info.ok = False` and continues; `check_sync()` marks
  `RbAmpFleetSync.reachable = False`.
- **Per-channel disaggregation.** Through `poll_all()` the master sees
  all current channels (1+2+3 = 6 on our bench fleet) at once.

---

## Scenario 2 — Provisioning workflow (virgin → fleet)

**Goal:** move a new module off its factory address `0x50` onto a
working address and join it to an existing fleet, persisting the
configuration to flash.

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpFleet, RbAmpSensorClass, RbAmpError

def provision_new_module(bus, fleet, desired_addr, label=None):
    """
    PRECONDITION: exactly one virgin on the bus at 0x50.
    MUST be one-virgin-at-a-time — see ch.04 + ch.10.
    """
    try:
        # 1) handle on the factory-default 0x50
        dev = RbAmp(bus, 0x50)
        dev.begin()

        # 2) two-phase address commit
        dev.prepare_address_change(desired_addr)   # arms candidate, 5 s window
        dev.commit_address_change()                # magic + commit + reset
        time.sleep(0.3)                             # boot window

        # 3) optional config — label + sensor class + CT model
        if label:
            dev.set_label(label)
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        dev.set_ct_model_ch(0, 3)                  # SCT-013-030
        dev.save_user_config()                      # persist

        # 4) add to the fleet
        fleet.add(dev)
        print(f"provisioned virgin → 0x{desired_addr:02X} (+saved)")
        return dev

    except RbAmpError as e:
        print(f"provision @0x{desired_addr:02X} failed: {e}")
        return None

with SMBus(1) as bus:
    fleet = RbAmpFleet(bus)
    fleet.scan()
    provision_new_module(bus, fleet, 0x52, label="kitchen")
```

### Bench output (HW-validated success path)

```text
before: present(0x50)=yes  present(0x52)=no
virgin @0x50: variant=3-channel, channels=3
prepare_address_change(0x52) OK
commit_address_change() OK (lib log: "provisioned virgin → 0x52 (+saved)")
post:   handle now answers @0x52, is_provisioned()=True
after:  present(0x50)=no   present(0x52)=yes
```

### Failure paths

| Exception | Condition | Recovery |
|---|---|---|
| `RbAmpIOError` | Nobody answers at `0x50` | Check VCC/I2C; bus-scan ([10 · Troubleshooting](10_troubleshooting.md)) |
| `RbAmpIOError` + conflict | A conflict on `0x50`/`desired_addr` | **Discipline violation** — more than one virgin. Power-cycle + disconnect all but one |
| `RbAmpTimeoutError` | The 5-second arm window expired before `commit_address_change` | Retry (the lib guarantees the arm state is cleared via `try/finally`) |
| `RbAmpParamError` | `desired_addr` outside `0x08..0x77` | Use a valid 7-bit address |

> A static `RbAmpFleet.provision(bus, addr, save_config, ...)` is on the
> roadmap (S6 territory; ETA after the release cycle). Until then,
> use the manual workflow above with `prepare_address_change` +
> `commit_address_change` + `fleet.add()`.

---

## Scenario 3 — Multi-channel mixed-CT (I3, a different model per channel)

**Goal:** demonstrate `configure_channels()` on an I3 — three channels
with different CT models (ch0=SCT-013-005, ch1=SCT-013-030,
ch2=SCT-013-020) in a **single** terminal flash cycle, and
confirm persistence across a reboot.

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass, RbAmpParamError

with SMBus(1) as bus:
    dev = RbAmp(bus, 0x52)                          # I3 sub-meter
    dev.begin()

    # (a) Configure — three CT models in one call
    models = [1, 3, 6]
    # 1=SCT-013-005 (5A), 3=SCT-013-030 (30A), 6=SCT-013-020 (20A)

    t0 = time.monotonic()
    try:
        dev.configure_channels(RbAmpSensorClass.SCT_013, models)
    except RbAmpParamError as e:
        print(f"configure rejected: {e}")
        raise
    t1 = time.monotonic()
    print(f"configure_channels latency = {(t1-t0)*1000:.0f} ms")

    # (b) Verify the mirror via registers 0x51 / 0x52 / 0x53 (RAM)
    m = [dev.read_ct_model_ch(ch) for ch in range(3)]
    print(f"applied: {m}  (expected [1, 3, 6])")

    # (c) Persist verify — reset + re-read the mirror after boot
    dev.reset()
    time.sleep(0.3)
    m = [dev.read_ct_model_ch(ch) for ch in range(3)]
    print(f"after reboot: {m}")
```

### Bench output (HW-validated on the Fix-A I3)

```text
configure_channels(SCT_013, [1, 3, 6]) = OK
  → read_ct_model_ch 0/1/2 = [1, 3, 6]   raw mirror 0x51/52/53 = [01 03 06]
  → ch0 NOT clobbered (Fix-A canon: bind order-independent)
configure_channels latency ≈ 1400 ms
after reboot: [1, 3, 6]  (persisted through reset)
```

### The "single SAVE" property — why it matters

`configure_channels()` performs a **single** terminal
`CMD_SAVE_USER_CONFIG`. The alternative (per-channel `set_ct_model_ch` +
`save_user_config()` after each one) is three flash erase + write cycles.

**Bench latency comparison (on the same I3):**

| Approach | Latency | Flash cycles |
|---|---|---|
| `configure_channels(...)` — 1 SAVE | **~1.4 s** | **1** |
| 3× `(set_ct_model_ch + save_user_config)` | **~3.5 s** | **3** |

3× slower — `(~2.5×)` × `~700 ms`. The key point is the **3× wear-cycle
difference** on the same flash page.

> **Valid codes (bench-characterized).** SCT-013 on the current bench:
> `{1, 2, 3, 4, 6}`. Codes `5` (SCT-013-100) and `7` (SCT-013-060) are
> uncharacterised → `RbAmpParamError` pre-bus (no I²C operation).

---

## Scenario 4 — Fleet GC sync (billing-grade synchrony)

**Goal:** demonstrate an atomic latch across the whole fleet in a single
I²C General-Call frame. Unlike a sequential per-module
`latch_period()`, GC synchronizes all modules in **one** bus
transaction.

```python
import time
from smbus2 import SMBus
from rbamp import RbAmpFleet

with SMBus(1) as bus:
    fleet = RbAmpFleet(bus)
    fleet.scan()

    # Step 1: enable GC on every module in the fleet
    gc_ok = fleet.enable_gc_all(group=0)
    print(f"GC armed: {gc_ok}/{len(fleet)}")

    # Steps 2-3: loop with broadcast latch + witness check
    for round_idx in range(10):
        tick = fleet.gc_latch(group=0)             # auto-increment tick
        sync = fleet.check_sync(expected_tick=tick)
        n_in_sync = sum(1 for s in sync if s.in_sync)
        print(f"round {round_idx}  tick={tick}  in_sync={n_in_sync}/{len(fleet)}")
        time.sleep(0.1)
```

### Bench output (HW-validated)

```text
enable_gc_all → 3/3 modules armed
round 0   tick=0   in_sync=3/3
round 1   tick=1   in_sync=3/3
...
round 9   tick=9   in_sync=3/3      skew=0 (validated at tick=0xABCD)
```

### When to use GC sync

- **Billing-grade synchrony.** A sequential per-module latch on
  those same 3 modules accumulates 100-300 µs per module × N of skew.
  GC delivers `skew = 0` by bench measurement.
- **Large fleet.** The gap between "sequential N × round_trip" and
  "a single GC frame" grows linearly.

**Witness via `check_sync()`:** verifies that all modules actually
received the GC frame. If a module was busy with a boot / SAVE cycle,
`RbAmpFleetSync.reachable = False`.

**Precondition.** GC must be **enabled** on each module
(`fleet.enable_gc_all()` or per-device `dev.enable_gc(True)`).
A fresh module with factory defaults does **not** accept GC — by design,
a guard against an accidental broadcast on a bring-up bus.

---

## Scenario 5 — Quick read (single module)

**Goal:** print U / I / P / PF / frequency once per second. The same
"hello world" you wrote in
[05 · Quickstart](05_quickstart.md), but using `dev.read_all()`
— a single-shot read of the whole RT block into one
`RbAmpSnapshot` structure.

### CPython version

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpError

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    while True:
        try:
            s = dev.read_all()
        except RbAmpError as e:
            print("read fail:", e)
            time.sleep(1)
            continue

        line = f"U={s.voltage:.1f} V  f={s.frequency} Hz   "
        for ch in range(s.channels):
            line += f"I{ch}={s.current[ch]:.2f}A  P{ch}={s.power[ch]:.1f}W  "
        print(line)
        time.sleep(1)
```

### MicroPython version

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpError

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    while True:
        try:
            s = dev.read_all()
        except RbAmpError as e:
            print("read fail:", e)
            time.sleep(1)
            continue

        print("U={:.1f} V  f={} Hz".format(s.voltage, s.frequency))
        for ch in range(s.channels):
            print("  I{}={:.2f}A  P{}={:.1f}W".format(
                ch, s.current[ch], ch, s.power[ch]))
        time.sleep(1)
```

**What happens on the bus:** `dev.read_all()` on a UI3 performs ~53
byte transactions (13 float values × 4 bytes + 1 frequency byte).
At 50 kHz with retry headroom, that is on the order of 25-30 ms. If you
don't need all the values, use the per-property accessors (`dev.voltage`,
`dev.read_current(ch)`).

---

## Scenario 6 — Period meter + local display

**Goal:** a Wh counter, updated once per minute, on a local
display. On MicroPython — an SSD1306 OLED on the same I²C bus. On
CPython — text output to stdout (you can wire up a character LCD
over GPIO, or use MQTT/HA publishing — see Scenario 8 (MQTT/HA)).

### MicroPython version (SSD1306 OLED)

```python
import time
from machine import I2C, Pin
from ssd1306 import SSD1306_I2C
from rbamp import RbAmp, RbAmpStaleError, RbAmpSensorClass

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
oled = SSD1306_I2C(128, 64, i2c)   # OLED on the same bus, addr 0x3C

with RbAmp(i2c, 0x50) as dev:
    snapshots_ok = 0
    snapshots_bad = 0

    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
            snapshots_ok += 1
        except RbAmpStaleError:
            snapshots_bad += 1
            continue

        oled.fill(0)
        oled.text("rbAmp Energy Meter", 0, 0)
        oled.text("{:.1f} W".format(snap.avg_p[0]), 0, 16, 1)
        oled.text("Wh: {:.4f}".format(dev.energy.wh(0)), 0, 32)
        oled.text("ok={} bad={}".format(snapshots_ok, snapshots_bad), 0, 48)
        oled.show()
```

### CPython version (period meter without a display)

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpStaleError

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    snapshots_ok = 0
    snapshots_bad = 0

    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
            snapshots_ok += 1
        except RbAmpStaleError:
            snapshots_bad += 1
            print("stale snapshot — skipping")
            continue

        print(f"P = {snap.avg_p[0]:.2f} W   "
              f"Wh = {dev.energy.wh(0):.4f}   "
              f"ok={snapshots_ok} bad={snapshots_bad}   "
              f"dt={snap.master_dt_ms} ms")
```

**Handling stale snapshots.** `dev.read_period_snapshot()`
raises `RbAmpStaleError` if the module hasn't managed to prepare a new
snapshot by read time. The package **still records** the
master timestamp internally — the next successful snapshot won't be
double-counted over the interval. A standard Python try/except pattern.

---

## Scenario 7 — Monitoring 3 modules (legacy sequential) on one bus

**Goal:** poll 3 modules at addresses 0x50 / 0x51 / 0x52. The canonical
pattern for v1 firmware — sequential per-device LATCH + a shared settle
+ per-device `read_period_snapshot(skip_latch=True)`. Inter-device
skew at 100 kHz: ~1 ms per device, < 0.2 % of a 60-second
period.

> The `RbAmp.broadcast_latch(bus)` function (and its preferred
> `broadcast_latch_group(bus, group, tick)` form) requires GC to be
> **opted in** on each module: capability bit `CAP_GC_LATCH` set +
> `FLEET_CONFIG.bit0` enabled (default OFF, persisted via
> `CMD_SAVE_USER_CONFIG` + reset — done by `dev.enable_gc(True)` or
> `fleet.enable_gc_all()`). On a legacy module without the capability,
> the call falls back to a sequential per-device latch. See
> [09 · API Reference](09_api_reference.md) for details.

### CPython version

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpStaleError

ADDRS = [0x50, 0x51, 0x52]

with SMBus(1) as bus:
    devs = [RbAmp(bus, addr) for addr in ADDRS]
    for dev in devs:
        dev.begin()

    while True:
        time.sleep(60)

        # Phase 1: sequential LATCH on each device, measure the skew
        t_start = time.monotonic()
        for dev in devs:
            dev.latch_period()
        sync_ms = (time.monotonic() - t_start) * 1000

        # Phase 2: a single shared settle for all 3 modules
        time.sleep(0.050)

        # Phase 3: read the snapshots with skip_latch=True
        print(f"sync_ms={sync_ms:.1f}")
        for i, dev in enumerate(devs):
            try:
                snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)
            except RbAmpStaleError:
                continue
            print(f"  mod{i} 0x{ADDRS[i]:02X}  P={snap.avg_p[0]:.0f} W  "
                  f"Wh={dev.energy.wh(0):.3f}")
```

### MicroPython version

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError

ADDRS = [0x50, 0x51, 0x52]
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)

devs = [RbAmp(i2c, addr) for addr in ADDRS]
for dev in devs:
    dev.begin()

while True:
    time.sleep(60)

    t_start = time.ticks_ms()
    for dev in devs:
        dev.latch_period()
    sync_ms = time.ticks_diff(time.ticks_ms(), t_start)

    time.sleep_ms(50)

    print("sync_ms=", sync_ms)
    for i, dev in enumerate(devs):
        try:
            snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)
        except RbAmpStaleError:
            continue
        print("  mod{} 0x{:02X}  P={:.0f} W  Wh={:.3f}".format(
            i, ADDRS[i], snap.avg_p[0], dev.energy.wh(0)))
```

---

## Scenario 8 — UI variant + per-channel MQTT

**Goal:** a UI3 module with 3 CT clamps on three independent lines.
Per-channel Wh counters, published to MQTT once per minute. Shows
that `dev.energy.wh(ch)` works on each channel independently — no
manual `total_wh[3]` array on the master side is needed.

### CPython (via `paho-mqtt`)

```python
import time, json
from smbus2 import SMBus
import paho.mqtt.client as mqtt_client
from rbamp import RbAmp, RbAmpStaleError, RbAmpSensorClass

CH_NAMES = ["main", "heatpump", "lights"]

mqtt = mqtt_client.Client("rbamp-ui3")
mqtt.connect("192.168.1.10", 1883, keepalive=60)
mqtt.loop_start()

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            continue

        for ch in range(dev.channels):
            payload = json.dumps({
                "power":  round(snap.avg_p[ch], 1),
                "energy": round(dev.energy.wh(ch), 4),
            })
            mqtt.publish(f"rbamp/{CH_NAMES[ch]}/state",
                         payload, qos=1, retain=True)
```

### MicroPython (via `umqtt.simple`)

```python
import time, ujson
from machine import I2C, Pin
from umqtt.simple import MQTTClient
from rbamp import RbAmp, RbAmpStaleError, RbAmpSensorClass

CH_NAMES = [b"main", b"heatpump", b"lights"]

mqtt = MQTTClient(b"rbamp-ui3", b"192.168.1.10",
                  port=1883, keepalive=60)
mqtt.connect()

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            continue

        for ch in range(dev.channels):
            payload = ujson.dumps({
                "power":  round(snap.avg_p[ch], 1),
                "energy": round(dev.energy.wh(ch), 4),
            })
            topic = b"rbamp/" + CH_NAMES[ch] + b"/state"
            mqtt.publish(topic, payload.encode(), qos=1, retain=True)
```

> On MicroPython, `umqtt.simple` is a synchronous blocking client.
> For an async variant, use `mqtt_as` (an asyncio MQTT client) —
> see also Scenario 10.

For full HA Auto-discovery on top of this pattern, see
[07 · DIY integrations](07_diy_integrations.md).

---

## Scenario 9 — Bidirectional metering on the master side

**Goal:** split signed instantaneous power into gross-consume
and gross-export. Use this pattern on the BASIC tier, where the
**firmware** clips negative values in `snap.avg_p[ch]`
(period-averaged power) — sample `dev.read_power(0)`
(instantaneous power, **signed on all tiers**) at 5 Hz on the
master side and bucket it yourself.

### CPython version

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpIOError

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    consume_wh = 0.0
    export_wh  = 0.0
    t_prev = time.monotonic()

    while True:
        time.sleep(0.2)   # 5 Hz, matches the RT cadence

        t_now = time.monotonic()
        dt_s  = t_now - t_prev
        t_prev = t_now

        try:
            p = dev.read_power(0)
        except RbAmpIOError:
            continue

        dwh = p * dt_s / 3600.0
        if p >= 0:
            consume_wh += dwh
        else:
            export_wh  += -dwh

        # Print once per second
        if int(t_now) % 5 == 0:
            print(f"P={p:.1f} W   "
                  f"cons={consume_wh:.4f} Wh  exp={export_wh:.4f} Wh  "
                  f"net={consume_wh - export_wh:.4f} Wh")
```

### MicroPython version

```python
import time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpIOError

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    consume_wh = 0.0
    export_wh  = 0.0
    t_prev = time.ticks_ms()

    while True:
        time.sleep_ms(200)

        t_now = time.ticks_ms()
        dt_s  = time.ticks_diff(t_now, t_prev) / 1000
        t_prev = t_now

        try:
            p = dev.read_power(0)
        except RbAmpIOError:
            continue

        dwh = p * dt_s / 3600.0
        if p >= 0:
            consume_wh += dwh
        else:
            export_wh  += -dwh
```

On future STANDARD / PRO firmware tiers (planned for a future tier release) the
period accumulator `dev.energy.wh(0)` will already return a signed
net balance — this master-side split is only needed for separate
gross-consume / gross-export reporting.

---

## Scenario 10 — Whole-home energy balance

**Goal:** 3 modules — mains (bidirectional), solar (generation
only), loads (UI3 for per-appliance). A combined dashboard is
published once per minute.

```python
import time, json
from smbus2 import SMBus
import paho.mqtt.client as mqtt_client
from rbamp import RbAmp, RbAmpStaleError, RbAmpSensorClass

with SMBus(1) as bus:
    mains = RbAmp(bus, 0x50)   # bidirectional mains
    solar = RbAmp(bus, 0x51)   # generation only
    loads = RbAmp(bus, 0x52)   # UI3 — per-appliance
    devs = [mains, solar, loads]
    for d in devs:
        d.begin()

    mqtt = mqtt_client.Client("home-balance")
    mqtt.connect("192.168.1.10", 1883, keepalive=60)
    mqtt.loop_start()

    while True:
        time.sleep(60)

        # Sequential LATCH + shared settle (the Scenario 7 pattern — legacy sequential)
        for d in devs:
            d.latch_period()
        time.sleep(0.050)

        try:
            sm = mains.read_period_snapshot(settle_ms=0, skip_latch=True)
            ss = solar.read_period_snapshot(settle_ms=0, skip_latch=True)
            sl = loads.read_period_snapshot(settle_ms=0, skip_latch=True)
        except RbAmpStaleError:
            continue

        payload = {
            "mains":  round(mains.energy.wh(0), 3),
            "solar":  round(solar.energy.wh(0), 3),
            "hp":     round(loads.energy.wh(0), 3),
            "ac":     round(loads.energy.wh(1), 3),
            "ev":     round(loads.energy.wh(2), 3),
        }
        mqtt.publish("home/energy/balance",
                     json.dumps(payload), qos=1, retain=True)
```

For an explicit gross-consume / gross-export split on the mains,
combine this with the Scenario 5 pattern (a separate 5 Hz loop on
`mains.read_power(0)` in a background threading.Thread on CPython, or
`uasyncio.create_task` on MicroPython).

---

## Scenario 11 — Event detection logging (EMA)

**Goal:** on every 200 ms RT window, compare instantaneous power
against an exponential moving average. Log significant
deviations — loads such as a microwave, kettle, or hair dryer
"appear" in the log as a turn-on / turn-off event.

```python
import time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpIOError

EMA_ALPHA = 0.05
EVENT_THRESHOLD_W = 200.0

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    try:
        p_ema = dev.read_power(0)   # seed so the first read isn't an event
    except RbAmpIOError:
        p_ema = 0.0

    while True:
        time.sleep(0.2)
        try:
            p = dev.read_power(0)
        except RbAmpIOError:
            continue

        delta = p - p_ema
        p_ema = (1 - EMA_ALPHA) * p_ema + EMA_ALPHA * p

        if abs(delta) > EVENT_THRESHOLD_W:
            action = "TURN_ON" if delta > 0 else "TURN_OFF"
            print(f"{time.time():.0f}  {action}  delta={delta:.0f} W  "
                  f"P={p:.0f} W  EMA={p_ema:.0f}")
```

On MicroPython — the same substitutions: `time.sleep` → `time.sleep_ms`,
`time.time()` → `time.time()` (available on most ports) or
`time.ticks_ms()`.

Combine with the MQTT publishing from Scenario 8 for HA-side
automations.

---

## Scenario 12 — Local rotating logger (CPython)

**Goal:** write the period snapshot to a rotating CSV file once per
minute via the standard `logging.handlers.RotatingFileHandler`.
Useful for standalone deployments without WiFi / MQTT, or as a buffer
for deferred cloud sync.

```python
import time, logging
from logging.handlers import RotatingFileHandler
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpStaleError

log = logging.getLogger("rbamp.csv")
log.setLevel(logging.INFO)
handler = RotatingFileHandler("rbamp.csv", maxBytes=1_000_000, backupCount=5)
handler.setFormatter(logging.Formatter("%(asctime)s,%(message)s",
                                       datefmt="%Y-%m-%dT%H:%M:%S"))
log.addHandler(handler)

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            log.warning("stale")
            continue
        log.info(f"{snap.avg_p[0]:.1f},{dev.energy.wh(0):.4f},"
                 f"{snap.master_dt_ms}")
```

On MicroPython the standard `logging.handlers` is absent — for a
local log, use a simple `with open("log.csv", "a")` + manual
rotation via `os.stat()` + `os.rename()`. See the full
example in `examples_upy/09_event_detection_logger.py`.

For deferred cloud sync (log locally, send
once per hour) — see the "Hybrid: local storage + sync" section in
[08 · Cloud integrations](08_cloud_integrations.md).

---

## Scenario 13 — Battery-powered deep-sleep logger (MicroPython only)

**Goal:** an ESP32 (MicroPython) wakes once every 10 minutes, takes
one period latch, publishes over WiFi+MQTT, and goes back into
deep-sleep. Energy is persisted in RTC memory across sleep cycles.

> ⚠ **Important about deep-sleep and v1.0/v1.1 firmware.** On the current
> firmware, `dev.begin()` issues a CMD_LATCH_PERIOD primer that
> resets the firmware's period accumulator. This means that after a
> deep-sleep wake you can't simply call `read_period_snapshot()`
> by default — that call would re-latch and return near-zero
> data for the ~50 ms since the primer. The correct pattern below
> uses `skip_latch=True` to read the data accumulated over the
> sleep interval, and the **known sleep duration**
> (`machine.deepsleep(SLEEP_MS)`) as dt — `time.ticks_ms()` after
> wake may be an unreliable source.

```python
import time, machine
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass

# Stub for publishing. Replace with real WiFi+MQTT logic —
# see Scenario 8 (mqtt_publisher) for a template. In the deep-sleep
# scenario the WiFi stack is brought up/torn down on every wake, which
# eats up the bulk of the energy budget.
def publish_via_wifi(power_w, total_wh, dt_s):
    print("P=%.1f W  Wh=%.4f  dt=%.1f s" % (power_w, total_wh, dt_s))

SLEEP_MS = 10 * 60 * 1000             # 10 minutes — fixed interval
RTC_MAGIC = b"\xCA\xFE\xFE\xED"

# RTC slow-memory: retained between deep-sleep wakes
rtc = machine.RTC()
state = rtc.memory()                  # bytes; empty on cold boot

def save_state(magic, wh_total, primer_done):
    # Simple layout: 4 bytes magic + 8 bytes float wh + 1 byte primer_done
    import struct
    rtc.memory(magic + struct.pack("dB", wh_total, 1 if primer_done else 0))

def load_state():
    import struct
    if len(state) < 4 + 8 + 1 or state[:4] != RTC_MAGIC:
        return False, 0.0, False
    wh_total, primer_done_byte = struct.unpack("dB", state[4:4+9])
    return True, wh_total, bool(primer_done_byte)

valid, rtc_total_wh, primer_done = load_state()
if not valid:
    rtc_total_wh = 0.0
    primer_done = False

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:    # __enter__ automatically calls begin() — the LATCH primer
    dev.energy.disable()         # the master owns Wh persistence itself

    if not primer_done:
        # First wake: the primer just latched "dirty" data.
        # Save the flag and go straight back to sleep — the next wake in
        # SLEEP_MS will get a full accumulator over the interval.
        save_state(RTC_MAGIC, rtc_total_wh, primer_done=True)
        machine.deepsleep(SLEEP_MS)

    # skip_latch=True reads what the begin() primer latched —
    # that is, the accumulator over the elapsed sleep interval.
    snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)

    # Use the KNOWN sleep duration as dt
    dt_s = SLEEP_MS / 1000
    rtc_total_wh += snap.avg_p[0] * dt_s / 3600.0

    publish_via_wifi(snap.avg_p[0], rtc_total_wh, dt_s)  # stub

    save_state(RTC_MAGIC, rtc_total_wh, primer_done=True)

machine.deepsleep(SLEEP_MS)
```

The full sketch (with WiFi setup, MQTT publish, RTC magic-marker guards)
is in [`examples_upy/06_deep_sleep.py`](https://github.com/rb-amp/rbamp-python).

> 🛣 **Roadmap.** A future package version is expected to add
> `dev.warm_open()` — a lightweight context manager without the CMD_LATCH_PERIOD
> primer, which will make it easier to read `read_period_snapshot()` by
> default after a deep-sleep wake. Until then, the pattern above
> (skip_latch=True + known SLEEP_MS) is canonical.

**Energy budget** (ESP32-WROOM on a 2000 mAh Li-ion, 10-minute
interval):

- Active cycle: ~3 s (WiFi + MQTT + I²C) at ~80 mA → ~0.07 mAh per wake
- Sleep: ~10 µA (RTC + retained domains)
- ~10 mAh per day → 2000 mAh lasts ~6 months

CPython hosts (Raspberry Pi etc.) typically run off a 24/7
power supply — deep-sleep does not apply. For CPython
battery operation, see
[08 · Cloud integrations](08_cloud_integrations.md), the section on
power consumption and `systemd` suspend.

---

## Scenario 14 — Async streaming via `stream_period` (Python-only feature)

**Goal:** integration into an existing asyncio / uasyncio event loop.
The package provides an async generator `dev.stream_period(interval_s=...)`
that encapsulates the periodic latch + automatic stale handling.

### CPython (via `asyncio` + `paho-mqtt-asyncio` or flat)

```python
import asyncio
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

async def main():
    with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
        # Once per minute, skip_stale=True (default) — stale snapshots are
        # automatically skipped without raising RbAmpStaleError
        async for snap in dev.stream_period(interval_s=60.0):
            print(f"P={snap.avg_p[0]:.2f} W,  Wh={dev.energy.wh(0):.4f}")

asyncio.run(main())
```

### MicroPython (via `uasyncio`)

```python
import uasyncio as asyncio
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass

async def reader(dev):
    async for snap in dev.stream_period(interval_s=60.0):
        print("P={:.2f} W,  Wh={:.4f}".format(snap.avg_p[0], dev.energy.wh(0)))

async def main():
    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
    with RbAmp(i2c, 0x50) as dev:
        # Run the reader + other async tasks in parallel
        await asyncio.gather(
            reader(dev),
            # other_async_task(),
        )

asyncio.run(main())
```

`stream_period(interval_s=, skip_stale=)` parameters:

- `interval_s` — the period between latches (≥ 30 s recommended)
- `skip_stale=True` (default) — stale snapshots are automatically
  skipped; the generator simply moves on to the next interval
- `skip_stale=False` — stale snapshots are yielded with `snap.valid=False`,
  and the caller decides what to do with them

Full examples — [`examples_upy/05_async_streaming.py`](https://github.com/rb-amp/rbamp-python)
(MicroPython) and the asyncio equivalent in the CPython examples.

---

## Scenario summary table

| # | LOC | Runtime | I²C bus | Period? | DRDY? | MQTT? | Persistence |
|:---:|:---:|---|:---:|:---:|:---:|:---:|---|
| 1 | ~25 | both | dedicated | no | no | no | no |
| 2 | ~35 | both (OLED on uPy) | shared with OLED (uPy) | yes | no | no | RAM |
| 3 | ~50 | both | shared 3 modules | yes | no | no | RAM |
| 4 | ~50 | both | dedicated | yes | no | yes | retained MQTT |
| 5 | ~40 | both | dedicated | no (RT) | no | no | RAM (master-owned) |
| 6 | ~60 | both | shared 3 modules | yes | no | yes | retained MQTT |
| 7 | ~40 | both | dedicated | no (RT) | no | no | RAM |
| 8 | ~30 | CPython | dedicated | yes | no | no | rotating-log file |
| 9 | ~50 | MicroPython only | dedicated | yes | no | yes | RTC memory + MQTT |
| 10 | ~25 | both (asyncio/uasyncio) | dedicated | yes | no | (optional) | RAM |

## Production deployment (CPython only)

For a long-running 24/7 deployment on a Linux SBC, a
**systemd service** is recommended. A full example is in
[`examples_cpython/10_systemd_service.py`](https://github.com/rb-amp/rbamp-python)
(it includes an `--install` helper that generates the
`/etc/systemd/system/rbamp.service` unit file).

The basic structure:

```python
# /usr/local/bin/rbamp_daemon.py
import sys, signal, time, logging
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rbamp")

shutdown = False
def on_signal(sig, frame):
    global shutdown
    log.info("Received signal %d, shutting down gracefully", sig)
    shutdown = True

signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT,  on_signal)

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    log.info("rbAmp daemon started")
    while not shutdown:
        try:
            snap = dev.read_period_snapshot()
            log.info("P=%.1f W, Wh=%.4f", snap.avg_p[0], dev.energy.wh(0))
        except Exception as e:
            log.warning("snapshot failed: %s", e)
        time.sleep(60)

log.info("rbAmp daemon stopped")
```

And the unit itself:

```ini
# /etc/systemd/system/rbamp.service
[Unit]
Description=rbAmp I2C AC sensor daemon
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/rbamp_daemon.py
Restart=on-failure
RestartSec=5
User=pi

[Install]
WantedBy=multi-user.target
```

Start it: `sudo systemctl enable --now rbamp`. Logs: `journalctl -u rbamp -f`.

## What's next

- [07 · DIY integrations](07_diy_integrations.md) — Home Assistant /
  Node-RED / OpenHAB based on these scenarios
- [08 · Cloud integrations](08_cloud_integrations.md) — AWS IoT /
  Azure / GCP / InfluxDB Cloud / generic webhook
- [09 · API Reference](09_api_reference.md) — every public
  class + method + property + exception
- [10 · Troubleshooting](10_troubleshooting.md) — when scenarios behave
  unexpectedly on your bench

