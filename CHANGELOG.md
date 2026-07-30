# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.1] — 2026-07-30
### Changed
- Refreshed the multi-module examples (`examples_cpython/03_multi_module_broadcast.py`,
  `examples_upy/03_multi_module.py`) and the whole-home energy-balance examples
  (`examples_cpython/07_home_energy_balance.py`, `examples_upy/08_home_energy_balance.py`)
  to the v1.3 coherent General-Call latch (`CAP_GC_LATCH`): `RbAmpFleet.enable_gc_all()`
  → `gc_latch()` → `check_sync()`, replacing the pre-v1.3 sequential per-device latching.
  The whole-home example now also publishes a `home/energy/sync/in_sync` MQTT topic.
  Examples-only patch — library API unchanged.

