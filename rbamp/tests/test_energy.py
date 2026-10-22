"""Unit tests for :class:`RbAmpEnergy`."""

import pytest

from rbamp import RbAmpEnergy, RbAmpPeriodSnapshot


def _snap(ch0_w=0.0, ch1_w=0.0, ch2_w=0.0, dt_ms=60_000, valid=True):
    s = RbAmpPeriodSnapshot()
    s.avg_p = [ch0_w, ch1_w, ch2_w]
    s.master_dt_ms = dt_ms
    s.valid = valid
    return s


def test_default_starts_at_zero():
    e = RbAmpEnergy()
    assert e.wh(0) == 0.0
    assert e.wh(1) == 0.0
    assert e.wh(2) == 0.0
    assert e.is_enabled


def test_tick_integrates_in_wh():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=100.0, dt_ms=60_000), channels=1)
    # 100 W * 60 s / 3600 = 1.6667 Wh
    assert abs(e.wh(0) - 100.0 * 60.0 / 3600.0) < 1e-6


def test_tick_signed_for_export():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=-50.0, dt_ms=60_000), channels=1)
    assert e.wh(0) < 0


def test_tick_ignores_invalid_snapshot():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=100.0, dt_ms=60_000, valid=False), channels=1)
    assert e.wh(0) == 0.0


def test_tick_ignores_zero_dt():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=100.0, dt_ms=0), channels=1)
    assert e.wh(0) == 0.0


def test_disable_freezes_accumulator():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=100.0, dt_ms=60_000), channels=1)
    frozen = e.wh(0)
    e.disable()
    e.tick(_snap(ch0_w=999.0, dt_ms=60_000), channels=1)
    assert e.wh(0) == frozen


def test_reset_one_channel_only():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=100.0, ch1_w=200.0, dt_ms=60_000), channels=2)
    e.reset(0)
    assert e.wh(0) == 0.0
    assert e.wh(1) > 0


def test_reset_all_channels():
    e = RbAmpEnergy()
    e.tick(_snap(ch0_w=100.0, ch1_w=200.0, ch2_w=300.0, dt_ms=60_000), channels=3)
    e.reset_all()
    assert e.wh(0) == 0.0
    assert e.wh(1) == 0.0
    assert e.wh(2) == 0.0


def test_wh_out_of_range_returns_zero():
    e = RbAmpEnergy()
    assert e.wh(3) == 0.0
    assert e.wh(-1) == 0.0


def test_tick_respects_channel_count():
    e = RbAmpEnergy()
    # avg_p[2] = 300 W but only 2 channels populated — ch 2 untouched
    e.tick(_snap(ch0_w=100.0, ch1_w=200.0, ch2_w=300.0, dt_ms=60_000), channels=2)
    assert e.wh(2) == 0.0


def test_tick_grows_for_senior_sku():
    """A 7-channel snapshot integrates all 7 channels — the accumulator grows
    from the default 3 on first sight of a wider device (UI5/UI7)."""
    e = RbAmpEnergy()
    s = RbAmpPeriodSnapshot(channels=7)
    s.avg_p = [float(100 * (ch + 1)) for ch in range(7)]   # 100..700 W
    s.master_dt_ms = 3_600_000                             # 1 h → Wh == W
    s.valid = True
    e.tick(s, channels=7)
    for ch in range(7):
        assert e.wh(ch) == pytest.approx(100.0 * (ch + 1), rel=1e-9)
    # reset_all now zeroes every grown channel
    e.reset_all()
    assert all(e.wh(ch) == 0.0 for ch in range(7))


def test_wh_still_zero_past_grown_range():
    """wh() past the populated range stays 0.0 (no IndexError)."""
    e = RbAmpEnergy()
    assert e.wh(3) == 0.0      # before growth
    s = RbAmpPeriodSnapshot(channels=5)
    s.avg_p = [10.0, 10.0, 10.0, 10.0, 10.0]
    s.master_dt_ms = 3_600_000
    s.valid = True
    e.tick(s, channels=5)
    assert e.wh(4) == pytest.approx(10.0, rel=1e-9)
    assert e.wh(5) == 0.0      # past grown range
