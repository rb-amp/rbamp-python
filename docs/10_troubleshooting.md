# 10 · Troubleshooting

![Troubleshooting decision tree: symptom → quick check → action](images/troubleshoot-flowchart.png)

This chapter is organized by **the symptom you observe**, not by
internal error classification. If you are here, something is not
working right. Find your symptom in the list below, go to the
matching section, and walk through the diagnostic procedure.

Most bench problems fall into three categories:

1. **Bus-level** — the module does not respond over I²C; reads
   raise `RbAmpIOError`.
2. **Data-level** — the module responds, but the numbers are wrong:
   zeros, odd values, wrong sign.
3. **Application-level** — the link is unstable, the Wh counter
   drifts, the script hangs / dies on timeout.

Shortcut for the impatient: in steady state, the public methods
should return values / yield snapshots without raising. If you
frequently see `RbAmpIOError` or `RbAmpStaleError` in the logs, jump
straight to "The module does not respond over I²C" below.

## The module does not respond over I²C

**What you see:** `dev.begin()` (or entering the
`with RbAmp(bus, 0x50) as dev:` context manager) raises
`RbAmpIOError` or `RbAmpVersionError`, or RT reads regularly raise
`RbAmpIOError`.

### Step 1 — Bus scan

First confirm the module is even present on the bus.

### CPython (Linux SBC)

External tool:

```sh
i2cdetect -y 1     # bus 1 — the standard one on the RPi 40-pin header
```

Or Python:

```python
from smbus2 import SMBus

with SMBus(1) as bus:
    print("Scanning...")
    for addr in range(0x08, 0x78):
        try:
            bus.write_quick(addr)
            print(f"Found 0x{addr:02X}")
        except OSError:
            pass
```

Expected output: `Found 0x50` (or another address, if you changed it).

### MicroPython

```python
from machine import I2C, Pin

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
print("Found:", [hex(a) for a in i2c.scan()])
```

Expected output: `Found: ['0x50']` (or your address).

### What to do if nothing is found

The problem is in the wiring or the power:

- SDA / SCL are not swapped (see [04 · Wiring](04_hardware.md) for
  per-host GPIO defaults).
- Both lines have a pull-up to 3.3 V (the module board has built-in
  4.7 kΩ resistors — no external ones are needed for a single module).
- The module's power pin actually reads 5 V (4.5–5.5 V).
- No other master (debug probe, second controller) is hanging on
  the same lines.
- On MicroPython, check that `freq=50_000` (not 100k or 400k — on
  ESP32 + the v1 firmware the baseline NACK pattern will eat the
  retry budget).
- On CPython, check that the kernel I²C driver is enabled —
  `lsmod | grep i2c_bcm` on the RPi.

### What to do if the module shows up at an unexpected address

Someone re-addressed it earlier on the bench. Update the second
argument of `RbAmp`:

```python
dev = RbAmp(bus, 0x52)   # address from the bus scan
```

### Step 2 — MicroPython + ESP32 baseline NACK pattern

If the bus scan finds the module but `dev.read_voltage()`
periodically raises `RbAmpIOError` — and you are on **MicroPython +
ESP32** — this is a documented baseline pattern: the I²C stack on
the ESP32 port of MicroPython inherited quirks of the ESP-IDF v5
driver, which when talking to rbAmp on the current firmware gives a
~20 % NACK rate at 100 kHz.

The mitigation is already built into `MachineI2CBackend` (3 retries ×
5 ms gap by default), but under heavy load the retry budget can run
out.

**What to do on ESP32:**

1. **Lower the bus speed to 50 kHz**:

   ```python
   i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
   ```

2. **Raise the number of retry attempts for a heavy workload** — this
   needs the advanced API via explicit backend creation:

   ```python
   from rbamp._io_micropython import MachineI2CBackend
   from rbamp import RbAmp

   backend = MachineI2CBackend(i2c, retry_attempts=5, retry_gap_ms=5)
   dev = RbAmp(backend, 0x50)   # the address goes to RbAmp, not to the backend
   ```

3. **Monitor the retry-exhaustion counter** — it should stay zero in
   steady state:

   ```python
   if backend.retry_exhaustion_count > 0:
       print("WARN: retry exhausted:", backend.retry_exhaustion_count)
   ```

### Step 3 — Non-ESP32 platforms (RP2040 / STM32 / CPython)

On RP2040 / STM32 / Pyboard (via MicroPython) there is no
baseline NACK pattern — these platforms show a ~0 % NACK rate with
rbAmp. `MachineI2CBackend` auto-detects the port and lowers the
retry default to 1 attempt.

On CPython (via `smbus2`) **there is no retry layer at all** — the
Linux kernel I²C driver is not subject to the NACK pattern; a single
`OSError` from `smbus2` is translated into `RbAmpIOError` without
retry.

If NACKs do happen on these platforms, look for:

- **High bus capacitance** — long wires + many devices. Lower the
  speed or shorten the cable.
- **Master contention** — the package does not support multi-master.
- **A floating SCL** between transactions — a missing pull-up.
- **On the RPi**: check `dtparam=i2c_arm_baudrate` in
  `/boot/config.txt` — 100000 is recommended.

## Current reads zero on a running load

**What you see:** `dev.current[0]` (or `dev.read_current(0)`)
returns `0.000` (or a very small value) even though a real consumer
is switched on (a kettle, a lamp, an iron). `dev.power_factor[0]`
may show an odd value (`nan` / `0` / `±1`) — that is a consequence;
the cause is in the current.

### Diagnostic procedure

1. **Check that the sensor class and the CT model are configured:**

   ```python
   from rbamp import RbAmp, RbAmpSensorClass, RbAmpModeError

   try:
       dev.set_sensor_class(RbAmpSensorClass.SCT_013)
   except RbAmpError as e:
       print("set_sensor_class failed:", e)

   try:
       dev.set_ct_model(3)   # or your model — see the table in 03
   except RbAmpModeError:
       print("Class must be set first!")
   except ValueError:
       print("Code not in per-class accept-set (SCT_013: {1,2,3,4,6})")
   ```

   Without these two calls the calibration coefficients are not
   loaded, and the current always reads as zero. This step is done
   **once** at first installation — the choice is saved to the
   module's flash.

2. **Check that the CT model matches the load.** A large CT clamp
   (for example, SCT-013-100, 100 A) on a small load (a 50 W lamp =
   ~0.2 A) will produce a signal at the edge of the noise floor and
   the readings will be zero. Choose the **smallest** CT model that
   covers your maximum expected current. The full table is in
   [03 · Current sensor selection](03_sensor_selection.md).

   If you have a multi-channel module (UI2 / UI3) and you want to see
   both small loads and peak surges, consider the **dual-CT
   pattern**: a small clamp (SCT-013-005) on a separate channel for
   the low range + a large one (SCT-013-030/100) on another channel
   for the high range; the master picks the value by threshold. The
   pattern is discussed in
   [03 · Current sensor selection](03_sensor_selection.md), the
   "Dual-CT topology" section.

3. **Check the clamp orientation.** The arrow on the clamp body must
   point **in the direction of current flow toward the load**. If
   the clamp is "backwards," `dev.read_current()` will give the
   correct value in absolute terms, but `dev.read_power()` will give
   a negative value on a consuming load. Confirmation —
   `dev.read_power_factor()` will show exactly −1.0 on a resistive
   load (instead of +1.0). Fix: either physically reinstall the
   clamp (unclip it, turn the arrow the right way, clip it back), or
   invert the sign on the application side (`p = -p; pf = -pf;` if you
   know the load is not a PV inverter).

### If all three steps pass and the current is still zero

Then the signal at the ADC really is below the noise floor. Check:

- **Whether current is actually flowing** — measure it with a
  multimeter (a DC clamp / AC clamp meter) on the same wire.
- **Whether the clamp is intact** — there should be an AC voltage at
  the clamp connector proportional to the current (a few millivolts
  for consumer loads).
- **Whether you are clamping the right wire** — the live wire, not
  the neutral (though a clamp on the neutral will also work — it
  measures current amplitude, not direction).

## Readings jump around or raise `RbAmpIOError`

**What you see:** `dev.voltage` / `dev.read_current(...)`
periodically raises `RbAmpIOError`. The property value was not
obtained — you need a retry on the application side or a try/except
guard.

`RbAmpIOError` from the package covers two classes of problem:

- **NACK after retry exhaustion** — the link is unstable.
- **The sanity filter rejected the value** — a float `NaN` / `Inf` /
  `|x| > 10000` (clearly not a physical value) came off the bus.

Tell them apart through the diagnostic counters (available on
MicroPython):

```python
from rbamp._io_micropython import MachineI2CBackend

# Created explicitly at the start of the program
backend = MachineI2CBackend(i2c, 0x50, retry_attempts=5)
dev = RbAmp(backend, 0x50)

# After a period of operation:
print("retries succeeded:", backend.retry_count_total)
print("retries exhausted:", backend.retry_exhaustion_count)
print("sanity rejects:",    dev.sanity_reject_count)
```

| Counter | What it means |
|---|---|
| `retry_count_total` ↑, `retry_exhaustion_count` = 0, `sanity_reject_count` = 0 | The bus is healthy; the package quietly recovers the rare NACKs. Normal. |
| `retry_count_total` ↑, `retry_exhaustion_count` ↑ | NACK plateau — increase `retry_attempts` (see Step 2 above). |
| `retry_count_total` low, `sanity_reject_count` ↑ | The sanity filter is catching garbage after failed retries. Raise `retry_attempts=5+`. |

On CPython (`SMBusBackend`) there is no retry layer — `RbAmpIOError`
is raised on the very first failure. The symptoms are rare (the
Linux kernel is usually reliable), but if they appear, check the
wiring / pull-ups / bus speed.

## Power Factor looks odd

**What you see:** `dev.power_factor[0]` (or
`dev.read_power_factor(0)`) returns a value that does not match the
load type.

Expectations by load:

| Load | Expected PF |
|---|---|
| Kettle, iron, incandescent lamp | +0.95 .. +1.0 (resistive) |
| Refrigerator, motor compressor | +0.6 .. +0.85 (inductive) |
| LED lamp, TV (switching power supply) | +0.5 .. +0.95 (non-linear) |
| PV inverter exporting power | negative PF |

### PF = nan or 0 at I = 0

PF is defined as `P / (U × I)`. At zero current `I=0` the math is
undefined. The exact returned value depends on the firmware (it may
be `nan`, `0`, or a placeholder) — that is normal as long as the
current really is zero. Once current appears, a valid PF appears.

### PF strictly −1.0 on a purely consuming load

The clamp is installed "backwards" — the arrow does not point in the
direction of current flow toward the load. Fix: either reinstall the
clamp correctly, or handle the sign on the application side
(`p = -p; pf = -pf;` if you know the load cannot be a PV inverter).

### PF wanders between +0.3 and +0.7 on a resistive load

- **Possible cause:** the CT clamp is on a wire of a **different
  phase** than the one to which the module's U input is connected. In
  a multi-phase distribution panel it is easy to clamp onto the
  wrong phase by mistake — a phase shift of 120° or 180° between U
  and I will give exactly these PF values. The fix is to install the
  module so that the U input and the CT are on the same phase.
- **Alternative:** the load really is not purely resistive. Repeat
  the test with a known-resistive load (an electric kettle at full
  power).

## Period snapshots are always `RbAmpStaleError`

**What you see:** `dev.read_period_snapshot()` raises
`RbAmpStaleError` on every call. This means the module did not finish
integrating the previous period by the time of the next read.

The package guards against double-counting Wh: on a stale read it
records the master timestamp, and the next successful snapshot covers
one period, not two.

**Acceptable:** rare stale reads (1–2 per hour at a 60 s cadence).
**Not acceptable:** consecutive stale reads — that means the firmware
is unresponsive or the master is polling too often.

### Cadence check procedure

1. **Check the cadence:** 60 s between latches is comfortable; 30 s
   is marginal; < 10 s guarantees stale reads.
2. **Check the module's responsiveness** between snapshots:

   ```python
   if dev.probe():
       print("alive")
   ```

3. **Check the flag directly**:

   ```python
   if dev.is_period_valid():
       # avg_p[] can be read
       ...
   ```

4. **Check the firmware version** — `dev.firmware_version >= 0x04`
   (v1.3) has the fewest stale reads; `0x02`/`0x03` (v1.1/v1.2) are
   still supported but stale-read rate goes up; `0x01` (v1.0) is the
   noisiest.

### Special case — MicroPython deep-sleep wake

If you use a deep-sleep pattern on MicroPython, the **default**
`dev.read_period_snapshot()` after the context-manager entry will
always give a stale (or near-zero) result — `__enter__` does a LATCH
prime that resets the firmware accumulator. The canonical pattern
uses `skip_latch=True` + a known sleep duration (the
`machine.deepsleep(SLEEP_MS)` argument) — see
[06 · Examples](06_examples.md), Scenario 9.

## Wh accounting drifts from the reference

**What you see:** `dev.energy.wh(0)` after several hours / days of
operation drifts from the utility meter reading or from a reference
meter (Kill-A-Watt or similar).

### First rule out the trivial

1. **Current sensor calibration:** make sure
   `dev.set_sensor_class()` and `dev.set_ct_model()` are called with
   the correct CT model (see
   [03 · Current sensor selection](03_sensor_selection.md)). Without
   this, the RMS current is computed with the default floor and the
   power value will be systematically biased.
2. **Dropped stale snapshots:** if `dev.energy.wh(0)` is
   consistently lower than the reference, those may be missed
   intervals — the snapshot came in stale, the package guarded
   against double-count, but the interval measurement was lost. Check
   the cadence (see above).
3. **Master clock drift:** `time.monotonic()` (CPython) and
   `time.ticks_ms()` (MicroPython) are reliable in normal operation.
   But if the master goes into deep sleep, use the RTC memory +
   known sleep duration pattern from Scenario 9.

   > ⚠ **`ticks_ms()` wrap-around on MicroPython.**
   > `time.ticks_ms()` wraps around at `2**30` ms (~12.4 days) — a
   > naive `t1 - t0` subtraction across the 12.4-day boundary gives a
   > negative / huge number. Internally the package uses
   > `time.ticks_diff(t1, t0)`, which is wrap-safe. If you compute
   > `master_dt_ms` yourself in user code (outside the package), use
   > `ticks_diff`, not raw subtraction.

### Accumulator precision

The Wh accumulator inside the handle:

| Runtime | Type | Long-term precision |
|---|---|---|
| CPython | 64-bit `float` (Python `float`) | drift < 1 LSB / year @ 60 s cadence |
| MicroPython (ESP32 N16R8 + double-precision build) | 64-bit `float` | same |
| MicroPython on ports without double-float | 32-bit `float` | ~0.01 % drift per day |

Checking precision on MicroPython (`sys.float_info` exists only on
CPython; on uPy we use an empirical check):

```python
# On CPython:
import sys
print(sys.float_info.dig)   # 15 → double; 7 → single

# On MicroPython — empirically:
import sys
print(sys.implementation.name)   # 'micropython'
test = 1.0 + 1e-15
print("double" if test != 1.0 else "single")   # 1.0 + 1e-15 != 1.0 only on double
```

For single-float MicroPython ports on long-running soak
installations, periodically reset `dev.energy.reset(0)` and store the
long-term total in your own persistent store (for example, via an
MQTT-retain message in HA).

## The script hangs / dies on timeout

**What you see:** the Python script starts up normally, then goes
into a hang / RuntimeError after a few minutes.

### MicroPython@ESP32: an I²C operation hangs on a marginal bus — three-layer mitigation

**What you see specifically:** a script on ESP32 (MicroPython
`machine.I2C`) polls rbAmp fine for several minutes, then hangs
"dead" in the middle of a `dev.read_period_snapshot()` /
`fleet.poll_all()` call. The WDT (if you have one enabled) catches
the hang; otherwise the script simply stops responding. If you have a
REPL, it hangs too.

**What is happening:** MicroPython-on-ESP32 `machine.I2C` wraps the
same IDF i2c_master driver that native ESP-IDF projects use. On a
marginal bus (weak pull-ups, long traces, EMI, ZC noise) `SDA`/`SCL`
can momentarily "stick" in an undefined state. Before the next
transaction, IDF i2c_master spins in an `i2c_ll_is_bus_busy` loop
waiting for the bus to free up — this spin is **not** bounded by the
library's I²C timeout.

**This is not a "debugger-only artifact."** Validated on the bench
(cross-lib, same hardware harness and load):

| Bus configuration | Hang rate |
|---|---|
| 2 weak module pull-ups + debugger NRST attached | ~1 hang / 2.1 min |
| 1 weak module pull-up, no debugger | ~1 hang / 3.3 min (≈ 12 hangs per hour) |
| + external **4.7 kΩ pull-up** on DUT SDA/SCL | **0 hangs** (12-min soak, 0 reboots) |

The correlation is clear — a bus-integrity fix removes the hang
entirely. The library logic is not at fault.

**Three-layer mitigation (apply all three layers):**

1. **External pull-up (~4.7 kΩ) on SDA and SCL** to 3.3 V at a single
   point on the bus. The ESP32 internal pull-ups (~45 kΩ) and the
   ones built into the modules (~10 kΩ with N modules in parallel)
   are too weak for a multi-module bus of real length. Cut the
   built-in ones on all modules except one. See
   [04 · Wiring](04_hardware.md), the "Multi-module bus — the primary
   topology" section.
2. **Do not run a production build with the debugger NRST attached.**
   In a debug session the reset line can hold SDA/SCL in an undefined
   state on detach/attach, provoking a bus wedge on the next
   transaction.
3. **An app-level task-WDT on the polling task** — defense-in-depth.
   **Proper bus pull-ups are the field fix; the task-WDT is a
   recovery posture.**

```python
# MicroPython-on-ESP32
from machine import WDT
import time

wdt = WDT(timeout=8000)   # 8 s

while True:
    snap = dev.read_period_snapshot()
    # ... processing ...
    wdt.feed()             # "I'm alive" — every cycle
    time.sleep_ms(200)
```

**What NOT to do**: do not try to work around the problem with a
larger `I2C(..., timeout=ms)` parameter — it does not bound the
bus-busy pre-spin, that is a different code path in the IDF driver.
What you need to fix is bus integrity (layer 1) + the supervisor
(layer 3).

### CPython@Linux SBC: the kernel I²C does not suffer from this problem

The Linux kernel drivers (`i2c-bcm2835` on the Raspberry Pi,
`i2c-i801` on x86, etc.) do their bus-busy probe **with a timeout** —
on a wedged bus they return `IOError(Errno 110: ETIMEDOUT)` or
`EREMOTEIO` instead of hanging. The library wraps that in
`RbAmpIOError`. Accordingly, **the three-layer mitigation is not
needed on CPython** — Linux handles it itself. An external `~4.7 kΩ`
pull-up is still desirable for signal integrity on a long bus.

### `latch_ms` reads ~27% lower than the real period — this is EXPECTED, not a bug

**What you see:** if, for your own diagnostics, you divide
`snap.latch_ms` (the chip-side software timer) by the real wall-clock
time between two `dev.read_period_snapshot()` calls, the ratio comes
out at **~0.73** (i.e., the chip-side timer undercounts by ~27%).

**This is by design and HW-validated** (bench measure across all 4
sister libs: esp-idf 0.7396, arduino 0.729, python 0.743, esphome
same). The cause is SysTick starvation on the slave: under load the
DMA/ISR leaves too little cycle budget to increment the software
timer regularly. `latch_ms` (register `0xEC`) is intended **only as a
diagnostic indicator** of slave activity, **not for billing**.

> **Canon**: the library integrates energy by **master wall-clock**:
> CPython — `time.monotonic()`; MicroPython — `time.ticks_ms()` +
> `time.ticks_diff()`. The `rbamp` Wh accounting is **mathematically
> exact** — bench measure: `rel_err = 0.0000%` (12.96 Wh lib vs
> 12.96 Wh ground-truth). All 4 sister libs (arduino, esp-idf,
> esphome, python) use the same canon.

If your code relies on `snap.latch_ms` for some sub-period
calculations, switch to your own `time.monotonic()` /
`time.ticks_diff()`. If you simply see the discrepancy in the logs
and are worried — it is normal; remove the discrepancy from your
diagnostics or accept the 27% gap as expected.

### `RbAmpStaleError` on every read — the library keeps the anchor automatically

**What you see:** `dev.read_period_snapshot()` raises
`RbAmpStaleError` periodically.

**Canonical dt-handling**: on a stale read the Python library does
**not** zero out the internal `_last_latch_ms` anchor. The next valid
read gets a `master_dt_ms` equal to the **full** interval since the
previous successful snapshot — i.e., it covers both the "lost" stale
period and the current one. The firmware preserves the `avg_p`
accumulator across an empty latch (firmware-side persistence): on the
next valid latch it is already an averaged value over the whole
interval. The Wh integration **stays exact** — no double-count, no
under-count.

Pattern for cold boot (`prime-until-valid`, ~25 retry budget OK):

```python
from rbamp import RbAmpStaleError

while True:
    try:
        snap = dev.read_period_snapshot()
        break
    except RbAmpStaleError:
        time.sleep(3.0)
```

After priming — just ignore `RbAmpStaleError` if it is rare; if it is
frequent, diagnose the cadence (the "Period snapshots are always
RbAmpStaleError" section above).

### CPython: `signal.SIGTERM` / `KeyboardInterrupt` are not handled

**Cause:** a long `time.sleep(60)` blocks the signal handler. In a
production deploy (systemd), `systemctl stop rbamp` waits for the
SIGTERM response up to a timeout (default 90 s) and then kills it
with SIGKILL — Wh can be lost.

**Fix:** a signal-aware loop:

```python
import signal, time
shutdown = False

def on_sig(sig, frame):
    global shutdown
    shutdown = True

signal.signal(signal.SIGTERM, on_sig)
signal.signal(signal.SIGINT,  on_sig)

while not shutdown:
    snap = dev.read_period_snapshot()
    # ... publish ...
    for _ in range(60):           # wake on signal every second
        if shutdown: break
        time.sleep(1)
```

Or use `asyncio` with a `signal_handler` — see the section below.

### MicroPython (ESP32): Watchdog timeout on WiFi connect

**Cause:** an unbounded WiFi connection loop triggers the task-WDT
after ~5 s (the default).

**Fix:** a bounded wait with a restart fallback:

```python
import network, time, machine

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect("ssid", "pass")

t0 = time.ticks_ms()
while not wlan.isconnected():
    time.sleep_ms(100)
    if time.ticks_diff(time.ticks_ms(), t0) > 30_000:
        machine.reset()
```

### The MQTT broker disconnects

**On CPython (`paho-mqtt`):** the default keepalive is 60 s, but
`mqtt.connect()` is blocking and `mqtt.loop()` must be called in an
event loop. Use `mqtt.loop_start()` (a background thread) or the
asyncio variant `aiomqtt`.

**On MicroPython (`umqtt.simple`):** sync with no keepalive — you
need to call `mqtt.ping()` manually every ~30 s. The alternative is
the async `mqtt_as` package with built-in keepalive.

### TLS handshake fail (CPython cloud + RPi with little RAM)

Rarely a problem on a 1+ GB RAM Pi, but on a Pi Zero / Zero W
(512 MB) a TLS handshake with a large cert store can be killed by the
OOM killer.

**Fix:**
- Use a specific CA instead of the system `ca-certificates` — less
  memory.
- Reduce the MQTT buffers.
- Move to a Pi 3/4/5 for a production cloud deploy.

## `set_sensor_class()` / `set_ct_model()` raises `RbAmpModeError`

**What you see:** one of the configuration calls raises
`RbAmpModeError` with the message:

```text
REG_SENSOR_CLASS is UNSET;
call dev.set_sensor_class(RbAmpSensorClass.SCT_013) first
```

**Cause:** `set_ct_model[_ch]` requires `set_sensor_class()` to be
called before it. This is an intentional guard.

**What to do:**

```python
from rbamp import RbAmpSensorClass

# Class first — MANDATORY
dev.set_sensor_class(RbAmpSensorClass.SCT_013)

# Then the model
dev.set_ct_model(3)   # or per-channel dev.set_ct_model_ch(ch, code)
```

More detail — [03 · Current sensor selection](03_sensor_selection.md)
and [09 · API reference](09_api_reference.md), the "Sensor
configuration" section.

`RbAmpModeError` also arises from `dev.factory_reset()` and
`dev.save_gains()` (factory operations require develop mode). An
address change on v1.3 is production-OK and does not raise
`RbAmpModeError`.

## `set_sensor_class()` / `set_ct_model*()` raises `RbAmpParamError` or `ValueError`

**What you see:** the call raises `RbAmpParamError` (on CPython it
also inherits `ValueError` via multi-base).

Possible causes:

1. **Invalid model code** — for the `SCT_013` class the accepted set is
   `{1, 2, 3, 4, 6}` (non-contiguous; code 5 is reserved in v1.3
   firmware, formerly SCT-013-100; code 7 is uncharacterized). For other
   classes, see the per-class accept-set table in
   [03](03_sensor_selection.md#per-class-ct-validation). Values outside
   the accept-set raise `RbAmpParamError(ValueError)`.
2. **Invalid channel index** in the per-channel form
   `dev.set_ct_model_ch(channel, code)` — it must be < `dev.channels`.
3. **A reserved `RbAmpSensorClass` value** —
   `RbAmpSensorClass.WIRED_CT` or `BUILTIN_CT` are not yet supported
   (not present on the current SKU); use `SCT_013`.

## Error handling: `REG_ERROR` last-write + `EVENT_FLAGS.bit3` durable (v1.3)

**v1.3 canon (HW-confirmed)**: `REG_ERROR (0x02)` is the
**last-write-outcome**, **not** sticky. On each register write the
firmware sets `reg_error = ERR_OK`; a subsequent unrelated write
**clears** the previous error.

**The durable error signal** = `EVENT_FLAGS (0x2A) bit3` — sticky
(W1C), asserted immediately on any rejected write/command.

**Pattern 1 — one-off post-operation capture** (the outcome of
exactly this operation):

```python
dev.save_user_config()
dev_err = dev.read_reg(0x02)   # IMMEDIATELY, before the next write
```

**Pattern 2 — durable monitoring** (canonical for long-running
operation):

```python
event_flags = dev.read_reg(0x2A)
if event_flags & (1 << 3):
    last_err = dev.read_reg(0x02)
    # handle it
    dev.write_reg(0x2A, (1 << 3))   # W1C clear bit3
    # or dev.clear_error()
```

> ⚠ **REVERSAL** of the early "sticky" documentation (v1.3 A3 root
> canon). REG_ERROR is the last-write-outcome, not sticky. Durability
> is provided by EVENT bit3.

> ⚠ **bit3 is an async channel, not for synchronous write
> validation**: on a rejected write, bit3 is set **with a delay**
> relative to the moment the I²C transaction returns (~200-300 ms).
> Do not poll bit3 right after a write to check that write's outcome —
> for the outcome of **your own write** use **Pattern 1** (one-off
> `read_last_error()` capture, immediate) or the `RbAmpParamError`
> raised by the setter. `bit3` is for long-running monitoring of
> async facts (runtime, the command path), not for validating an
> operation you just performed.

**Caveat:** `DEV_ERR_CLONE (0xF9)` is **not** cleared by
`CMD_CLEAR_ERROR` — it is an anti-clone sentinel.

## A fresh-flashed module shows `0xFB` on first boot (NORMAL)

**What you see:** a freshly flashed module on first boot shows
`REG_ERROR = 0xFB` (`DEV_ERR_FLASH_PARAMS_BAD`) + EVENT bit3.

**Cause:** the params page is uninitialized → factory defaults
loaded. This is **NORMAL** for a virgin module, not fatal.

**Fix:** configure sensor_class + ct_model + (optionally) the
address, then `dev.save_user_config()`. After a successful SAVE →
`REG_ERROR = 0x00`, bit3 clear.

## Reads fail while switching relay loads (EMI transients)

**What you see:** `dev.voltage` / `dev.current()` periodically raise
`RbAmpIOError` / `OSError` — but **only during active switching** of
a relay/inductive load on the same setup. Steady-state reads are
reliable.

**Cause (C.12 HW-confirmed)**: during relay switching (a 5A load),
EMI fails ~67% of I²C transactions. The bus self-recovers, **no
wedge occurs**, and no module is lost.

**Fix — application-level retry**:

```python
def robust_read_voltage(dev, attempts=5, gap_s=0.020):
    for _ in range(attempts):
        try:
            return dev.voltage
        except (RbAmpIOError, OSError):
            time.sleep(gap_s)
    raise RbAmpIOError("read failed after retries")
```

> ⚠ **The backend retry alone is not enough**: when an EMI glitch
> outlasts the backend's retry window, you need an explicit
> application-level retry with a pause.

## `commit_address_change()` raises `RbAmpIOError` after a bus reset

**What you see:** `prepare_address_change()` succeeded, but after the
commit + reset the module does not respond at the new address.

> ✅ **v1.3 (Fix 4)**: `REG_I2C_ADDRESS (0x30)` reads the **active**
> address on boot. After the post-commit reboot, reading 0x30 at the
> new address will show that same address — this confirms the commit
> went through. If the module does not respond at the new address,
> try a bus scan via `_io_smbus` (it will show which address the
> module is actually at).

More on the two-phase commit — see
[09 · API reference](09_api_reference.md) and
[04 · Wiring](04_hardware.md).

## `commit_address_change()` raises `RbAmpTimeoutError`

If `prepare` succeeded but `commit` raises `RbAmpTimeoutError`, the
"arming" window expired (5 seconds after `prepare`). `dev.probe()`
**will not help** here (the module responds; the problem is in the
module's state machine). The fix is to call
`dev.prepare_address_change(new_addr)` again and then immediately,
**in the same function/iteration**, with no network calls between
them, call `dev.commit_address_change()`. Any blocking I/O between
`prepare` and `commit` (WiFi, MQTT, HTTP) is the main cause of the
window expiring.

### `wait_ready()` raises `RbAmpTimeoutError`

The module's ready bit was not set within `timeout_ms`. Possible
causes:

- The module has not finished its cold start (~250 ms) — increase
  `timeout_ms` or repeat the call.
- The module does not respond over I²C — see the "The module does not
  respond over I²C" section above.
- The supply level dropped below 4.5 V — check `VCC` with a
  multimeter.

More on the public-but-warned methods — see
[09 · API reference](09_api_reference.md), the "Sensor configuration"
section (the address-change subsection lives there together with
`save_gains` and `factory_reset`).

## Exception summary table

`RbAmpError` (a subclass of **`OSError`**) is the base class. An
existing `except OSError:` handler on the application side
**continues to catch** rbAmp errors without changes — the standard
Python convention for hardware-I/O drivers.

| Exception | When | Where to look |
|---|---|---|
| `RbAmpIOError` | NACK after retry; sanity reject; bus-level failure | the "The module does not respond over I²C" section |
| `RbAmpTimeoutError` | `wait_ready()` expired; the `commit_address_change` window (5 s) expired | the "`commit_address_change` returns `RbAmpTimeoutError`" section below |
| `RbAmpNotReadyError` | (reserved; not raised in v1.1.0, exported for forward-compat) | if you catch this type — repro it in an issue |
| `RbAmpStaleError` | period snapshot stale | the "Period snapshots are always `RbAmpStaleError`" section |
| `RbAmpParamError` (on CPython also `ValueError`) | bad argument: `dev=None`, `ch` out of range, `code` not in the per-class accept-set (for `SCT_013` — `{1, 2, 3, 4, 6}`, code 5 reserved), a reserved `cls`. Also a precondition violation: `set_ct_model*()` without a preceding `set_sensor_class()` | check the call arguments + that `set_sensor_class()` was called |
| `RbAmpModeError` | develop mode not set (factory_reset / save_gains; an address change on v1.3 is production-OK) | check the mode flag |
| `RbAmpVersionError` | `REG_VERSION` = 0/0xFF on `begin()` (the module does not respond or the firmware is corrupted). **Per-channel `set_ct_model_ch` on v1.0/v1.1 may NACK** (the opcode does not exist) and raise `RbAmpIOError`, not `RbAmpVersionError` — the version guard is NOT implemented client-side; check `dev.firmware_version` ≥ 0x03 before the call | check `dev.firmware_version` |

All exceptions have a meaningful `__str__` message — the package
carefully constructs error messages with context.

## Diagnostic counter summary table

In a healthy soak run (the 1-hour long-soak harness) they all stay
**zero**:

| Counter | Steady-state | Reset | Availability |
|---|---|---|---|
| `dev.sanity_reject_count` | 0 | `dev.reset_counters()` | both runtimes |
| `MachineI2CBackend.retry_exhaustion_count` | 0 | `backend.reset_counters()` | MicroPython only |
| `MachineI2CBackend.retry_count_total` | low (~5-20 per hour is normal) | `backend.reset_counters()` | MicroPython only |
| stale fraction in period snapshots | < 1 % | (cumulative — no reset) | both runtimes |

If any of these is non-zero in steady state, go back to the
corresponding section above.

> The long-soak harness `tests/test_long_soak.py` is run via
> `pytest libs/python/rbamp/tests/test_long_soak.py --soak --bus N --addr 0x50`.
> Its six acceptance criteria — see the "Long-soak regression
> harness" section in README.md.

## Bus-level debug with a logic analyzer

For deep debugging, when the package can no longer tell you what is
happening on the wire, capture SDA + SCL with a logic analyzer
(Saleae, DSLogic Plus, anything Sigrok-compatible):

- Sample rate ≥ 1 MS/s at 100 kHz I²C; ≥ 4 MS/s at 400 kHz.
- The I²C decoder in Sigrok / Saleae will show ACK / NACK for each
  byte + the address phase.
- Compare your script's calls (`dev.voltage`, etc.) against the
  expected byte sequence — they should match exactly. Reads are
  burst-OK (auto-increment). Writes are NOT auto-increment
  (byte-at-a-time for multi-byte registers).

If the package's behavior diverges from the capture, open an issue
with the capture file attached (`.sal` / `.dsl` / `.csv`).

## When to contact support

If you have gone through the matching section above and the problem
persists, open an issue:

[github.com/rb-amp/rbamp-python/issues](https://github.com/rb-amp/rbamp-python/issues)

In the issue include:

- **Runtime + version**: `python --version` /
  `mpremote eval 'import sys; print(sys.version, sys.implementation)'`.
- **Host platform**: "RPi 4B Bookworm", "ESP32-S3 N16R8 MicroPython
  1.22", etc.
- **Package version**: `rbamp --version` (CPython) or
  `python -c "import rbamp; print(rbamp.__version__)"`.
- **Module firmware version** — `dev.firmware_version` from the logs.
- **A minimal script** (~30 LOC) that reproduces the problem.
- **The exception traceback** with full context.
- **The counters** at the moment of failure:
  `dev.sanity_reject_count`, `backend.retry_exhaustion_count`
  (uPy only).
- **The library's verbose logs** — the simplest way to collect them
  is `dev.set_logger(print)` before the operation that fails. The
  package will print its internal steps (the retry loop, settle
  timeouts, the latch primer, etc.) — attach the stdout to the issue.
- **(If available)** the logic-analyzer capture file.

## CLI — `rbamp scan` / `rbamp read` / `rbamp address`

The `rbamp` CLI is a thin wrapper over `RbAmp.*` for bench operations
(see [09 · API reference](09_api_reference.md), the "CLI" section).
Because it uses the same `RbAmp` class, ALL of the symptoms above
apply. CLI-specific errors:

- **`rbamp scan` finds nothing** — the same as in Step 1 of the "The
  module does not respond over I²C" section above. Check
  `i2cdetect -y 1` directly. If it finds the module, it is an issue
  in our backend autodetect; open an issue with the output of
  `rbamp --verbose scan`.
- **`rbamp <subcommand>` fails with `PermissionError: /dev/i2c-1`** —
  the user is not in the `i2c` group. See README § Installation for
  `sudo usermod -aG i2c $USER` + re-login.
- **`rbamp: command not found`** after `pip install` — the
  `[project.scripts]` console entry is not active. Check that
  `pip show rbamp` shows the installed location; make sure
  `$HOME/.local/bin` is in `$PATH` (for `pip install --user`).

## Links

- [05 · Quickstart](05_quickstart.md) — your first working script
- [09 · API reference](09_api_reference.md) — the full API + warnings
  on the public-with-WARNING methods
- [03 · Current sensor selection](03_sensor_selection.md) — the table
  of SCT-013 models, the dual-CT topology for a wide dynamic range,
  approaches to boosting sensitivity at small currents

