"""Unit tests for :class:`RbAmpEnergy`."""

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
