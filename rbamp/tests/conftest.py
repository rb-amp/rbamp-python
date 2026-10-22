"""
pytest fixtures — mock backend that emulates the rbAmp device register model.

The mock implements the same duck-typed backend interface as
:class:`SMBusBackend` / :class:`MachineI2CBackend` so it can be passed
directly to :class:`RbAmp` via the test-mock branch in
``_rbamp_core.RbAmp.__init__``.
"""
from __future__ import annotations

import struct

import pytest

from rbamp import _registers_v2 as R


# Variant → REG_HW_VARIANT byte mapping (truth-doc §1.2 + v1.4 root A5)
_VARIANT_BYTE = {
    (1, True):  1,   # UI1
    (2, True):  2,   # UI2
    (3, True):  3,   # UI3
    (1, False): 4,   # I1
    (2, False): 5,   # I2
    (3, False): 6,   # I3
    (5, True):  7,   # UI5  (v1.4 senior)
    (7, True):  8,   # UI7  (v1.4 senior)
}


class MockBackend:
    """Mocks a rbAmp slave at a fixed address.

    Maintains an internal 256-byte register table; exposes the same methods
    as the real backends. Time is controlled manually (set ``self._now_ms``)
    so tests run instantly without real ``sleep_ms`` delays.

    Configurable per-test:

    * ``self.channels``       — variant (1, 2, 3); controls REG_HW_VARIANT byte.
    * ``self.has_voltage``    — sets REG_V03_U_RMS magnitude + UI vs I SKU byte.
    * ``self.mode_factory``   — 0 or 1; gates flash-write tests.
    * ``self.period_valid``   — toggle to test stale-snapshot handling.
    * ``self.fail_addresses`` — set of (addr, reg) pairs that NACK on read.
    """

    def __init__(self, slave_addr=0x50, channels=3, has_voltage=True, mode_factory=0):
        self._slave_addr = slave_addr
        self._regs = bytearray(256)
        self._regs[R.REG_VERSION] = 0x04  # v1.3
        self._regs[R.REG_MODE] = mode_factory
        self._regs[R.REG_V03_STATUS] = 0x01
        self._regs[R.REG_V03_PERIOD_VALID] = 0x01
        self._regs[R.REG_AC_FREQ] = 50
        self._regs[R.REG_I2C_ADDRESS] = slave_addr
        self._regs[R.REG_HW_VARIANT] = _VARIANT_BYTE.get(
            (channels, has_voltage), 3 if has_voltage else 6,
        )
        # v1.4: REG_TOPOLOGY (0x24) = I-channel count. Semantic COUNT, not the
        # legacy SINGLE/SPLIT/THREE enum (root A4). Junior boards fill 1/2/3,
        # senior 5/7. Ancient v1.0 firmware would leave 0x00 — we don't
        # simulate that path here (fixture is always ≥ v1.3).
        self._regs[R.REG_TOPOLOGY] = channels
        self._regs[R.REG_PRODUCT_ID] = 0x01  # rbAmp sensor
        self._regs[R.REG_FLEET_CONFIG] = 0x00  # GC disabled by default
        self._regs[R.REG_GROUP_ID] = 0x00
        # REG_GC_TICK 0xFFFF = never received
        self._regs[R.REG_GC_TICK] = 0xFF
        self._regs[R.REG_GC_TICK + 1] = 0xFF
        self.channels = channels
        self.has_voltage = has_voltage
        self.period_valid = True
        self.mode_factory = mode_factory
        self.fail_addresses = set()
        self._now = 0
        self._addr_commit_armed = False
        self._reset_gain = {}    # ch -> gain applied on the next CMD_RESET
        self.reset_count = 0     # number of CMD_RESET writes seen
        self.writes = []         # log of (addr, reg, val) for assertions
        self.broadcasts = []     # log of payload bytes
        self.sleeps = []         # log of sleep_ms calls

        # v1.4 channel-access window state: (field, ch) selected by writes
        # to REG_CHANNEL_SELECT; reads of REG_CHANNEL_DATA..+3 return the
        # snapshot stashed at select-time. Seed with plausible per-(field,ch)
        # values so tests can assert without pre-seeding.
        self._window_selected = (0, 0)   # last (field, ch)
        self._window_data = bytes(4)      # last snapshotted 4 bytes
        # Per-(field, ch) seed table for senior SKUs. Fields 0..3 live, 13 latched.
        # Value formula makes each cell distinct for cross-check tests.
        self._window_values = {}
        for _f in (0, 1, 2, 3, 13):
            for _ch in range(max(3, channels)):
                self._window_values[(_f, _ch)] = self._seed_window_value(_f, _ch)

        # v1.4.9+ / v1.4.18: field 15 = CT_MODEL (u8 RW per-channel). Stored
        # separately from the float snapshot: each channel keeps its own
        # applied model byte, updated in place on a window write to 0x3C
        # (unless the (class, code) pair is in :attr:`err_param_pairs` — the
        # module rejects with ERR_PARAM 0xFE without clobbering).
        self._channel_models = [0] * max(3, channels)
        # (class, code) pairs the mock module will reject with ERR_PARAM.
        # Empty by default; tests seed to simulate a preset table that lacks
        # a row for a code the registry admits.
        self.err_param_pairs = set()

        # Optional post-SAVE NACK simulation (root A3): if > 0, the mock
        # NACKs REG_V03_STATUS reads for this many advance_ms ticks after
        # the last CMD_SAVE_GAINS write.
        self.nack_after_save_ms = 0
        self._save_nack_deadline = 0

        # Seed RT float registers
        self._set_float(R.REG_V03_U_RMS, 230.0 if has_voltage else 0.0)
        self._set_float(R.REG_V03_U_PEAK, 325.0 if has_voltage else 0.0)
        self._set_float(R.REG_V03_I0_RMS, 1.23)
        self._set_float(R.REG_V03_I1_RMS, 2.34)
        self._set_float(R.REG_V03_I2_RMS, 3.45)
        self._set_float(R.REG_V03_P0_REAL, 100.0)
        self._set_float(R.REG_V03_P1_REAL, 200.0)
        self._set_float(R.REG_V03_P2_REAL, 300.0)
        self._set_float(R.REG_V03_PF0, 0.95)
        self._set_float(R.REG_V03_PF1, 0.97)
        self._set_float(R.REG_V03_PF2, 0.99)
        # Period block (v2 names)
        self._set_float(R.REG_V03_PERIOD_AVG_P, 95.0)         # ch0 (0xDC)
        self._set_float(R.REG_V03_PERIOD_AVG_P_CH1, 195.0)    # ch1 (0xC2)
        self._set_float(R.REG_V03_PERIOD_AVG_P_CH2, 295.0)    # ch2 (0xC6)
        self._set_float(R.REG_V03_PERIOD_MAX_P, 110.0)        # 0xE0
        self._set_u32_le(R.REG_V03_PERIOD_LATCH_MS, 60_012)

    def _seed_window_value(self, field, ch):
        """Distinct-per-cell seed formula for the channel window fixture.
        Field 0 (I_RMS): 1.0 + ch*0.1;  1 (I_PEAK): 5.0 + ch*0.5;
        Field 2 (P_REAL): 100.0 + ch*50; 3 (PF): 0.90 + ch*0.01;
        Field 13 (PERIOD_AVG_P): 95.0 + ch*50."""
        base = {0: 1.0 + ch * 0.1,
                1: 5.0 + ch * 0.5,
                2: 100.0 + ch * 50.0,
                3: 0.90 + ch * 0.01,
                13: 95.0 + ch * 50.0}
        return base.get(field, 0.0)

    def set_window_value(self, field, ch, value):
        """Test helper: override the per-(field, ch) window snapshot value."""
        self._window_values[(field, ch)] = float(value)

    # ----- public knobs ----------------------------------------------------

    def set_rt_power(self, ch, watts):
        addrs = {0: R.REG_V03_P0_REAL, 1: R.REG_V03_P1_REAL, 2: R.REG_V03_P2_REAL}
        self._set_float(addrs[ch], watts)

    def set_period_avg(self, ch, watts):
        addrs = {0: R.REG_V03_PERIOD_AVG_P,
                 1: R.REG_V03_PERIOD_AVG_P_CH1,
                 2: R.REG_V03_PERIOD_AVG_P_CH2}
        self._set_float(addrs[ch], watts)

    def set_gain(self, ch, g):
        """Test helper: seed the applied gain register for ch0-2 (0xF4/0xF8/0xFC,
        float32 LE) — the value :meth:`RbAmp._read_ct_gain` reads."""
        self._set_float(R.REG_V03_I0_GAIN + ch * 4, g)

    def set_sensor_class(self, cls):
        """Test helper: pin REG_SENSOR_CLASS (0x25)."""
        self._regs[R.REG_SENSOR_CLASS] = cls & 0xFF

    def set_model(self, ch, code):
        """Test helper: set the applied CT model mirror for a channel."""
        if ch <= 2:
            self._regs[R.REG_CT_MODEL_CH0 + ch] = code & 0xFF
        if ch < len(self._channel_models):
            self._channel_models[ch] = code & 0xFF

    def set_gain_after_reset(self, ch, g):
        """Test helper: stage the gain a channel will show AFTER the next
        CMD_RESET (models the flash state recovered on restart)."""
        self._reset_gain[ch] = float(g)

    def advance_ms(self, ms):
        self._now += ms

    def set_stale(self):
        self.period_valid = False

    def clear_stale(self):
        self.period_valid = True

    def _set_float(self, reg, val):
        b = struct.pack("<f", val)
        for i in range(4):
            self._regs[reg + i] = b[i]

    def _set_u32_le(self, reg, val):
        for i in range(4):
            self._regs[reg + i] = (val >> (8 * i)) & 0xFF

    # ----- backend-protocol surface (matches SMBusBackend duck type) -------

    def read_byte(self, addr, reg):
        if (addr, reg) in self.fail_addresses:
            from rbamp import RbAmpIOError
            raise RbAmpIOError("mock NACK at 0x{:02X}/0x{:02X}".format(addr, reg))
        if addr != self._slave_addr:
            from rbamp import RbAmpIOError
            raise RbAmpIOError("mock NACK at 0x{:02X} (expected 0x{:02X})".format(
                addr, self._slave_addr))
        # v1.4 post-SAVE NACK simulation (root A3): NACK STATUS reads until
        # the deadline expires.
        if reg == R.REG_V03_STATUS and self._now < self._save_nack_deadline:
            from rbamp import RbAmpIOError
            raise RbAmpIOError("mock post-SAVE tail-window NACK (until {} ms)".format(
                self._save_nack_deadline))
        # Live-pin REG_V03_PERIOD_VALID to the knob
        if reg == R.REG_V03_PERIOD_VALID:
            return 0x01 if self.period_valid else 0x00
        # v1.4 channel-access-window data reads (0x3C..0x3F).
        # Field 15 (CT_MODEL) returns u8 in low byte (0x3C), high bytes zero.
        if R.REG_CHANNEL_DATA <= reg < R.REG_CHANNEL_DATA + R.REG_CHANNEL_DATA_SIZE:
            offset = reg - R.REG_CHANNEL_DATA
            field, ch = self._window_selected
            if field == R.CHFIELD_CT_MODEL:
                return self._channel_models[ch] & 0xFF if offset == 0 else 0
            return self._window_data[offset]
        # Flat CT_MODEL_CH0-2 mirrors reflect self._channel_models.
        if reg == R.REG_CT_MODEL_CH0:
            return self._channel_models[0] & 0xFF
        if reg == R.REG_CT_MODEL_CH1:
            return self._channel_models[1] & 0xFF
        if reg == R.REG_CT_MODEL_CH2:
            return self._channel_models[2] & 0xFF
        return self._regs[reg]

    def write_byte(self, addr, reg, val):
        if (addr, reg) in self.fail_addresses:
            from rbamp import RbAmpIOError
            raise RbAmpIOError("mock NACK at 0x{:02X}/0x{:02X}".format(addr, reg))
        if addr != self._slave_addr:
            from rbamp import RbAmpIOError
            raise RbAmpIOError("mock NACK at 0x{:02X}".format(addr))
        self.writes.append((addr, reg, val))
        self._regs[reg] = val & 0xFF
        # Side-effect: a fresh latch resets stale flag back to valid.
        if reg == R.REG_COMMAND and val == R.CMD_LATCH_PERIOD:
            self.period_valid = True
        # v1.4 channel-access-window selector write — snapshot the value.
        if reg == R.REG_CHANNEL_SELECT:
            field = (val >> 4) & 0x0F
            ch = val & 0x0F
            self._window_selected = (field, ch)
            v = self._window_values.get((field, ch), 0.0)
            self._window_data = struct.pack("<f", v)
        # v1.4.9+ / v1.4.18: window write to REG_CHANNEL_DATA (0x3C..0x3F)
        # for the CT_MODEL field. Firmware requires each byte its own txn
        # (burst unsupported); only the low byte at 0x3C carries the model.
        if reg == R.REG_CHANNEL_DATA:
            field, ch = self._window_selected
            if field == R.CHFIELD_CT_MODEL:
                cls = self._regs[R.REG_SENSOR_CLASS]
                if (cls, val & 0xFF) in self.err_param_pairs:
                    # Module rejects: set REG_ERROR, keep prior model.
                    self._regs[R.REG_ERROR] = R.DEV_ERR_PARAM
                else:
                    if ch < len(self._channel_models):
                        self._channel_models[ch] = val & 0xFF
                    # Clear any prior error on successful apply.
                    self._regs[R.REG_ERROR] = R.DEV_ERR_OK
        # Legacy per-channel CMD_SET_CT_MODEL_CH<N>: consume staged REG_CT_MODEL.
        if reg == R.REG_COMMAND and val in (
                R.CMD_SET_CT_MODEL_CH0, R.CMD_SET_CT_MODEL_CH1, R.CMD_SET_CT_MODEL_CH2):
            ch = val - R.CMD_SET_CT_MODEL_CH0
            staged = self._regs[R.REG_CT_MODEL]
            self._channel_models[ch] = staged & 0xFF
        # CMD_CLEAR_ERROR clears REG_ERROR.
        if reg == R.REG_COMMAND and val == R.CMD_CLEAR_ERROR:
            self._regs[R.REG_ERROR] = R.DEV_ERR_OK
        # v1.4 SAVE tail-window: schedule NACK deadline for STATUS reads.
        # Applies to both SAVE_GAINS (factory) and SAVE_USER_CONFIG (production).
        if reg == R.REG_COMMAND and val in (R.CMD_SAVE_GAINS, R.CMD_SAVE_USER_CONFIG) \
                and self.nack_after_save_ms > 0:
            self._save_nack_deadline = self._now + self.nack_after_save_ms
        # v1.3 two-phase address commit (truth-doc §6.1):
        # REG_ADDR_COMMIT_MAGIC=0xA5 arms; CMD_COMMIT_ADDR stages the new
        # address (consumes magic); CMD_RESET applies the new address.
        if reg == R.REG_ADDR_COMMIT_MAGIC and val == 0xA5:
            self._addr_commit_armed = True
        if reg == R.REG_COMMAND and val == R.CMD_COMMIT_ADDR and self._addr_commit_armed:
            self._pending_new_addr = self._regs[R.REG_I2C_ADDRESS]
            self._addr_commit_armed = False
            self._regs[R.REG_ADDR_COMMIT_MAGIC] = 0  # magic is single-shot
        if reg == R.REG_COMMAND and val == R.CMD_RESET \
                and getattr(self, "_pending_new_addr", None) is not None:
            self._slave_addr = self._pending_new_addr
            self._pending_new_addr = None
        # SCT rating-switch tests: CMD_RESET recovers the flash (factory-gain)
        # state — apply any staged post-reset gain per channel.
        if reg == R.REG_COMMAND and val == R.CMD_RESET:
            self.reset_count += 1
            for _ch, _g in self._reset_gain.items():
                self._set_float(R.REG_V03_I0_GAIN + _ch * 4, _g)

    def register_acks(self, addr, reg):
        """Legacy ACK-probe shim — kept for backward compat with old tests.

        Per truth-doc §1.1 + §3 firmware NEVER NACKs on register reads;
        always returns 0x00 on unmapped. Library no longer uses this for
        variant detection (rewritten to read REG_HW_VARIANT 0x55). The
        mock simply returns True for any reg ≤ 0xFF on the slave addr.
        """
        if addr != self._slave_addr:
            return False
        return True

    def broadcast(self, payload):
        self.broadcasts.append(bytes(payload))
        # v1.3 GC frame: A5 27 group tick_lo tick_hi — apply latch +
        # stash tick into REG_GC_TICK when GC enabled + group matches.
        if len(payload) == 5 and payload[0] == 0xA5 and payload[1] == R.CMD_LATCH_PERIOD:
            group = payload[2]
            tick = payload[3] | (payload[4] << 8)
            if (self._regs[R.REG_FLEET_CONFIG] & 0x01) and \
                    (group == 0 or group == self._regs[R.REG_GROUP_ID]):
                self.period_valid = True
                self._regs[R.REG_GC_TICK] = tick & 0xFF
                self._regs[R.REG_GC_TICK + 1] = (tick >> 8) & 0xFF
        return True

    def now_ms(self):
        return self._now

    @staticmethod
    def ms_diff(later, earlier):
        return later - earlier

    def sleep_ms(self, ms):
        self.sleeps.append(ms)
        self._now += ms  # advance virtual time so wait_ready/period_dt work


def pytest_addoption(parser):
    """Register the --soak / --bus / --addr options used by test_long_soak.py.

    These are global pytest options (registered at the test root via conftest)
    so individual test modules can read them via ``request.config.getoption``.
    """
    parser.addoption("--soak", action="store_true", default=False,
                     help="Enable the opt-in long-soak test")
    parser.addoption("--bus", type=lambda s: int(s, 0), default=None,
                     help="SMBus number for the real-bench soak path")
    parser.addoption("--addr", type=lambda s: int(s, 0), default=0x50,
                     help="rbAmp slave address for the real-bench soak path")


@pytest.fixture
def mock_backend():
    """A fresh mock backend (UI3, voltage hardware present, production mode)."""
    return MockBackend(slave_addr=0x50, channels=3, has_voltage=True, mode_factory=0)


@pytest.fixture
def mock_ui1():
    """A UI1 (single-channel, current-only) variant."""
    return MockBackend(slave_addr=0x50, channels=1, has_voltage=False, mode_factory=0)


@pytest.fixture
def mock_ui5():
    """A UI5 senior SKU (5 current channels + voltage)."""
    return MockBackend(slave_addr=0x50, channels=5, has_voltage=True, mode_factory=0)


@pytest.fixture
def mock_ui7():
    """A UI7 senior SKU (7 current channels + voltage)."""
    return MockBackend(slave_addr=0x50, channels=7, has_voltage=True, mode_factory=0)


@pytest.fixture
def mock_factory():
    """A device in factory mode — required for address-change tests."""
    return MockBackend(slave_addr=0x50, channels=3, has_voltage=True, mode_factory=1)
