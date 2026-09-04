# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.5.0] — 2026-09-04

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
- **Registry-driven model validation**: `libs/spec/sensor_models.yaml` is now the
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

## [1.4.0] — 2026-08-19

Senior-SKU support (UI5/UI7), senior-channel RT metrics, fleet-aligned
snapshots for multi-channel modules.

## [1.3.1] — 2026-07-30
### Changed
- Refreshed the multi-module examples (`examples_cpython/03_multi_module_broadcast.py`,
  `examples_upy/03_multi_module.py`) and the whole-home energy-balance examples
  (`examples_cpython/07_home_energy_balance.py`, `examples_upy/08_home_energy_balance.py`)
  to the v1.3 coherent General-Call latch (`CAP_GC_LATCH`): `RbAmpFleet.enable_gc_all()`
  → `gc_latch()` → `check_sync()`, replacing the pre-v1.3 sequential per-device latching.
  The whole-home example now also publishes a `home/energy/sync/in_sync` MQTT topic.
  Examples-only patch — library API unchanged.

