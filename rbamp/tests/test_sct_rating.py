# SPDX-License-Identifier: MIT
"""
SCT-013 rating-switch correction — libs/spec/SCT_RATING_SWITCH.md.

Two layers:
  * pure policy (rbamp._sct_rating.rating_correction) against the spec's
    §Tests values;
  * RbAmp integration against the mock backend — class gating, the same-boot
    CMD_RESET, and the scale applied to I / P / energy (not PF / U).
"""
import pytest

from rbamp import RbAmp, RbAmpParamError
from rbamp import _sct_rating as SCT
from rbamp import _registers_v2 as R
from rbamp.tests.conftest import MockBackend


# ------------------------------------------------------------------
# Pure policy — spec §Tests
# ------------------------------------------------------------------

def test_spec_flash_state_x5():
    """Flash state, X = 5, G_a = 0.6414: s = 2.1622 x 0.962 / 0.6349 = 3.276."""
    s, action, _ = SCT.rating_correction(5, 0.6414)
    assert action == SCT.RATING_OK
    assert s == pytest.approx(2.1622 * 0.962 / 0.6349, rel=1e-6)
    assert s == pytest.approx(3.276, abs=1e-3)


def test_spec_model_3_is_unity():
    """X = 3 (the calibration model): s = 1."""
    s, action, _ = SCT.rating_correction(3, 0.6349)
    assert action == SCT.RATING_UNITY
    assert s == 1.0


def test_spec_same_boot_requests_one_reset():
    """Same-boot state G_a == preset(5): one CMD_RESET requested."""
    s, action, _ = SCT.rating_correction(5, SCT.PRESET_G[5])   # 2.1622
    assert action == SCT.RATING_NEEDS_RESET
    assert s == 1.0
    # after a reset that did NOT recover (still raw preset) -> uncalibrated, k=1
    s2, action2, _ = SCT.rating_correction(5, SCT.PRESET_G[5], post_reset=True)
    assert action2 == SCT.RATING_WARN_UNCAL
    assert s2 == pytest.approx(0.962, rel=1e-9)   # == c(5)


def test_spec_gain_off_8pct_warns_unity():
    """G_a off by 8 %: warning + s = 1."""
    s, action, _ = SCT.rating_correction(5, 0.6349 * 1.08)
    assert action == SCT.RATING_WARN_GAIN
    assert s == 1.0


def test_gain_within_5pct_ok():
    """A factory gain within +-5 % of preset(3) takes the normal path; the
    scale is independent of G_a (it cancels against k)."""
    s, action, _ = SCT.rating_correction(5, 0.6349 * 1.04)
    assert action == SCT.RATING_OK
    assert s == pytest.approx(2.1622 * 0.962 / 0.6349, rel=1e-6)


def test_unknown_model_is_unity():
    s, action, _ = SCT.rating_correction(99, 0.6349)
    assert action == SCT.RATING_UNITY
    assert s == 1.0


def test_non_100_models_have_c_equal_1():
    """Only the -100 (model 5) carries a rating correction; others c = 1."""
    for model, expect in ((1, None), (2, None), (4, None), (6, None), (7, None)):
        s, action, _ = SCT.rating_correction(model, 0.6349)
        assert action == SCT.RATING_OK
        assert s == pytest.approx(SCT.PRESET_G[model] / 0.6349, rel=1e-6)


# ------------------------------------------------------------------
# RbAmp integration against the mock
# ------------------------------------------------------------------

def _sct_mock(model, gain, cls=1, channels=3):
    m = MockBackend(slave_addr=0x50, channels=channels, has_voltage=True)
    m.set_sensor_class(cls)
    m.set_model(0, model)
    m.set_gain(0, gain)
    return m


def test_integration_flash_state_scales_i_p_energy():
    """X = 5 flash state: I, P and period avg_p are scaled by s; PF and U are not."""
    m = _sct_mock(model=5, gain=0.6414)
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    s = 2.1622 * 0.962 / 0.6349

    assert dev.ct_rating_scale(0) == pytest.approx(s, rel=1e-6)
    # ch0 raw seeds: I=1.23 A, P=100 W, PF=0.95, U=230 V, avg_p=95 W
    assert dev.read_current(0) == pytest.approx(1.23 * s, rel=1e-6)
    assert dev.read_power(0) == pytest.approx(100.0 * s, rel=1e-6)
    assert dev.read_period_avg_power(0) == pytest.approx(95.0 * s, rel=1e-6)
    # not scaled
    assert dev.read_power_factor(0) == pytest.approx(0.95, rel=1e-6)
    assert dev.read_voltage() == pytest.approx(230.0, rel=1e-6)


def test_integration_model_3_no_scale():
    m = _sct_mock(model=3, gain=0.6349)
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    assert dev.ct_rating_scale(0) == 1.0
    assert dev.read_current(0) == pytest.approx(1.23, rel=1e-6)


def test_integration_class_2_not_touched():
    """A factory-paired class-2 module is never scaled (model untouched)."""
    m = _sct_mock(model=10, gain=0.6414, cls=2)
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    assert dev.ct_rating_scale(0) == 1.0
    assert dev.read_current(0) == pytest.approx(1.23, rel=1e-6)


def test_integration_same_boot_issues_one_reset_then_recovers():
    """Same-boot raw preset(5): begin() issues exactly one CMD_RESET; the mock
    recovers the factory gain on reset and the normal scale is then applied."""
    m = _sct_mock(model=5, gain=SCT.PRESET_G[5])   # raw preset == same-boot
    m.set_gain_after_reset(0, 0.6414)              # flash state recovered on reset
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    # exactly one reset from the rating compute (begin() itself issues no reset)
    assert m.reset_count == 1
    s = 2.1622 * 0.962 / 0.6349
    assert dev.ct_rating_scale(0) == pytest.approx(s, rel=1e-6)
    assert dev.ct_rating_status(0)[0] == SCT.RATING_OK


def test_integration_same_boot_uncalibrated_warns():
    """If the gain stays the raw preset even after the reset, the channel is
    uncalibrated: warn, k = 1, s = c(X)."""
    m = _sct_mock(model=5, gain=SCT.PRESET_G[5])
    # no set_gain_after_reset -> gain stays preset(5) after reset
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    assert m.reset_count == 1
    assert dev.ct_rating_status(0)[0] == SCT.RATING_WARN_UNCAL
    assert dev.ct_rating_scale(0) == pytest.approx(0.962, rel=1e-6)


def test_integration_bad_gain_warns_unity():
    """Factory gain 8 % off preset(3): warn and s = 1 (do not guess)."""
    m = _sct_mock(model=5, gain=0.6349 * 1.08)
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    assert dev.ct_rating_scale(0) == 1.0
    assert dev.ct_rating_status(0)[0] == SCT.RATING_WARN_GAIN
    assert dev.read_current(0) == pytest.approx(1.23, rel=1e-6)


def test_integration_fw_self_correct_disables():
    """Firmware that self-corrects (REG_CAPABILITY bit 12 = CAP_SCT_RATING_SELF,
    reserved in fw 1.4.20) -> library applies s = 1, no reset (host test #5).
    Detected by the capability bit, never by VERSION."""
    m = _sct_mock(model=5, gain=0.6414)
    # bit 12 = 0x1000 in the u16 LE REG_CAPABILITY (0x57 lo, 0x58 hi)
    m._regs[R.REG_CAPABILITY] = 0x00
    m._regs[R.REG_CAPABILITY + 1] = 0x10
    dev = RbAmp(m, addr=0x50)
    assert dev._sct_self_corrects() is True
    dev.begin()
    assert dev.ct_rating_scale(0) == 1.0
    assert m.reset_count == 0
    assert dev.read_current(0) == pytest.approx(1.23, rel=1e-6)


def test_integration_disable_toggle():
    """set_rating_correction(False) restores raw readings and recomputes."""
    m = _sct_mock(model=5, gain=0.6414)
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    assert dev.read_current(0) != pytest.approx(1.23, rel=1e-3)
    dev.set_rating_correction(False)
    assert dev.rating_correction_enabled is False
    assert dev.ct_rating_scale(0) == 1.0
    assert dev.read_current(0) == pytest.approx(1.23, rel=1e-6)


def test_integration_energy_inherits_scale():
    """Wh accumulator integrates the scaled avg_p."""
    m = _sct_mock(model=5, gain=0.6414)
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    s = 2.1622 * 0.962 / 0.6349
    dev.read_period_snapshot()          # anchor
    m.advance_ms(3_600_000)             # +1 h
    dev.read_period_snapshot()          # integrate (ch0 avg_p = 95 W * s)
    assert dev.energy.wh(0) == pytest.approx(95.0 * s, rel=1e-3)


def test_unset_class_no_scale():
    """An UNSET-class module (no SCT provisioning) is not scaled."""
    m = MockBackend(slave_addr=0x50, channels=3, has_voltage=True)  # class 0
    dev = RbAmp(m, addr=0x50)
    dev.begin()
    assert dev.ct_rating_scale(0) == 1.0
    assert dev.read_current(0) == pytest.approx(1.23, rel=1e-6)
