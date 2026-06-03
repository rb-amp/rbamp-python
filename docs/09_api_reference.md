# 09 · API Reference

The complete public API of the `rbamp` package, v1.1.0. Sources:
[`__init__.py`](../__init__.py) (re-exports),
[`_rbamp_core.py`](../_rbamp_core.py) (the main `RbAmp` class),
[`_snapshot.py`](../_snapshot.py) (POD structures + the exception
hierarchy), [`_energy.py`](../_energy.py) (Wh accumulator),
[`_registers.py`](../_registers.py) (auto-generated constants).

This chapter is a reference: signatures, return values, side
effects, edge cases. For working examples, see
[06 · Examples](06_examples.md); for a quick start, see
[05 · Quickstart](05_quickstart.md).

## Imports

```python
from rbamp import (
    RbAmp,                     # main class
    RbAmpEnergy,               # Wh accumulator (accessed via dev.energy)
    RbAmpSnapshot,             # RT-block dataclass-style
    RbAmpPeriodSnapshot,       # period snapshot dataclass-style
    RbAmpSensorClass,          # sensor-class enum (v1.1.0+)
    TOPOLOGY_SINGLE, TOPOLOGY_SPLIT_PHASE, TOPOLOGY_THREE_PHASE,
    topology_name,             # helper: int → topology name
    # Exception hierarchy
    RbAmpError,                # base class of all errors
    RbAmpIOError,
    RbAmpTimeoutError,
    RbAmpNotReadyError,
    RbAmpStaleError,
    RbAmpParamError,
    RbAmpModeError,
    RbAmpVersionError,
    # Metadata
    __version__,               # "1.1.0"
    __protocol_version__,      # 0x03 (RBAMP_PROTOCOL_VERSION)
    REGISTERS, COMMANDS,       # raw protocol constants (advanced use)
)
```

Minimal import for a typical application:

```python
from rbamp import RbAmp, RbAmpSensorClass, RbAmpError
```

## General idioms

The API follows standard Python patterns:

- **Class-based**: the main `RbAmp` class owns everything — RT
  readings, period accounting, configuration, diagnostics. The
  handle holds a backend plus an energy accumulator internally.
- **Context manager**: `with RbAmp(bus, addr) as dev:` is the
  recommended idiom. `__enter__` calls `dev.begin()`
  automatically.
- **Properties for RT readings**: `dev.voltage`, `dev.frequency` —
  one property equals one I²C transaction. Channel-indexed values
  go through `_ChannelProxy`: `dev.current[ch]`, `dev.power[ch]`,
  `dev.power_factor[ch]`.
- **Methods for channel-indexed values**: `dev.read_current(ch)`,
  `dev.read_power(ch)`. Fully equivalent to the property form —
  use whichever is convenient.
- **Exceptions for errors**: every error is a subclass of
  `RbAmpError`. Use the standard Python `try / except` pattern
  (not `last_error()` + return code).
- **Async generator** for periodic streaming: `async for snap in
  dev.stream_period(interval_s=...)`.

## Types

### `RbAmp`

```python
class RbAmp:
    def __init__(self, bus, addr: int = 0x50): ...
```

The main class. One instance per slave device. `bus` is any
object that matches one of the "known" API signatures:
`smbus2.SMBus`, `machine.I2C`, or a custom backend (see
[04 · Hardware connection](04_hardware.md)).

The backend is selected **automatically** on the first bus access:

| bus-object method | Backend |
|---|---|
| `bus.readfrom_mem`, `bus.writeto_mem` | `MachineI2CBackend` (MicroPython) |
| `bus.read_byte_data`, `bus.write_byte_data` | `SMBusBackend` (CPython + smbus2) |
| `bus.read_byte`, `bus.register_acks`, `bus.now_ms` | already-wrapped (test mocks, FTDI adapters) |

If no signature matches, `RbAmpParamError` is raised with a
description.

### Exception hierarchy

Every error from the package is a subclass of `RbAmpError`, which
itself inherits from **`OSError`** (not a bare `Exception`). This
means existing code with `except OSError:` handlers **keeps
catching** rbAmp errors without any rewrite — the standard Python
convention for hardware-I/O drivers.

![rbAmp Python exception hierarchy (RbAmpError and subclasses)](images/python-exception-hierarchy.png)

> **Note**: `RbAmpParamError` inherits from `RbAmpError` +
> `ValueError` on CPython (multi-base). On MicroPython, where
> multi-base inheritance is not supported by all ports, it falls
> back to single-base. From the user's standpoint,
> `try / except RbAmpParamError` works the same either way.
> **However, `try / except ValueError` catches it **only on
> CPython** — on MicroPython that handler will be missed.** Code
> that needs to run cross-platform must catch `RbAmpParamError`
> explicitly.

Standard usage:

```python
try:
    snap = dev.read_period_snapshot()
except RbAmpStaleError:
    # period not ready yet — the master timestamp was pinned by the package
    continue
except RbAmpIOError as e:
    log.warning("bus failure: %s", e)
except RbAmpError as e:
    log.error("unexpected: %s", e)
```

### `RbAmpSensorClass` (v1.1.0+)

```python
class RbAmpSensorClass(IntEnum):
    UNSET       = 0   # factory value after reset
    SCT_013     = 1   # SCT-013 series (shipping default)
    WIRED_CT    = 2   # Reserved — STANDARD tier
    BUILTIN_CT  = 3   # Reserved — PRO tier
```

> **Note**: on CPython this uses `IntEnum` from the stdlib `enum`
> module. On MicroPython ports without an `enum` module, it falls
> back to a plain class with the same member values. From the
> user's standpoint both forms are identical:
> `dev.set_sensor_class(RbAmpSensorClass.SCT_013)` or
> `dev.set_sensor_class(1)` — both work.

Only `UNSET` and `SCT_013` are meaningful for the current
firmware. The reserved values are present in the API for
compatibility with future SKUs — for now, passing them to
`set_sensor_class()` raises `RbAmpParamError`.

### `RbAmpSnapshot`

Returned by `dev.read_all()`. A plain Python class (not a
`@dataclass`, for MicroPython compatibility). All fields are in SI
units.

```python
class RbAmpSnapshot:
    voltage:        float       # V — RMS voltage
    voltage_peak:   float       # V — peak voltage
    current:        list        # [ch0, ch1, ch2] — RMS current per channel, A
    current_peak:   list        # [ch0, ch1, ch2] — peak current, A
    power:          list        # [ch0, ch1, ch2] — active power (signed), W
    power_factor:   list        # [ch0, ch1, ch2] — dimensionless, −1..+1
    frequency:      float       # Hz
    topology:       int         # TOPOLOGY_* constant
    channels:       int         # 1..3 — number of valid channels
    has_voltage_hw: bool        # True if a voltage sensor is present
```

Unused channels (beyond `channels`) are filled with zeros.

### `RbAmpPeriodSnapshot`

Returned by `dev.read_period_snapshot()`. The energy-accounting
primitive.

```python
class RbAmpPeriodSnapshot:
    avg_p:        list   # [ch0, ch1, ch2] — average P over the period, W
    max_p:        float  # peak instantaneous power on channel 0
    latch_ms:     int    # period duration (the device's view)
    master_dt_ms: int    # master wall-clock dt since the last latch
    valid:        bool   # True if the latch-ready flag was set
```

> For energy integration use `master_dt_ms`, not `latch_ms` — the
> module's internal timer has limited accuracy.

### Topology constants

```python
TOPOLOGY_SINGLE      = 1   # 1 current channel (UI1 / I1)
TOPOLOGY_SPLIT_PHASE = 2   # 2 current channels (UI2 / I2)
TOPOLOGY_THREE_PHASE = 3   # 3 current channels (UI3 / I3)

topology_name(topology: int) -> str   # 1 → "SINGLE", 2 → "SPLIT_PHASE", ...
```

## Lifecycle

### `RbAmp(bus, addr=0x50)`

Constructor. Backend resolution happens in `__init__`. No I²C
traffic is generated until `begin()`.

| Parameter | Description |
|---|---|
| `bus` | I²C bus object (smbus2.SMBus, machine.I2C, or a wrapper) |
| `addr` | 7-bit slave address. Range 0x08..0x77, default 0x50 |

**Raises**: `RbAmpParamError` if the bus object is not recognized
or `addr` is out of range.

### `dev.begin()`

Probes the device, caches the topology, and primes the LATCH.

Sequence:

1. Read `REG_VERSION` — raises `RbAmpIOError` on NACK,
   `RbAmpVersionError` if `0x00` / `0xFF` is returned.
2. Cache the topology (on current firmware `begin()` does not
   auto-probe; it uses the hint from the constructor).
3. Read `U_rms` to determine whether a voltage sensor is present
   (threshold 1.0 V).
4. Write `CMD_LATCH_PERIOD` (the primer) + wait 50 ms. The first
   snapshot after power-up is discarded.
5. Store `time.monotonic()` (or `time.ticks_ms()` on uPy) for
   subsequent energy integration.

Idempotent: safe to call again.

### Context manager

```python
with RbAmp(bus, 0x50) as dev:
    # dev.begin() has already been called in __enter__
    ...
# __exit__ — no-op (closing the bus is the caller's responsibility)
```

The recommended idiom. The package's `__exit__` does NOT close
the bus — management of the bus object stays with the caller (the
typical pattern is nested
`with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:`).

### `dev.probe() -> bool`

A lightweight liveness check. A single read of `REG_VERSION` with
no side effects.

**Returns**: `True` if the slave ACK'd and reported a supported
version; `False` otherwise. **Does not raise** — meant for
polling scenarios.

### `dev.wait_ready(timeout_ms=1000) -> None`

Polls the module's ready flag until bit 0 is set. Useful after
power-up — the module may need up to 200 ms to produce its first
RT window.

**Raises**: `RbAmpTimeoutError` if the bit is not seen before
`timeout_ms` elapses.

### Properties — handle state

```python
dev.firmware_version: int      # REG_VERSION: 0x01 (v1.0), 0x02 (v1.1), 0x03 (v1.2)
dev.topology:         int      # TOPOLOGY_* constant
dev.topology_name:    str      # "SINGLE" / "SPLIT_PHASE" / "THREE_PHASE"
dev.channels:         int      # 1..3
dev.has_voltage_hw:   bool     # True if begin() detected U_rms > 1.0
dev.address:          int      # 7-bit I²C address (updated after commit_address_change)
```

**`firmware_version` is a live property: every read touches the
bus** (one single-byte read of `REG_VERSION`). Cache it
application-side if you poll it in a hot loop: read
`fw = dev.firmware_version` once after `begin()`. If `begin()`
has not been called yet, `firmware_version` raises `RbAmpIOError`
(the module may NACK while the handle is still "cold").

The other properties (`topology`, `channels`, `has_voltage_hw`,
`address`) are cached in the handle and return the values stored
after `begin()` without touching the bus.

## Real-time readings (RT block, 200 ms refresh)

All methods raise `RbAmpIOError` on a communication failure (NACK
after retry, or a sanity reject) or `RbAmpParamError` on an
invalid argument.

### Properties

```python
dev.voltage         -> float   # V — RMS voltage
dev.voltage_peak    -> float   # V — peak voltage
dev.frequency       -> float   # Hz — mains frequency
```

Channel-indexed via `_ChannelProxy` — they support `[ch]`
indexing and iteration:

```python
dev.current         # _ChannelProxy → dev.current[0], dev.current[1], ...
dev.current_peak    # _ChannelProxy
dev.power           # _ChannelProxy
dev.power_factor    # _ChannelProxy
```

Usage:

```python
i0 = dev.current[0]                       # a single channel
for i in dev.current:                      # iterate over all valid channels
    print(i)
total = sum(p for p in dev.power)          # sum of all channels
```

`len(dev.current)` equals `dev.channels` (1..3).

### Methods (fully equivalent to the property form)

```python
dev.read_voltage(phase=0)         -> float
dev.read_voltage_peak(phase=0)    -> float
dev.read_current(ch=0)            -> float
dev.read_current_peak(ch=0)       -> float
dev.read_power(ch=0)              -> float       # signed
dev.read_power_factor(ch=0)       -> float
dev.read_frequency()              -> float
```

### One-shot read of the entire RT block

```python
dev.read_all()  -> RbAmpSnapshot
```

Equivalent to consecutive `read_voltage` + `read_voltage_peak` +
`read_current(0..N)` + `read_current_peak(0..N)` + `read_power(0..N)` +
`read_power_factor(0..N)` + `read_frequency`. Unused channels
(beyond `channels`) are filled with zeros.

**Raises**: `RbAmpIOError` if any sub-read fails.

## Period accounting

See [01 · Overview](01_overview.md) for the big picture and
[05 · Quickstart](05_quickstart.md) Step 5 for a minimal template.

### `dev.latch_period() -> None`

Writes `CMD_LATCH_PERIOD`. Does not wait — the caller must allow a
50 ms settle and check `dev.is_period_valid()` before reading.

For most tasks, use `read_period_snapshot()` — it encapsulates the
whole sequence.

### `dev.is_period_valid() -> bool`

Reads the latch-ready bit. Returns `True` if the latest snapshot
is fresh.

### `dev.read_period_avg_power(ch=0) -> float`

The average active power on a channel over the latched period.
Must be called after `latch_period()` + a 50 ms settle + a valid
check.

### `dev.read_period_max_power() -> float`

The peak instantaneous power on channel 0 over the latched period.
Channel 0 only on v1 firmware.

### `dev.read_period_latch_ms() -> int`

The period duration from the **device's** perspective, in ms.

> **Diagnostic value.** The module's internal timer has limited
> accuracy. For energy integration use `master_dt_ms` from
> `RbAmpPeriodSnapshot`, not this field.

### `dev.read_period_snapshot(settle_ms=50, skip_latch=False) -> RbAmpPeriodSnapshot`

**The recommended entry point** for period accounting. The full
sequence under the hood:

1. If `skip_latch=True`, skip the LATCH write (for the
   multi-module pattern after a manual series of LATCH commands).
2. Otherwise, write `CMD_LATCH_PERIOD`.
3. Capture `time.monotonic()` to compute `master_dt_ms`.
4. `time.sleep(settle_ms / 1000)` — 50 ms by default.
5. Check the latch-ready flag. If 0, raise `RbAmpStaleError`
   (after pinning the timestamp first, so the next snapshot does
   not double-count `dt`).
6. Read `avg_p[0..channels-1]` + `max_p` + `latch_ms`.
7. Update the timestamp + call `energy.tick(snap)` for Wh
   integration (unless the accumulator is disabled via
   `energy.disable()`).

| Parameter | Description |
|---|---|
| `settle_ms` | Wait after LATCH, in ms. Default 50. |
| `skip_latch` | If `True`, do not write LATCH; read only. |

**Raises**: `RbAmpStaleError` (snapshot stale); `RbAmpIOError`
(bus failure).

### `async dev.stream_period(interval_s=60.0, skip_stale=True)`

An async generator that yields `RbAmpPeriodSnapshot` at the
`interval_s` interval. Works on CPython `asyncio` and MicroPython
`uasyncio` (on MicroPython ≥ 1.20, `import asyncio` also works;
for earlier versions use `import uasyncio as asyncio`).

```python
async for snap in dev.stream_period(interval_s=60.0):
    # snap is a RbAmpPeriodSnapshot; .valid is guaranteed True (skip_stale=True by default)
    print(snap.avg_p[0])
```

| Parameter | Description |
|---|---|
| `interval_s` | The interval between latches (recommended ≥ 30 s) |
| `skip_stale` | `True` (default) — stale snapshots are skipped automatically. `False` — they are yielded with `snap.valid=False`, and your code must check `if not snap.valid: continue` itself |

> ⚠ **CPython: blocks the event loop.** Under the hood,
> `stream_period()` calls the synchronous, **blocking**
> `read_period_snapshot()` — which does a `time.sleep(0.05)`
> settle plus ~50–100 ms of single-byte bus reads. On CPython
> this blocks the entire `asyncio` event loop for those 100 ms —
> other coroutines (MQTT, HTTP, WiFi keepalive) get no CPU. For
> production servers, wrap it in `run_in_executor`:
>
> ```python
> import asyncio
>
> async def stream_period_nonblocking(dev, interval_s=60.0):
>     loop = asyncio.get_event_loop()
>     while True:
>         await asyncio.sleep(interval_s)
>         snap = await loop.run_in_executor(None, dev.read_period_snapshot)
>         if snap.valid:
>             yield snap
> ```
>
> On **MicroPython** the problem is smaller — the uasyncio
> scheduler yields to other tasks less often, and blocking I²C is
> considered acceptable in embedded async patterns. If you need
> non-blocking behavior, use a separate thread via
> `_thread.start_new_thread` (on ESP32 µPy).

## Energy accounting (master-side accumulator)

### `dev.energy` — an `RbAmpEnergy` instance

A per-channel Wh accumulator. Accessed through a property — the
package owns the instance:

```python
dev.energy.wh(ch=0)         -> float    # current Wh total for the channel
dev.energy.reset(ch=0)      -> None     # zero a single channel
dev.energy.reset_all()      -> None     # zero all channels
dev.energy.disable()        -> None     # turn off auto-integration
dev.energy.enable()         -> None     # turn it back on
```

The accumulator updates automatically on every successful
`dev.read_period_snapshot()`. It is signed — a negative value
means net export.

Integration formula:

```text
wh[ch] += snap.avg_p[ch] × master_dt_ms / 1000 / 3600
         [W]             [milliseconds]              → [Wh]
```

`dev.energy.disable()` is useful for deep-sleep scenarios where
the master itself owns Wh persistence in RTC memory — see
[06 · Examples](06_examples.md), Scenario 9.

## Sensor configuration

A two-step sequence. For a detailed model-selection guide, see
[03 · Current sensor selection](03_sensor_selection.md).

### `dev.set_sensor_class(cls) -> None` (v1.1.0+)

Sets the current-sensor class and persists it to flash. Blocking,
~705 ms.

```python
dev.set_sensor_class(RbAmpSensorClass.SCT_013)
# or equivalently:
dev.set_sensor_class(1)
```

| Parameter | Description |
|---|---|
| `cls` | An `RbAmpSensorClass` enum or an int 0..3 |

**Raises**:

- `RbAmpParamError` — `cls` is not in {`UNSET`, `SCT_013`} (the
  reserved values are not supported on the current SKU)
- `RbAmpIOError` — communication failure

On v1.2+ firmware it must be called **before** `set_ct_model*()`.
On v1.0/v1.1 it is a no-op (the register write goes through, but
the firmware ignores it).

### `dev.set_ct_model(code) -> None`

The single-parameter (legacy) form — sets the CT-clamp model
**on channel 0 only**.

| `code` | Model |
|:---:|---|
| 1 | SCT-013-005 |
| 2 | SCT-013-010 |
| 3 | SCT-013-030 |
| 4 | SCT-013-050 |
| 5 | SCT-013-100 |

**Raises**:

- `ValueError` (and also `RbAmpParamError`) — `code` outside 1..5
- `RbAmpModeError` — on v1.2+ firmware if `set_sensor_class()` was
  not called (see the message below)
- `RbAmpIOError` — communication failure

The `RbAmpModeError` message on v1.2 without a pinned class:

```text
REG_SENSOR_CLASS is UNSET on v1.2+ firmware;
call dev.set_sensor_class(RbAmpSensorClass.SCT_013) first
```

Equivalent to `dev.set_ct_model_ch(0, code)` on v1.2+ firmware.

### `dev.set_ct_model_ch(channel, code) -> None` (v1.1.0+)

The per-channel form. Sets the CT-clamp model on a specific
channel.

Under the hood: write `REG_CT_MODEL` → command
`CMD_SET_CT_MODEL_CH0/1/2` → 5 ms settle → `CMD_SAVE_GAINS` →
700 ms. Blocking, ~705 ms.

> **Important: call order matters.** Writing `REG_CT_MODEL` also
> fires a legacy callback on the device side, which applies the
> preset to channel 0 as a side effect. Assign channels in
> **descending index order**:
>
> ```python
> dev.set_ct_model_ch(2, 5)  # channel 2 = SCT-013-100
> dev.set_ct_model_ch(1, 3)  # channel 1 = SCT-013-030
> dev.set_ct_model_ch(0, 1)  # channel 0 = SCT-013-005
> ```
>
> Final state: `ch0=1, ch1=3, ch2=5`. ✓

**Raises**:

- `RbAmpVersionError` — `dev.firmware_version < 0x03` (v1.2)
- `RbAmpParamError` — `channel` outside 0..2 or `code` outside 1..5
- `RbAmpModeError` — `set_sensor_class()` was not called (v1.2+ guard)

### `dev.save_gains() -> None`

A "bare" `CMD_SAVE_GAINS` write without any accompanying register
changes.

> ⚠ **Normally called internally by the package.** A bare
> `save_gains()` is appropriate ONLY if the caller has manually
> written to non-public calibration registers via raw bus access —
> this is an out-of-warranty operation that bypasses the
> SKU-matched preset table. Incorrect values will produce wrong
> current/power readings with no explicit warning. Each call
> performs a flash erase+write cycle (~700 ms); flash endurance is
> finite (~10,000 cycles per page) — **do not call it in a loop.**

**Raises**: `RbAmpIOError` on a communication failure.

### `dev.prepare_address_change(new_addr) -> None`

Step 1 of 2 for changing the module's I²C address. Flow: range
validation → device-mode check → record the "arm" timestamp. The
caller must call `commit_address_change()` within 5 seconds, or
the arming expires.

> ⚠ **Develop-mode-only operation.** Changing the address
> requires the module to be in develop mode (`REG_MODE == 1`, set
> at the factory). On a standard production module `REG_MODE == 0`,
> and this method raises `RbAmpModeError` — the device will NOT
> accept an address change. The
> `prepare_address_change()` + `commit_address_change()` pair is
> intended for factory provisioning and integrator bench
> operations, not for user code. If a deployed module needs a
> different I²C address, the documented path is reconfiguration via
> the factory bench (outside the package's scope).

**Raises**:

- `RbAmpParamError` — `new_addr` outside 0x08..0x77 or equal to
  the current address
- `RbAmpModeError` — the device is not in develop mode
- `RbAmpIOError` — communication failure

### `dev.commit_address_change() -> None`

Step 2 of 2. Must be called within 5 seconds of
`prepare_address_change()`.

> ⚠ **Develop-mode-only operation.** Same restriction as
> `prepare_address_change()` — on a production module it raises
> `RbAmpModeError`. See the previous paragraph for the full
> semantics.

An additional subtlety:

> ⚠ **Restart and re-enumeration after commit.** After a
> successful commit, the device resets and re-enumerates at the NEW
> address. Subsequent calls on this handle instance address the
> new address transparently — but any OTHER master on the bus
> (another Python script, an ESP-IDF component, a debug probe) will
> keep thinking the device is at the old address until its internal
> state is updated manually.

**Raises**:

- `RbAmpTimeoutError` — the arming window expired
- `RbAmpModeError` — develop mode is not set
- `RbAmpIOError` — communication failure

### `dev.factory_reset() -> None`

The `CMD_FACTORY_RESET` command + a 1500 ms wait.

> ⚠ **Destructive operation.** Erases ALL flash parameters (CT
> model, sensor class, calibration coefficients, I²C address). The
> module returns to factory defaults — `RbAmpSensorClass` becomes
> `UNSET`, `REG_CT_MODEL` becomes 0. Any configuration previously
> applied via `set_sensor_class()` / `set_ct_model*()` is gone. The
> next user MUST re-apply both `set_sensor_class()` and
> `set_ct_model*()` before accounting works again. This is **not a
> "soft restart"** — for a soft restart use `reset()`.
> `factory_reset()` is reserved for recovering from a known-bad
> state or handing the module to another user / installation.

**Raises**: `RbAmpIOError` on a communication failure.

### `dev.reset() -> None`

The `CMD_RESET` command + a 100 ms wait. A soft restart of the
device with no loss of flash parameters.

**Raises**: `RbAmpIOError` on a communication failure.

## Multi-module bus (static)

### `RbAmp.broadcast_latch(bus) -> bool`

A static method. Reserved for future firmware versions. The method
**attempts an I²C General Call** (a write to address 0x00) with a
two-byte payload `[REG_COMMAND, CMD_LATCH_PERIOD]`. It returns:

- `True` — the General-Call write completed without an exception
  (on v2+ firmware this means synchronization went through).
- `False` — the bus rejected the write (most hosts NACK a General
  Call when GC is disabled in the slave peripheral — which is the
  case on v1 rbAmp firmware).

**On v1 rbAmp firmware, expect `False`** — GC is disabled and the
slave will not acknowledge the write. Use a sequential series of
`latch_period()` on each device plus a shared settle — the
canonical multi-module sync pattern.

For details, see [06 · Examples](06_examples.md), Scenario 3.

> **If you get `True` on v1 firmware**, it means your bus (a test
> mock, FTDI, a non-standard backend) accepts a GC without slave
> acknowledgement. The firmware's behavior in that case is
> undefined — actual synchronization may not happen.

## Diagnostics

### `dev.set_logger(log_callable) -> None`

An optional diagnostics sink. `log_callable` takes a single `str`
argument. When set, the package briefly logs `probe()` results,
stale snapshots, and mode rejections.

Typical usage:

```python
import logging
log = logging.getLogger("rbamp")
log.setLevel(logging.INFO)
log.addHandler(logging.StreamHandler())
dev.set_logger(log.info)

# Or the simplest option — a plain print:
dev.set_logger(print)
```

### `dev.sanity_reject_count: int`

A counter of float values rejected by the sanity filter
(`!isfinite(x) or |x| > 10000`). Steady-state is **0**. A nonzero
value after retry mitigation usually means a rare I²C-stack
artifact that survived the retry layer.

### `MachineI2CBackend` diagnostic counters (MicroPython only)

`MachineI2CBackend` (the internal backend on MicroPython) exposes
two counters:

- `backend.retry_exhaustion_count` — how many times the retry loop
  was exhausted without success (`RbAmpIOError` was raised)
- `backend.retry_count_total` — how many times the retry loop
  recovered successfully with a silent retry

These counters are accessible through the advanced API:

```python
from rbamp._io_micropython import MachineI2CBackend
from rbamp import RbAmp

backend = MachineI2CBackend(i2c, retry_attempts=5)
dev = RbAmp(backend, 0x50)   # the address is a RbAmp parameter, not a backend one

# ...soak-test for an hour...
print("retries succeeded:", backend.retry_count_total)
print("retries exhausted:", backend.retry_exhaustion_count)
print("sanity rejects:",    dev.sanity_reject_count)
```

`SMBusBackend` (CPython) has no retry layer — the Linux kernel I²C
driver is not subject to the NACK pattern.

### `dev.reset_counters() -> None`

Zeros all counters (RbAmp + the backend, if it is a
`MachineI2CBackend`). Use it at the start of a soak test.

## Metadata and protocol constants

```python
rbamp.__version__              # "1.1.0" — PEP 440 version
rbamp.__protocol_version__     # 0x03  — RBAMP_PROTOCOL_VERSION
rbamp.RBAMP_REG_SCHEMA_CRC32   # CRC32 of the schema codegen contract
```

`REGISTERS` and `COMMANDS` are auto-generated protocol-level dicts:

```python
from rbamp import REGISTERS, COMMANDS

REGISTERS["U_RMS"]           # 0x86
REGISTERS["PERIOD_VALID"]    # 0x07
COMMANDS["LATCH_PERIOD"]     # 0x27
COMMANDS["SAVE_GAINS"]       # 0x26
COMMANDS["FACTORY_RESET"]    # 0xAA
```

Used internally by the package. Exposed for advanced users who do
raw transit through their own backend; ordinary users never touch
them.

## References

- [05 · Quickstart](05_quickstart.md) — your first working script
- [06 · Examples](06_examples.md) — working scenarios
- [10 · Troubleshooting](10_troubleshooting.md) — decoding errors
  and fixing common problems
- Sources: [`__init__.py`](../__init__.py),
  [`_rbamp_core.py`](../_rbamp_core.py),
  [`_snapshot.py`](../_snapshot.py),
  [`_energy.py`](../_energy.py),
  [`_io_micropython.py`](../_io_micropython.py),
  [`_io_smbus.py`](../_io_smbus.py)


---

[← Cloud Integrations](08_cloud_integrations.md) | [Contents](README.md) | [Troubleshooting →](10_troubleshooting.md)
