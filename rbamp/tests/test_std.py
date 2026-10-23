"""
v1.6.0 STD-line (16-bit map) client tests.

Driven against a byte-model of the STD register file (``StdMockBackend``) with
golden values; no DUT. Covers discovery, the common readers routed to STD,
voltage angle + phase sequence, reactive/apparent, the energy snapshot + µWh
gating + delta, config / CFG_START_UA, set_time, and the BASIC-module
NotSupported guard. The pure decode/delta helpers also have direct unit
coverage here.
"""
import struct

import pytest

from rbamp import (
    RbAmp,
    RbAmpParamError,
    RbAmpNotSupportedError,
)
from rbamp import _std_registers as S
from rbamp import _std as std
from rbamp.tests.conftest import MockBackend


# ---------------------------------------------------------------------------
# Byte-model backend for the STD 16-bit register file
# ---------------------------------------------------------------------------

class StdMockBackend:
    """Sparse byte-model of an STD module's 16-bit register space.

    Exposes the backend surface RbAmp needs (``std_read`` / ``std_write`` plus
    the attributes the constructor's mock-detect branch checks). A write to
    REG_COMMAND is dispatched like the firmware: CMD_SEQ increments, CMD_STATE
    stays idle (immediate completion), ERROR reflects the verdict.
    """

    def __init__(self):
        self.mem = {}
        self.writes = []       # (reg, bytes) log of std_write payloads
        self.reads = []        # (reg, n) log of std_read requests
        self._now = 0
        self._cmd_seq = 0

    # ----- model setters (little-endian) -----
    def set_u8(self, reg, v):
        self.mem[reg] = v & 0xFF

    def _put(self, reg, data):
        for i, b in enumerate(data):
            self.mem[reg + i] = b & 0xFF

    def set_u16(self, reg, v): self._put(reg, struct.pack("<H", v & 0xFFFF))
    def set_i16(self, reg, v): self._put(reg, struct.pack("<h", v))
    def set_u32(self, reg, v): self._put(reg, struct.pack("<I", v & 0xFFFFFFFF))
    def set_i32(self, reg, v): self._put(reg, struct.pack("<i", v))
    def set_i64(self, reg, v): self._put(reg, struct.pack("<q", v))

    def set_identity(self, version, caps, status, n_voltage, n_current, variant):
        self.mem[S.REG_ID] = ord('S')
        self.mem[S.REG_ID + 1] = ord('T')
        self.set_u8(S.REG_VERSION, version)
        self.set_u16(S.REG_CAPABILITIES, caps)
        self.set_u8(S.REG_STATUS, status)
        self.set_u8(S.REG_N_VOLTAGE, n_voltage)
        self.set_u8(S.REG_N_CURRENT, n_current)
        self.set_u8(S.REG_VARIANT, variant)

    # ----- 16-bit transport -----
    def std_read(self, addr, reg, n, retry=True):
        self.reads.append((reg, n))
        return bytes(self.mem.get(reg + i, 0) for i in range(n))

    def std_write(self, addr, reg, data, retry=True):
        data = bytes(data)
        self.writes.append((reg, data))
        if reg == S.REG_COMMAND:
            self._dispatch(data[0])
            return
        self._put(reg, data)

    def _dispatch(self, opcode):
        self._cmd_seq = (self._cmd_seq + 1) & 0xFFFF
        self.set_u16(S.REG_CMD_SEQ, self._cmd_seq)
        self.set_u8(S.REG_CMD_STATE, 0)  # never busy — immediate completion
        if opcode == S.CMD_SET_TIME:
            epoch = struct.unpack("<I", bytes(self.mem.get(S.REG_TIME_SET + i, 0)
                                              for i in range(4)))[0]
            if epoch < 1577836800:  # 2020-01-01
                self.set_u8(S.REG_ERROR, S.DEV_ERR_VALUE_CRC)
                return
            self.set_u8(S.REG_STATUS, self.mem.get(S.REG_STATUS, 0) | S.ST_TIME_VALID)
        self.set_u8(S.REG_ERROR, S.DEV_ERR_OK)

    # ----- surface the constructor's mock-detect branch expects -----
    def read_byte(self, addr, reg): return self.mem.get(reg, 0)
    def write_byte(self, addr, reg, val): self.mem[reg] = val & 0xFF
    def register_acks(self, addr, reg): return True
    def reset_counters(self): pass

    def now_ms(self): return self._now
    @staticmethod
    def ms_diff(later, earlier): return later - earlier
    def sleep_ms(self, ms): self._now += ms


# ---------------------------------------------------------------------------
# Golden three-phase (3PH_3CH) fixture (map 0x36, fw 0x7B, full energy)
# ---------------------------------------------------------------------------

CAPS_FULL = (S.CAP_ADDR16 | S.CAP_MULTI_CHANNEL | S.CAP_REACTIVE | S.CAP_THD
             | S.CAP_ONDEVICE_ENERGY | S.CAP_ENERGY_UWH)


def _u3i3(caps=CAPS_FULL, fw=0x0001007B, map_version=0x36):
    m = StdMockBackend()
    m.set_identity(map_version, caps, 0x07, n_voltage=3, n_current=3,
                   variant=S.VARIANT_3PH_3CH)
    m.set_u32(S.REG_FW_VER, fw)
    m.set_u16(S.REG_FREQ, 5000)        # 50.00 Hz
    m.set_u8(S.REG_PHASE_SEQ, 1)       # forward
    # voltages
    m.set_i32(S.VOLTAGE(0) + S.VOLTAGE_U_RMS, 230000)
    m.set_i32(S.VOLTAGE(0) + S.VOLTAGE_U_PEAK, 325000)
    m.set_u16(S.VOLTAGE(0) + S.VOLTAGE_THD_U, 150)     # 1.50 %
    m.set_i16(S.VOLTAGE(0) + S.VOLTAGE_ANGLE, 0)
    m.set_i32(S.VOLTAGE(1) + S.VOLTAGE_U_RMS, 230000)
    m.set_i16(S.VOLTAGE(1) + S.VOLTAGE_ANGLE, -12000)  # -120.00°
    m.set_i32(S.VOLTAGE(2) + S.VOLTAGE_U_RMS, 230000)
    m.set_i16(S.VOLTAGE(2) + S.VOLTAGE_ANGLE, 12000)   # +120.00°
    # channel 0
    m.set_i32(S.CURRENT(0) + S.CURRENT_I_RMS, 5_000_000)   # 5 A
    m.set_i32(S.CURRENT(0) + S.CURRENT_P, 1_100_000)       # 1100 W
    m.set_i32(S.CURRENT(0) + S.CURRENT_Q, 200_000)         # 200 var
    m.set_i32(S.CURRENT(0) + S.CURRENT_S, 1_150_000)       # 1150 VA
    m.set_i16(S.CURRENT(0) + S.CURRENT_PF, 1000)           # 0.9766
    m.set_i32(S.CURRENT(0) + S.CURRENT_I_PEAK, 7_070_000)
    # totals(0) grand
    m.set_i32(S.TOTALS(0) + S.TOTALS_I_RMS, 5_000_000)
    m.set_i32(S.TOTALS(0) + S.TOTALS_P, 1_100_000)
    # energy
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_IMP, 12345)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_EXP, 10)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_NET, 12335)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_REA_IMP, 500)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_REA_EXP, 5)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_IMP_MWH, 12_345_678)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_EXP_MWH, 10_111)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_NET_MWH, 12_335_567)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_IMP_UWH, 12_345_678_901)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_EXP_UWH, 10_111_222)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_NET_UWH, 12_335_567_679)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_REA_IMP_UVARH, 500_123_456)
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_REA_EXP_UVARH, 5_123_456)
    m.set_u32(S.ENERGY_BASE + S.ENERGY_UPTIME, 3600)
    m.set_u32(S.REG_LT_GEN, 7)
    m.set_u8(S.REG_LTG_STATUS, 0)
    # per-channel energy (3 channels)
    for c in range(3):
        m.set_i64(S.GROUP_ENERGY_MWH(c) + S.GROUP_ENERGY_MWH_E_IMP, 1_000_000 * (c + 1))
        m.set_i64(S.GROUP_ENERGY_MWH(c) + S.GROUP_ENERGY_MWH_E_EXP, 10 * (c + 1))
        m.set_i64(S.GROUP_ENERGY_UWH(c) + S.GROUP_ENERGY_UWH_E_IMP, 1_000_000_123 * (c + 1))
        m.set_i64(S.GROUP_ENERGY_UWH(c) + S.GROUP_ENERGY_UWH_E_EXP, 10_123 * (c + 1))
    # config
    m.set_u8(S.REG_CFG_CLASS, 1)
    m.set_u8(S.REG_CFG_CT_MODEL, 3)
    m.set_u8(S.REG_CFG_TIER, 0)
    m.set_u8(S.REG_CFG_I2C_ADDR, 0x50)
    m.set_u16(S.REG_CFG_RATE, 200)
    m.set_u8(S.REG_CFG_HARM, 0)
    m.set_u16(S.REG_CFG_START_UA, 400)
    # health block
    m.set_u32(S.REG_FRAME_CNT, 123456)
    m.set_u32(S.REG_CRC_ERR, 0)
    m.set_u8(S.REG_LTA_STATUS, 0)
    m.set_u8(S.REG_CAL_SRC, 1)
    return m


def _dev(m):
    d = RbAmp(m, addr=0x50)
    d.begin()
    return d


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discovery_sets_std():
    d = _dev(_u3i3())
    assert d.is_std is True
    assert d.channels == 3
    assert d.has_voltage_hw is True
    assert d.std.map_version == 0x36
    assert d.std.fw_ver == 0x0001007B
    assert d.std.n_voltage == 3


def test_basic_module_not_std():
    d = RbAmp(MockBackend(channels=3), addr=0x50)
    d.begin()
    assert d.is_std is False


def test_std_magic_bad_version_raises():
    m = _u3i3()
    m.set_u8(S.REG_VERSION, 0x2F)  # below 0x30
    with pytest.raises(Exception):
        RbAmp(m, addr=0x50).begin()


# ---------------------------------------------------------------------------
# Common readers routed to STD
# ---------------------------------------------------------------------------

def test_common_readers():
    d = _dev(_u3i3())
    assert d.read_voltage(0) == pytest.approx(230.0)
    assert d.read_voltage(2) == pytest.approx(230.0)
    assert d.read_current(0) == pytest.approx(5.0)
    assert d.read_power(0) == pytest.approx(1100.0)
    assert d.read_power_factor(0) == pytest.approx(1000 / 1024.0, abs=1e-4)
    assert d.read_frequency() == pytest.approx(50.0)
    assert d.read_current_peak(0) == pytest.approx(7.07, abs=1e-3)


def test_reactive_apparent():
    d = _dev(_u3i3())
    assert d.read_reactive(0) == pytest.approx(200.0)
    assert d.read_apparent(0) == pytest.approx(1150.0)


def test_voltage_out_of_range():
    d = _dev(_u3i3())
    with pytest.raises(RbAmpParamError):
        d.read_voltage(3)
    with pytest.raises(RbAmpParamError):
        d.read_current(3)


# ---------------------------------------------------------------------------
# Angle + phase sequence
# ---------------------------------------------------------------------------

def test_voltage_angle():
    d = _dev(_u3i3())
    deg0, valid0 = d.read_voltage_angle(0)
    assert valid0 is True and deg0 == pytest.approx(0.0)
    deg1, valid1 = d.read_voltage_angle(1)
    assert valid1 is True and deg1 == pytest.approx(-120.0)


def test_angle_none_and_old_fw():
    # ANGLE sentinel 0x8000 → not valid
    m = _u3i3()
    m.set_i16(S.VOLTAGE(1) + S.VOLTAGE_ANGLE, -0x8000)
    d = _dev(m)
    deg, valid = d.read_voltage_angle(1)
    assert valid is False and deg == 0.0
    # firmware < 0x7A → angle never valid, phase_seq NotSupported
    d2 = _dev(_u3i3(fw=0x00010079))
    _deg, valid2 = d2.read_voltage_angle(1)
    assert valid2 is False
    with pytest.raises(RbAmpNotSupportedError):
        d2.read_phase_sequence()


def test_phase_sequence():
    d = _dev(_u3i3())
    assert d.read_phase_sequence() == std.PHASE_SEQ_FORWARD
    assert std.phase_seq_name(d.read_phase_sequence()) == "forward"


# ---------------------------------------------------------------------------
# Energy snapshot + µWh gating
# ---------------------------------------------------------------------------

def test_energy_fine():
    d = _dev(_u3i3())
    e = d.read_std_energy()
    assert e.act_import_wh == 12345
    assert e.act_export_wh == 10
    assert e.act_net_wh == 12335
    assert e.act_import_mwh == 12_345_678
    assert e.generation == 7
    assert e.uptime_s == 3600
    assert e.channels == 3
    # fine µWh from the module (not scaled)
    assert e.fine is True
    assert e.fine_reactive is True
    assert e.act_import_uwh == 12_345_678_901
    assert e.rea_import_uvarh == 500_123_456
    assert e.ch_import_mwh[1] == 2_000_000
    assert e.ch_import_uwh[2] == 1_000_000_123 * 3


def test_energy_coarse_when_no_uwh_cap():
    # CAP_ENERGY_UWH clear → fine False, µWh scaled from mWh (×1000)
    caps = CAPS_FULL & ~S.CAP_ENERGY_UWH
    d = _dev(_u3i3(caps=caps))
    e = d.read_std_energy()
    assert e.fine is False
    assert e.fine_reactive is False
    assert e.act_import_uwh == 12_345_678 * 1000
    assert e.rea_import_uvarh == 500 * 1_000_000
    assert e.ch_import_uwh[0] == 1_000_000 * 1000


def test_energy_reactive_coarse_below_7b():
    # fw 0x7A: active µWh fine, reactive µvarh still coarse (varh × 1e6)
    d = _dev(_u3i3(fw=0x0001007A))
    e = d.read_std_energy()
    assert e.fine is True
    assert e.fine_reactive is False
    assert e.rea_import_uvarh == 500 * 1_000_000


def test_energy_not_supported_without_cap():
    caps = CAPS_FULL & ~S.CAP_ONDEVICE_ENERGY
    d = _dev(_u3i3(caps=caps))
    with pytest.raises(RbAmpNotSupportedError):
        d.read_std_energy()


def test_energy_delta_via_dev():
    d = _dev(_u3i3())
    e0 = d.read_std_energy()
    m = d.std._io
    m.set_i64(S.ENERGY_BASE + S.ENERGY_E_ACT_IMP, 12_445)   # +100 Wh
    m.set_u32(S.ENERGY_BASE + S.ENERGY_UPTIME, 3700)
    e1 = d.read_std_energy()
    delta, restarted = d.std_energy_delta(e0, e1)
    assert restarted is False
    assert delta.act_import_wh == 100


# ---------------------------------------------------------------------------
# Config + CFG_START_UA
# ---------------------------------------------------------------------------

def test_read_config():
    d = _dev(_u3i3())
    cfg = d.read_std_config()
    assert cfg.sensor_class == 1
    assert cfg.ct_model == 3
    assert cfg.i2c_addr == 0x50
    assert cfg.rate_ms == 200
    assert cfg.start_ua_supported is True
    assert cfg.start_ua == 400


def test_start_current_get_set():
    d = _dev(_u3i3())
    assert d.get_start_current() == 400
    d.set_start_current(250)
    assert d.get_start_current() == 250
    d.set_start_current(0)  # off
    assert d.get_start_current() == 0


def test_start_current_invalid_no_write():
    d = _dev(_u3i3())
    m = d.std._io
    before = len(m.writes)
    with pytest.raises(RbAmpParamError):
        d.set_start_current(5)       # not a 10 µA step
    with pytest.raises(RbAmpParamError):
        d.set_start_current(2550)    # over max
    assert len(m.writes) == before   # no bus write on a bad value


def test_start_current_unsupported_below_7b():
    d = _dev(_u3i3(fw=0x0001007A))
    with pytest.raises(RbAmpNotSupportedError):
        d.get_start_current()
    with pytest.raises(RbAmpNotSupportedError):
        d.set_start_current(400)
    cfg = d.read_std_config()
    assert cfg.start_ua_supported is False


# ---------------------------------------------------------------------------
# set_time
# ---------------------------------------------------------------------------

def test_set_time_ok():
    d = _dev(_u3i3())
    d.set_time(1_700_000_000)  # 2023
    assert d.std._io.mem.get(S.REG_STATUS, 0) & S.ST_TIME_VALID


def test_set_time_rejects_pre_2020():
    d = _dev(_u3i3())
    with pytest.raises(RbAmpParamError):
        d.set_time(100)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_read_health():
    d = _dev(_u3i3())
    h = d.read_std_health()
    assert h.cal_ok is True
    assert h.sensor_ok is True
    assert h.mains_ok is True
    assert h.frame_count == 123456
    assert h.crc_errors == 0
    assert h.uptime_s == 3600


# ---------------------------------------------------------------------------
# BASIC-module NotSupported guard
# ---------------------------------------------------------------------------

def test_std_only_methods_raise_on_basic():
    d = RbAmp(MockBackend(channels=3), addr=0x50)
    d.begin()
    for call in (lambda: d.read_std_energy(),
                 lambda: d.read_reactive(0),
                 lambda: d.read_apparent(0),
                 lambda: d.read_phase_sequence(),
                 lambda: d.read_std_config(),
                 lambda: d.get_start_current(),
                 lambda: d.set_time(1_700_000_000)):
        with pytest.raises(RbAmpNotSupportedError):
            call()


# ---------------------------------------------------------------------------
# Coherent-read discipline: each block read in one transaction
# ---------------------------------------------------------------------------

def test_energy_block_reads_are_coherent():
    d = _dev(_u3i3())
    m = d.std._io
    m.reads.clear()
    d.read_std_energy()
    # the base Wh+mWh+gen block, the fine block, and each per-channel block
    # are single reads — assert the 40-byte fine block came in one request.
    fine_off = S.ENERGY_BASE + S.ENERGY_E_ACT_IMP_UWH
    assert (fine_off, 40) in m.reads
