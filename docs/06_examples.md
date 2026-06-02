# 06 · Examples

This chapter walks through 10 working scenarios — from a minimal
"hello world" to a production deployment. Most scenarios have
parallel implementations in **MicroPython** (under
[`examples_upy/`](../rbamp/examples_upy/)) and **CPython** (under
[`examples_cpython/`](../rbamp/examples_cpython/)). The code below is a
**distillation of the key logic**, not a complete script; the full
versions live in the corresponding example files.

| # | Scenario | MicroPython | CPython |
|:---:|---|:---:|:---:|
| 1 | Quick read | [`01_quick_read.py`](../rbamp/examples_upy/01_quick_read.py) | [`01_quick_read.py`](../rbamp/examples_cpython/01_quick_read.py) |
| 2 | Period meter + local display | [`02_oled_period.py`](../rbamp/examples_upy/02_oled_period.py) | [`02_period_meter.py`](../rbamp/examples_cpython/02_period_meter.py) |
| 3 | Monitoring 3 modules on one bus | [`03_multi_module.py`](../rbamp/examples_upy/03_multi_module.py) | [`03_multi_module_broadcast.py`](../rbamp/examples_cpython/03_multi_module_broadcast.py) |
| 4 | UI3 + MQTT per channel | [`04_mqtt.py`](../rbamp/examples_upy/04_mqtt.py) | [`04_mqtt_publisher.py`](../rbamp/examples_cpython/04_mqtt_publisher.py) |
| 5 | Master-side bidirectional metering | [`07_bidirectional_energy.py`](../rbamp/examples_upy/07_bidirectional_energy.py) | [`05_bidirectional_energy.py`](../rbamp/examples_cpython/05_bidirectional_energy.py) |
| 6 | Whole-home energy balance (multi-module + MQTT) | [`08_home_energy_balance.py`](../rbamp/examples_upy/08_home_energy_balance.py) | [`07_home_energy_balance.py`](../rbamp/examples_cpython/07_home_energy_balance.py) |
| 7 | Event detection logging (EMA) | [`09_event_detection_logger.py`](../rbamp/examples_upy/09_event_detection_logger.py) | (composition) |
| 8 | Local rotating logger | (`with open(...)` flash-based) | [`08_rotating_file_logger.py`](../rbamp/examples_cpython/08_rotating_file_logger.py) |
| 9 | Battery-powered deep-sleep logger | [`06_deep_sleep.py`](../rbamp/examples_upy/06_deep_sleep.py) | (not applicable) |
| 10 | Async streaming via `stream_period` | [`05_async_streaming.py`](../rbamp/examples_upy/05_async_streaming.py) | (asyncio equivalent) |

> All scenarios assume that the sensor class and CT model are
> already configured via `dev.set_sensor_class()` + `dev.set_ct_model()`
> (see [05 · Quickstart](05_quickstart.md), Step 4). This is done
> once at install time — the settings are persisted to the module's
> flash and survive a reset.

---

## Scenario 1 — Quick read

**Goal:** print U / I / P / PF / frequency once per second. This is
the same "hello world" you wrote in
[05 · Quickstart](05_quickstart.md), but using `dev.read_all()`
— a single-shot read of the entire RT block into one
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
At 50 kHz with retry headroom, that's on the order of 25-30 ms. If
you don't need every value, use the per-property accessors
(`dev.voltage`, `dev.read_current(ch)`).

---

## Scenario 2 — Period meter + local display

**Goal:** a Wh counter, updated once per minute, on a local
display. On MicroPython — an SSD1306 OLED on the same I²C bus. On
CPython — text output to stdout (you can attach a character LCD via
GPIO or use a web page — see Scenario 6).

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

### CPython version (period meter, no display)

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

**Handling stale snapshots.** `dev.read_period_snapshot()` raises
`RbAmpStaleError` if the module hasn't managed to prepare a new
snapshot by read time. The package **records the master timestamp
anyway** internally — the next successful snapshot won't be
double-counted over the interval. A standard Python try/except
pattern.

---

## Scenario 3 — Monitoring 3 modules on one bus

**Goal:** poll 3 modules at addresses 0x50 / 0x51 / 0x52. The
canonical pattern for v1 firmware — sequential per-device LATCH +
shared settle + per-device `read_period_snapshot(skip_latch=True)`.
Inter-device skew at 100 kHz: ~1 ms per device, < 0.2 % of the
60-second period.

> The `RbAmp.broadcast_latch(bus)` function is reserved for v2
> firmware (General-Call is disabled in the v1 module's I²C
> peripheral) — it returns `False` without touching the bus. See
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

        # Phase 1: sequential LATCH on each device, measure skew
        t_start = time.monotonic()
        for dev in devs:
            dev.latch_period()
        sync_ms = (time.monotonic() - t_start) * 1000

        # Phase 2: one shared settle for all 3 modules
        time.sleep(0.050)

        # Phase 3: read snapshots with skip_latch=True
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

## Scenario 4 — UI3 + MQTT per channel

**Goal:** a UI3 module with 3 CT clamps on three independent lines.
Per-channel Wh counters, published to MQTT once per minute. Shows
that `dev.energy.wh(ch)` works on each channel independently — no
need for a manual `total_wh[3]` array on the master side.

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

> On MicroPython, `umqtt.simple` is a synchronous, blocking client.
> For an async variant, use `mqtt_as` (an asyncio MQTT client) —
> see also Scenario 10.

For full HA auto-discovery on top of this pattern, see
[07 · DIY integrations](07_diy_integrations.md).

---

## Scenario 5 — Master-side bidirectional metering

**Goal:** split signed instantaneous power into gross-consume and
gross-export. Use this pattern on the BASIC tier, where the
**firmware** clips negative values in `snap.avg_p[ch]`
(period-averaged power) — sample `dev.read_power(0)` (instantaneous
power, **signed on all tiers**) at 5 Hz on the master side and bucket
the values yourself.

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

On future STANDARD / PRO firmware tiers (planned for v1.3+), the
period accumulator `dev.energy.wh(0)` will already return a signed
net balance — this master-side split is only needed for separate
gross-consume / gross-export reporting.

---

## Scenario 6 — Whole-home energy balance

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

        # Sequential LATCH + shared settle (Scenario 3 pattern)
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

For an explicit gross-consume / gross-export split on mains, combine
this with the Scenario 5 pattern (a separate 5 Hz loop on
`mains.read_power(0)` in a background threading.Thread on CPython, or
`uasyncio.create_task` on MicroPython).

---

## Scenario 7 — Event detection logging (EMA)

**Goal:** on every 200 ms RT window, compare instantaneous power
with an exponential moving average. Log significant deviations —
loads like a microwave, kettle, or hairdryer "appear" in the log as
turn-on / turn-off events.

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

On MicroPython, apply the same substitutions: `time.sleep` →
`time.sleep_ms`, and `time.time()` → `time.time()` (available on
most ports) or `time.ticks_ms()`.

Combine this with the MQTT publishing from Scenario 4 for HA-side
automations.

---

## Scenario 8 — Local rotating logger (CPython)

**Goal:** write a period snapshot to a rotating CSV file once per
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

On MicroPython the standard `logging.handlers` module isn't
available — for a local log, use a simple `with open("log.csv", "a")`
plus manual rotation via `os.stat()` + `os.rename()`. See the full
example in `examples_upy/09_event_detection_logger.py`.

For deferred cloud sync (log locally, send once per hour), see the
"Hybrid: local storage + sync" section in
[08 · Cloud integrations](08_cloud_integrations.md).

---

## Scenario 9 — Battery-powered deep-sleep logger (MicroPython only)

**Goal:** an ESP32 (MicroPython) wakes once every 10 minutes,
performs a single period latch, publishes via WiFi+MQTT, and goes
back into deep sleep. Energy is persisted across sleep cycles via
RTC memory.

> ⚠ **Important note about deep-sleep and v1.0/v1.1 firmware.** On
> the current firmware, `dev.begin()` issues a CMD_LATCH_PERIOD
> primer that resets the firmware's period accumulator. This means
> that after a deep-sleep wake you cannot simply call
> `read_period_snapshot()` by default — that call would latch again
> and return near-zero data covering only the ~50 ms since the
> primer. The correct pattern below uses `skip_latch=True` to read
> the data accumulated during the sleep interval, and the **known
> sleep duration** (`machine.deepsleep(SLEEP_MS)`) as dt —
> `time.ticks_ms()` after wake may be an unreliable source.

```python
import time, machine
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass

# Publishing stub. Replace with real WiFi+MQTT logic —
# see Scenario 4 (mqtt_publisher) for a template. In a deep-sleep
# scenario, the WiFi stack is brought up/torn down on every wake,
# which eats up the bulk of the energy budget.
def publish_via_wifi(power_w, total_wh, dt_s):
    print("P=%.1f W  Wh=%.4f  dt=%.1f s" % (power_w, total_wh, dt_s))

SLEEP_MS = 10 * 60 * 1000             # 10 minutes — fixed interval
RTC_MAGIC = b"\xCA\xFE\xFE\xED"

# RTC slow memory: persisted across deep-sleep wakes
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
        # First wake: the primer just latched away the "dirty" data.
        # Save the flag and go straight back to sleep — the next wake,
        # SLEEP_MS later, will get a full accumulator for the interval.
        save_state(RTC_MAGIC, rtc_total_wh, primer_done=True)
        machine.deepsleep(SLEEP_MS)

    # skip_latch=True reads what the begin() primer latched —
    # i.e. the accumulator for the elapsed sleep interval.
    snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)

    # Use the KNOWN sleep duration as dt
    dt_s = SLEEP_MS / 1000
    rtc_total_wh += snap.avg_p[0] * dt_s / 3600.0

    publish_via_wifi(snap.avg_p[0], rtc_total_wh, dt_s)  # stub

    save_state(RTC_MAGIC, rtc_total_wh, primer_done=True)

machine.deepsleep(SLEEP_MS)
```

The full sketch (with WiFi setup, MQTT publish, RTC magic-marker
guards) is in [`examples_upy/06_deep_sleep.py`](../rbamp/examples_upy/06_deep_sleep.py).

> 🛣 **Roadmap.** A future package version (v1.2+) is expected to
> add `dev.warm_open()` — a lightweight context manager without the
> CMD_LATCH_PERIOD primer, which will make it simpler to read
> `read_period_snapshot()` by default after a deep-sleep wake. Until
> then, the pattern above (skip_latch=True + known SLEEP_MS) is
> canonical.

**Energy budget** (ESP32-WROOM on a 2000 mAh Li-ion, 10-minute
interval):

- Active cycle: ~3 s (WiFi + MQTT + I²C) at ~80 mA → ~0.07 mAh per wake
- Sleep: ~10 µA (RTC + retained domains)
- ~10 mAh per day → 2000 mAh lasts ~6 months

CPython hosts (Raspberry Pi etc.) typically run on a 24/7 power
supply — deep sleep doesn't apply. For CPython battery operation,
see the section on power consumption and `systemd` suspend in
[08 · Cloud integrations](08_cloud_integrations.md).

---

## Scenario 10 — Async streaming via `stream_period` (Python-only feature)

**Goal:** integrate into an existing asyncio / uasyncio event loop.
The package provides an async generator `dev.stream_period(interval_s=...)`
that encapsulates the periodic latch plus automatic stale handling.

### CPython (via `asyncio` + `paho-mqtt-asyncio`, or flat)

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
        # Run reader + other async tasks in parallel
        await asyncio.gather(
            reader(dev),
            # other_async_task(),
        )

asyncio.run(main())
```

`stream_period(interval_s=, skip_stale=)` parameters:

- `interval_s` — the period between latches (recommended ≥ 30 s)
- `skip_stale=True` (default) — stale snapshots are automatically
  skipped; the generator simply advances to the next interval
- `skip_stale=False` — stale snapshots are yielded with
  `snap.valid=False`, and the caller decides what to do with them

Full examples — [`examples_upy/05_async_streaming.py`](../rbamp/examples_upy/05_async_streaming.py)
(MicroPython) and the asyncio equivalent in the CPython examples.

---

## Scenario summary table

| # | LOC | Runtime | I²C bus | Period? | DRDY? | MQTT? | Persistence |
|:---:|:---:|---|:---:|:---:|:---:|:---:|---|
| 1 | ~25 | both | dedicated | no | no | no | no |
| 2 | ~35 | both (OLED on uPy) | shared with OLED (uPy) | yes | no | no | RAM |
| 3 | ~50 | both | shared, 3 modules | yes | no | no | RAM |
| 4 | ~50 | both | dedicated | yes | no | yes | retained MQTT |
| 5 | ~40 | both | dedicated | no (RT) | no | no | RAM (master-owned) |
| 6 | ~60 | both | shared, 3 modules | yes | no | yes | retained MQTT |
| 7 | ~40 | both | dedicated | no (RT) | no | no | RAM |
| 8 | ~30 | CPython | dedicated | yes | no | no | rotating-log file |
| 9 | ~50 | MicroPython only | dedicated | yes | no | yes | RTC memory + MQTT |
| 10 | ~25 | both (asyncio/uasyncio) | dedicated | yes | no | (optional) | RAM |

## Production deployment (CPython only)

For a long-running 24/7 deployment on a Linux SBC, a **systemd
service** is recommended. The full example is in
[`examples_cpython/10_systemd_service.py`](../rbamp/examples_cpython/10_systemd_service.py)
(it includes an `--install` helper that generates the
`/etc/systemd/system/rbamp.service` unit file).

Basic structure:

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

To start: `sudo systemctl enable --now rbamp`. Logs: `journalctl -u rbamp -f`.

## What's next

- [07 · DIY integrations](07_diy_integrations.md) — Home Assistant /
  Node-RED / OpenHAB built on these scenarios
- [08 · Cloud integrations](08_cloud_integrations.md) — AWS IoT /
  Azure / GCP / InfluxDB Cloud / generic webhook
- [09 · API Reference](09_api_reference.md) — every public class +
  method + property + exception
- [10 · Troubleshooting](10_troubleshooting.md) — when scenarios
  misbehave on your bench


---

[← Quickstart](05_quickstart.md) | [Contents](README.md) | [DIY Integrations →](07_diy_integrations.md)
