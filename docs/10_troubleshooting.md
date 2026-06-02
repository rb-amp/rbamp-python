# 10 · Troubleshooting

This chapter is organized around **the symptoms you actually see**,
not around an internal error taxonomy. If you're here, something
isn't working right. Find your symptom in the list below, jump to
the matching section, and follow the diagnostic procedure.

Most bench problems fall into three categories:

1. **Bus-level** — the module doesn't answer over I²C, and reads
   raise `RbAmpIOError`.
2. **Data-level** — the module answers, but the numbers are wrong:
   zeros, odd values, the wrong sign.
3. **Application-level** — the link is unstable, the Wh counter
   drifts, or the script hangs / dies on a timeout.

Shortcut for the impatient: in steady state, the public methods
should return values / yield snapshots without raising. If you see
`RbAmpIOError` or `RbAmpStaleError` frequently in the logs, jump
straight to the "Module doesn't answer over I²C" section below.

## Module doesn't answer over I²C

**What you see:** `dev.begin()` (or entering the
`with RbAmp(bus, 0x50) as dev:` context manager) raises
`RbAmpIOError` or `RbAmpVersionError`, or RT reads regularly raise
`RbAmpIOError`.

### Step 1 — Bus scan

First, confirm the module is even present on the bus.

### CPython (Linux SBC)

External tool:

```sh
i2cdetect -y 1     # bus 1 — the standard one on the RPi 40-pin header
```

Or in Python:

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

It's a wiring or power problem:

- SDA / SCL aren't swapped (see [04 · Hardware connection](04_hardware.md)
  for per-host GPIO defaults).
- Both lines have a pull-up to 3.3 V (the module board has built-in
  4.7 kΩ resistors — no externals needed for a single module).
- The module's supply pin really has 5 V on it (4.5–5.5 V).
- No other master (debug probe, second controller) is hanging on
  the same lines.
- On MicroPython, check that `freq=50_000` (not 100k or 400k — on
  ESP32 with v1 firmware the baseline NACK pattern will eat your
  retry budget).
- On CPython, check that the kernel I²C driver is loaded —
  `lsmod | grep i2c_bcm` on RPi.

### What to do if the module shows up at an unexpected address

Someone re-addressed it earlier on the bench. Update the second
argument to `RbAmp`:

```python
dev = RbAmp(bus, 0x52)   # address from the bus scan
```

### Step 2 — MicroPython + ESP32 baseline NACK pattern

If the bus scan finds the module but `dev.read_voltage()`
occasionally raises `RbAmpIOError` — and you're on **MicroPython +
ESP32** — this is a documented baseline pattern: the I²C stack on
the ESP32 port of MicroPython inherits quirks from the ESP-IDF v5
driver, which, when talking to rbAmp on the current firmware,
produces a ~20 % NACK rate at 100 kHz.

The mitigation is already built into `MachineI2CBackend` (3 retries
× 5 ms gap by default), but under heavy workloads the retry budget
can run out.

**What to do on ESP32:**

1. **Lower the bus speed to 50 kHz:**

   ```python
   i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
   ```

2. **Raise the retry count for a heavy workload** — this needs the
   advanced API, by creating the backend explicitly:

   ```python
   from rbamp._io_micropython import MachineI2CBackend
   from rbamp import RbAmp

   backend = MachineI2CBackend(i2c, retry_attempts=5, retry_gap_ms=5)
   dev = RbAmp(backend, 0x50)   # the address goes to RbAmp, not to the backend
   ```

3. **Monitor the retry-exhaustion counter** — it should stay zero
   in steady state:

   ```python
   if backend.retry_exhaustion_count > 0:
       print("WARN: retry exhausted:", backend.retry_exhaustion_count)
   ```

### Step 3 — Non-ESP32 platforms (RP2040 / STM32 / CPython)

On RP2040 / STM32 / Pyboard (via MicroPython) there is no
baseline-NACK pattern — these platforms show a ~0 % NACK rate with
rbAmp. `MachineI2CBackend` auto-detects the port and lowers the
retry default to a single attempt.

On CPython (via `smbus2`) **there is no retry layer at all** — the
Linux kernel I²C driver isn't subject to the NACK pattern; a single
`OSError` from `smbus2` is translated into `RbAmpIOError` with no
retry.

If NACKs do occur on these platforms, look for:

- **High bus capacitance** — long wires plus many devices. Lower
  the speed or shorten the cable.
- **Master contention** — the package does not support multi-master.
- **Floating SCL** between transactions — a missing pull-up.
- **On RPi:** check `dtparam=i2c_arm_baudrate` in `/boot/config.txt`
  — 100000 is recommended.

## Current reads zero on a working load

**What you see:** `dev.current[0]` (or `dev.read_current(0)`)
returns `0.000` (or a very small value), even though a real
consumer is on (a kettle, a lamp, an iron). `dev.power_factor[0]`
may show an odd value (`nan` / `0` / `±1`) — that's a side effect;
the root cause is the current.

### Diagnostic procedure

1. **Check that the sensor class and CT model are configured:**

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
       print("Code out of range 1..5")
   ```

   On v1.2+ firmware, without these two calls the calibration
   coefficients aren't loaded, and current always reads as zero.
   This step is done **once** at first install — the choice is
   stored in the module's flash.

2. **Check that the CT model matches the load.** A large CT clamp
   (for example SCT-013-100, 100 A) on a small load (a 50 W lamp =
   ~0.2 A) produces a signal right at the noise floor, and the
   readings will be zero. Pick the **smallest** CT model that
   covers your maximum expected current. The full table is in
   [03 · Current sensor selection](03_sensor_selection.md).

   If you have a multi-channel module (UI2 / UI3) and want to see
   both small loads and peak spikes, consider the **dual-CT
   pattern**: a small clamp (SCT-013-005) on one channel for the
   low range plus a large one (SCT-013-030/100) on another channel
   for the high range; the master picks the value by a threshold.
   The pattern is discussed in
   [03 · Current sensor selection](03_sensor_selection.md), the
   "Dual-CT topology" section.

3. **Check the clamp orientation.** The arrow on the clamp body
   should point **in the direction of current flow toward the
   load**. If the clamp is "backwards," `dev.read_current()` gives
   the right value in absolute terms, but `dev.read_power()`
   returns a negative value on a consuming load. Confirmation:
   `dev.read_power_factor()` will read exactly −1.0 on a resistive
   load (instead of +1.0). The fix is either to physically
   reinstall the clamp (unclip it, flip it so the arrow points
   correctly, clip it back) or to invert the sign on the
   application side (`p = -p; pf = -pf;` if you know the load isn't
   a PV inverter).

### If all three steps pass but the current is still zero

Then the signal at the ADC really is below the noise floor. Check:

- **Whether current is actually flowing** — measure with a
  multimeter (DC clamp / AC clamp meter) on the same wire.
- **Whether the clamp is intact** — its connector should carry an
  AC voltage proportional to the current (a few millivolts for
  consumer loads).
- **Whether you're clamping the right wire** — the line (phase)
  conductor, not the neutral (although a clamp on the neutral will
  also work — it measures current amplitude, not direction).

## Readings jump around or raise `RbAmpIOError`

**What you see:** `dev.voltage` / `dev.read_current(...)`
occasionally raises `RbAmpIOError`. The property value wasn't
obtained — you need an application-side retry or a try/except guard.

`RbAmpIOError` from the package covers two classes of problem:

- **NACK after retry exhaustion** — the link is unstable.
- **The sanity filter rejected a value** — a float `NaN` / `Inf` /
  `|x| > 10000` came off the bus (clearly not a physical value).

Tell them apart via the diagnostic counters (available on MicroPython):

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
| `retry_count_total` ↑, `retry_exhaustion_count` = 0, `sanity_reject_count` = 0 | Bus is healthy; the package quietly recovers the occasional NACK. Normal. |
| `retry_count_total` ↑, `retry_exhaustion_count` ↑ | NACK plateau — increase `retry_attempts` (see Step 2 above). |
| `retry_count_total` low, `sanity_reject_count` ↑ | The sanity filter is catching garbage after failed retries. Raise `retry_attempts=5+`. |

On CPython (`SMBusBackend`) there is no retry layer — `RbAmpIOError`
is raised on the very first failure. The symptoms are rare (the
Linux kernel is usually reliable), but when they appear, check the
wiring / pull-ups / bus speed.

## Power Factor looks wrong

**What you see:** `dev.power_factor[0]` (or
`dev.read_power_factor(0)`) returns a value that doesn't match the
load type.

Expectations by load:

| Load | Expected PF |
|---|---|
| Kettle, iron, incandescent lamp | +0.95 .. +1.0 (resistive) |
| Refrigerator, compressor motor | +0.6 .. +0.85 (inductive) |
| LED lamp, TV (switch-mode PSU) | +0.5 .. +0.95 (nonlinear) |
| PV inverter exporting power | negative PF |

### PF = nan or 0 when I = 0

PF is defined as `P / (U × I)`. At zero current `I=0`, the math is
undefined. The exact value returned depends on the firmware (it may
be `nan`, `0`, or a placeholder) — that's normal as long as the
current really is zero. Once current appears, a valid PF appears.

### PF is exactly −1.0 on a purely consuming load

The clamp is installed "backwards" — the arrow points away from the
direction of current flow to the load. The fix is either to
reinstall the clamp correctly or to handle the sign on the
application side (`p = -p; pf = -pf;` if you know the load can't be
a PV inverter).

### PF floats between +0.3 and +0.7 on a resistive load

- **Possible cause:** the voltage reference is taken from a
  different phase. This applies to split-phase (240 V in the US)
  and 3-phase grids, where the module takes U from one phase while
  the CT clamp hangs on another — a 120° or 180° phase shift
  between U and I yields exactly these PF values. The fix is to
  install the module so the U input and the CT are on the same phase.
- **Alternative:** the load really isn't purely resistive. Repeat
  the test with a known-resistive load (an electric kettle at full
  power).

## Period snapshots are always `RbAmpStaleError`

**What you see:** `dev.read_period_snapshot()` raises
`RbAmpStaleError` on every call. This means the module hasn't
finished integrating the previous period by the time of the next read.

The package protects against double-counting Wh: on a stale read it
records the master timestamp, so the next successful snapshot covers
one period, not two.

**Acceptable:** rare stales (1–2 per hour at a 60 s cadence).
**Not acceptable:** consecutive stales — that means the firmware is
unresponsive or the master is polling too often.

### Cadence-check procedure

1. **Check the cadence:** 60 s between latches is comfortable; 30 s
   is marginal; < 10 s guarantees stales.
2. **Check the module's responsiveness** between snapshots:

   ```python
   if dev.probe():
       print("alive")
   ```

3. **Check the flag directly:**

   ```python
   if dev.is_period_valid():
       # safe to read avg_p[]
       ...
   ```

4. **Check the firmware version** — `dev.firmware_version >= 0x02`
   shows fewer stales than 0x01.

### Special case — MicroPython deep-sleep wake

If you use a deep-sleep pattern on MicroPython, the **default**
`dev.read_period_snapshot()` after a context-manager entry will
always be stale (or give near-zero values) — `__enter__` issues a
priming LATCH that resets the firmware accumulator. The canonical
pattern uses `skip_latch=True` plus a known sleep duration (the
`machine.deepsleep(SLEEP_MS)` argument) — see
[06 · Examples](06_examples.md), Scenario 9.

## Wh accounting drifts from a reference

**What you see:** `dev.energy.wh(0)`, after several hours / days of
operation, diverges from the utility meter or a reference meter
(Kill-A-Watt, etc.).

### First, rule out the trivial

1. **Current sensor calibration:** confirm that
   `dev.set_sensor_class()` and `dev.set_ct_model()` were called
   with the correct CT model (see
   [03 · Current sensor selection](03_sensor_selection.md)).
   Without this, RMS current is computed against the default floor
   and the power value will be systematically biased.
2. **Dropped stale snapshots:** if `dev.energy.wh(0)` is
   consistently below the reference, those may be missed intervals
   — the snapshot came back stale, the package protected against
   double-counting, but the interval measurement was lost. Check
   the cadence (see above).
3. **Master clock drift:** `time.monotonic()` (CPython) and
   `time.ticks_ms()` (MicroPython) are reliable under normal
   operation. But if the master goes into deep sleep, use the
   RTC-memory + known-sleep-duration pattern from Scenario 9.

   > ⚠ **`ticks_ms()` wrap-around on MicroPython.**
   > `time.ticks_ms()` wraps at `2**30` ms (~12.4 days) — a naïve
   > `t1 - t0` subtraction across the 12.4-day boundary gives a
   > negative / huge number. Internally the package uses
   > `time.ticks_diff(t1, t0)`, which is wrap-safe. If you compute
   > `master_dt_ms` yourself in user code (outside the package),
   > use `ticks_diff`, not raw subtraction.

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

For single-float MicroPython ports on long soak installs,
periodically call `dev.energy.reset(0)` and store the long-term
total in your own persistent store (for example, via MQTT-retain in HA).

## The script hangs / dies on a timeout

**What you see:** the Python script starts normally, then goes into
a hang / RuntimeError after a few minutes.

### CPython: `signal.SIGTERM` / `KeyboardInterrupt` not handled

**Cause:** a long `time.sleep(60)` blocks the signal handler. In a
production deploy (systemd), `systemctl stop rbamp` waits for the
SIGTERM response timeout (90 s default) and then kills via SIGKILL
— Wh can be lost.

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
    for _ in range(60):           # wake on the signal every second
        if shutdown: break
        time.sleep(1)
```

Or use `asyncio` with a `signal_handler` — see the section below.

### MicroPython (ESP32): Watchdog timeout on WiFi connect

**Cause:** an unbounded WiFi connection loop trips the task-WDT
after ~5 s (default).

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

**On MicroPython (`umqtt.simple`):** synchronous, with no keepalive
— you have to call `mqtt.ping()` manually every ~30 s. The
alternative is the async `mqtt_as` package with built-in keepalive.

### TLS handshake fails (CPython cloud + RPi with little RAM)

Rarely a problem on 1+ GB RAM Pis, but on a Pi Zero / Zero W
(512 MB) the large cert-store TLS handshake can get the process
killed by the OOM killer.

**Fix:**
- Use a specific CA instead of the system `ca-certificates` — less
  memory.
- Reduce the MQTT buffers.
- Move to a Pi 3/4/5 for a production cloud deploy.

## `set_sensor_class()` / `set_ct_model()` raises `RbAmpModeError`

**What you see:** one of the setup calls raises `RbAmpModeError`
with the message:

```text
REG_SENSOR_CLASS is UNSET on v1.2+ firmware;
call dev.set_sensor_class(RbAmpSensorClass.SCT_013) first
```

**Cause:** on v1.2+ firmware, `set_ct_model[_ch]` requires that
`set_sensor_class()` has been called before it. This is an
intentional guard.

**What to do:**

```python
from rbamp import RbAmpSensorClass

# Class first — MANDATORY
dev.set_sensor_class(RbAmpSensorClass.SCT_013)

# Then the model
dev.set_ct_model(3)   # or per-channel dev.set_ct_model_ch(ch, code)
```

More detail in [03 · Current sensor selection](03_sensor_selection.md)
and [09 · API reference](09_api_reference.md), the "Sensor
configuration" section.

`RbAmpModeError` is also raised from `prepare_address_change()` /
`commit_address_change()` when the module isn't in develop mode — a
different situation, see the next item.

## `set_sensor_class()` / `set_ct_model*()` raises `RbAmpParamError` or `ValueError`

**What you see:** the call raises `RbAmpParamError` (on CPython it
also inherits `ValueError` through multi-base).

Possible causes:

1. **Invalid model code** — the valid range is 1..5 (see the table
   in [03](03_sensor_selection.md)). Values 0 and 6+ raise
   `RbAmpParamError(ValueError)`.
2. **Invalid channel index** in the per-channel form
   `dev.set_ct_model_ch(channel, code)` — it must be < `dev.channels`.
3. **A reserved `RbAmpSensorClass` value** —
   `RbAmpSensorClass.WIRED_CT` or `BUILTIN_CT` aren't supported yet
   (not present on the current SKU); use `SCT_013`.

## `set_ct_model_ch()` raises `RbAmpVersionError`

**What you see:** the per-channel form raises `RbAmpVersionError`.

**Cause:** the module's firmware is too old for per-channel commands
(the `CMD_SET_CT_MODEL_CH0/1/2` opcodes appeared in v1.2). On v1.0 /
v1.1 this command doesn't exist.

**What to do:**

- **Check the version:** `dev.firmware_version` should return
  `0x03` or higher for the per-channel form.
- **Use the legacy single-arg form** `dev.set_ct_model(code)` — it
  works on every firmware version, but only configures channel 0.
- **Update the module firmware** to v1.2+ if you need full
  per-channel configuration.

## `prepare/commit_address_change()` raises `RbAmpModeError`

**What you see:** the I²C-address-change methods raise
`RbAmpModeError`.

> ⚠ **Develop-mode-only operation.** Changing the address requires
> the module to be in develop mode (an internal flag, set at the
> factory). On a standard production module this flag is **not
> set**, and these methods raise `RbAmpModeError` — that's expected
> behavior, not a bug. The
> `dev.prepare_address_change()` + `dev.commit_address_change()`
> method pair is intended for factory provisioning and integrator
> bench operations, not for user code. If a deployed module needs a
> different I²C address, the documented path is reconfiguration on
> the factory bench (outside the package's responsibility).

### `commit_address_change()` raises `RbAmpTimeoutError`

If you have a module with develop mode enabled and `prepare`
succeeded, but `commit` raises `RbAmpTimeoutError`, the "arming"
window has expired (5 seconds after `prepare`). `dev.probe()`
**won't help** here (the module answers; the problem is in the
module's state machine). The fix is to call
`dev.prepare_address_change(new_addr)` again and immediately, **in
the same function/iteration**, with no network calls in between,
call `dev.commit_address_change()`. Any blocking I/O between
`prepare` and `commit` (WiFi, MQTT, HTTP) is the main cause of
window expiry.

### `wait_ready()` raises `RbAmpTimeoutError`

The module's ready bit wasn't set within `timeout_ms`. Possible
causes:

- The module hasn't finished its cold start yet (~250 ms) —
  increase `timeout_ms` or retry the call.
- The module doesn't answer over I²C — see the "Module doesn't
  answer over I²C" section above.
- The supply level dropped below 4.5 V — check `VCC` with a
  multimeter.

For more on the public-with-warning methods, see
[09 · API reference](09_api_reference.md), the "Sensor
configuration" section (the address-change subsection lives there
along with `save_gains` and `factory_reset`).

## Exception summary table

`RbAmpError` (a subclass of **`OSError`**) is the base class. An
existing `except OSError:` handler on the application side **keeps
catching** rbAmp errors with no changes — the standard Python
convention for hardware-I/O drivers.

| Exception | When | Where to look |
|---|---|---|
| `RbAmpIOError` | NACK after retry; sanity reject; bus-level failure | "Module doesn't answer over I²C" section |
| `RbAmpTimeoutError` | `wait_ready()` expired; the `commit_address_change` window (5 s) expired | "`commit_address_change` raises `RbAmpTimeoutError`" section below |
| `RbAmpNotReadyError` | (reserved; not raised in v1.1.0, exported for forward-compat) | if you catch this type — repro it in an issue |
| `RbAmpStaleError` | period snapshot stale | "Period snapshots are always `RbAmpStaleError`" section |
| `RbAmpParamError` (on CPython also `ValueError`) | bad argument: `dev=None`, `ch` out of range, `code` outside 1..5, a reserved `cls`. On v1.2+ also a precondition violation: `set_ct_model*()` without a preceding `set_sensor_class()` | check the call arguments; on v1.2+ — that `set_sensor_class()` was called |
| `RbAmpModeError` | develop mode not set for an address change | "`prepare_address_change` / `commit_address_change` raises `RbAmpModeError`" section |
| `RbAmpVersionError` | `REG_VERSION` = 0/0xFF on `begin()` (the module doesn't answer or the firmware is corrupt). **Per-channel `set_ct_model_ch` on v1.0/v1.1 may NACK** (the opcode doesn't exist) and raise `RbAmpIOError`, not `RbAmpVersionError` — the version guard is NOT implemented client-side; check `dev.firmware_version` ≥ 0x03 before calling | check `dev.firmware_version` |

Every exception has a `__str__` with a meaningful message — the
package carefully constructs error messages with context.

## Diagnostic counters summary table

In a healthy soak run (the 1-hour long-soak harness) they all stay
**zero**:

| Counter | Steady-state | Reset | Availability |
|---|---|---|---|
| `dev.sanity_reject_count` | 0 | `dev.reset_counters()` | both runtimes |
| `MachineI2CBackend.retry_exhaustion_count` | 0 | `backend.reset_counters()` | MicroPython only |
| `MachineI2CBackend.retry_count_total` | low (~5-20 per hour is normal) | `backend.reset_counters()` | MicroPython only |
| stale fraction in period snapshots | < 1 % | (cumulative — no reset) | both runtimes |

If any of these is nonzero in steady state, go back to the matching
section above.

> The long-soak harness `tests/test_long_soak.py` runs via
> `pytest libs/python/rbamp/tests/test_long_soak.py --soak --bus N --addr 0x50`.
> For the six acceptance criteria, see the README.md "Long-soak
> regression harness" section.

## Bus-level debug with a logic analyzer

For deep debugging, when the package can no longer tell you what's
happening on the wire, capture SDA + SCL with a logic analyzer
(Saleae, DSLogic Plus, Sigrok-compatible):

- Sample rate ≥ 1 MS/s at 100 kHz I²C; ≥ 4 MS/s at 400 kHz.
- The I²C decoder in Sigrok / Saleae will show ACK / NACK on each
  byte plus the address phase.
- Compare your script's calls (`dev.voltage`, etc.) against the
  expected byte sequence — they should match exactly (single byte
  per address phase, no auto-increment).

If the package's behavior diverges from the capture, open an issue
with the capture file attached (`.sal` / `.dsl` / `.csv`).

## When to contact support

If you've worked through the matching section above and the problem
persists, open an issue:

[github.com/rb-amp/rbamp-python/issues](https://github.com/rb-amp/rbamp-python/issues)

In the issue, include:

- **Runtime + version**: `python --version` / `mpremote eval 'import sys; print(sys.version, sys.implementation)'`.
- **Host platform**: "RPi 4B Bookworm", "ESP32-S3 N16R8 MicroPython 1.22", etc.
- **Package version**: `rbamp --version` (CPython) or
  `python -c "import rbamp; print(rbamp.__version__)"`.
- **Module firmware version** — `dev.firmware_version` from the logs.
- **A minimal script** (~30 LOC) that reproduces the problem.
- **The exception traceback** with full context.
- **The counters** at the time of failure: `dev.sanity_reject_count`,
  `backend.retry_exhaustion_count` (uPy only).
- **Verbose library logs** — the easiest way to collect them is
  `dev.set_logger(print)` before the operation that fails. The
  package will print its internal steps (the retry loop, settle
  timeouts, the latch primer, and so on) — attach stdout to the issue.
- **(If you have one)** a logic-analyzer capture file.

## CLI — `rbamp scan` / `rbamp read` / `rbamp address`

The `rbamp` CLI is a thin wrapper over `RbAmp.*` for bench
operations (see [09 · API reference](09_api_reference.md), the "CLI"
section). Because it uses the same `RbAmp` class, ALL the symptoms
above apply. CLI-specific errors:

- **`rbamp scan` finds nothing** — same as Step 1 in the "Module
  doesn't answer over I²C" section above. Check `i2cdetect -y 1`
  directly. If it finds the module, it's a bug in our backend
  autodetect; open an issue with the output of `rbamp --verbose scan`.
- **`rbamp <subcommand>` fails with `PermissionError: /dev/i2c-1`**
  — the user isn't in the `i2c` group. See README § Installation for
  `sudo usermod -aG i2c $USER` plus a re-login.
- **`rbamp: command not found`** after `pip install` — the
  `[project.scripts]` console entry isn't active. Check that
  `pip show rbamp` shows the installed location; make sure
  `$HOME/.local/bin` is on `$PATH` (for `pip install --user`).

## Links

- [05 · Quickstart](05_quickstart.md) — your first working script
- [09 · API reference](09_api_reference.md) — the full API plus
  the warnings on public-with-WARNING methods
- [03 · Current sensor selection](03_sensor_selection.md) — the
  SCT-013 model table, the dual-CT topology for a wide dynamic
  range, and approaches to boosting sensitivity at low currents


---

[← API Reference](09_api_reference.md) | [Contents](README.md) | [Changelog →](11_changelog.md)
