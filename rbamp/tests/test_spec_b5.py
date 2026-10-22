"""SPEC §B.5 — retry + loose sanity filter regression tests.

Covers Task A (MachineI2CBackend retry) and Task B (loose sanity in
_rbamp_core._read_float_le). Task C (SMBusBackend left untouched) is
covered by the absence of new retry-on-SMBus tests — the old
test_rbamp_class.py suite continues to pass against the unchanged
SMBusBackend semantics.
"""
from __future__ import annotations

import struct

import pytest

from rbamp import RbAmp, RbAmpIOError
from rbamp import _registers as R
from rbamp._io_micropython import MachineI2CBackend


# ---------------------------------------------------------------------------
# Task A — MachineI2CBackend per-byte retry
# ---------------------------------------------------------------------------

class _FlakyI2C:
    """machine.I2C-shaped mock that NACKs the first N attempts then succeeds.

    Counts every readfrom_mem / writeto_mem call so tests can assert the
    exact number of retries that fired.
    """

    def __init__(self, fail_count, read_value=0x42):
        self._fail_count = int(fail_count)
        self._read_value = read_value
        self.read_calls = 0
        self.write_calls = 0
        self.last_write = None

    def readfrom_mem(self, addr, reg, n):
        self.read_calls += 1
        if self.read_calls <= self._fail_count:
            raise OSError(19, "ENODEV (mock NACK)")
        return bytes([self._read_value & 0xFF] * n)

    def writeto_mem(self, addr, reg, payload):
        self.write_calls += 1
        if self.write_calls <= self._fail_count:
            raise OSError(19, "ENODEV (mock NACK)")
        self.last_write = (addr, reg, bytes(payload))


def test_read_byte_succeeds_on_first_attempt():
    bus = _FlakyI2C(fail_count=0, read_value=0xAB)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    val = be.read_byte(0x50, R.REG_VERSION)
    assert val == 0xAB
    assert bus.read_calls == 1


def test_read_byte_recovers_after_two_nacks():
    """3 NACKs would exhaust; 2 NACKs + 1 success = expected v1 firmware case."""
    bus = _FlakyI2C(fail_count=2, read_value=0x55)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    val = be.read_byte(0x50, R.REG_VERSION)
    assert val == 0x55
    assert bus.read_calls == 3


def test_read_byte_exhausts_and_raises():
    """3 NACKs in a row with retry_attempts=3 must propagate RbAmpIOError."""
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError) as exc_info:
        be.read_byte(0x50, R.REG_VERSION)
    assert "after 3 attempts" in str(exc_info.value)
    assert bus.read_calls == 3
    # Underlying cause chain is preserved per `raise ... from`.
    assert isinstance(exc_info.value.__cause__, OSError)


def test_write_byte_succeeds_on_first_attempt():
    bus = _FlakyI2C(fail_count=0)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.write_byte(0x50, R.REG_COMMAND, R.CMD_LATCH_PERIOD)
    assert bus.write_calls == 1
    assert bus.last_write == (0x50, R.REG_COMMAND, bytes([R.CMD_LATCH_PERIOD]))


def test_write_byte_recovers_after_one_nack():
    bus = _FlakyI2C(fail_count=1)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.write_byte(0x50, R.REG_COMMAND, R.CMD_LATCH_PERIOD)
    assert bus.write_calls == 2
    assert bus.last_write == (0x50, R.REG_COMMAND, bytes([R.CMD_LATCH_PERIOD]))


def test_write_byte_exhausts_and_raises():
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError) as exc_info:
        be.write_byte(0x50, R.REG_COMMAND, R.CMD_LATCH_PERIOD)
    assert "after 3 attempts" in str(exc_info.value)
    assert bus.write_calls == 3
    assert isinstance(exc_info.value.__cause__, OSError)


def test_retry_attempts_clamped_to_min_one():
    """Constructor must clamp retry_attempts<1 to 1 (no infinite loops)."""
    bus = _FlakyI2C(fail_count=0)
    be = MachineI2CBackend(bus, retry_attempts=0, retry_gap_ms=0)
    be.read_byte(0x50, R.REG_VERSION)
    assert bus.read_calls == 1


def test_retry_attempts_one_disables_retry():
    """retry_attempts=1 == single shot, NACK propagates immediately.

    This is the recommended setting on non-ESP32 MicroPython ports and
    on ESP32 once v1.1 firmware fixes the NACK source.
    """
    bus = _FlakyI2C(fail_count=1)
    be = MachineI2CBackend(bus, retry_attempts=1, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    assert bus.read_calls == 1


def test_register_acks_remains_single_shot():
    """register_acks() MUST NOT retry — it's a NACK probe.

    Retry would mask the very NACK signal the probe is testing for, per
    the comment in the task brief.
    """
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    assert be.register_acks(0x50, R.REG_V03_PERIOD_AVG_P_F2) is False
    assert bus.read_calls == 1  # exactly one — no retry


# ---------------------------------------------------------------------------
# Task B — _rbamp_core._read_float_le loose sanity filter
# ---------------------------------------------------------------------------

def _stuff_float(mock, reg, value):
    """Write a float value into the mock backend's reg table as 4 bytes LE."""
    raw = struct.pack("<f", value)
    for i in range(4):
        mock._regs[reg + i] = raw[i]


def test_sanity_filter_accepts_normal_voltage(mock_backend):
    """230 V is finite and well within ±10000 — must pass."""
    with RbAmp(mock_backend, 0x50) as dev:
        u = dev.read_voltage()
    assert abs(u - 230.0) < 0.01


def test_sanity_filter_accepts_zero(mock_backend):
    """Brownout / disconnect → U=0 V must pass through unfiltered."""
    _stuff_float(mock_backend, R.REG_V03_U_RMS, 0.0)
    with RbAmp(mock_backend, 0x50) as dev:
        u = dev.read_voltage()
    assert u == 0.0


def test_sanity_filter_accepts_brownout_voltage(mock_backend):
    """Brownout at 80 V is a real, user-visible condition — MUST NOT filter."""
    _stuff_float(mock_backend, R.REG_V03_U_RMS, 80.0)
    with RbAmp(mock_backend, 0x50) as dev:
        u = dev.read_voltage()
    assert abs(u - 80.0) < 0.01


def test_sanity_filter_accepts_negative_power(mock_backend):
    """Bidirectional energy: P can be negative. -500 W must pass."""
    _stuff_float(mock_backend, R.REG_V03_P0_REAL, -500.0)
    with RbAmp(mock_backend, 0x50) as dev:
        p = dev.read_power(0)
    assert abs(p - (-500.0)) < 0.01


def test_sanity_filter_accepts_the_1962v_ghost(mock_backend):
    """The IDF buffer-leak pattern 0x3C2FFB3F decodes to 1.962 V.

    At the BACKEND level this would have already been rejected by retry —
    by the time _read_float_le sees the bytes, retry has succeeded. The
    1.962 V value is finite and < 10000, so the sanity filter ITSELF
    must let it pass. This is by design per SPEC §B.5: sanity only catches
    NaN/Inf/exotic patterns, not values that look superficially plausible.
    The retry layer is the primary defense; sanity is the safety net for
    NaN/Inf only.
    """
    _stuff_float(mock_backend, R.REG_V03_U_RMS, 1.962)
    with RbAmp(mock_backend, 0x50) as dev:
        u = dev.read_voltage()
    assert abs(u - 1.962) < 0.001


def test_sanity_filter_rejects_nan(mock_backend):
    """NaN bit pattern in reg table → RbAmpIOError on read."""
    # 0x7FC00000 is canonical quiet-NaN in IEEE-754 binary32.
    mock_backend._regs[R.REG_V03_U_RMS + 0] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 1] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 2] = 0xC0
    mock_backend._regs[R.REG_V03_U_RMS + 3] = 0x7F
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpIOError) as exc_info:
            dev.read_voltage()
    assert "non-physical" in str(exc_info.value)


def test_sanity_filter_rejects_positive_inf(mock_backend):
    """+Inf bit pattern 0x7F800000 → reject."""
    mock_backend._regs[R.REG_V03_U_RMS + 0] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 1] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 2] = 0x80
    mock_backend._regs[R.REG_V03_U_RMS + 3] = 0x7F
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpIOError):
            dev.read_voltage()


def test_sanity_filter_rejects_negative_inf(mock_backend):
    """-Inf bit pattern 0xFF800000 → reject."""
    mock_backend._regs[R.REG_V03_U_RMS + 0] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 1] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 2] = 0x80
    mock_backend._regs[R.REG_V03_U_RMS + 3] = 0xFF
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpIOError):
            dev.read_voltage()


def test_sanity_filter_rejects_above_ten_thousand(mock_backend):
    """|value| > 10000 V is non-physical — reject."""
    _stuff_float(mock_backend, R.REG_V03_U_RMS, 15000.0)
    with RbAmp(mock_backend, 0x50) as dev:
        with pytest.raises(RbAmpIOError):
            dev.read_voltage()


def test_sanity_filter_accepts_exactly_ten_thousand(mock_backend):
    """Boundary: |value| == 10000.0 passes (filter is strict >).

    Pick a non-large RT register so the mock-default U_RMS variant probe
    doesn't trip; P0_REAL is fine.
    """
    _stuff_float(mock_backend, R.REG_V03_P0_REAL, 10000.0)
    with RbAmp(mock_backend, 0x50) as dev:
        p = dev.read_power(0)
    assert abs(p - 10000.0) < 0.01


# ---------------------------------------------------------------------------
# Diagnostic counters (Phase 2 — long-soak instrumentation)
# ---------------------------------------------------------------------------

def test_machine_backend_retry_counter_starts_at_zero():
    bus = _FlakyI2C(fail_count=0)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    assert be.retry_exhaustion_count == 0


def test_machine_backend_retry_counter_unchanged_on_success():
    bus = _FlakyI2C(fail_count=2)  # 2 NACKs then success — within retry budget
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_exhaustion_count == 0


def test_machine_backend_retry_counter_increments_on_exhaustion():
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_exhaustion_count == 1
    # And second exhaustion bumps it again
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_exhaustion_count == 2


def test_machine_backend_write_exhaustion_also_counted():
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError):
        be.write_byte(0x50, R.REG_COMMAND, 0x27)
    assert be.retry_exhaustion_count == 1


def test_machine_backend_reset_counters_zeros_them():
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_exhaustion_count == 1
    be.reset_counters()
    assert be.retry_exhaustion_count == 0


def test_rbamp_sanity_counter_starts_at_zero(mock_backend):
    with RbAmp(mock_backend, 0x50) as dev:
        assert dev.sanity_reject_count == 0


def test_rbamp_sanity_counter_increments_on_reject(mock_backend):
    """Bump sanity_reject_count once per filtered read."""
    # Inject +Inf
    mock_backend._regs[R.REG_V03_P0_REAL + 0] = 0x00
    mock_backend._regs[R.REG_V03_P0_REAL + 1] = 0x00
    mock_backend._regs[R.REG_V03_P0_REAL + 2] = 0x80
    mock_backend._regs[R.REG_V03_P0_REAL + 3] = 0x7F
    with RbAmp(mock_backend, 0x50) as dev:
        # begin() may have bumped on the U_RMS read if voltage was Inf, but
        # default mock has 230.0 V — counter stays 0 after begin.
        baseline = dev.sanity_reject_count
        with pytest.raises(RbAmpIOError):
            dev.read_power(0)
        assert dev.sanity_reject_count == baseline + 1
        # Second attempt bumps again
        with pytest.raises(RbAmpIOError):
            dev.read_power(0)
        assert dev.sanity_reject_count == baseline + 2


def test_rbamp_reset_counters_zeros_both_sides(mock_backend):
    """reset_counters() clears both the rbamp-side and backend-side counters.

    v1.3 _detect_variant() reads REG_HW_VARIANT (byte, no sanity filter)
    so begin() can no longer trigger a sanity bump on its own. Use an
    explicit read_voltage() with NaN to bump the counter.
    """
    # Inject NaN into U_RMS.
    mock_backend._regs[R.REG_V03_U_RMS + 0] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 1] = 0x00
    mock_backend._regs[R.REG_V03_U_RMS + 2] = 0xC0
    mock_backend._regs[R.REG_V03_U_RMS + 3] = 0x7F
    dev = RbAmp(mock_backend, 0x50)
    dev.begin()
    with pytest.raises(RbAmpIOError):
        dev.read_voltage()
    assert dev.sanity_reject_count >= 1
    dev.reset_counters()
    assert dev.sanity_reject_count == 0
    # Mock-backend doesn't expose retry_exhaustion_count; reset_counters
    # must NOT raise when the backend lacks the method.


def test_rbamp_reset_counters_resets_real_backend_too():
    """End-to-end via MachineI2CBackend wrapping a flaky bus."""
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    # Trigger exhaustion at backend level (no RbAmp involved)
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_exhaustion_count == 1
    # Wrap in RbAmp (skip begin() — no working device behind the flaky bus)
    dev = RbAmp.__new__(RbAmp)
    dev._io = be
    dev.sanity_reject_count = 5
    dev.reset_counters()
    assert dev.sanity_reject_count == 0
    assert be.retry_exhaustion_count == 0


# ---------------------------------------------------------------------------
# retry_count_total — bumped on every silent recovery, used by the
# soak engine to compute retry_rate and scheduler-jitter-excluding-retries.
# ---------------------------------------------------------------------------

def test_retry_count_total_starts_at_zero():
    bus = _FlakyI2C(fail_count=0)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    assert be.retry_count_total == 0


def test_retry_count_total_unchanged_on_first_attempt_success():
    bus = _FlakyI2C(fail_count=0)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_count_total == 0


def test_retry_count_total_increments_per_silent_recovery():
    """Counts each retry sleep — distinct from exhaustion_count."""
    bus = _FlakyI2C(fail_count=2)  # 2 fails + 1 success = 2 retries
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_count_total == 2
    # exhaustion_count stays 0 because retry succeeded
    assert be.retry_exhaustion_count == 0


def test_retry_count_total_increments_on_exhausted_path_too():
    """Exhausting all attempts still counts the inter-attempt sleeps."""
    bus = _FlakyI2C(fail_count=99)  # always fails
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    # 3 attempts = 2 inter-attempt sleeps
    assert be.retry_count_total == 2
    assert be.retry_exhaustion_count == 1


def test_retry_count_total_resets_with_reset_counters():
    bus = _FlakyI2C(fail_count=2)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    be.read_byte(0x50, R.REG_VERSION)
    assert be.retry_count_total == 2
    be.reset_counters()
    assert be.retry_count_total == 0
    assert be.retry_exhaustion_count == 0


def test_retry_count_total_distinguishes_recovery_from_failure():
    """Recovery and failure both bump retry_count_total, but only failure
    bumps retry_exhaustion_count. This is the contract the soak engine
    relies on to separate bus-health (recovery rate) from outright errors."""
    bus = _FlakyI2C(fail_count=99)
    be = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=0)
    # 1 exhausted call: 2 retries, 1 exhaustion
    with pytest.raises(RbAmpIOError):
        be.read_byte(0x50, R.REG_VERSION)
    assert (be.retry_count_total, be.retry_exhaustion_count) == (2, 1)
    # Reset and run a recovery-only call
    be.reset_counters()
    bus2 = _FlakyI2C(fail_count=1)
    be2 = MachineI2CBackend(bus2, retry_attempts=3, retry_gap_ms=0)
    be2.read_byte(0x50, R.REG_VERSION)
    assert (be2.retry_count_total, be2.retry_exhaustion_count) == (1, 0)
