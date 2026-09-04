"""Stage 1A v1.3 fixes — wire-protocol + L9 energy + per-quantity sanity.

Bug refs from rbamp-dev.docs cross-lib parity audit (baton 18:30Z 2026-06-14)
and rbamp-dev.root seed v1.3 (2026-06-16 L9 anti-revert callout):

- #1: CT model order — canonical REG_CT_MODEL=code → CMD_SET_CT_MODEL_CHn →
       5 ms settle → CMD_SAVE_GAINS (was CMD→REG→SAVE in v1.1, broken).
- #2: L9 energy = MASTER wall-clock. Stale-hold = anchor not advanced on
       PERIOD_VALID=0; next success integrates the full multi-period dt.
       FW preserves accumulator across empty latches (OI-3 root-verified).
- #5: Per-quantity loose sanity — U≤500, I≤150, P≤30000, PF≤1.5, Q≤30000.
       Old single |x|>10000 limit rejected legitimate P readings on ≥10 kW
       loads (230 V × 45 A ≈ 10.35 kW).
"""
from __future__ import annotations

import math
import struct

import pytest

from rbamp import _registers_v2 as R
from rbamp import RbAmp, RbAmpIOError, RbAmpStaleError, RbAmpSensorClass


# =====================================================================
# Bug #1 — CT model order: REG → CMD → 5 ms → SAVE
# =====================================================================

def test_ct_model_ch_canonical_order(mock_backend):
    """set_ct_model_ch must write REG_CT_MODEL FIRST, then CMD_SET_CT_MODEL_CHn,
    then a 5 ms settle, then CMD_SAVE_GAINS. Inverted order silently fails
    device-side (CMD primes with stale code)."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)  # v1.3 fw guard precondition
    mock_backend.writes.clear()
    mock_backend.sleeps.clear()

    dev.set_ct_model_ch(1, 3)

    # Filter out any check-reads that may have happened on writes log
    write_seq = [(reg, val) for (_, reg, val) in mock_backend.writes]

    # First: REG_CT_MODEL = 3
    assert write_seq[0] == (R.REG_CT_MODEL, 3), \
        f"expected REG_CT_MODEL=3 first, got {write_seq[0]!r}"
    # Second: CMD_SET_CT_MODEL_CH1
    assert write_seq[1] == (R.REG_COMMAND, R.CMD_SET_CT_MODEL_CH0 + 1), \
        f"expected CMD_SET_CT_MODEL_CH1 second, got {write_seq[1]!r}"
    # Third: CMD_SAVE_USER_CONFIG (v1.5.0: switched from CMD_SAVE_GAINS, which
    # is develop-gated in production and silently no-ops user persist).
    assert write_seq[2] == (R.REG_COMMAND, R.CMD_SAVE_USER_CONFIG), \
        f"expected CMD_SAVE_USER_CONFIG third, got {write_seq[2]!r}"
    # Sleep schedule: 5 ms settle then SETTLE_MS_SAVE_USER_CONFIG (700 ms)
    assert 5 in mock_backend.sleeps, \
        f"expected 5 ms settle between CMD_CHn and SAVE; sleeps={mock_backend.sleeps}"
    assert R.SETTLE_MS_SAVE_USER_CONFIG in mock_backend.sleeps
    # Anti-regression: must NOT use CMD_SAVE_GAINS for user persist (dev-gated).
    assert R.CMD_SAVE_GAINS not in [w[2] for w in mock_backend.writes
                                    if w[1] == R.REG_COMMAND], \
        "user setter must not emit CMD_SAVE_GAINS (develop-gated in production)"


def test_ct_model_ch_descending_pattern_clobber_mitigation(mock_backend):
    """Multi-channel setup: order-independent on v1.3 (truth-doc A1 — REG_CT_MODEL
    no longer auto-applies); the descending-channel pattern remains as a
    defensive idiom for legacy v1.x firmware. This test exercises the
    sequence with v1.3-accepted codes (6, 3, 1) and verifies caller-order
    preservation in the write log."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)  # v1.3 fw guard precondition
    mock_backend.writes.clear()

    dev.set_ct_model_ch(2, 6)  # ch2 = SCT-013-020 (v1.3-accepted)
    dev.set_ct_model_ch(1, 3)  # ch1 = SCT-013-030
    dev.set_ct_model_ch(0, 1)  # ch0 = SCT-013-005

    # Three REG_CT_MODEL writes — in caller order 6, 3, 1.
    reg_ct_writes = [val for (_, reg, val) in mock_backend.writes
                     if reg == R.REG_CT_MODEL]
    assert reg_ct_writes == [6, 3, 1], reg_ct_writes


# =====================================================================
# Bug #2 — L9 energy = MASTER wall-clock; stale-hold anchor
# =====================================================================

def test_period_snapshot_advances_anchor_on_success(mock_backend):
    """Successful read_period_snapshot updates _last_latch_ms forward."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    t_after_primer = dev._last_latch_ms

    mock_backend.advance_ms(60_000)
    snap = dev.read_period_snapshot()

    assert snap.valid
    assert snap.master_dt_ms >= 60_000
    # Anchor advanced past primer point (master_dt captured at the latch
    # write moment, before the settle — same semantic as the primer).
    assert dev._last_latch_ms > t_after_primer


def test_period_snapshot_stale_holds_anchor_l9(mock_backend):
    """Stale (PERIOD_VALID=0) raises and does NOT advance _last_latch_ms.
    Next successful snapshot's master_dt_ms covers the FULL multi-period
    interval (FW preserves accumulator → avg_p is over full interval →
    integration correct). L9 anti-revert behaviour.

    Uses ``skip_latch=True`` for the stale call so the mock's latch-side-
    effect doesn't auto-re-validate before the valid check.
    """
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    anchor_before = dev._last_latch_ms

    # Force stale → raise. read_period_snapshot must NOT advance anchor.
    mock_backend.advance_ms(60_000)
    mock_backend.set_stale()
    with pytest.raises(RbAmpStaleError):
        dev.read_period_snapshot(skip_latch=True)

    # Anchor unchanged (stale-hold).
    assert dev._last_latch_ms == anchor_before, \
        "stale must not advance anchor (L9 stale-hold)"

    # Next successful snapshot — master_dt_ms spans BOTH periods.
    mock_backend.advance_ms(60_000)
    mock_backend.clear_stale()
    snap = dev.read_period_snapshot()
    assert snap.valid
    # Full interval ≥ 2×60 s
    assert snap.master_dt_ms >= 120_000, \
        f"stale-hold should yield ~120 s dt, got {snap.master_dt_ms} ms"


def test_energy_integration_via_master_dt_not_chip_period_ms(mock_backend):
    """L9: energy.tick() consumes snap.master_dt_ms (master wall-clock)
    NOT snap.latch_ms (chip-reported, under-counts ~26%). Verify by
    setting an obvious mismatch."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()

    # Chip latch_ms artificially small (under-count simulation: ~26%)
    mock_backend._set_u32_le(R.REG_V03_PERIOD_LATCH_MS, 44_000)  # 44 s
    mock_backend.advance_ms(60_000)  # but master wall-clock = 60 s

    mock_backend.set_period_avg(0, 1000.0)  # 1 kW avg power for the period
    snap = dev.read_period_snapshot()

    # Integration uses master_dt (60 s) not chip latch_ms (44 s).
    # 1 kW × 60 s / 3600 = 16.67 Wh
    wh = dev.energy.wh(0)
    expected = 1000.0 * 60.0 / 3600.0  # 16.667
    assert math.isclose(wh, expected, rel_tol=0.05), \
        f"L9 violated: expected ~{expected:.2f} Wh (master_dt), got {wh:.2f} Wh"


# =====================================================================
# Bug #5 — Per-quantity sanity filter (U/I/P/PF/Q ceilings)
# =====================================================================

def test_sanity_p_accepts_10kw_now(mock_backend):
    """High-power loads (e.g. 230 V × 45 A ≈ 10.35 kW) MUST pass the
    sanity filter. Old uniform |x|>10000 limit rejected them."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.set_rt_power(0, 10350.0)

    p = dev.read_power(0)
    assert math.isclose(p, 10350.0, rel_tol=0.001)


def test_sanity_p_accepts_near_ceiling(mock_backend):
    """P ceiling is 30 kW — readings up to that pass."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.set_rt_power(0, 25000.0)
    assert math.isclose(dev.read_power(0), 25000.0, rel_tol=0.001)


def test_sanity_p_rejects_above_ceiling(mock_backend):
    """P > 30 kW rejected — non-physical for production rbAmp range."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.set_rt_power(0, 35_000.0)
    with pytest.raises(RbAmpIOError):
        dev.read_power(0)
    assert dev.sanity_reject_count == 1


def test_sanity_u_rejects_above_500(mock_backend):
    """U > 500 V is non-physical for any rbAmp deployment."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend._set_float(R.REG_V03_U_RMS, 600.0)
    with pytest.raises(RbAmpIOError):
        dev.read_voltage()


def test_sanity_i_rejects_above_150(mock_backend):
    """I > 150 A is non-physical for any rbAmp deployment."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend._set_float(R.REG_V03_I0_RMS, 200.0)
    with pytest.raises(RbAmpIOError):
        dev.read_current(0)


def test_sanity_pf_allows_overshoot_to_1p5(mock_backend):
    """PF transient overshoot to ±1.5 allowed (real PF is in [-1,+1] but
    noisy edge cycles can briefly compute beyond that)."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend._set_float(R.REG_V03_PF0, 1.4)
    assert math.isclose(dev.read_power_factor(0), 1.4, rel_tol=0.001)


def test_sanity_nan_inf_still_rejected(mock_backend):
    """NaN / Inf rejected on all kinds (preserved from original SPEC §B.5)."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()

    # Inject NaN bit pattern
    b = struct.pack("<f", float("nan"))
    for i in range(4):
        mock_backend._regs[R.REG_V03_U_RMS + i] = b[i]
    with pytest.raises(RbAmpIOError):
        dev.read_voltage()
