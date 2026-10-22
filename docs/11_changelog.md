# 11 · Changelog

This page mirrors `CHANGELOG.md` from the repository.

## 1.6.0 — 2026-10-22

> Public releases resume with this version; intermediate versions (1.4.x, 1.5.x) were released internally only.

Adds the STD module line (16-bit register map) alongside the existing modules, per-channel period energy on 5/7-channel modules, and an SCT-013 rating-switch correction. One `RbAmp` class serves both module lines; the module type is detected automatically at `begin()`.

### Added
- SCT-013-060 and -100 are accepted (they were refused client-side as reserved); sensor-model clip limits updated to measured values.
- STD module line (16-bit map), auto-detected. Common readers (`voltage`, `current`, `power`, `power_factor`, `frequency`) work on every module; STD-only calls raise `RbAmpNotSupportedError` on a module that does not provide them.
  - On-device energy: `dev.read_std_energy()` (active import/export/net + reactive, Wh/mWh/µWh, per-channel); `RbAmp.std_energy_delta(prev, now)` (power-cut-safe).
  - Multi-phase: `dev.read_voltage_angle(phase)`, `dev.read_phase_sequence()`, `dev.read_reactive(ch)`, `dev.read_apparent(ch)`.
  - Starting current: `dev.get_start_current()` / `dev.set_start_current(µA)` + `dev.save_config()`.
  - `dev.read_std_voltage/channel/totals/config/health()`, `dev.set_time()`, `dev.is_std`; value objects `RbAmpStdEnergy/Voltage/Channel/Config/Health`; `RbAmpNotSupportedError`.
  - Validated on CPython (`smbus2`); the MicroPython 16-bit path is implemented but not yet hardware-validated.
- Per-channel period energy on 5/7-channel modules: `dev.read_period_avg_power(ch)` resolves every channel; the period snapshot and the Wh accumulator cover all channels.
- SCT-013 rating-switch correction: when a channel is switched to a different sensor model, current/power/energy are corrected to the selected model relative to the gain the module applies; power factor and voltage unchanged. Automatic; `dev.set_rating_correction()`, `dev.ct_rating_scale(ch)`, `dev.ct_rating_status(ch)`. Firmware that performs this itself is detected and left alone.
- Read-before-write guard on sensor-class / CT-model writes; `dev.set_ct_model_ch_force()`, `dev.read_sensor_class()`.

### Fixed
- Period snapshot on 5/7-channel modules no longer errored on channels beyond the third.

## 1.5.0 — 2026-09-04

Per-channel CT-model assignment for all channels, senior-SKU support (UI5/UI7),
sensor-model registry, and persist fix. Firmware: v1.4.18+.

### Added
- **UI5 / UI7 support (senior SKUs)**: auto-detected via `REG_TOPOLOGY` +
  `REG_HW_VARIANT`; `dev.channels` returns 5 or 7; all live/period readers
  work uniformly for `ch 0..channels-1`.
- **Per-channel CT model, any channel**: `dev.set_ct_model_ch(channel, code)` and
  `dev.read_ct_model_ch(channel)` now accept `channel 0..channels-1`. Channels
  0-2 use the flat register path; channels 3+ use the channel-access window
  (field 15).
- **Registry-driven model validation**: the model registry is now the
  single source for CT model codes. Auto-generated into `rbamp/_sensor_models.py`;
  the old hand-maintained `_CT_ACCEPTED` dict is gone.
- `RbAmpWindowField.CT_MODEL = 15` (public enum).
- `RbAmpFleet.aligned_snapshot()` populates per-channel lists for the module's
  full channel width.

### Fixed
- **CT model / sensor class now persist in production.** Prior versions persisted
  these via `CMD_SAVE_GAINS`, which is factory-gated — on a production module the
  save was a silent no-op, so the binding applied to RAM but was lost on reboot.
  Now persists via `CMD_SAVE_USER_CONFIG` (ungated). **Users of prior versions in
  production silently lost the CT model binding on every reboot — update before
  shipping devices.**

## 1.4.0 — 2026-08-19

Senior-SKU support (UI5/UI7), senior-channel RT metrics, fleet-aligned
snapshots for multi-channel modules.

## 1.3.1 — 2026-07-30
### Changed
- Refreshed the multi-module examples (`examples_cpython/03_multi_module_broadcast.py`,
  `examples_upy/03_multi_module.py`) and the whole-home energy-balance examples
  (`examples_cpython/07_home_energy_balance.py`, `examples_upy/08_home_energy_balance.py`)
  to the v1.3 coherent General-Call latch (`CAP_GC_LATCH`): `RbAmpFleet.enable_gc_all()`
  → `gc_latch()` → `check_sync()`, replacing the pre-v1.3 sequential per-device latching.
  The whole-home example now also publishes a `home/energy/sync/in_sync` MQTT topic.
  Examples-only patch — library API unchanged.

## 1.3.0 — Fleet + v1.3 protocol

Brought the `rbamp` Python package in line with the rbAmp v1.3 wire
contract and the cross-platform reference model (Arduino / ESP-IDF /
ESPHome / STM32 HAL). Mock test suite: **179/179** PASS.

### Added

- **`RbAmpFleet`** — host-side multi-module manager: `scan()` (with
  conflict-detect + Tier-2 wedge-canary), `add()`, `find()`, `count`,
  iteration; batched `poll_all()` (failure-isolated, MISS-resilient);
  fleet-wide `total_power()` / `total_energy_wh()` / `poll_errors()`;
  General-Call sync (`enable_gc_all()` / `gc_latch()` / `check_sync()`);
  `assign_address()` / `check_conflict()`. Soft cap
  `RBAMP_FLEET_MAX_MODULES = 16`.
- **POD structures**: `RbAmpFleetPoll` (per-member poll result),
  `RbAmpFleetSync` (per-member GC sync witness).
- **Identity / capability**: `read_variant()` (HW_VARIANT 0x55 byte,
  1..6 = UI1/UI2/UI3/I1/I2/I3), `read_capability()` (u16 LE feature
  bitmap; branch on bits, not version heuristics), `read_product_id()`
  (0x01 = rbAmp sensor family), `read_uid()` (12 bytes), `read_label()`
  / `set_label()` (8-byte ASCII via byte-loop write).
- **Event / error channel (v1.3 two-channel model)**: `read_last_error()`
  (device REG_ERROR byte, one wire read), `read_event_flags()`,
  `clear_event_flags(mask)`, `has_error()` (sticky bit3 helper),
  `clear_error()` (CMD_CLEAR_ERROR).
- **Per-channel CT configuration**: `configure_channels(class, models, n)`
  (batched, one terminal flash SAVE; variant-clamped; client-side
  per-class validation `_validate_ct_code`); `read_ct_model_ch(ch)`
  (applied-model mirror read from 0x51-0x53).
- **Per-device fleet primitives**: `enable_gc(enable=True)` (RMW
  REG_the GC_ENABLE flag of FLEET_CONFIG + save + reset; ~1 s blocking; idempotent
  no-op if already correct), `read_fleet_config()`, `set_group_id()`
  / `read_group_id()`, `read_gc_tick()` (u16 witness; 0xFFFF = never
  received), `RbAmp.broadcast_latch_group(bus, group, tick)` static
  (5-byte GC frame `A5 27 g tl th`).
- **Exception hierarchy**: `RbAmpError` base + `RbAmpIOError`,
  `RbAmpTimeoutError`, `RbAmpStaleError`, `RbAmpParamError`,
  `RbAmpModeError`, `RbAmpVersionError`.

### Changed

- **Energy** is integrated against the master wall-clock, **never**
  against the module's diagnostic `latch_ms` (a diagnostic timer, not
  for energy). On CPython use
  `time.monotonic()`; on MicroPython use `time.ticks_ms()` +
  `time.ticks_diff()`. On a **stale** read (`PERIOD_VALID = 0`) the
  library raises `RbAmpStaleError` and does **NOT** reset the anchor;
  the next valid snapshot covers the full interval, the firmware
  preserves the accumulator → the integration math stays exact, with
  no double- or under-counting.
- **CT model** codes are a per-class accepted set (`SCT_013 {1,2,3,4,5,6,7}`,
  `WIRED_CT {1,2,3}`, `BUILTIN_CT ∅`); a code outside the class set makes the library raise
  `RbAmpParamError` **before** any I²C operation. `REG_CT_MODEL` is
  pure staging: bind via a per-channel command, and multi-channel binds
  are **order-independent** on v1.3 firmware (on legacy v1.x the
  defensive descending pattern is retained in the `set_ct_model_ch`
  cycle).
- **Address change** is a production-OK two-phase magic commit; factory
  mode is **not required**. `prepare_address_change()` arms the candidate +
  the `0xA5` magic window of 5 seconds; `commit_address_change()` issues
  `CMD_COMMIT_ADDR` + reset. The library guarantees the arm-state is cleared
  via try/finally on partial failure; an expired window →
  `RbAmpTimeoutError`. Compatible with legacy v1.x firmware via a
  capability-gated fallback.
- **Variant detection** — `read_variant()` reads `REG_HW_VARIANT`
  (the v1.3 canonical SKU byte). The NACK-probe path has been
  **removed** (the firmware never NACKs reads; unmapped reads → `0x00`).
- **Multi-byte WRITE byte-at-a-time** .
  The library's `set_label()` writes the 8 LABEL bytes with a byte-loop
  automatically (rbAmp does not auto-increment writes).

### Bench-robustness (Stage 1B)

- **CPython** `SMBusBackend` exposes a generic NACK-retry
  (`retry_attempts=3`, `retry_gap_ms=2`) with `retry_count_total` +
  `retry_exhaustion_count` diagnostics. It is used on **all** wire
  operations (config writes follow the same discipline as reads —
  otherwise a config write that is silently dropped on a contended bus
  would be invisible).
- **MicroPython-on-ESP32** `MachineI2CBackend` keeps the IDF
  i2c_master 50 kHz spin-discipline (NACK-retry with a tight wait
  between attempts so a contended bus does not silently drop a
  write). See
  [09 · API Reference](09_api_reference.md)
  → "Wire-protocol details" for the full contract.

### Notes

- **Marginal bus / ESP32-based hosts**: the same IDF i2c_master driver
  used in native ESP-IDF projects — it can hang on a held bus below the
  library level. We recommend external ~4.7 kΩ pull-ups, no debugger
  NRST in production, and an app-level task-watchdog on the polling task
  as a recovery posture. See chapter
  [10 · Troubleshooting](10_troubleshooting.md).

## 1.1.0 — extended period snapshot

Extended period snapshot (max P, latch_ms diagnostic), master-side
Wh accounting.

## 1.0.0 — Initial release

Single-device RT metering (RMS U / I / P / PF / frequency), period
energy (Wh) integration, CT-model + sensor-class configuration, and
I²C address change. Dual-backend (CPython smbus2 / MicroPython
machine.I2C).
