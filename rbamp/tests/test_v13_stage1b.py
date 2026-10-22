"""Stage 1B v1.3 fixes — variant detect via 0x55 + GC frame + two-phase addr.

Bug refs from the cross-library parity audit (2026-06-14)
plus the v1.3 seed (2026-06-16):

- #3: 5-byte GC frame `A5 27 <group> <tick_lo> <tick_hi>` per truth-doc §5.4.
       Latch-only by FW design (destructive opcodes not honoured over GC).
- #4: `_detect_variant` reads `REG_HW_VARIANT (0x55)` per truth-doc §1.2.
       Old ACK-probe approach (0xC2/0xC6) broken — FW returns 0x00 on
       unmapped, never NACKs.
- #6 (mechanism): two-phase address commit per truth-doc §6.1 —
       REG_I2C_ADDRESS → REG_ADDR_COMMIT_MAGIC=0xA5 → CMD_COMMIT_ADDR → RESET.
       Production-OK (no mode gate).
"""
from __future__ import annotations

import pytest

from rbamp import _registers_v2 as R
from rbamp import RbAmp, RbAmpVersionError, RbAmpSensorClass


# =====================================================================
# Bug #4 — Variant detection via REG_HW_VARIANT (0x55)
# =====================================================================

@pytest.mark.parametrize("hw_byte,channels,has_voltage", [
    (1, 1, True),   # UI1
    (2, 2, True),   # UI2
    (3, 3, True),   # UI3
    (4, 1, False),  # I1
    (5, 2, False),  # I2
    (6, 3, False),  # I3
])
def test_variant_detect_via_0x55(mock_backend, hw_byte, channels, has_voltage):
    """REG_HW_VARIANT 1..6 → topology + voltage hw correctly mapped."""
    mock_backend._regs[R.REG_HW_VARIANT] = hw_byte
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.channels == channels
    assert dev.has_voltage_hw == has_voltage


def test_variant_detect_rejects_unknown_sku(mock_backend):
    """REG_HW_VARIANT outside 1..6 → RbAmpVersionError."""
    mock_backend._regs[R.REG_HW_VARIANT] = 0x99
    dev = RbAmp(mock_backend, addr=0x50)
    with pytest.raises(RbAmpVersionError) as exc_info:
        dev.begin()
    assert "0x99" in str(exc_info.value) or "153" in str(exc_info.value)


def test_variant_detect_rejects_zero(mock_backend):
    """REG_HW_VARIANT == 0 (unmapped read fallback) → unknown."""
    mock_backend._regs[R.REG_HW_VARIANT] = 0
    dev = RbAmp(mock_backend, addr=0x50)
    with pytest.raises(RbAmpVersionError):
        dev.begin()


def test_variant_detect_does_not_use_ack_probe(mock_backend):
    """Confirm _detect_variant no longer issues NACK-probes on 0xC2/0xC6."""
    # Force those registers to NACK — old detect would have classified
    # SINGLE; new detect ignores them and uses 0x55.
    mock_backend.fail_addresses.add((0x50, R.REG_V03_PERIOD_AVG_P_CH1))
    mock_backend.fail_addresses.add((0x50, R.REG_V03_PERIOD_AVG_P_CH2))
    mock_backend._regs[R.REG_HW_VARIANT] = 3  # UI3
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    # UI3 → 3 channels regardless of the NACK probe behaviour.
    assert dev.channels == 3


# =====================================================================
# Bug #3 — 5-byte GC frame + witness check
# =====================================================================

def test_broadcast_latch_group_5byte_frame(mock_backend):
    """broadcast_latch_group sends `A5 27 group tick_lo tick_hi`."""
    class _Bus:
        def __init__(self, b):
            self._b = b
            self.read_byte_data = b.read_byte
            self.write_byte_data = b.write_byte
        def write_i2c_block_data(self, addr, cmd, data):
            self._b.broadcast(bytes([cmd]) + bytes(data))

    bus = _Bus(mock_backend)
    assert RbAmp.broadcast_latch_group(bus, group=7, tick=0x1234) is True
    payload = mock_backend.broadcasts[-1]
    assert payload == bytes([0xA5, R.CMD_LATCH_PERIOD, 7, 0x34, 0x12])


def test_broadcast_latch_group_rejects_invalid(mock_backend):
    """group / tick range validation — fast-fail before bus write."""
    class _Bus:
        write_byte_data = lambda *a, **k: None
        read_byte_data = lambda *a, **k: 0
        write_i2c_block_data = lambda *a, **k: None
    bus = _Bus()
    with pytest.raises(Exception):
        RbAmp.broadcast_latch_group(bus, group=256, tick=0)
    with pytest.raises(Exception):
        RbAmp.broadcast_latch_group(bus, group=0, tick=0x10000)


def test_gc_witness_via_read_gc_tick(mock_backend):
    """After GC frame, modules with GC enabled + group-matching record tick."""
    # Enable GC + set group 5 on the mock
    mock_backend._regs[R.REG_FLEET_CONFIG] = 0x01
    mock_backend._regs[R.REG_GROUP_ID] = 5

    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    # Before any GC: tick = 0xFFFF (never received)
    assert dev.read_gc_tick() == 0xFFFF

    # Simulate a GC frame for group 5, tick 0xABCD
    mock_backend.broadcast(bytes([0xA5, R.CMD_LATCH_PERIOD, 5, 0xCD, 0xAB]))
    assert dev.read_gc_tick() == 0xABCD


def test_gc_witness_ignored_when_gc_disabled(mock_backend):
    """GC frame ignored when REG_FLEET_CONFIG.bit0 == 0."""
    mock_backend._regs[R.REG_FLEET_CONFIG] = 0x00  # disabled
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.broadcast(bytes([0xA5, R.CMD_LATCH_PERIOD, 0, 0x42, 0x00]))
    assert dev.read_gc_tick() == 0xFFFF


def test_gc_witness_ignored_on_wrong_group(mock_backend):
    """GC frame with non-zero group ignored unless module's group_id matches."""
    mock_backend._regs[R.REG_FLEET_CONFIG] = 0x01
    mock_backend._regs[R.REG_GROUP_ID] = 3
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    # Frame for group 7 — not 3 nor all-call (0)
    mock_backend.broadcast(bytes([0xA5, R.CMD_LATCH_PERIOD, 7, 0x42, 0x00]))
    assert dev.read_gc_tick() == 0xFFFF


def test_gc_all_call_matches_any_group(mock_backend):
    """GC frame with group == 0 (all-call) matches modules regardless of group_id."""
    mock_backend._regs[R.REG_FLEET_CONFIG] = 0x01
    mock_backend._regs[R.REG_GROUP_ID] = 99
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.broadcast(bytes([0xA5, R.CMD_LATCH_PERIOD, 0, 0x00, 0x80]))
    assert dev.read_gc_tick() == 0x8000


# =====================================================================
# Bug #6 — Two-phase address commit (production-OK)
# =====================================================================

def test_address_change_writes_magic_byte(mock_backend):
    """commit_address_change writes 0xA5 to REG_ADDR_COMMIT_MAGIC."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.prepare_address_change(0x51)
    dev.commit_address_change()
    magic_writes = [w for w in mock_backend.writes
                    if w[1] == R.REG_ADDR_COMMIT_MAGIC]
    assert magic_writes, "no write to REG_ADDR_COMMIT_MAGIC"
    assert magic_writes[0][2] == 0xA5


def test_address_change_uses_cmd_commit_addr(mock_backend):
    """commit_address_change issues CMD_COMMIT_ADDR (0x30), not CMD_SAVE_GAINS."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.prepare_address_change(0x51)
    dev.commit_address_change()
    commits = [w for w in mock_backend.writes
               if w[1] == R.REG_COMMAND and w[2] == R.CMD_COMMIT_ADDR]
    saves = [w for w in mock_backend.writes
             if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_GAINS]
    assert len(commits) == 1
    assert not saves, "v1.3 must NOT use CMD_SAVE_GAINS for address commit"


def test_address_change_no_mode_gate_v13(mock_backend):
    """v1.3 truth-doc §6.1: address commit production-OK (mode_factory == 0)."""
    assert mock_backend.mode_factory == 0
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    # Must not raise:
    dev.prepare_address_change(0x60)
    dev.commit_address_change()
    assert dev.address == 0x60


# =====================================================================
# Identity / capability helpers (v1.3 truth-doc §1)
# =====================================================================

def test_read_variant(mock_backend):
    mock_backend._regs[R.REG_HW_VARIANT] = 3
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.read_variant() == 3


def test_read_product_id(mock_backend):
    """Default mock product id = 0x01 (rbAmp sensor)."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.read_product_id() == 0x01


def test_read_capability_returns_u16(mock_backend):
    """Set a few bits in REG_CAPABILITY, library reads them back."""
    mock_backend._regs[R.REG_CAPABILITY + 0] = 0x06
    mock_backend._regs[R.REG_CAPABILITY + 1] = 0x01
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.read_capability() == 0x0106


def test_read_uid_12_bytes(mock_backend):
    for i in range(12):
        mock_backend._regs[R.REG_UID + i] = i + 0xA0
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    uid = dev.read_uid()
    assert len(uid) == 12
    assert uid == bytes(range(0xA0, 0xAC))


# =====================================================================
# Error / event channel helpers
# =====================================================================

def test_read_last_error(mock_backend):
    mock_backend._regs[R.REG_ERROR] = 0xFE  # DEV_ERR_PARAM
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.read_last_error() == 0xFE


def test_has_error_reflects_event_flags_bit3(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.has_error() is False
    mock_backend._regs[R.REG_EVENT_FLAGS] = R.EVENT_ERROR
    assert dev.has_error() is True


def test_clear_event_flags_write_back_mask(mock_backend):
    mock_backend._regs[R.REG_EVENT_FLAGS] = 0xFF
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.clear_event_flags(R.EVENT_ERROR)
    assert any(w == (0x50, R.REG_EVENT_FLAGS, R.EVENT_ERROR)
               for w in mock_backend.writes)


def test_clear_error_issues_cmd_clear_error(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.clear_error()
    assert any(w == (0x50, R.REG_COMMAND, R.CMD_CLEAR_ERROR)
               for w in mock_backend.writes)


# =====================================================================
# Fleet / GC config helpers
# =====================================================================

def test_set_group_id_writes_register(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.set_group_id(42)
    assert mock_backend._regs[R.REG_GROUP_ID] == 42


def test_set_group_id_rejects_invalid(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    with pytest.raises(Exception):
        dev.set_group_id(-1)
    with pytest.raises(Exception):
        dev.set_group_id(256)


def test_enable_gc_sets_bit0_saves_and_resets(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.enable_gc(True)
    # Bit0 of REG_FLEET_CONFIG is set
    assert mock_backend._regs[R.REG_FLEET_CONFIG] & 0x01
    # Save + reset commands issued
    saves = [w for w in mock_backend.writes
             if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG]
    resets = [w for w in mock_backend.writes
              if w[1] == R.REG_COMMAND and w[2] == R.CMD_RESET]
    assert saves and resets
