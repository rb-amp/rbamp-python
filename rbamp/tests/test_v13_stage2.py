"""Stage 2 v1.3 fixes — per-class CT-bind + RbAmpFleet + label setter + bus-robustness.

Per root [SEED v1.3] (2026-06-16):
- §3: CT-bind order-independent A1 + per-class. SCT_013 {1,2,3,4,6},
       WIRED_CT {1,2,3}, BUILTIN_CT {} (5/7 reject). NOT contiguous.
- §2: bus-robustness — NACK-retry + R/W timeouts, generic для CPython.
       upython-on-ESP32 keeps L8 IDF spin discipline.
- L-006 F.13: multi-byte WRITE byte-at-a-time (LABEL setter must use loop).
- Mirror arduino c5726bc RbAmpFleet shape — scan, poll_all, gc_latch +
  check_sync, total_power, total_energy_wh, poll_errors.
"""
from __future__ import annotations

import pytest

from rbamp import _registers_v2 as R
from rbamp import (
    RbAmp,
    RbAmpFleet,
    RbAmpIOError,
    RbAmpParamError,
    RbAmpSensorClass,
)
from rbamp.tests.conftest import MockBackend


# =====================================================================
# Per-class CT-bind A1 reject (root seed §3)
# =====================================================================

def _make_dev_v13_sct013(mock_backend):
    """RbAmp on v1.3 fw with SENSOR_CLASS pre-pinned to SCT_013."""
    mock_backend._regs[R.REG_VERSION] = 0x04
    mock_backend._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.SCT_013)
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    return dev


@pytest.mark.parametrize("code", [1, 2, 3, 4, 6])
def test_sct013_accepts_official_codes(mock_backend, code):
    dev = _make_dev_v13_sct013(mock_backend)
    dev.set_ct_model_ch(0, code)  # must not raise
    ct_writes = [w for w in mock_backend.writes if w[1] == R.REG_CT_MODEL]
    assert ct_writes[-1][2] == code


@pytest.mark.parametrize("code", [5, 7])
def test_sct013_rejects_reserved_codes(mock_backend, code):
    """SCT_013 codes 5 (-100) + 7 (-060) reserved-uncharacterised on v1.3."""
    dev = _make_dev_v13_sct013(mock_backend)
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch(0, code)


# v1.5.0 canon: client accepts any registry-known non-reserved row (legacy
# rows are still real presets on the module). WIRED_CT registry contents:
#   1..5 = legacy (accept)  6..9 = characterized (accept)
#   10, 11, 13 = reserved (reject client-side, module would ERR_PARAM)
#   12 = characterized (accept)
@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 7, 8, 9, 12])
def test_wired_ct_accepts_registered(mock_backend, code):
    mock_backend._regs[R.REG_VERSION] = 0x04
    mock_backend._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.WIRED_CT)
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.set_ct_model_ch(0, code)  # must not raise


@pytest.mark.parametrize("code", [10, 11, 13])
def test_wired_ct_rejects_reserved(mock_backend, code):
    """Reserved rows: allocated in registry, no preset table row -> guaranteed ERR_PARAM."""
    mock_backend._regs[R.REG_VERSION] = 0x04
    mock_backend._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.WIRED_CT)
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch(0, code)


@pytest.mark.parametrize("code", [14, 15, 200])
def test_wired_ct_rejects_unknown(mock_backend, code):
    """Codes not in the registry at all: rejected as unknown."""
    mock_backend._regs[R.REG_VERSION] = 0x04
    mock_backend._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.WIRED_CT)
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch(0, code)


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 7])
def test_builtin_ct_rejects_all_codes(mock_backend, code):
    """BUILTIN_CT has zero rows in the registry — every code is unknown."""
    mock_backend._regs[R.REG_VERSION] = 0x04
    mock_backend._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.BUILTIN_CT)
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch(0, code)


def test_v12_firmware_keeps_legacy_1to5_range(mock_backend):
    """On pre-v1.3 fw (REG_VERSION < 0x04) code range = 1..5, no per-class."""
    mock_backend._regs[R.REG_VERSION] = 0x03  # v1.2
    mock_backend._regs[R.REG_SENSOR_CLASS] = int(RbAmpSensorClass.SCT_013)
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    # Legacy code 5 accepted on v1.2
    dev.set_ct_model_ch(0, 5)
    # Code 6 rejected (out of legacy range)
    with pytest.raises(RbAmpParamError):
        dev.set_ct_model_ch(0, 6)


# =====================================================================
# CT model verify mirrors (truth-doc §7.3)
# =====================================================================

def test_read_ct_model_ch_returns_mirror(mock_backend):
    """REG_CT_MODEL_CH0/1/2 mirrors are read-only verify regs."""
    mock_backend._channel_models[0] = 1
    mock_backend._channel_models[1] = 3
    mock_backend._channel_models[2] = 6
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.read_ct_model_ch(0) == 1
    assert dev.read_ct_model_ch(1) == 3
    assert dev.read_ct_model_ch(2) == 6


def test_read_ct_model_ch_rejects_invalid_channel(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.read_ct_model_ch(3)
    with pytest.raises(RbAmpParamError):
        dev.read_ct_model_ch(-1)


# =====================================================================
# LABEL setter — byte-loop per L-006 F.13
# =====================================================================

def test_set_label_writes_byte_loop(mock_backend):
    """L-006: set_label must write byte-at-a-time (F.13 asymmetry).

    Verify by inspecting writes log: 8 separate (addr, reg+i, byte_i)
    pairs, not a single block-write.
    """
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.set_label("boiler")  # 6 ASCII bytes + 2 NUL pad

    label_writes = [w for w in mock_backend.writes
                    if R.REG_LABEL <= w[1] < R.REG_LABEL + R.REG_LABEL_SIZE]
    assert len(label_writes) == R.REG_LABEL_SIZE  # all 8 bytes individually
    # Sequential ascending register addrs
    addrs = [w[1] for w in label_writes]
    assert addrs == list(range(R.REG_LABEL, R.REG_LABEL + R.REG_LABEL_SIZE))
    # Payload: 'b','o','i','l','e','r',0,0
    assert bytes(w[2] for w in label_writes) == b"boiler\x00\x00"


def test_set_label_persists_via_save_user_config(mock_backend):
    """LABEL persists via CMD_SAVE_USER_CONFIG (production-OK, per §8.1)."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    mock_backend.writes.clear()
    dev.set_label("kitchen")
    saves = [w for w in mock_backend.writes
             if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG]
    assert saves


def test_set_label_truncates_to_8_bytes(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.set_label("longer_than_eight_chars")
    # Mock register state: first 8 bytes of the input
    raw = bytes(mock_backend._regs[R.REG_LABEL + i] for i in range(8))
    assert raw == b"longer_t"


def test_set_label_rejects_non_ascii(mock_backend):
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    with pytest.raises(RbAmpParamError):
        dev.set_label("кухня")  # Cyrillic — non-ASCII


def test_read_label_strips_nuls(mock_backend):
    for i, c in enumerate(b"meter\x00\x00\x00"):
        mock_backend._regs[R.REG_LABEL + i] = c
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    assert dev.read_label() == "meter"


# =====================================================================
# Bus-robustness — NACK-retry + R/W timeouts (CPython generic)
# =====================================================================

class _FlakyBus:
    """smbus2 duck-typed bus that NACKs the first N reads then succeeds."""

    def __init__(self, fail_count, read_value=0x55):
        self._fail = fail_count
        self._read_value = read_value
        self.write_fail_count = 0
        self.reads = 0
        self.writes = 0

    def read_byte_data(self, addr, reg):
        self.reads += 1
        if self._fail > 0:
            self._fail -= 1
            raise OSError(121, "remote i/o error")
        return self._read_value

    def write_byte_data(self, addr, reg, val):
        self.writes += 1
        if self.write_fail_count > 0:
            self.write_fail_count -= 1
            raise OSError(121, "remote i/o error")

    def write_i2c_block_data(self, addr, cmd, data):
        pass


def test_smbus_backend_retries_on_nack():
    from rbamp._io_smbus import SMBusBackend
    bus = _FlakyBus(fail_count=2)  # 2 NACKs then success
    be = SMBusBackend(bus, retry_attempts=3, retry_gap_ms=0)
    assert be.read_byte(0x50, 0x03) == 0x55
    assert be.retry_count_total == 2  # 2 retries before success
    assert be.retry_exhaustion_count == 0


def test_smbus_backend_exhausts_after_n_attempts():
    from rbamp._io_smbus import SMBusBackend
    bus = _FlakyBus(fail_count=10)
    be = SMBusBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, 0x03)
    assert be.retry_exhaustion_count == 1


def test_smbus_backend_write_retries_too():
    from rbamp._io_smbus import SMBusBackend
    bus = _FlakyBus(fail_count=0)
    bus.write_fail_count = 1
    be = SMBusBackend(bus, retry_attempts=2, retry_gap_ms=0)
    be.write_byte(0x50, 0x03, 0xAA)
    assert be.retry_count_total >= 1


def test_smbus_backend_reset_counters_zeros():
    from rbamp._io_smbus import SMBusBackend
    bus = _FlakyBus(fail_count=2)
    be = SMBusBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.read_byte(0x50, 0x03)
    assert be.retry_count_total > 0
    be.reset_counters()
    assert be.retry_count_total == 0
    assert be.retry_exhaustion_count == 0


def test_smbus_backend_min_one_attempt():
    """retry_attempts < 1 clamped to 1."""
    from rbamp._io_smbus import SMBusBackend
    bus = _FlakyBus(fail_count=0)
    be = SMBusBackend(bus, retry_attempts=0)
    assert be.read_byte(0x50, 0x03) == 0x55  # one attempt suffices


# =====================================================================
# RbAmpFleet — host-side fleet manager
# =====================================================================

def _make_fleet_with_two_mocks():
    """Construct a fleet with two mocks at 0x50 + 0x51 sharing one bus.

    The fleet bus duck-types smbus2 by dispatching to the right mock
    based on the slave addr.
    """
    a = MockBackend(slave_addr=0x50, channels=3, has_voltage=True)
    b = MockBackend(slave_addr=0x51, channels=1, has_voltage=False)
    b._regs[R.REG_HW_VARIANT] = 4  # I1

    class _MultiBus:
        def __init__(self, mocks):
            self._mocks = mocks
            self.broadcasts = []
        def _pick(self, addr):
            for m in self._mocks:
                if m._slave_addr == addr:
                    return m
            raise OSError(121, "remote i/o error")
        def read_byte_data(self, addr, reg):
            return self._pick(addr).read_byte(addr, reg)
        def write_byte_data(self, addr, reg, val):
            self._pick(addr).write_byte(addr, reg, val)
        def write_i2c_block_data(self, addr, cmd, data):
            payload = bytes([cmd]) + bytes(data)
            self.broadcasts.append(payload)
            # Replay to every member that supports GC
            for m in self._mocks:
                m.broadcast(payload)

    bus = _MultiBus([a, b])
    fleet = RbAmpFleet(bus)
    # Pre-populate (no scan loop, since mocks don't simulate full addr space)
    dev_a = RbAmp(bus, addr=0x50); dev_a.begin()
    dev_b = RbAmp(bus, addr=0x51); dev_b.begin()
    fleet.add(dev_a)
    fleet.add(dev_b)
    return fleet, a, b, bus


def test_fleet_basic_membership():
    fleet, _, _, _ = _make_fleet_with_two_mocks()
    assert fleet.count == 2
    assert len(fleet) == 2
    assert fleet[0].address == 0x50
    assert fleet[1].address == 0x51
    assert fleet.find(0x50) is not None
    assert fleet.find(0x99) is None


def test_fleet_add_rejects_duplicate_address():
    fleet, _, _, bus = _make_fleet_with_two_mocks()
    dup = RbAmp(bus, addr=0x50)
    dup.begin()
    with pytest.raises(RbAmpParamError):
        fleet.add(dup)


def test_fleet_poll_all_returns_per_member_result():
    fleet, a, b, _ = _make_fleet_with_two_mocks()
    a.set_rt_power(0, 800.0)
    b.set_rt_power(0, 200.0)
    out = fleet.poll_all()
    assert len(out) == 2
    snap_a, info_a = out[0]
    snap_b, info_b = out[1]
    assert info_a.ok and info_b.ok
    assert snap_a.power[0] == pytest.approx(800.0)
    assert snap_b.power[0] == pytest.approx(200.0)


def test_fleet_poll_isolates_one_dead_module():
    fleet, a, b, _ = _make_fleet_with_two_mocks()
    # Force b to NACK on every read
    b.fail_addresses.add((0x51, R.REG_V03_U_RMS))
    out = fleet.poll_all()
    snap_a, info_a = out[0]
    snap_b, info_b = out[1]
    assert info_a.ok          # a survives
    assert not info_b.ok      # b dropped
    assert snap_a is not None
    assert snap_b is None


def test_fleet_total_power_aggregates():
    fleet, a, b, _ = _make_fleet_with_two_mocks()
    a.set_rt_power(0, 100.0)
    a.set_rt_power(1, 200.0)
    a.set_rt_power(2, 300.0)
    b.set_rt_power(0, 50.0)
    total = fleet.total_power()
    assert total == pytest.approx(650.0)


def test_fleet_gc_latch_and_check_sync():
    """gc_latch broadcasts a 5-byte frame; check_sync reads back REG_GC_TICK."""
    fleet, a, b, bus = _make_fleet_with_two_mocks()
    # Enable GC + group 0 on both
    a._regs[R.REG_FLEET_CONFIG] = 0x01
    b._regs[R.REG_FLEET_CONFIG] = 0x01
    tick = fleet.gc_latch(group=0, tick=0x4242, settle_ms=0)
    assert tick == 0x4242
    sync = fleet.check_sync(expected_tick=0x4242)
    assert len(sync) == 2
    for s in sync:
        assert s.reachable
        assert s.in_sync
        assert s.gc_tick == 0x4242


def test_fleet_gc_latch_auto_increments_tick():
    fleet, a, b, _ = _make_fleet_with_two_mocks()
    a._regs[R.REG_FLEET_CONFIG] = 0x01
    b._regs[R.REG_FLEET_CONFIG] = 0x01
    t1 = fleet.gc_latch(group=0, tick=None, settle_ms=0)
    t2 = fleet.gc_latch(group=0, tick=None, settle_ms=0)
    assert t2 == t1 + 1


def test_fleet_poll_errors_per_member():
    fleet, a, b, _ = _make_fleet_with_two_mocks()
    a._regs[R.REG_EVENT_FLAGS] = R.EVENT_ERROR  # a has error
    b._regs[R.REG_EVENT_FLAGS] = 0              # b clean
    errs = fleet.poll_errors()
    assert errs[0x50] is True
    assert errs[0x51] is False


def test_fleet_enable_gc_all():
    fleet, a, b, _ = _make_fleet_with_two_mocks()
    ok = fleet.enable_gc_all(group=7)
    assert ok == 2
    assert a._regs[R.REG_FLEET_CONFIG] & 0x01
    assert b._regs[R.REG_FLEET_CONFIG] & 0x01
    assert a._regs[R.REG_GROUP_ID] == 7
    assert b._regs[R.REG_GROUP_ID] == 7


# =====================================================================
# Self-review polish — enable_gc no-op + addr commit arm-clear on failure
# =====================================================================

def test_enable_gc_is_noop_when_bit_already_correct(mock_backend):
    """Calling enable_gc(True) twice should NOT burn a second flash write."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.enable_gc(True)
    saves_first = sum(1 for w in mock_backend.writes
                      if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG)
    # Second call must early-return without a save.
    dev.enable_gc(True)
    saves_second = sum(1 for w in mock_backend.writes
                       if w[1] == R.REG_COMMAND and w[2] == R.CMD_SAVE_USER_CONFIG)
    assert saves_second == saves_first


def test_commit_address_change_clears_arm_on_failure(mock_backend):
    """Partial-failure path (NACK between writes) must clear arm state in
    finally, so a retry caller doesn't double-commit."""
    dev = RbAmp(mock_backend, addr=0x50)
    dev.begin()
    dev.prepare_address_change(0x55)
    assert dev._addr_change_armed is True

    # Force a NACK on the magic write (mid-sequence)
    mock_backend.fail_addresses.add((0x50, R.REG_ADDR_COMMIT_MAGIC))
    with pytest.raises(RbAmpIOError):
        dev.commit_address_change()
    # Arm cleared by finally so caller can re-prepare safely.
    assert dev._addr_change_armed is False
