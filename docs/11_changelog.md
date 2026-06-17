# 11 · Changelog

## 1.3.0 — Fleet + v1.3 protocol

Brought the `rbamp` Python package in line with the rbAmp v1.3 wire
contract and the cross-platform reference model (Arduino / ESP-IDF /
ESPHome / STM32 HAL). Bench-validated: **11/11 PASS** on a heterogeneous
Fix-A fleet (UI1 + I2 + I3) standfw 2026-06-17 + **179/179 mock
tests** PASS. **Schema CRC**: `0x5FB3E9F3` (registers_v2).

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
  REG_FLEET_CONFIG.bit0 + save + reset; ~1 s blocking; idempotent
  no-op if already correct), `read_fleet_config()`, `set_group_id()`
  / `read_group_id()`, `read_gc_tick()` (u16 witness; 0xFFFF = never
  received), `RbAmp.broadcast_latch_group(bus, group, tick)` static
  (5-byte GC frame `A5 27 g tl th`).
- **Exception hierarchy**: `RbAmpError` base + `RbAmpIOError`,
  `RbAmpTimeoutError`, `RbAmpStaleError`, `RbAmpParamError`,
  `RbAmpModeError`, `RbAmpVersionError`.

### Changed

- **Energy** is integrated against the master wall-clock, **never**
  against the chip's diagnostic `latch_ms` (the chip timer undercounts
  by ~25-30 %; HW-validated bench measure: master_dt/wall = 0.999 vs
  latch_ms/wall = 0.743 = **26 % undercount**). On CPython use
  `time.monotonic()`; on MicroPython use `time.ticks_ms()` +
  `time.ticks_diff()`. On a **stale** read (`PERIOD_VALID = 0`) the
  library raises `RbAmpStaleError` and does **NOT** reset the anchor;
  the next valid snapshot covers the full interval, the firmware
  preserves the accumulator → the integration math stays exact, with
  no double- or under-counting.
- **CT model** codes are a per-class accepted set (`SCT_013 {1,2,3,4,6}`,
  `WIRED_CT {1,2,3}`, `BUILTIN_CT ∅`); `5` (SCT-013-100) and `7`
  (SCT-013-060) are reserved-uncharacterised, and the library raises
  `RbAmpParamError` **before** any I²C operation. `REG_CT_MODEL` is
  pure staging: bind via a per-channel command, and multi-channel binds
  are **order-independent** on v1.3 firmware (on legacy v1.x the
  defensive descending pattern is retained in the `set_ct_model_ch`
  cycle).
- **Address change** is a production-OK two-phase magic commit; develop
  mode is **not required**. `prepare_address_change()` arms the candidate +
  the `0xA5` magic window of 5 seconds; `commit_address_change()` issues
  `CMD_COMMIT_ADDR` + reset. The library guarantees the arm-state is cleared
  via try/finally on partial failure; an expired window →
  `RbAmpTimeoutError`. Compatible with legacy v1.x firmware via a
  capability-gated fallback.
- **Variant detection** — `read_variant()` reads `REG_HW_VARIANT`
  (the v1.3 canonical SKU byte). The NACK-probe path has been
  **removed** (the firmware never NACKs reads; unmapped reads → `0x00`).
- **Multi-byte WRITE byte-at-a-time** (F.13 wire-canon, HW-confirmed).
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
