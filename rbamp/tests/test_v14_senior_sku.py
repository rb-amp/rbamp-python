# SPDX-License-Identifier: MIT
"""
v1.4 senior-SKU (UI5/UI7) test suite.

Covers:
  * Detection: TOPOLOGY=5/7 + HW_VARIANT=7/8 mapping (root A4/A5)
  * Channel-access window: select/read/re-select rule #1 (root A6)
  * Field 13 = PERIOD_AVG_P energy path (root A1)
  * fleet.aligned_snapshot() coherent latch + sweep (root A1/A2)
  * SAVE post-hoc wait-ready NACK-tolerance (root A3)
  * read_seq_digest() at REG_DIGEST + 1 (root A7)
  * RbAmpSnapshot pad-to-max shape (channel-count widening)
"""
import struct
import pytest

from rbamp import (
    RbAmp,
    RbAmpFleet,
    RbAmpIOError,
    RbAmpSnapshot,
    RbAmpWindowField,
    TOPOLOGY_FIVE_CH,
    TOPOLOGY_SEVEN_CH,
    topology_name,
    _registers_v2 as R,
)
from rbamp.tests.conftest import MockBackend


# ------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------

def test_detect_ui5(mock_ui5):
    dev = RbAmp(mock_ui5, addr=0x50)
    dev.begin()
    assert dev.channels == 5
    assert dev.topology == TOPOLOGY_FIVE_CH
    assert dev.has_voltage_hw is True
    assert dev.topology_name == "FIVE_CH"


def test_detect_ui7(mock_ui7):
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    assert dev.channels == 7
    assert dev.topology == TOPOLOGY_SEVEN_CH
    assert dev.has_voltage_hw is True
    assert dev.topology_name == "SEVEN_CH"


def test_detect_topology_hw_variant_mismatch():
    """UI5 mock but HW_VARIANT tampered to 3 (UI3) → should raise."""
    m = MockBackend(slave_addr=0x50, channels=5, has_voltage=True)
    m._regs[R.REG_HW_VARIANT] = 3   # tamper
    dev = RbAmp(m, addr=0x50)
    from rbamp import RbAmpVersionError
    with pytest.raises(RbAmpVersionError, match="senior SKU"):
        dev.begin()


def test_detect_junior_still_works(mock_backend):
    """UI3 junior detection unaffected by v1.4 additions."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.channels == 3
    assert dev.topology_name == "THREE_PHASE"


# ------------------------------------------------------------------
# Channel-access window — select/read
# ------------------------------------------------------------------

def test_window_read_current_ch3_to_ch6(mock_ui7):
    """Window read of I_RMS across ch 3..6 returns seeded per-cell values."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    for ch in (3, 4, 5, 6):
        val = dev.read_current(ch)
        # Seed formula (conftest): field 0 → 1.0 + ch*0.1
        assert val == pytest.approx(1.0 + ch * 0.1, abs=1e-4), \
            "ch{} I_RMS={}".format(ch, val)


def test_window_read_all_4_live_fields_ch4(mock_ui7):
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    ch = 4
    assert dev.read_current(ch)      == pytest.approx(1.0 + ch * 0.1, abs=1e-4)
    assert dev.read_current_peak(ch) == pytest.approx(5.0 + ch * 0.5, abs=1e-4)
    assert dev.read_power(ch)        == pytest.approx(100.0 + ch * 50.0, abs=1e-3)
    assert dev.read_power_factor(ch) == pytest.approx(0.90 + ch * 0.01, abs=1e-4)


def test_window_field_13_period_avg_p(mock_ui7):
    """Field 13 PERIOD_AVG_P via window — the v1.4 senior energy path."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    for ch in (3, 4, 5, 6):
        val = dev.read_period_avg_p(ch)
        assert val == pytest.approx(95.0 + ch * 50.0, abs=1e-3)


def test_window_ch2_stays_on_flat_path(mock_ui7):
    """ch 0..2 always goes through flat block, not window (dispatcher rule)."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    mock_ui7.writes.clear()
    _ = dev.read_current(2)
    # No writes to REG_CHANNEL_SELECT (0x32) — flat path only.
    selector_writes = [w for w in mock_ui7.writes if w[1] == R.REG_CHANNEL_SELECT]
    assert selector_writes == []


def test_window_reselect_rule_1(mock_ui7):
    """Every window read MUST issue a fresh select — re-select rule #1.
    Two successive reads of the SAME (field, ch) still both write 0x32."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    mock_ui7.writes.clear()
    _ = dev.read_current(3)
    _ = dev.read_current(3)
    selector_writes = [w for w in mock_ui7.writes if w[1] == R.REG_CHANNEL_SELECT]
    assert len(selector_writes) == 2, \
        "expected TWO selector writes for two reads; got {}".format(selector_writes)


def test_window_selector_encoding_field_and_channel(mock_ui7):
    """Selector byte = (field << 4) | ch — check for field 2 P_REAL ch 5."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    mock_ui7.writes.clear()
    _ = dev.read_power(5)
    sel = next(w for w in mock_ui7.writes if w[1] == R.REG_CHANNEL_SELECT)
    assert sel[2] == (2 << 4) | 5


def test_window_field_13_selector_encoding(mock_ui7):
    """Selector byte for PERIOD_AVG_P (field 13) ch 6 = (13<<4)|6 = 0xD6."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    mock_ui7.writes.clear()
    _ = dev.read_period_avg_p(6)
    sel = next(w for w in mock_ui7.writes if w[1] == R.REG_CHANNEL_SELECT)
    assert sel[2] == 0xD6


def test_check_channel_bounds_senior(mock_ui7):
    from rbamp import RbAmpParamError
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    dev.read_current(6)                       # last valid
    with pytest.raises(RbAmpParamError):
        dev.read_current(7)                    # out of range


def test_window_field_enum_values():
    """RbAmpWindowField enum wire-byte values match root A6."""
    assert int(RbAmpWindowField.I_RMS)         == 0
    assert int(RbAmpWindowField.I_PEAK)        == 1
    assert int(RbAmpWindowField.P_REAL)        == 2
    assert int(RbAmpWindowField.PF)            == 3
    assert int(RbAmpWindowField.PERIOD_AVG_P)  == 13
    # Match generated CHFIELD_* constants
    assert int(RbAmpWindowField.I_RMS)         == R.CHFIELD_I_RMS
    assert int(RbAmpWindowField.PERIOD_AVG_P)  == R.CHFIELD_PERIOD_AVG_P


# ------------------------------------------------------------------
# read_all / RbAmpSnapshot shape
# ------------------------------------------------------------------

def test_snapshot_pad_to_max_junior():
    """Junior SKU (1 ch) still has 3-element lists — pad-to-max backward compat."""
    s = RbAmpSnapshot(channels=1)
    assert len(s.current) == 3
    assert s.current[2] == 0.0        # accessible, safe zero


def test_snapshot_widens_for_senior():
    """UI7 snapshot has 7-element lists."""
    s = RbAmpSnapshot(channels=7)
    assert len(s.current) == 7
    assert len(s.power) == 7


def test_read_all_ui7(mock_ui7):
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    snap = dev.read_all()
    assert snap.channels == 7
    assert snap.topology == TOPOLOGY_SEVEN_CH
    assert len(snap.current) == 7
    # ch 0..2 via flat block
    assert snap.current[0] == pytest.approx(1.23, abs=1e-4)
    # ch 3..6 via window (seeded 1.0 + ch*0.1)
    for ch in (3, 4, 5, 6):
        assert snap.current[ch] == pytest.approx(1.0 + ch * 0.1, abs=1e-4)


# ------------------------------------------------------------------
# Period avg power + energy for ch3+ on senior SKUs (window field 13).
# ch0-2 read flat regs; ch3..N-1 read the channel window — same coherent
# latch. (Also the overflow guard: in an earlier revision, the fill ran off the fixed-3
# avg_p → IndexError on UI5/UI7; now avg_p is sized to the channel count.)
# ------------------------------------------------------------------

def test_read_period_avg_power_ch3plus_via_window(mock_ui7):
    """read_period_avg_power(ch) resolves ch3+ through window field 13 —
    same path as read_period_avg_p (they are now one implementation)."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    for ch in (3, 4, 5, 6):
        mock_ui7.set_window_value(13, ch, 200.0 + ch * 10.0)
        assert dev.read_period_avg_power(ch) == pytest.approx(200.0 + ch * 10.0, abs=1e-3)
        # alias resolves identically
        assert dev.read_period_avg_p(ch) == pytest.approx(200.0 + ch * 10.0, abs=1e-3)


def test_period_snapshot_ui7_fills_all_channels(mock_ui7):
    """read_period_snapshot() on a 7-ch module fills avg_p[0..6] (no IndexError)
    — ch0-2 flat, ch3-6 via the window — and the trailing fields are intact."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    for ch in range(3):
        mock_ui7.set_period_avg(ch, 10.0 + ch)              # 10, 11, 12 W
    for ch in (3, 4, 5, 6):
        mock_ui7.set_window_value(13, ch, 100.0 + ch)       # 103..106 W

    snap = dev.read_period_snapshot()                       # must NOT raise

    assert snap.valid is True
    assert len(snap.avg_p) == 7                             # sized to channels
    for ch in range(3):
        assert snap.avg_p[ch] == pytest.approx(10.0 + ch, abs=1e-3)
    for ch in (3, 4, 5, 6):
        assert snap.avg_p[ch] == pytest.approx(100.0 + ch, abs=1e-3)


def test_period_snapshot_ui5_fills_all_channels(mock_ui5):
    """Same on the 5-channel senior SKU — avg_p sized to 5, no overflow."""
    dev = RbAmp(mock_ui5, addr=0x50)
    dev.begin()
    snap = dev.read_period_snapshot()                       # must NOT raise
    assert snap.valid is True
    assert len(snap.avg_p) == 5


def test_period_energy_integrates_ch3plus_on_ui7(mock_ui7):
    """Energy accumulator integrates every channel on a senior SKU, not just
    ch0-2 — dev.energy.wh(ch) is non-zero for ch3-6 after a dt."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    for ch in range(3):
        mock_ui7.set_period_avg(ch, 100.0 + ch * 100.0)     # 100, 200, 300 W
    for ch in (3, 4, 5, 6):
        mock_ui7.set_window_value(13, ch, 100.0 + ch * 100.0)  # 400..700 W

    dev.read_period_snapshot()                              # anchor the clock
    mock_ui7.advance_ms(3_600_000)                          # +1 h
    dev.read_period_snapshot()                              # integrate

    # Each channel integrated P·dt/3600; 1 h → Wh ≈ P (settle adds ~0.05 s).
    for ch in range(7):
        assert dev.energy.wh(ch) == pytest.approx(100.0 + ch * 100.0, rel=1e-3)


# ------------------------------------------------------------------
# read_seq_digest — REG_DIGEST + 1 = 0x71
# ------------------------------------------------------------------

def test_read_seq_digest(mock_ui7):
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    # Seed a known value into the SEQ byte
    mock_ui7._regs[R.REG_DIGEST + 1] = 42
    assert dev.read_seq_digest() == 42


def test_read_seq_digest_detects_commit_change(mock_ui7):
    """Two reads; commit-change simulated by bumping the byte between them."""
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    mock_ui7._regs[R.REG_DIGEST + 1] = 100
    s1 = dev.read_seq_digest()
    mock_ui7._regs[R.REG_DIGEST + 1] = 101   # commit happened
    s2 = dev.read_seq_digest()
    assert s1 != s2


# ------------------------------------------------------------------
# SAVE post-hoc wait-ready NACK tolerance (root A3)
# ------------------------------------------------------------------

def test_save_gains_absorbs_tail_nack(mock_ui7):
    """After SAVE_GAINS, mock NACKs STATUS for 10 ms; save_gains() should
    absorb it and return silently."""
    mock_ui7.nack_after_save_ms = 10
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    # Fixture starts at virtual now=0; begin() advances via sleeps.
    # save_gains: writes CMD then sleep 700ms → advance past NACK window,
    # then wait-ready probe should succeed immediately.
    dev.save_gains()  # must NOT raise


def test_save_gains_gives_up_on_persistent_nack(mock_ui7):
    """Post-SAVE probe raises if NACK persists past 30 ms budget.
    NACK window = SETTLE (700ms) + wait budget (30ms) + margin → 740ms
    keeps NACK alive through the whole probe span."""
    mock_ui7.nack_after_save_ms = 740
    dev = RbAmp(mock_ui7, addr=0x50)
    dev.begin()
    with pytest.raises(RbAmpIOError, match="post-SAVE probe"):
        dev.save_gains()


# ------------------------------------------------------------------
# fleet.aligned_snapshot()
# ------------------------------------------------------------------

class _MockSMBus2:
    """smbus2-shaped shim wrapping N MockBackends (fan-out on GC 0x00)."""
    def __init__(self, mocks):
        self._mocks = mocks

    def read_byte_data(self, addr, reg):
        m = self._mocks.get(addr)
        if m is None:
            raise OSError("no slave at 0x{:02X}".format(addr))
        return m.read_byte(addr, reg)

    def write_byte_data(self, addr, reg, val):
        m = self._mocks.get(addr)
        if m is None:
            raise OSError("no slave at 0x{:02X}".format(addr))
        m.write_byte(addr, reg, val)

    def write_i2c_block_data(self, addr, cmd, data):
        if addr == 0x00:
            frame = bytes([cmd] + list(data))
            for m in self._mocks.values():
                m.broadcast(frame)
            return
        m = self._mocks.get(addr)
        if m is not None:
            m.write_byte(addr, cmd, data[0] if data else 0)


def _seed_ui7(addr, base_power):
    m = MockBackend(slave_addr=addr, channels=7, has_voltage=True)
    # ch 0..2 read the flat PERIOD_AVG_P registers (0xDC/0xC2/0xC6), ch 3+ read
    # window field 13. Seed both paths with the same formula so the fleet
    # aligned_snapshot check sees a uniform list.
    for ch in range(3):
        m.set_period_avg(ch, base_power + ch * 10.0)
    for ch in range(3, 7):
        m.set_window_value(13, ch, base_power + ch * 10.0)
    return m


def test_fleet_aligned_snapshot_ui7():
    mocks = {0x50: _seed_ui7(0x50, 100.0), 0x51: _seed_ui7(0x51, 500.0)}
    bus = _MockSMBus2(mocks)
    fleet = RbAmpFleet(bus)
    for addr in (0x50, 0x51):
        dev = RbAmp(bus, addr=addr); dev.begin()
        fleet.add(dev)
    fleet.enable_gc_all(group=0)

    rows = fleet.aligned_snapshot(group=0)
    assert len(rows) == 2
    assert all(r["in_sync"] for r in rows)
    assert all(r["period_valid"] for r in rows)
    # Per-module avg_p list: 7 elements, matching seed formula
    for r, base in zip(rows, (100.0, 500.0)):
        assert len(r["avg_p"]) == 7
        for ch, ap in enumerate(r["avg_p"]):
            assert ap == pytest.approx(base + ch * 10.0, abs=1e-3), \
                "addr=0x{:02X} ch{} avg_p={}".format(r["addr"], ch, ap)
