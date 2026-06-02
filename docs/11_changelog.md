# 11 · Changelog

Release notes for the `rbamp` Python package. The package's SemVer is
**independent** of the rbAmp protocol version — a single package
version can support several module firmware versions (via
version-gated guards).

## v1.1.0 — 2026-05-29 (v1.2 firmware parity)

A minor release — additive extensions to the public API for the v1.2
firmware. Backward compatible with v1.0.0 — nothing is broken, new
methods are added. Existing v1.0.0 users can upgrade without code
changes (the new methods are only called if the application uses
them).

### Added

- **`RbAmpSensorClass`** — an enum of sensor classes (wire-byte
  values: `UNSET=0`, `SCT_013=1`, `WIRED_CT=2` (reserved),
  `BUILTIN_CT=3` (reserved)). On CPython it is an `IntEnum` from the
  stdlib; on MicroPython ports without the `enum` module, a plain-class
  fallback with the same values.
- **`RbAmp.set_sensor_class(cls)`** — sets the sensor class on v1.2+
  firmware. Required before `set_ct_model[_ch]()`. Accepts either a
  `RbAmpSensorClass` enum or a plain int. On v1.0/v1.1 firmware it is a
  no-op (the register write goes through, the firmware ignores it).
- **`RbAmp.set_ct_model_ch(channel, code)`** — per-channel selection
  of the CT clamp model on v1.2+ firmware. Opcodes
  `CMD_SET_CT_MODEL_CH0/1/2`. Enables the descending-order convention
  for multi-channel scenarios (see
  [03 · Current sensor selection](03_sensor_selection.md)).

### Changed

- **`RbAmp.set_ct_model(code)`** now enforces a v1.2+ precondition: if
  `set_sensor_class()` was not called before it on v1.2+ firmware, it
  raises `RbAmpModeError` with a specific message:

  ```text
  REG_SENSOR_CLASS is UNSET on v1.2+ firmware;
  call dev.set_sensor_class(RbAmpSensorClass.SCT_013) first
  ```

  **Backward compatible**: the guard is skipped on v1.0/v1.1 firmware
  (no `REG_VERSION >= 0x03` → no check). Code that worked on rbamp
  1.0.0 against v1.0/1.1 firmware keeps working unchanged.

- **`RbAmpModeError`** — expanded triggers. It is now raised from two
  places:
  1. `set_ct_model[_ch]()` when `REG_SENSOR_CLASS = UNSET` on v1.2+
     firmware (new in v1.1.0).
  2. `prepare_address_change()` / `commit_address_change()` when the
     device is not in develop mode (as before).

### Unchanged

- The retry + sanity discipline on `MachineI2CBackend` (default 3
  attempts × 5 ms gap; configurable via
  `MachineI2CBackend(retry_attempts=..., retry_gap_ms=...)`).
- All existing reads (`read_voltage` / `read_current` / `read_power` /
  etc, and their property equivalents `dev.voltage` /
  `dev.current[ch]` / etc).
- The period metric and Wh accumulator (`read_period_snapshot()`,
  `dev.energy.wh()`).
- Async streaming via `dev.stream_period()`.
- `dev.broadcast_latch(bus)` remains reserved-for-v2 — it returns
  `False` without touching the bus.

### Tests

`pytest libs/python/rbamp/tests -v` → **93 passed, 1 skipped** (up
from 83/1 in v1.0.0 — +10 tests for the v1.1.0 surface):

- `test_set_sensor_class_writes_and_saves`
- `test_set_sensor_class_accepts_plain_int`
- `test_set_sensor_class_rejects_out_of_range`
- `test_set_ct_model_ch_per_channel`
- `test_set_ct_model_ch_rejects_invalid_channel`
- `test_set_ct_model_ch_rejects_invalid_code`
- `test_set_ct_model_guards_on_unset_class_v12`
- `test_set_ct_model_passes_on_v12_with_pinned_class`
- `test_set_ct_model_skips_guard_on_v10`
- `test_sensor_class_enum_wire_values` (checks
  `UNSET=0`, `SCT_013=1`, `WIRED_CT=2`, `BUILTIN_CT=3` — the wire-byte
  encoding from SPEC).

### Versions

- `pyproject.toml`: `version = "1.0.0"` → `"1.1.0"`
- `package.json`: `version: "1.0.0"` → `"1.1.0"` (MicroPython mip)

## v1.0.0 — 2026-05-25 (first public release)

The first public release of the `rbamp` Python package. It implements
the canonical rbAmp API for **protocol v1.0** with forward-readiness
to v1.1. A unified single-source codebase for MicroPython + CPython.

### Features

**Main class `RbAmp`** — one instance per slave device,
context-manager-friendly:

```python
with RbAmp(bus, 0x50) as dev:  # __enter__ calls dev.begin()
    ...
```

**Unified backend** — automatic selection by the type of the bus
object:

| Bus-object method | Backend |
|---|---|
| `bus.readfrom_mem`, `bus.writeto_mem` | `MachineI2CBackend` (MicroPython) |
| `bus.read_byte_data`, `bus.write_byte_data` | `SMBusBackend` (CPython + smbus2) |
| `bus.read_byte`, `bus.register_acks`, `bus.now_ms` | already-wrapped (FTDI adapters, mocks) |

**Full rbAmp v1.0 API:**

- **Lifecycle**: `__init__`, `begin`, `probe`, `wait_ready`,
  handle properties (`firmware_version`, `topology`, `channels`,
  `has_voltage_hw`, `address`)
- **RT reads** (200 ms refresh):
  - Methods: `read_voltage`, `read_voltage_peak`, `read_current(ch)`,
    `read_current_peak(ch)`, `read_power(ch)`, `read_power_factor(ch)`,
    `read_frequency`, `read_all()`
  - Properties: `voltage`, `voltage_peak`, `frequency`
  - Channel-indexed properties via `_ChannelProxy`:
    `current[ch]`, `current_peak[ch]`, `power[ch]`, `power_factor[ch]`
    (support iteration: `for p in dev.power: ...`)
- **Period metric**: `latch_period`, `is_period_valid`,
  `read_period_avg_power`, `read_period_max_power`,
  `read_period_latch_ms`, `read_period_snapshot`
- **Async streaming**: `async for snap in dev.stream_period(interval_s=)`
  — works on CPython `asyncio` and MicroPython `uasyncio`
- **Wh accumulator**: `dev.energy.wh(ch)`, `reset(ch)`, `reset_all()`,
  `disable()`, `enable()` — auto-updated on every successful
  `read_period_snapshot()`
- **Sensor configuration**: `set_ct_model(code)` (legacy single-arg)
- **Public-with-warning** (factory/integrator territory):
  `save_gains`, `prepare_address_change`, `commit_address_change`,
  `factory_reset`, `reset`
- **Multi-module**: `RbAmp.broadcast_latch(bus)` — static method,
  returns `False` on v1 firmware (reserved for v2 firmware)
- **Diagnostics**: `set_logger(callable)`, `sanity_reject_count`
  (cross-backend), `reset_counters()`. On MicroPython, additionally
  `MachineI2CBackend.retry_exhaustion_count` + `retry_count_total`

**Exception hierarchy** — every error inherits from `RbAmpError`:

```text
RbAmpError                                  # base
├── RbAmpIOError       — I²C transport error (NACK / sanity reject)
├── RbAmpTimeoutError  — timeout (wait_ready / commit_address_change window)
├── RbAmpNotReadyError — (reserved)
├── RbAmpStaleError    — period snapshot stale
├── RbAmpParamError    — bad argument (multi-base ValueError on CPython)
├── RbAmpModeError     — requires develop mode (or sensor class in v1.1.0)
└── RbAmpVersionError  — incompatible firmware version
```

The standard Python `try / except` pattern instead of error codes.

**POD structures** (plain classes, not `@dataclass` for MicroPython
compat): `RbAmpSnapshot`, `RbAmpPeriodSnapshot`.

**`MachineI2CBackend` retry + sanity discipline** (SPEC §B.5):

- Default 3 attempts × 5 ms gap on a single-byte read
- Loose-sanity filter (`isfinite + |x| < 10000`)
- Counter accessors for long-soak observability:
  `backend.retry_exhaustion_count`, `backend.retry_count_total`,
  `dev.sanity_reject_count`

**`SMBusBackend`** (CPython) — no retry layer; the Linux kernel
I²C driver is not subject to the NACK pattern. An `OSError` from smbus2
is translated into `RbAmpIOError`.

**Forward-readiness:**

- The `RBAMP_PROTOCOL_VERSION` constant (as of v1.0.0 = 0x02 for
  forward-compat with v1.1).
- The `REGISTERS` + `COMMANDS` dicts — auto-generated from SPEC, for
  advanced raw-protocol users.

### Supported runtimes and platforms

| Runtime | Platforms | Backend |
|---|---|---|
| CPython 3.8+ | Linux SBC (RPi / Orange Pi / Rock Pi), x86 + USB-I²C | `SMBusBackend` |
| MicroPython 1.20+ | ESP32 family (ESP32 / S2 / S3 / C3) | `MachineI2CBackend` (retry default 3) |
| MicroPython 1.20+ | RP2040 (Pico / Pico W) | `MachineI2CBackend` (retry default 1 — no NACK pattern) |
| MicroPython 1.20+ | STM32 (Pyboard / Nucleo) | `MachineI2CBackend` (retry default 1) |
| CircuitPython | (not directly) | — `busio.I2C` ≠ `machine.I2C`; an adapter is needed |

### Examples

10 parallel scripts per runtime:

- **`examples_upy/`** — `01_quick_read.py`, `02_oled_period.py`,
  `03_multi_module.py`, `04_mqtt.py`, `05_async_streaming.py`,
  `06_deep_sleep.py`, `07_bidirectional_energy.py`,
  `08_home_energy_balance.py`, `09_event_detection_logger.py`,
  `10_ha_autodiscovery.py`
- **`examples_cpython/`** — `01_quick_read.py`, `02_period_meter.py`,
  `03_multi_module_broadcast.py`, `04_mqtt_publisher.py`,
  `05_bidirectional_energy.py`, `06_rest_gateway.py`,
  `07_home_energy_balance.py`, `08_rotating_file_logger.py`,
  `09_ha_autodiscovery.py`, `10_systemd_service.py`

### CLI (CPython only)

`pip install rbamp` installs the `rbamp` command-line tool:

```sh
rbamp --version            # rbamp 1.1.0
rbamp --bus 1 scan         # I²C scan
rbamp read --watch 5       # RT reads every 5 seconds
rbamp period               # period snapshot
rbamp info                 # firmware version + topology
rbamp address 0x51         # change address (develop mode only)
```

### Long-soak regression harness

`tests/test_long_soak.py` (pytest opt-in via `--soak`) +
standalone `tools_bench/long_soak.py` (CPython) +
`tools_bench/long_soak_upy.py` (MicroPython). Six acceptance
criteria:

1. Per-cycle validity ratio > 99 %
2. Zero retry exhaustions
3. Zero sanity rejects
4. Monotonic Wh (positive load)
5. Bounded scheduler jitter
6. Bounded retry rate

### Tests

`pytest libs/python/rbamp/tests -v` → **83 passed, 1 skipped** in
v1.0.0. (In v1.1.0 — 93/1.)

### Documentation

11 reference chapters in [`docs/`](.) cover: overview, tiers, sensor
selection, wiring (dual-runtime per-host), quickstart, examples, DIY
and cloud integrations, the API reference, and troubleshooting.

### Known limitations

Intentional omissions from v1.0 — tracked for future minor releases:

- `broadcast_latch()` always returns `False` (General-Call is
  disabled in the v1 module's I²C peripheral). When the module
  firmware enables GC, the method will start working without an API
  change.
- Reactive power is not read — it is a STANDARD / PRO tier feature,
  not exposed in the v1.x protocol.
- Dimmer control is not implemented — it is out of scope for v1 (see
  the future companion `rbamp_dimmer` package or a CLI subcommand).
- MicroPython deep-sleep on v1.0/v1.1 firmware requires the canonical
  pattern with `skip_latch=True` + a known SLEEP_MS constant (see
  [06 · Examples](06_examples.md) Scenario 9) — a simplified
  `dev.warm_open()` will appear in v1.2+ once the firmware adds the
  corresponding accessor.
- CircuitPython is not supported directly — `busio.I2C` has a
  different API than `machine.I2C` (an adapter wrapper is required).

### Firmware compatibility matrix

| Package version | Firmware version | Behavior |
|---|---|---|
| 1.0 | 1.0 | Constructor topology hint. Fully functional. |
| 1.0 | 1.1 | Constructor hint; `REG_TOPOLOGY` ignored. Identical to 1.0/1.0. |
| 1.0 | 1.2 | Per-channel `set_ct_model_ch` is absent (v1.0 does not know about the new methods). Single-arg `set_ct_model` works (legacy path). |
| 1.1 | 1.0 | `set_sensor_class` is a no-op. `set_ct_model_ch` → `RbAmpVersionError`. Single-arg works. |
| 1.1 | 1.1 | Identical to 1.1/1.0; `REG_TOPOLOGY` is still ignored. |
| 1.1 | 1.2 | Per-channel `set_ct_model_ch` works. `set_sensor_class` is required before `set_ct_model*`. Per-channel signed `dev.read_power(ch)` returns the sign (negative = export). |
| 1.2+ (planned) | 1.x | Extended diagnostics, `dev.warm_open()` for deep-sleep, additional accessors. |

### Bench validation

The v1.0.0 release passed bench regression acceptance through the
long-soak harness (60 s minimum for pytest, 3600 s for the
release gate):

- **All 83 unit tests pass** + 1 skipped (HW-dependent)
- **Long-soak harness PASS** on CPython + RPi 4 + a real DUT
- **MicroPython long-soak PASS** on ESP32 + Pico via
  `tools_bench/upy_session.py`
- **CLI smoke** (`rbamp scan`, `rbamp read --watch 5`) — works on a
  stock Pi installation

The concrete accuracy figures (V / I / P / PF against a calibrated
reference) will be published once the bench-measurement program is
complete (the IP-001 + IP-010 program — for a discussion of the
dual-CT pattern and operation at low currents, see
[03 · Current sensor selection](03_sensor_selection.md)).

## Future releases — planned

### v1.1.x (patch — bug fixes only)

- TBD based on user reports.

### v1.2.0 (minor — additive, after the v1.1 / v1.3 firmware ships)

- **`dev.begin()` reads `REG_TOPOLOGY`** when
  `dev.firmware_version >= 0x02` and uses it as authoritative (the
  constructor hint becomes a fallback).
- **`dev.warm_open()`** (or `with RbAmp(bus, 0x50, warm=True)`) — a
  lightweight init without the CMD_LATCH_PERIOD primer, for
  MicroPython deep-sleep wake scenarios (it simplifies the pattern in
  [06 · Examples](06_examples.md) Scenario 9 — it removes the need for
  `skip_latch=True` on a warm wake).
- **Per-channel polarity-invert config flag** — an accessor for
  correcting CT clamp orientation without physically reinstalling it.
  Until it ships, the workaround is to invert the sign on the
  application side: `p = -p; pf = -pf;` (see
  [10 · Troubleshooting](10_troubleshooting.md), the section "PF stuck
  at exactly −1.0 on a purely resistive load").
- **STANDARD / PRO tiers via firmware** — `dev.energy.wh(ch)` will
  start returning a **signed** accumulator without the need for the
  master-side consume/export split from the current Scenario 5.
- Additionally: automatic lowering of the `MachineI2CBackend` retry
  default to 1 on ESP32 if firmware ≥ v0x02 is detected (the
  slave-side NACK fix removes the need for retry).

### v2.0.0 (major — breaking, after the v2 firmware ships)

- `RbAmp.broadcast_latch(bus)` actually transmits via the I²C
  General-Call (once v2 firmware enables GC).
- Reactive power: `dev.reactive_power[ch]` / `dev.read_reactive_power(ch)`.
- Dimmer control in a companion package: the `rbamp_dimmer` PyPI package.
- Native CircuitPython support via a `busio.I2C` adapter (if there is
  interest).
- A possible non-breaking optimization: switching single-byte reads to
  bulk reads of a contiguous float block for ~4× speedup — the public
  API is preserved.

## Distribution

### PyPI (CPython)

Published on `pypi.org` under the name `rbamp`. Install:

```sh
pip install rbamp smbus2     # CPython on a Linux SBC
pip install rbamp pyftdi     # for x86 hosts via FT232H
```

### `mpremote mip` (MicroPython)

Published under `github:rb-amp/rbamp-python`. Install:

```sh
mpremote mip install github:rb-amp/rbamp-python
```

Or copy manually:

```sh
mpremote cp -r path/to/rbamp/ :rbamp/
```

For frozen-bytecode MicroPython firmware builds — include
`libs/python/rbamp/` in `freeze_modules.py`.

### Editable for development

```sh
git clone https://github.com/rb-amp/rbamp-python.git
cd rbamp-python
pip install -e .[dev]
```

## Bug reports + contributing

Open an issue:
[github.com/rb-amp/rbamp-python/issues](https://github.com/rb-amp/rbamp-python/issues)

Include in the issue the diagnostics bundle from
[10 · Troubleshooting](10_troubleshooting.md), the section "When to
contact support".

Pull requests are welcome — the package is in a pure Python 3.8+
compatible subset (no `match` statements, no `tomllib`) that runs on
CPython and MicroPython at the same time. Before submitting, run
`pytest libs/python/rbamp/tests -v` (74-93 cases depending on the
version) against a real DUT through the long-soak harness (the PR
template walks you through the steps).

## License

MIT — see [LICENSE](../LICENSE).


---

[← Troubleshooting](10_troubleshooting.md) | [Contents](README.md)
