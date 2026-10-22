"""
v1.5.0 tests — per-channel CT_MODEL via channel-access window field 15.

Root release contract (firmware v1.4.18 signed on production silicon):
  1. Window 0x32 = (field<<4)|ch, field 15 = CT_MODEL, RW, per-channel.
     Write: 0x3C=model, 0x3D=0, 0x3E=0, 0x3F=0 — each byte its own txn.
     Read : 0x3C..0x3F u32 LE, low byte significant.
  2. Unknown (class, model) -> ERR_PARAM 0xFE, channel keeps prior model.
  3. Channel nibble 0..15; validate against variant channels, not hardcode 3.
  4. Model list ONLY from libs/spec/sensor_models.yaml (registry).
  5. Public API canon: one call with channel index (route internally).
  6. Test rule: every DUT write is confirmed by readback.

v1.6.0 note: WIRED_CT is a factory-paired class, so the read-before-write
guard refuses a model change on the plain `set_ct_model_ch`. The wire-mechanics
tests below use the service path `set_ct_model_ch_force` (identical apply/save/
readback machinery, guard bypassed) on these paired fixtures. The guard itself
is covered in test_v13_stage2 (plain-setter refusal, no-op skip, force).
"""
import pytest

from rbamp import (
    RbAmp, RbAmpSensorClass, RbAmpParamError, RbAmpWindowField,
)
from rbamp import _registers_v2 as R
from rbamp import _sensor_models as SM
from rbamp.tests.conftest import MockBackend


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _ui7_wired_ct(addr=0x50):
    """UI7 senior SKU with sensor class pre-pinned to WIRED_CT."""
    m = MockBackend(slave_addr=addr, channels=7, has_voltage=True)
    m._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.WIRED_CT)
    return m


def _ui3_wired_ct(addr=0x50):
    m = MockBackend(slave_addr=addr, channels=3, has_voltage=True)
    m._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.WIRED_CT)
    return m


# ---------------------------------------------------------------------------
# CHFIELD_CT_MODEL constant + enum
# ---------------------------------------------------------------------------

def test_chfield_ct_model_constant():
    """CHFIELD_CT_MODEL is emitted by codegen at 15."""
    assert R.CHFIELD_CT_MODEL == 15


def test_window_field_enum_has_ct_model():
    assert int(RbAmpWindowField.CT_MODEL) == 15


# ---------------------------------------------------------------------------
# set_ct_model_ch — extended range (rule 3: variant-aware, not hardcoded 3)
# ---------------------------------------------------------------------------

def test_set_ct_model_ch_accepts_full_ui7_range():
    """UI7 accepts channels 0..6; each stays independent."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    # Assign a different characterized WIRED_CT model to each channel.
    # Registry rows for class 2: 6=CLAMP2000_1A, 7=5A, 8=10A, 9=20A, 12=80A.
    plan = {0: 6, 1: 7, 2: 8, 3: 9, 4: 6, 5: 12, 6: 7}
    for ch, code in plan.items():
        dev.set_ct_model_ch_force(ch, code)
    # Rule 6: readback each channel to confirm the write landed.
    for ch, code in plan.items():
        assert dev.read_ct_model_ch(ch) == code, \
            "ch{} readback mismatch: expected {}, got {}".format(
                ch, code, dev.read_ct_model_ch(ch))


def test_set_ct_model_ch_rejects_out_of_range():
    """Channel guard uses variant channels, not hardcoded 3."""
    m = _ui3_wired_ct()  # 3-channel variant
    dev = RbAmp(m, addr=0x50); dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch_force(3, 6)  # UI3 has only 0..2
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch_force(-1, 6)


def test_set_ct_model_ch_ui7_rejects_channel_7():
    """UI7: channel 7 is beyond the last valid index (0..6)."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch_force(7, 6)


# ---------------------------------------------------------------------------
# Rule 1: window write sequence (select + 4 byte txns)
# ---------------------------------------------------------------------------

def test_set_ct_model_ch5_uses_window_write_sequence():
    """Wire canon for ch 3+: select field 15, then 0x3C..0x3F each own txn."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    m.writes.clear()

    dev.set_ct_model_ch_force(5, 6)  # WIRED_CT CLAMP2000_1A

    # Filter to just the window-related writes for ch=5, field=15.
    window_writes = [w for w in m.writes if w[1] in (
        R.REG_CHANNEL_SELECT, R.REG_CHANNEL_DATA,
        R.REG_CHANNEL_DATA + 1, R.REG_CHANNEL_DATA + 2, R.REG_CHANNEL_DATA + 3)]
    # Selector first: (15<<4)|5 = 0xF5
    assert window_writes[0] == (0x50, R.REG_CHANNEL_SELECT, 0xF5)
    # Then four separate byte transactions on 0x3C..0x3F
    assert window_writes[1] == (0x50, R.REG_CHANNEL_DATA + 0, 6)
    assert window_writes[2] == (0x50, R.REG_CHANNEL_DATA + 1, 0)
    assert window_writes[3] == (0x50, R.REG_CHANNEL_DATA + 2, 0)
    assert window_writes[4] == (0x50, R.REG_CHANNEL_DATA + 3, 0)


def test_read_ct_model_ch5_reselects_before_read():
    """Re-select before every read (rule #1 of the window contract)."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    dev.set_ct_model_ch_force(5, 7)
    m.writes.clear()

    result = dev.read_ct_model_ch(5)

    # Must include a fresh selector write for (field=15, ch=5)
    assert (0x50, R.REG_CHANNEL_SELECT, 0xF5) in m.writes
    assert result == 7


# ---------------------------------------------------------------------------
# Rule 2: ERR_PARAM surfaces without clobbering
# ---------------------------------------------------------------------------

def test_set_ct_model_ch5_err_param_surfaced():
    """Module rejects (class, code); library raises and channel keeps prior model."""
    m = _ui7_wired_ct()
    # First set ch5 to a known-good model so we can assert non-clobber.
    m._channel_models[5] = 6  # WIRED_CT CLAMP2000_1A
    # Now simulate the module refusing code 7 (mock err_param_pairs list).
    m.err_param_pairs.add((int(RbAmpSensorClass.WIRED_CT), 7))

    dev = RbAmp(m, addr=0x50); dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch_force(5, 7)

    # Rule 2 (non-clobber): channel keeps prior model.
    assert dev.read_ct_model_ch(5) == 6


def test_set_ct_model_ch5_err_param_clears_error_reg():
    """After surfacing ERR_PARAM, library issues CMD_CLEAR_ERROR."""
    m = _ui7_wired_ct()
    m.err_param_pairs.add((int(RbAmpSensorClass.WIRED_CT), 8))
    dev = RbAmp(m, addr=0x50); dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch_force(4, 8)
    # After exception + cleanup, REG_ERROR should be back to OK.
    assert m._regs[R.REG_ERROR] == R.DEV_ERR_OK


# ---------------------------------------------------------------------------
# Rule 4: registry as sole source (no hand-maintained whitelist)
# ---------------------------------------------------------------------------

def test_client_rejects_reserved_row_client_side():
    """Reserved status in registry (row exists, no preset table entry)."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    # WIRED_CT model 11 = CLAMP2000_50A, status=reserved.
    with pytest.raises(RbAmpParamError) as ei:
        dev.set_ct_model_ch_force(3, 11)
    assert "RESERVED" in str(ei.value)


def test_client_rejects_unknown_row_client_side():
    """Codes not in the registry at all."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    with pytest.raises(RbAmpParamError) as ei:
        dev.set_ct_model_ch_force(3, 200)
    assert "unknown" in str(ei.value).lower()


def test_registry_has_v145_wired_ct_expansion():
    """Sanity: registry knows the four new WIRED_CT models 6-9 (SKU-1/5/10/20 A)."""
    for code in (6, 7, 8, 9):
        entry = SM.lookup(2, code)
        assert entry is not None
        _name, desc, status = entry
        assert status == "characterized", "class 2 code {} status: {}".format(code, status)
        assert "Clamp 2000:1" in desc


def test_registry_reserved_wired_ct_11_13():
    """Rows allocated but no preset: 11/13 for 50/100 A SKUs.
    (10 = CLAMP2000_30A became characterized in the 2026-09-04 registry.)"""
    for code in (11, 13):
        entry = SM.lookup(2, code)
        assert entry is not None
        assert entry[2] == "reserved"


# ---------------------------------------------------------------------------
# Rule 5: single public call with channel index (route centralization)
# ---------------------------------------------------------------------------

def test_read_current_uses_central_route(mock_ui7):
    """ch<=2 hits flat block; ch>=3 hits window — one internal method."""
    m = mock_ui7
    # Seed distinct values in flat block ch2 and window ch5.
    m._set_float(R.REG_V03_I0_RMS + 2 * 4, 7.7)   # flat ch2
    m.set_window_value(0, 5, 11.11)               # window ch5
    dev = RbAmp(m, addr=0x50); dev.begin()

    assert dev.read_current(2) == pytest.approx(7.7, abs=1e-3)
    assert dev.read_current(5) == pytest.approx(11.11, abs=1e-3)


def test_route_used_by_all_four_readers(mock_ui7):
    """Sanity: current/peak/power/pf all delegate to _route_channel_read."""
    m = mock_ui7
    # Seed each window field with a distinct value on ch4.
    m.set_window_value(R.CHFIELD_I_RMS,  4, 4.44)
    m.set_window_value(R.CHFIELD_I_PEAK, 4, 6.28)
    m.set_window_value(R.CHFIELD_P_REAL, 4, 444.0)
    m.set_window_value(R.CHFIELD_PF,     4, 0.94)
    dev = RbAmp(m, addr=0x50); dev.begin()

    assert dev.read_current(4)       == pytest.approx(4.44, abs=1e-3)
    assert dev.read_current_peak(4)  == pytest.approx(6.28, abs=1e-3)
    assert dev.read_power(4)         == pytest.approx(444.0, abs=1e-3)
    assert dev.read_power_factor(4)  == pytest.approx(0.94, abs=1e-3)


# ---------------------------------------------------------------------------
# Rule 6: readback rule (every write confirmed by read)
# ---------------------------------------------------------------------------

def test_write_readback_confirms_ch4_ui7():
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    for code in (6, 7, 8, 9, 12):
        dev.set_ct_model_ch_force(4, code)
        assert dev.read_ct_model_ch(4) == code, \
            "readback failed for code {}: got {}".format(code, dev.read_ct_model_ch(4))


def test_write_readback_confirms_ch0_flat_path():
    """Rule 6 also applies to the flat ch0-2 path (backward-compat verified)."""
    m = _ui3_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    dev.set_ct_model_ch_force(0, 6)
    dev.set_ct_model_ch_force(1, 7)
    dev.set_ct_model_ch_force(2, 8)
    assert dev.read_ct_model_ch(0) == 6
    assert dev.read_ct_model_ch(1) == 7
    assert dev.read_ct_model_ch(2) == 8


def test_mixed_channel_models_hold_independently_ui7():
    """ch3-6 each store distinct model bytes; a write to one leaves others alone."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    dev.set_ct_model_ch_force(3, 6)
    dev.set_ct_model_ch_force(4, 7)
    dev.set_ct_model_ch_force(5, 8)
    dev.set_ct_model_ch_force(6, 9)
    # Overwrite just ch4 — others should not budge.
    dev.set_ct_model_ch_force(4, 12)
    assert dev.read_ct_model_ch(3) == 6
    assert dev.read_ct_model_ch(4) == 12
    assert dev.read_ct_model_ch(5) == 8
    assert dev.read_ct_model_ch(6) == 9


# ---------------------------------------------------------------------------
# Legacy compatibility (ch 0-2 still uses REG+CMD flat wire canon)
# ---------------------------------------------------------------------------

def test_flat_path_still_uses_reg_cmd_wire_canon():
    """Backward-compat: ch 0-2 path unchanged (REG_CT_MODEL then CMD_SET_CT_MODEL_CH<N>)."""
    m = _ui3_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    m.writes.clear()
    dev.set_ct_model_ch_force(1, 7)  # WIRED_CT CLAMP2000_5A

    # Expect the legacy sequence in order: REG_CT_MODEL, CMD_SET_CT_MODEL_CH1.
    ct_writes = [w for w in m.writes if w[1] == R.REG_CT_MODEL]
    cmd_writes = [w for w in m.writes if w[1] == R.REG_COMMAND
                  and w[2] == R.CMD_SET_CT_MODEL_CH1]
    assert ct_writes and ct_writes[0][2] == 7
    assert cmd_writes  # CMD_SET_CT_MODEL_CH1 fired
    # And NO window write for the flat path.
    win_selects = [w for w in m.writes if w[1] == R.REG_CHANNEL_SELECT]
    assert win_selects == []


# ---------------------------------------------------------------------------
# Persist-opcode anti-regression (root audit 2026-09-04, bus 14:50Z)
# ---------------------------------------------------------------------------

def test_set_ct_model_ch_uses_save_user_config_not_save_gains_flat():
    """Flat ch0-2 path: persist via CMD_SAVE_USER_CONFIG (production-ungated).

    CMD_SAVE_GAINS is develop-gated in production firmware; using it there
    silently no-ops for user persist — latent bug that ships as "worked in
    dev, doesn't persist in prod". esp-idf hit this and switched; the
    Python library switched in v1.5.0 as part of the field15 release.
    """
    m = _ui3_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    m.writes.clear()
    dev.set_ct_model_ch_force(1, 7)
    cmds = [w[2] for w in m.writes if w[1] == R.REG_COMMAND]
    assert R.CMD_SAVE_USER_CONFIG in cmds
    assert R.CMD_SAVE_GAINS not in cmds, \
        "flat path emitted CMD_SAVE_GAINS — user persist is silently blocked in production"


def test_set_ct_model_ch_uses_save_user_config_not_save_gains_window():
    """Window ch3+ path: persist via CMD_SAVE_USER_CONFIG."""
    m = _ui7_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    m.writes.clear()
    dev.set_ct_model_ch_force(5, 6)
    cmds = [w[2] for w in m.writes if w[1] == R.REG_COMMAND]
    assert R.CMD_SAVE_USER_CONFIG in cmds
    assert R.CMD_SAVE_GAINS not in cmds


def test_set_sensor_class_uses_save_user_config():
    """set_sensor_class also uses CMD_SAVE_USER_CONFIG."""
    # Start from UNSET (not a paired class) so the class write proceeds.
    m = MockBackend(slave_addr=0x50, channels=3, has_voltage=True)
    dev = RbAmp(m, addr=0x50); dev.begin()
    m.writes.clear()
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    cmds = [w[2] for w in m.writes if w[1] == R.REG_COMMAND]
    assert R.CMD_SAVE_USER_CONFIG in cmds
    assert R.CMD_SAVE_GAINS not in cmds


def test_flat_path_reads_flat_mirror_not_window():
    """read_ct_model_ch(0..2) reads REG_CT_MODEL_CH<N>, no window select."""
    m = _ui3_wired_ct()
    dev = RbAmp(m, addr=0x50); dev.begin()
    m._channel_models[1] = 7
    m.writes.clear()
    result = dev.read_ct_model_ch(1)
    assert result == 7
    # No selector write for flat ch1 path.
    assert not any(w[1] == R.REG_CHANNEL_SELECT for w in m.writes)
