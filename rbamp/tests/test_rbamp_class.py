"""Functional tests for :class:`rbamp.RbAmp` using the mock backend."""

import pytest

from rbamp import (
    RbAmp,
    RbAmpStaleError,
    RbAmpModeError,
    RbAmpParamError,
    RbAmpVersionError,
    RbAmpSensorClass,
    TOPOLOGY_SINGLE,
    TOPOLOGY_THREE_PHASE,
)
from rbamp import _registers as R


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_begin_detects_ui3_with_voltage(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    assert dev.topology == TOPOLOGY_THREE_PHASE
    assert dev.channels == 3
    assert dev.has_voltage_hw is True


def test_begin_detects_ui1_no_voltage(mock_ui1):
    dev = RbAmp(mock_ui1, 0x50)
    dev.begin()
    assert dev.topology == TOPOLOGY_SINGLE
    assert dev.channels == 1
    assert dev.has_voltage_hw is False


def test_begin_issues_primer_latch(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    # Last write must be CMD_LATCH_PERIOD to REG_COMMAND
    cmd_writes = [w for w in mock_backend.writes
                  if w[1] == R.REG_COMMAND and w[2] == R.CMD_LATCH_PERIOD]
    assert len(cmd_writes) == 1


def test_begin_raises_on_zero_version(mock_backend):
    mock_backend._regs[R.REG_VERSION] = 0
    with pytest.raises(RbAmpVersionError):
        RbAmp(mock_backend, 0x50).begin()


def test_context_manager_calls_begin(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        assert dev.channels == 3


# ---------------------------------------------------------------------------
# Real-time reads
# ---------------------------------------------------------------------------

def test_read_voltage_returns_float(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        u = dev.read_voltage()
        assert abs(u - 230.0) < 0.01


def test_read_current_each_channel(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        assert abs(dev.read_current(0) - 1.23) < 0.01
        assert abs(dev.read_current(1) - 2.34) < 0.01
        assert abs(dev.read_current(2) - 3.45) < 0.01


def test_read_channel_out_of_range_raises(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpParamError):
            dev.read_current(3)


def test_read_channel_out_of_range_on_ui1(mock_ui1):
    with RbAmp(mock_ui1, 0x50) as dev:
        # ch=0 ok, ch=1 must fail (UI1 has only 1 channel)
        dev.read_current(0)
        with pytest.raises(RbAmpParamError):
            dev.read_current(1)


def test_read_all_populates_struct(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        s = dev.read_all()
    assert s.channels == 3
    assert s.has_voltage_hw is True
    assert s.frequency == 50.0
    assert abs(s.voltage - 230.0) < 0.01
    assert all(s.current[ch] > 0 for ch in range(3))


def test_property_shortcuts(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        assert abs(dev.voltage - 230.0) < 0.01
        assert abs(dev.power[1] - 200.0) < 0.01
        assert abs(dev.power_factor[2] - 0.99) < 0.01
        assert dev.frequency == 50.0


def test_channel_proxy_iterable(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        currents = list(dev.current)
    assert len(currents) == 3


# ---------------------------------------------------------------------------
# Period metering
# ---------------------------------------------------------------------------

def test_read_period_snapshot_happy_path(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    # begin()'s 50ms primer settle has already advanced the mock clock.
    # Now simulate the user waiting another 60 s before calling read.
    mock_backend.advance_ms(60_000)
    snap = dev.read_period_snapshot()
    assert snap.valid
    # master_dt_ms = wall-clock between the two LATCH writes. The first
    # latch happened at t=0; the second at t=50 (begin's settle) + 60_000
    # (test-advanced) = 60_050.
    assert snap.master_dt_ms == 60_050
    assert abs(snap.avg_p[0] - 95.0) < 0.1
    assert abs(snap.avg_p[1] - 195.0) < 0.1
    assert abs(snap.avg_p[2] - 295.0) < 0.1


def test_read_period_snapshot_stale_raises(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    mock_backend.advance_ms(60_000)
    mock_backend.set_stale()
    # Writing CMD_LATCH_PERIOD inside read_period_snapshot would clear stale —
    # but the backend's write_byte logs the write and resets period_valid=True
    # only on CMD_LATCH_PERIOD. We want to test the STALE path, so we set
    # stale, then issue our own latch_period and immediately stale it before
    # reading.
    mock_backend.set_stale()  # ensures stale after the auto-latch
    dev.latch_period()
    mock_backend.set_stale()  # immediately re-stale before the read path
    with pytest.raises(RbAmpStaleError):
        dev.read_period_snapshot(skip_latch=True)


def test_energy_integrates_after_snapshot(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    assert dev.energy.wh(0) == 0.0
    mock_backend.advance_ms(60_000)
    dev.read_period_snapshot()
    # Energy = 95 W * (60_050 ms / 3_600_000 ms/h)
    # See test_read_period_snapshot_happy_path for the dt rationale.
    expected_wh = 95.0 * (60_050 / 3_600_000.0)
    assert abs(dev.energy.wh(0) - expected_wh) < 1e-6


def test_energy_disable_opts_out(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    dev.energy.disable()
    mock_backend.advance_ms(60_000)
    dev.read_period_snapshot()
    assert dev.energy.wh(0) == 0.0  # no integration


# ---------------------------------------------------------------------------
# Configuration / address change
# ---------------------------------------------------------------------------

def test_set_ct_model_writes_and_saves(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    # v1.3 fw guard: pin sensor class before CT model writes
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    mock_backend.writes.clear()
    dev.set_ct_model(3)
    # Expect: write REG_CT_MODEL=3, then write REG_COMMAND=CMD_SAVE_GAINS
    ct_writes = [w for w in mock_backend.writes if w[1] == R.REG_CT_MODEL]
    save_writes = [w for w in mock_backend.writes
                   if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG]
    assert len(ct_writes) == 1
    assert ct_writes[0][2] == 3
    assert len(save_writes) >= 1


def test_set_ct_model_rejects_out_of_range(mock_backend):
    """Codes outside the v1.3 accepted set raise RbAmpParamError."""
    mock_backend._regs[R.REG_SENSOR_CLASS] = 1  # SCT_013
    with RbAmp(mock_backend, 0x50) as dev:
        # 0 = unset (not in accepted set)
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model(0)
        # 5 = reserved-uncharacterised on v1.3 SCT_013
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model(5)
        # 7 = reserved-uncharacterised on v1.3 SCT_013
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model(7)


# ---------------------------------------------------------------------------
# v1.1.0 library surface — v1.2 protocol parity
# (set_sensor_class + per-channel set_ct_model_ch + guard)
# ---------------------------------------------------------------------------

def test_set_sensor_class_writes_and_saves(mock_backend):
    """Write REG_SENSOR_CLASS=SCT_013, then CMD_SAVE_GAINS."""
    with RbAmp(mock_backend, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    cls_writes = [w for w in mock_backend.writes if w[1] == R.REG_SENSOR_CLASS]
    save_writes = [w for w in mock_backend.writes
                   if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG]
    assert len(cls_writes) == 1
    assert cls_writes[0][2] == 1  # int(SCT_013) == 1
    assert len(save_writes) >= 1


def test_set_sensor_class_accepts_plain_int(mock_backend):
    """Calling with bare int (not enum member) also works — wire byte semantics."""
    with RbAmp(mock_backend, 0x50) as dev:
        dev.set_sensor_class(1)
    cls_writes = [w for w in mock_backend.writes if w[1] == R.REG_SENSOR_CLASS]
    assert cls_writes[0][2] == 1


def test_set_sensor_class_rejects_out_of_range(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpParamError):
            dev.set_sensor_class(-1)
        with pytest.raises(RbAmpParamError):
            dev.set_sensor_class(4)


def test_set_ct_model_ch_per_channel(mock_backend):
    """Per-channel write: CMD_SET_CT_MODEL_CH<N> followed by REG_CT_MODEL."""
    with RbAmp(mock_backend, 0x50) as dev:
        # Sensor class precondition (mock at v1.0 doesn't enforce, but
        # exercise the canonical sequence regardless).
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        mock_backend.writes.clear()
        dev.set_ct_model_ch(1, 3)
    select_writes = [w for w in mock_backend.writes
                     if w[1] == R.REG_COMMAND and w[2] == R.CMD_SET_CT_MODEL_CH0 + 1]
    ct_writes = [w for w in mock_backend.writes if w[1] == R.REG_CT_MODEL]
    save_writes = [w for w in mock_backend.writes
                   if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG]
    assert len(select_writes) == 1
    assert len(ct_writes) == 1
    assert ct_writes[0][2] == 3
    assert len(save_writes) == 1


def test_set_ct_model_ch_rejects_invalid_channel(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model_ch(-1, 1)
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model_ch(3, 1)


def test_set_ct_model_ch_rejects_invalid_code(mock_backend):
    """Codes outside the v1.3 accepted set for the pinned class reject."""
    with RbAmp(mock_backend, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        # 0 = unset (not in accepted set)
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model_ch(0, 0)
        # 5 = reserved-uncharacterised on v1.3 SCT_013
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model_ch(0, 5)
        # 7 = reserved-uncharacterised on v1.3 SCT_013
        with pytest.raises(RbAmpParamError):
            dev.set_ct_model_ch(0, 7)


def test_set_ct_model_guards_on_unset_class_v12(mock_backend):
    """v1.2+ firmware (REG_VERSION>=0x03) + SENSOR_CLASS==UNSET → RbAmpModeError."""
    mock_backend._regs[R.REG_VERSION] = 0x03
    mock_backend._regs[R.REG_SENSOR_CLASS] = 0  # UNSET
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpModeError) as exc_info:
            dev.set_ct_model(3)
        assert "UNSET" in str(exc_info.value)
        with pytest.raises(RbAmpModeError):
            dev.set_ct_model_ch(1, 3)


def test_set_ct_model_passes_on_v12_with_pinned_class(mock_backend):
    """v1.2+ firmware + SENSOR_CLASS=SCT_013 → guard passes, write succeeds."""
    mock_backend._regs[R.REG_VERSION] = 0x03
    mock_backend._regs[R.REG_SENSOR_CLASS] = 1  # SCT_013
    with RbAmp(mock_backend, 0x50) as dev:
        dev.set_ct_model(3)
    ct_writes = [w for w in mock_backend.writes if w[1] == R.REG_CT_MODEL]
    assert ct_writes and ct_writes[-1][2] == 3


def test_set_ct_model_skips_guard_on_v10(mock_backend):
    """v1.0 firmware: guard skipped, REG_SENSOR_CLASS not consulted."""
    # Force the mock to v1.0 explicitly (default is v1.3 since Stage 1B).
    mock_backend._regs[R.REG_VERSION] = 0x01
    mock_backend._regs[R.REG_SENSOR_CLASS] = 0
    with RbAmp(mock_backend, 0x50) as dev:
        # Should NOT raise — v1.0 firmware has no precondition.
        dev.set_ct_model(2)
    ct_writes = [w for w in mock_backend.writes if w[1] == R.REG_CT_MODEL]
    assert ct_writes and ct_writes[-1][2] == 2


def test_sensor_class_enum_wire_values():
    """Verify the wire encoding matches the spec (registers.yaml)."""
    assert int(RbAmpSensorClass.UNSET) == 0
    assert int(RbAmpSensorClass.SCT_013) == 1
    assert int(RbAmpSensorClass.WIRED_CT) == 2
    assert int(RbAmpSensorClass.BUILTIN_CT) == 3


def test_address_change_production_ok_v13(mock_backend):
    """v1.3 (truth-doc §6.1): address change is PRODUCTION-OK — no mode gate.

    Prior to v1.3 this was factory-only; new canon allows field-swap of
    production spares via the two-phase commit (magic + opcode).
    """
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    # Must NOT raise.
    dev.prepare_address_change(0x51)
    dev.commit_address_change()
    assert dev.address == 0x51


def test_address_change_two_phase_commit_sequence_v13(mock_backend):
    """Truth-doc §6.1 sequence: write REG_I2C_ADDRESS → write 0xA5 magic →
    CMD_COMMIT_ADDR → CMD_RESET."""
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.prepare_address_change(0x55)
    dev.commit_address_change()

    # Filter just the writes performed by commit (begin's already cleared)
    seq = [(reg, val) for (_, reg, val) in mock_backend.writes]
    # Find indices of the four canonical writes
    idx_addr = next(i for i, (r, v) in enumerate(seq) if r == R.REG_I2C_ADDRESS)
    idx_magic = next(i for i, (r, v) in enumerate(seq)
                     if r == R.REG_ADDR_COMMIT_MAGIC and v == 0xA5)
    idx_commit = next(i for i, (r, v) in enumerate(seq)
                      if r == R.REG_COMMAND and v == R.CMD_COMMIT_ADDR)
    idx_reset = next(i for i, (r, v) in enumerate(seq)
                     if r == R.REG_COMMAND and v == R.CMD_RESET)
    # Strict ordering: addr → magic → commit → reset
    assert idx_addr < idx_magic < idx_commit < idx_reset
    # Address actually changed
    assert dev.address == 0x55


def test_address_change_arm_expires_after_5s(mock_factory):
    dev = RbAmp(mock_factory, 0x50)
    dev.begin()
    dev.prepare_address_change(0x55)
    mock_factory.advance_ms(6000)
    with pytest.raises(OSError):
        dev.commit_address_change()


def test_address_change_rejects_invalid_address(mock_factory):
    dev = RbAmp(mock_factory, 0x50)
    dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.prepare_address_change(0x05)  # < 0x08
    with pytest.raises(RbAmpParamError):
        dev.prepare_address_change(0x78)  # > 0x77
    with pytest.raises(RbAmpParamError):
        dev.prepare_address_change(0x50)  # equal to current


# ---------------------------------------------------------------------------
# Broadcast LATCH
# ---------------------------------------------------------------------------

def test_broadcast_latch_static_method(mock_backend):
    ok = RbAmp.broadcast_latch(_ducktyped_bus(mock_backend))
    assert ok
    # broadcast not logged because the static path uses a fresh backend
    # wrapping the bus; the bus itself wasn't called.


def _ducktyped_bus(mock_backend):
    """Wrap MockBackend to look like an smbus2 bus for broadcast_latch."""
    class _Bus:
        def __init__(self, b):
            self._b = b
        def read_byte_data(self, addr, reg):
            return self._b.read_byte(addr, reg)
        def write_byte_data(self, addr, reg, val):
            self._b.write_byte(addr, reg, val)
        def write_i2c_block_data(self, addr, cmd, data):
            payload = bytes([cmd]) + bytes(data)
            self._b.broadcast(payload)
    return _Bus(mock_backend)


def test_broadcast_latch_payload_is_v13_gc_frame(mock_backend):
    """v1.3 GC frame: A5 27 group tick_lo tick_hi (truth-doc §5.4)."""
    bus = _ducktyped_bus(mock_backend)
    # Legacy wrapper delegates to broadcast_latch_group(group=0, tick=0)
    assert RbAmp.broadcast_latch(bus) is True
    assert len(mock_backend.broadcasts) == 1
    payload = mock_backend.broadcasts[0]
    assert payload == bytes([0xA5, R.CMD_LATCH_PERIOD, 0x00, 0x00, 0x00])


# ---------------------------------------------------------------------------
# Settle times — verify the library actually waits
# ---------------------------------------------------------------------------

def test_save_gains_sleeps_700ms(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    mock_backend.sleeps.clear()
    dev.save_gains()
    # One sleep of 700ms after CMD_SAVE_GAINS
    assert 700 in mock_backend.sleeps


def test_factory_reset_sleeps_1500ms(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    mock_backend.sleeps.clear()
    dev.factory_reset()
    assert 1500 in mock_backend.sleeps


def test_latch_settle_50ms_during_snapshot(mock_backend):
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    mock_backend.sleeps.clear()
    mock_backend.advance_ms(60_000)
    dev.read_period_snapshot()
    assert 50 in mock_backend.sleeps
