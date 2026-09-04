# SPDX-License-Identifier: MIT
"""Long-soak regression test — opt-in via ``--soak`` or ``RBAMP_SOAK=1``.

By default the file collects only one tiny "engine wiring" test that runs
against the mock backend in <2 seconds (so the full pytest suite stays
sub-second). The full soak (≥60 s on real bench, ≥3600 s for the release
gate) is gated behind the ``--soak`` flag or the ``RBAMP_SOAK`` env var.

Pytest CLI:
    pytest libs/python/rbamp/tests/test_long_soak.py -v --soak
    RBAMP_SOAK_DURATION_S=60 pytest libs/python/rbamp/tests/test_long_soak.py -v --soak

Bench (smbus2 real bus):
    RBAMP_SOAK_DURATION_S=60 pytest -v --soak --bus 1 --addr 0x50

The soak engine itself lives at ``libs/python/tools_bench/_soak_core.py`` —
the test imports it and drives via either the mock backend (no hardware)
or smbus2 (Linux SBC + DUT).
"""
import os
import sys

import pytest

# Make tools_bench/ importable so we can pull in _soak_core
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "..", "tools_bench"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

from _soak_core import run_soak, check_pass  # noqa: E402

from rbamp import RbAmp  # noqa: E402


@pytest.fixture
def soak_enabled(request):
    cli = request.config.getoption("--soak")
    env = os.environ.get("RBAMP_SOAK") in ("1", "yes", "true")
    return cli or env


@pytest.fixture
def soak_duration():
    return float(os.environ.get("RBAMP_SOAK_DURATION_S", "60"))


@pytest.fixture
def soak_cadence():
    return float(os.environ.get("RBAMP_SOAK_CADENCE_S", "10"))


# ---------------------------------------------------------------------------
# Always-on engine wiring check (uses mock backend, runs fast)
# ---------------------------------------------------------------------------

def test_soak_engine_short_run_with_mock(mock_backend):
    """Engine wiring smoke — 2 cycles @ 1 s = ~2 s, mock backend.

    Verifies: counters reach end-of-run zero (mock never NACKs / never returns
    exotic floats), per-channel Wh state populated, summary dict shape.
    """
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    # Force a non-zero P so the wh-sign-check has signal
    mock_backend.set_period_avg(0, 95.0)

    rows = []
    summary = run_soak(dev, duration_s=2.0, cadence_s=1.0,
                       emit_row_callback=lambda r: rows.append(r))
    assert summary["cycles_total"] >= 2
    assert summary["cycles_valid"] == summary["cycles_total"]
    assert summary["cycles_stale"] == 0
    assert summary["cycles_failed"] == 0
    assert summary["sanity_reject_count"] == 0
    # Mock backend has no retry counter
    assert summary["retry_exhaustion_count"] == 0
    # Phase-2 follow-up: split metrics also populated
    assert "retry_count_total" in summary
    assert "retry_rate" in summary
    assert "cadence_jitter_ms_excluding_retries" in summary
    assert summary["retry_count_total"] == 0  # mock never retries
    assert summary["retry_rate"] == 0.0
    # NOTE: cadence_jitter check is intentionally NOT called via check_pass
    # because the mock backend uses a manually-advanced virtual clock
    # (now_ms = _now), so master_dt_ms reflects sleep_ms calls (~50ms)
    # rather than the real wall-clock cadence_s. Real backends drive
    # check_pass with valid scheduler-jitter numbers — verified by the
    # opt-in real-bench soak.
    # Per-channel slice present
    assert len(summary["per_channel"]) == dev.channels
    # ch0 Wh accumulated > 0 because mock_backend.set_period_avg(0, 95.0)
    assert summary["per_channel"][0]["wh_total"] > 0


def test_soak_engine_check_pass_passes_on_synthetic_clean_summary():
    """check_pass() returns (True, []) on a hand-built clean summary.

    Decouples the predicate test from mock-backend timing quirks.
    """
    clean = {
        "cycles_total": 60,
        "cycles_valid": 60,
        "cycles_stale": 0,
        "cycles_failed": 0,
        "retry_exhaustion_count": 0,
        "retry_count_total": 5,         # 5/60 ≈ 0.08 < 0.5 → OK
        "retry_rate": 5 / 60,
        "sanity_reject_count": 0,
        "master_dt_jitter_ms": 100,
        "cadence_jitter_ms_excluding_retries": 80,  # < 200 → OK
        "channels": 1,
        "per_channel": [{"ch": 0, "wh_sign_ok": True}],
    }
    ok, fails = check_pass(clean)
    assert ok, "clean synthetic summary must pass: {}".format(fails)


def test_soak_engine_check_pass_flags_retry_rate_breach():
    """retry_rate >= 0.5 is the bus-health gate."""
    sick = {
        "cycles_total": 10,
        "cycles_valid": 10,
        "cycles_stale": 0,
        "cycles_failed": 0,
        "retry_exhaustion_count": 0,
        "retry_count_total": 6,         # 6/10 = 0.6 ≥ 0.5
        "retry_rate": 0.6,
        "sanity_reject_count": 0,
        "master_dt_jitter_ms": 100,
        "cadence_jitter_ms_excluding_retries": 80,
        "channels": 1,
        "per_channel": [{"ch": 0, "wh_sign_ok": True}],
    }
    ok, fails = check_pass(sick)
    assert not ok
    assert any("retry_rate" in f and "5.2.H" in f for f in fails)


def test_soak_engine_check_pass_flags_scheduler_jitter_breach():
    """cadence_jitter ≥ 200 ms is the scheduler-quality gate."""
    laggy = {
        "cycles_total": 10,
        "cycles_valid": 10,
        "cycles_stale": 0,
        "cycles_failed": 0,
        "retry_exhaustion_count": 0,
        "retry_count_total": 0,
        "retry_rate": 0.0,
        "sanity_reject_count": 0,
        "master_dt_jitter_ms": 250,
        "cadence_jitter_ms_excluding_retries": 250,  # ≥ 200
        "channels": 1,
        "per_channel": [{"ch": 0, "wh_sign_ok": True}],
    }
    ok, fails = check_pass(laggy)
    assert not ok
    assert any("cadence_jitter" in f and "scheduler" in f for f in fails)


# ---------------------------------------------------------------------------
# Real-bench soak (opt-in via --soak or RBAMP_SOAK)
# ---------------------------------------------------------------------------

def test_soak_real_bench(soak_enabled, soak_duration, soak_cadence, request):
    """Drive the soak against a real SMBus DUT.

    Skipped unless --soak / RBAMP_SOAK is set. Default duration 60 s for
    CI smoke; override via ``RBAMP_SOAK_DURATION_S=3600`` for release gate.
    """
    if not soak_enabled:
        pytest.skip("long-soak gated; pass --soak or set RBAMP_SOAK=1")

    bus_no = request.config.getoption("--bus")
    if bus_no is None:
        pytest.skip("--bus not provided; cannot reach real DUT")

    try:
        from smbus2 import SMBus
    except ImportError:
        pytest.skip("smbus2 not installed")

    bus = SMBus(bus_no)
    try:
        dev = RbAmp(bus, request.config.getoption("--addr"))
        dev.begin()
        summary = run_soak(dev, duration_s=soak_duration,
                            cadence_s=soak_cadence)
    finally:
        bus.close()

    ok, fails = check_pass(summary)
    print("soak summary:", summary)
    assert ok, "long-soak failed pass criteria: {}".format(fails)
