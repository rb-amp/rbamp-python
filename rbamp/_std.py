"""
rbamp STD line — 16-bit register-map client (shared MicroPython + CPython).

The STD firmware line (map VERSION >= 0x30, CAPABILITIES bit9 ADDR16) uses a
16-bit big-endian register pointer with auto-increment and little-endian field
encoding, unlike the BASIC line's 8-bit single-byte map. This module adds that
transport plus the realtime / energy / config / status surface, mirroring the
esp-idf component (``components/rbamp/src/rbamp.c`` + ``rbamp_std_decode.c``) so
the two libraries agree field for field.

It is driven by :class:`rbamp.RbAmp`: ``begin()`` probes for the ``'ST'`` magic
and, on a match, builds a :class:`_StdClient`. STD-only methods on ``RbAmp``
raise :class:`RbAmpNotSupportedError` on a BASIC module.

Wire discipline mirrors SPEC §B.5: data reads/writes retry via the backend;
COMMAND writes are issued once (a retried opcode could land twice). Coherent
blocks (voltage, each channel, the energy snapshot, the per-channel energy
block) are each read in ONE transaction so fields never mix two measurement
windows (publish cadence 100 ms from image 0x7C; ~160 ms @50 Hz / ~133 ms
@60 Hz on images <= 0x7B, published every 200 ms).

The pure helpers (``energy_decode_*`` / ``energy_fill_coarse`` /
``energy_delta`` / ``start_ua_valid`` / ``angle_decode`` / ``phase_seq_decode``)
take no backend and are host-tested against a byte-model of the register file.
"""

import struct

from . import _std_registers as S
from ._snapshot import (
    RbAmpError,
    RbAmpIOError,
    RbAmpParamError,
    RbAmpTimeoutError,
    RbAmpNotSupportedError,
)

# --- transport / gating constants (mirror rbamp.c + rbamp_std_decode.h) ---
BURST_MAX = 128                     # coherent block ceiling, one transaction
FW_ANGLE_MIN     = 0x0001007A       # VOLTAGE.ANGLE, PHASE_SEQ, SAVE_CAL cal_ok marker
FW_UVARH_MIN     = 0x0001007B       # ENERGY +0xE8/+0xF0 reactive µvarh
FW_START_UA_MIN  = 0x0001007B       # CFG_START_UA
FW_RESET_CAUSE_MIN = 0x00010076     # RESET_CAUSE / BOOT_COUNT
ANGLE_NONE = -0x8000                # i16 0x8000 — "no angle"
START_UA_STEP = 10
START_UA_MAX  = 2540
STD_MAX_CHANNELS = 8                # per-channel energy arrays (group max)
SETTLE_SET_TIME_MS = S.SETTLE_MS_SET_TIME

# --- phase sequence (PHASE_SEQ 0x0012) ---
PHASE_SEQ_NA       = 0
PHASE_SEQ_FORWARD  = 1
PHASE_SEQ_REVERSE  = 2
PHASE_SEQ_ABNORMAL = 3
_PHASE_SEQ_NAMES = {0: "n/a", 1: "forward", 2: "reverse", 3: "abnormal"}


def phase_seq_name(value):
    """Human-readable name for a phase-sequence code (0..3)."""
    return _PHASE_SEQ_NAMES.get(value, "unknown")


# ---------------------------------------------------------------------------
# Little-endian field decoders
# ---------------------------------------------------------------------------

def _i16(b, off=0):
    return struct.unpack_from("<h", b, off)[0]


def _u16(b, off=0):
    return struct.unpack_from("<H", b, off)[0]


def _i32(b, off=0):
    return struct.unpack_from("<i", b, off)[0]


def _u32(b, off=0):
    return struct.unpack_from("<I", b, off)[0]


def _i64(b, off=0):
    return struct.unpack_from("<q", b, off)[0]


# ---------------------------------------------------------------------------
# Pure helpers (no backend — host-tested)
# ---------------------------------------------------------------------------

def start_ua_valid(ua):
    """True if ``ua`` is a legal CFG_START_UA value: 0 (off) or 10..2540 in
    10 µA steps."""
    if ua == 0:
        return True
    return START_UA_STEP <= ua <= START_UA_MAX and (ua % START_UA_STEP) == 0


def angle_decode(raw, fw_has_angle):
    """(degrees, valid) from the raw i16 ANGLE field (centi-degrees).

    ``0x8000`` and firmware older than 0x7A mean "no angle" → ``(0.0, False)``.
    """
    if not fw_has_angle or raw == ANGLE_NONE:
        return 0.0, False
    return raw * 0.01, True


def phase_seq_decode(raw):
    """Map the raw PHASE_SEQ byte onto a phase-sequence code (0..3)."""
    if raw in (1, 2, 3):
        return raw
    return PHASE_SEQ_NA


# ---------------------------------------------------------------------------
# Snapshot value objects (plain classes — MicroPython has no @dataclass)
# ---------------------------------------------------------------------------

class RbAmpStdVoltage:
    """One voltage phase: RMS/peak V, THD %, angle vs phase 0 (degrees)."""

    __slots__ = ("u_rms", "u_peak", "thd_u", "angle_deg", "angle_valid")

    def __init__(self):
        self.u_rms = 0.0
        self.u_peak = 0.0
        self.thd_u = 0.0
        self.angle_deg = 0.0
        self.angle_valid = False

    def __repr__(self):
        return ("RbAmpStdVoltage(u_rms={:.3f}, u_peak={:.3f}, thd_u={:.2f}, "
                "angle_deg={:.2f}, angle_valid={})".format(
                    self.u_rms, self.u_peak, self.thd_u,
                    self.angle_deg, self.angle_valid))


class RbAmpStdChannel:
    """One current channel (or a TOTALS block): I/P/Q/S/PF + harmonics."""

    __slots__ = ("i_rms", "i_peak", "p", "q", "s", "pf", "thd_i", "pf1", "q1")

    def __init__(self):
        self.i_rms = 0.0
        self.i_peak = 0.0
        self.p = 0.0
        self.q = 0.0
        self.s = 0.0
        self.pf = 0.0
        self.thd_i = 0.0
        self.pf1 = 0.0
        self.q1 = 0.0

    def __repr__(self):
        return ("RbAmpStdChannel(i_rms={:.4f}, p={:.3f}, q={:.3f}, s={:.3f}, "
                "pf={:.3f})".format(self.i_rms, self.p, self.q, self.s, self.pf))


class RbAmpStdConfig:
    """STD configuration block (CFG_* registers)."""

    __slots__ = ("sensor_class", "ct_model", "tier", "i2c_addr", "rate_ms",
                 "harmonics", "start_ua", "start_ua_supported")

    def __init__(self):
        self.sensor_class = 0
        self.ct_model = 0
        self.tier = 0
        self.i2c_addr = 0
        self.rate_ms = 0
        self.harmonics = False
        self.start_ua = 0
        self.start_ua_supported = False

    def __repr__(self):
        return ("RbAmpStdConfig(sensor_class={}, ct_model={}, tier={}, "
                "i2c_addr=0x{:02X}, rate_ms={}, harmonics={}, start_ua={}, "
                "start_ua_supported={})".format(
                    self.sensor_class, self.ct_model, self.tier, self.i2c_addr,
                    self.rate_ms, self.harmonics, self.start_ua,
                    self.start_ua_supported))


class RbAmpStdHealth:
    """STD status / health block."""

    __slots__ = ("status", "cal_ok", "sensor_ok", "mains_ok", "time_valid",
                 "nvm_fault", "maint", "frame_count", "crc_errors",
                 "lta_status", "lta_src", "cal_src", "group_status",
                 "ring_drop", "lost_frames", "uptime_s", "reset_cause",
                 "boot_count")

    def __init__(self):
        self.status = 0
        self.cal_ok = False
        self.sensor_ok = False
        self.mains_ok = False
        self.time_valid = False
        self.nvm_fault = False
        self.maint = False
        self.frame_count = 0
        self.crc_errors = 0
        self.lta_status = 0
        self.lta_src = 0
        self.cal_src = 0
        self.group_status = 0
        self.ring_drop = 0
        self.lost_frames = 0
        self.uptime_s = 0
        self.reset_cause = 0
        self.boot_count = 0

    def __repr__(self):
        return ("RbAmpStdHealth(status=0x{:02X}, cal_ok={}, time_valid={}, "
                "nvm_fault={}, frame_count={}, crc_errors={}, uptime_s={})".format(
                    self.status, self.cal_ok, self.time_valid, self.nvm_fault,
                    self.frame_count, self.crc_errors, self.uptime_s))


class RbAmpStdEnergy:
    """On-device life-time energy snapshot (counter path).

    Active energy is carried at Wh, mWh (map 0x36+) and µWh resolution; reactive
    at varh and µvarh. The µ fields are always populated: when the module does
    not expose the fine counters (CAPABILITIES bit15 ENERGY_UWH clear, or
    firmware < 0x7A/0x7B) they are scaled up from the coarse ones and ``fine``
    (active) / ``fine_reactive`` are ``False`` — so a caller can always read the
    µ fields and consult the flag for provenance. Per-channel arrays carry
    ``channels`` valid entries.
    """

    __slots__ = (
        "act_import_wh", "act_export_wh", "act_net_wh",
        "rea_import_varh", "rea_export_varh",
        "generation", "uptime_s",
        "act_import_mwh", "act_export_mwh", "act_net_mwh",
        "channels", "group_status",
        "ch_import_mwh", "ch_export_mwh",
        "fine", "fine_reactive",
        "act_import_uwh", "act_export_uwh", "act_net_uwh",
        "rea_import_uvarh", "rea_export_uvarh",
        "ch_import_uwh", "ch_export_uwh",
    )

    def __init__(self):
        self.act_import_wh = 0
        self.act_export_wh = 0
        self.act_net_wh = 0
        self.rea_import_varh = 0
        self.rea_export_varh = 0
        self.generation = 0
        self.uptime_s = 0
        self.act_import_mwh = 0
        self.act_export_mwh = 0
        self.act_net_mwh = 0
        self.channels = 0
        self.group_status = 0
        self.ch_import_mwh = [0] * STD_MAX_CHANNELS
        self.ch_export_mwh = [0] * STD_MAX_CHANNELS
        self.fine = False
        self.fine_reactive = False
        self.act_import_uwh = 0
        self.act_export_uwh = 0
        self.act_net_uwh = 0
        self.rea_import_uvarh = 0
        self.rea_export_uvarh = 0
        self.ch_import_uwh = [0] * STD_MAX_CHANNELS
        self.ch_export_uwh = [0] * STD_MAX_CHANNELS

    def __repr__(self):
        return ("RbAmpStdEnergy(act_import_wh={}, act_export_wh={}, "
                "act_net_wh={}, gen={}, uptime_s={}, channels={}, fine={}, "
                "fine_reactive={})".format(
                    self.act_import_wh, self.act_export_wh, self.act_net_wh,
                    self.generation, self.uptime_s, self.channels, self.fine,
                    self.fine_reactive))


# ---------------------------------------------------------------------------
# Energy decode / fill / delta (pure — mirror rbamp_std_decode.c)
# ---------------------------------------------------------------------------

def energy_decode_fine(e, b, reactive_fine):
    """Decode the 40-byte fine block (ENERGY +0xD0..+0xF7) into ``e``.

    ``b`` starts at ENERGY_E_ACT_IMP_UWH (0xD0). Sets active µWh always; the
    reactive µvarh only when ``reactive_fine`` (firmware 0x7B+)."""
    base = S.ENERGY_E_ACT_IMP_UWH
    e.act_import_uwh = _i64(b, S.ENERGY_E_ACT_IMP_UWH - base)
    e.act_export_uwh = _i64(b, S.ENERGY_E_ACT_EXP_UWH - base)
    e.act_net_uwh    = _i64(b, S.ENERGY_E_ACT_NET_UWH - base)
    e.fine = True
    if reactive_fine:
        e.rea_import_uvarh = _i64(b, S.ENERGY_E_REA_IMP_UVARH - base)
        e.rea_export_uvarh = _i64(b, S.ENERGY_E_REA_EXP_UVARH - base)
        e.fine_reactive = True


def energy_decode_group_fine(e, g, n):
    """Decode ``n`` per-channel µWh entries (GROUP_ENERGY_UWH block) into ``e``."""
    for c in range(min(n, STD_MAX_CHANNELS)):
        p = c * S.GROUP_ENERGY_UWH_STRIDE
        e.ch_import_uwh[c] = _i64(g, p + S.GROUP_ENERGY_UWH_E_IMP)
        e.ch_export_uwh[c] = _i64(g, p + S.GROUP_ENERGY_UWH_E_EXP)


def energy_fill_coarse(e, active_fine, reactive_fine, have_mwh):
    """Scale the µ fields up from the coarse counters where the module did not
    supply fine values, and clear the corresponding ``fine`` flag."""
    if not active_fine:
        e.fine = False
        if have_mwh:
            e.act_import_uwh = e.act_import_mwh * 1000
            e.act_export_uwh = e.act_export_mwh * 1000
            e.act_net_uwh    = e.act_net_mwh * 1000
            for c in range(min(e.channels, STD_MAX_CHANNELS)):
                e.ch_import_uwh[c] = e.ch_import_mwh[c] * 1000
                e.ch_export_uwh[c] = e.ch_export_mwh[c] * 1000
        else:
            e.act_import_uwh = e.act_import_wh * 1000000
            e.act_export_uwh = e.act_export_wh * 1000000
            e.act_net_uwh    = e.act_net_wh * 1000000
    if not reactive_fine:
        e.fine_reactive = False
        e.rea_import_uvarh = e.rea_import_varh * 1000000
        e.rea_export_uvarh = e.rea_export_varh * 1000000


def energy_delta(prev, now):
    """Energy consumed between two snapshots.

    Returns ``(delta, module_restarted)``:

    * ``module_restarted`` is ``True`` when ``now.uptime_s < prev.uptime_s``.
    * ``delta`` is a :class:`RbAmpStdEnergy` of the per-field increase, or
      ``None`` if the generation counter changed (an adopt / life-time reset —
      the two readings are not comparable).

    A counter that comes back a little below the previous reading (power-cut
    resume) clamps to 0; NET is always IMP − EXP of the clamped parts, never a
    floored NET. Per-channel entries are filled only when both snapshots carry
    the same channel count.
    """
    module_restarted = now.uptime_s < prev.uptime_s
    if now.generation != prev.generation:
        return None, module_restarted

    def d0(attr):
        a = getattr(now, attr)
        b = getattr(prev, attr)
        return a - b if a > b else 0

    delta = RbAmpStdEnergy()
    delta.act_import_wh = d0("act_import_wh")
    delta.act_export_wh = d0("act_export_wh")
    delta.act_net_wh = delta.act_import_wh - delta.act_export_wh
    delta.rea_import_varh = d0("rea_import_varh")
    delta.rea_export_varh = d0("rea_export_varh")
    delta.act_import_mwh = d0("act_import_mwh")
    delta.act_export_mwh = d0("act_export_mwh")
    delta.act_net_mwh = delta.act_import_mwh - delta.act_export_mwh
    delta.act_import_uwh = d0("act_import_uwh")
    delta.act_export_uwh = d0("act_export_uwh")
    delta.act_net_uwh = delta.act_import_uwh - delta.act_export_uwh
    delta.rea_import_uvarh = d0("rea_import_uvarh")
    delta.rea_export_uvarh = d0("rea_export_uvarh")
    delta.fine = prev.fine and now.fine
    delta.fine_reactive = prev.fine_reactive and now.fine_reactive
    delta.generation = now.generation
    delta.uptime_s = now.uptime_s
    delta.group_status = now.group_status
    if now.channels and now.channels == prev.channels:
        delta.channels = now.channels
        for c in range(min(now.channels, STD_MAX_CHANNELS)):
            delta.ch_import_mwh[c] = (now.ch_import_mwh[c] - prev.ch_import_mwh[c]
                                      if now.ch_import_mwh[c] > prev.ch_import_mwh[c] else 0)
            delta.ch_export_mwh[c] = (now.ch_export_mwh[c] - prev.ch_export_mwh[c]
                                      if now.ch_export_mwh[c] > prev.ch_export_mwh[c] else 0)
            delta.ch_import_uwh[c] = (now.ch_import_uwh[c] - prev.ch_import_uwh[c]
                                      if now.ch_import_uwh[c] > prev.ch_import_uwh[c] else 0)
            delta.ch_export_uwh[c] = (now.ch_export_uwh[c] - prev.ch_export_uwh[c]
                                      if now.ch_export_uwh[c] > prev.ch_export_uwh[c] else 0)
    return delta, module_restarted


# ---------------------------------------------------------------------------
# STD transport + read/command client
# ---------------------------------------------------------------------------

class _StdClient:
    """Internal 16-bit transport + decode for one STD module.

    Owned by :class:`rbamp.RbAmp`; not a public user class. Built by
    :meth:`detect` after the ``'ST'`` identity probe succeeds.
    """

    def __init__(self, io, addr, map_version, caps, status,
                 n_voltage, n_current, variant):
        self._io = io
        self._addr = addr
        self.map_version = map_version
        self.caps = caps
        self.status = status
        self.n_voltage = n_voltage
        self.n_current = n_current
        self.variant = variant
        self.channels = n_current if n_current <= STD_MAX_CHANNELS else STD_MAX_CHANNELS
        self.fw_ver = 0
        self._cmd_seq = 0

    # ---- discovery ------------------------------------------------------

    @classmethod
    def detect(cls, io, addr):
        """Probe for an STD module. Returns an :class:`_StdClient` or ``None``
        (BASIC line). Raises :class:`RbAmpIOError` on a transport failure or an
        STD magic with an unsupported map version / caps.

        The backend must expose a 16-bit ``std_read``; a backend without it
        (pure 8-bit BASIC) returns ``None`` without any bus traffic.
        """
        if not hasattr(io, "std_read"):
            return None
        try:
            idn = io.std_read(addr, S.REG_ID, 10)
        except (AttributeError, RbAmpIOError):
            # The bus cannot do a 16-bit combined transaction (e.g. an smbus
            # object without i2c_rdwr) → this cannot be an STD module we can
            # talk to; fall back to the BASIC 8-bit probe.
            return None
        if len(idn) < 10 or idn[0] != ord('S') or idn[1] != ord('T'):
            return None  # BASIC line
        ver = idn[S.REG_VERSION]
        caps = _u16(idn, S.REG_CAPABILITIES)
        if ver < 0x30 or not (caps & S.CAP_ADDR16):
            raise RbAmpIOError(
                "STD magic but map version 0x{:02X} / caps 0x{:04X} unsupported".format(ver, caps))
        self = cls(io, addr, ver, caps, idn[S.REG_STATUS],
                   idn[S.REG_N_VOLTAGE], idn[S.REG_N_CURRENT], idn[S.REG_VARIANT])
        self._read_discovery()
        return self

    def _read_discovery(self):
        self.fw_ver = 0
        if self.map_version >= 0x35:
            try:
                fv = self._read_u32(S.REG_FW_VER)
                if fv != 0xFFFFFFFF:
                    self.fw_ver = fv
            except RbAmpError:
                pass

    # ---- transport ------------------------------------------------------

    def _read(self, reg, n, retry=True):
        """Burst-read ``n`` bytes from 16-bit ``reg`` (chunked at BURST_MAX;
        each chunk is one coherent transaction)."""
        out = bytearray()
        while n:
            k = n if n <= BURST_MAX else BURST_MAX
            out += self._io.std_read(self._addr, reg, k, retry)
            reg = (reg + k) & 0xFFFF
            n -= k
        return bytes(out)

    def _write(self, reg, data, retry=True):
        self._io.std_write(self._addr, reg, bytes(data), retry)

    def _read_u8(self, reg, retry=True):
        return self._read(reg, 1, retry)[0]

    def _read_u16(self, reg, retry=True):
        return _u16(self._read(reg, 2, retry))

    def _read_u32(self, reg, retry=True):
        return _u32(self._read(reg, 4, retry))

    # ---- commands (CMD_STATE / CMD_SEQ completion contract) -------------

    def _cmd(self, opcode):
        """Write one opcode to REG_COMMAND — single attempt, no retry."""
        self._write(S.REG_COMMAND, bytes([opcode & 0xFF]), retry=False)

    def _mark_seq(self):
        if self.map_version >= 0x35:
            try:
                self._cmd_seq = self._read_u16(S.REG_CMD_SEQ)
            except RbAmpError:
                pass

    def _wait_idle(self, timeout_ms):
        if self.map_version < 0x35:
            return
        deadline = self._io.now_ms() + timeout_ms
        while True:
            try:
                st = self._read_u8(S.REG_CMD_STATE)
                if not (st & 0x01):
                    return
            except RbAmpError:
                pass
            if self._io.ms_diff(deadline, self._io.now_ms()) <= 0:
                raise RbAmpTimeoutError(
                    "STD module still busy after {} ms".format(timeout_ms))
            self._io.sleep_ms(10)

    def _wait_done(self, settle_ms, timeout_ms):
        """Completion = CMD_STATE BUSY back to 0 AND CMD_SEQ advanced. OVERRUN
        (bit1) means the opcode was dropped."""
        if self.map_version < 0x35:
            self._io.sleep_ms(settle_ms)
            return
        seq0 = self._cmd_seq
        deadline = self._io.now_ms() + timeout_ms
        idle_seen_ms = 0
        self._io.sleep_ms(10)
        while True:
            st = None
            try:
                st = self._read_u8(S.REG_CMD_STATE)
            except RbAmpError:
                st = None
            if st is not None and not (st & 0x01):
                seq = seq0
                try:
                    seq = self._read_u16(S.REG_CMD_SEQ)
                except RbAmpError:
                    seq = seq0
                if seq != seq0:
                    self._cmd_seq = seq
                    return
                if idle_seen_ms == 0:
                    idle_seen_ms = self._io.now_ms()
                if (st & 0x02) or self._io.ms_diff(self._io.now_ms(), idle_seen_ms) > 200:
                    if st & 0x02:
                        raise RbAmpError(
                            "STD command dropped by the module (OVERRUN — written while busy)")
                    return  # trivial command, counter not stepped
            if self._io.ms_diff(deadline, self._io.now_ms()) <= 0:
                raise RbAmpTimeoutError(
                    "STD command not completed within {} ms".format(timeout_ms))
            self._io.sleep_ms(10)

    def _check_cmd_error(self):
        """Read REG_ERROR after a command's completion and raise for a non-OK
        verdict. Returns True if still PENDING (caller re-reads)."""
        if self.map_version < 0x34:
            return False
        e = self._read_u8(S.REG_ERROR)
        if e == S.DEV_ERR_OK:
            return False
        if e == S.DEV_ERR_PENDING:
            return True
        if e == S.DEV_ERR_PARAM:
            raise RbAmpParamError("STD module rejected command/config: ERR_PARAM (0x{:02X})".format(e))
        if e == S.DEV_ERR_UNKNOWN_CMD:
            raise RbAmpNotSupportedError("STD module does not know this command (0x{:02X})".format(e))
        if e == S.DEV_ERR_VALUE_CRC:
            raise RbAmpParamError("STD module rejected the payload: value/CRC (0x{:02X})".format(e))
        raise RbAmpError("STD module reports ERROR=0x{:02X}".format(e))

    def _cmd_sync(self, opcode, settle_ms, timeout_ms=1000):
        if timeout_ms < 1000:
            timeout_ms = 1000
        self._wait_idle(timeout_ms)
        self._mark_seq()
        self._cmd(opcode)
        self._wait_done(settle_ms, timeout_ms)
        for _ in range(10):
            if not self._check_cmd_error():
                return
            self._io.sleep_ms(10)

    # ---- realtime -------------------------------------------------------

    def read_frequency(self):
        """Mains frequency in Hz (FREQ 0x0010, cHz)."""
        return self._read_u16(S.REG_FREQ) / 100.0

    def read_voltage(self, v=0):
        """:class:`RbAmpStdVoltage` for phase ``v`` (one coherent burst)."""
        if v < 0 or v >= self.n_voltage:
            raise RbAmpParamError("voltage phase must be 0..{}".format(self.n_voltage - 1))
        b = self._read(S.VOLTAGE(v), S.VOLTAGE_ANGLE + 2)
        out = RbAmpStdVoltage()
        out.u_rms = _i32(b, S.VOLTAGE_U_RMS) * 1e-3
        out.u_peak = _i32(b, S.VOLTAGE_U_PEAK) * 1e-3
        out.thd_u = _u16(b, S.VOLTAGE_THD_U) / 100.0
        out.angle_deg, out.angle_valid = angle_decode(
            _i16(b, S.VOLTAGE_ANGLE), self.fw_ver >= FW_ANGLE_MIN)
        return out

    def _decode_ip_block(self, b):
        out = RbAmpStdChannel()
        out.i_rms = _i32(b, S.CURRENT_I_RMS) * 1e-6
        out.p = _i32(b, S.CURRENT_P) * 1e-3
        out.q = _i32(b, S.CURRENT_Q) * 1e-3
        out.s = _i32(b, S.CURRENT_S) * 1e-3
        out.pf = _i16(b, S.CURRENT_PF) / 1024.0
        out.i_peak = _i32(b, S.CURRENT_I_PEAK) * 1e-6
        out.thd_i = _u16(b, S.CURRENT_THD_I) / 100.0
        out.pf1 = _i16(b, S.CURRENT_PF1) / 1024.0
        out.q1 = _i32(b, S.CURRENT_Q1) * 1e-3
        return out

    _IP_BLOCK_LEN = S.CURRENT_Q1 + 4

    def read_channel(self, ch):
        """:class:`RbAmpStdChannel` for current channel ``ch`` (one burst)."""
        if ch < 0 or ch >= self.channels:
            raise RbAmpParamError("channel must be 0..{}".format(self.channels - 1))
        return self._decode_ip_block(self._read(S.CURRENT(ch), self._IP_BLOCK_LEN))

    def read_totals(self, k=0):
        """:class:`RbAmpStdChannel` for TOTALS block ``k`` (0 = grand total).
        Per-phase totals (k>=1) require map 0x36+."""
        if k != 0 and self.map_version < 0x36:
            raise RbAmpNotSupportedError("per-phase totals require STD map 0x36+")
        if k < 0 or k >= S.TOTALS_MAX:
            raise RbAmpParamError("totals index must be 0..{}".format(S.TOTALS_MAX - 1))
        return self._decode_ip_block(self._read(S.TOTALS(k), self._IP_BLOCK_LEN))

    def read_phase_seq(self):
        """Phase-sequence code (0 n/a · 1 forward · 2 reverse · 3 abnormal).
        Firmware < 0x7A has no angle data → NOT_SUPPORTED (no bus traffic)."""
        if self.fw_ver < FW_ANGLE_MIN:
            raise RbAmpNotSupportedError("phase sequence requires firmware 0x7A+")
        return phase_seq_decode(self._read_u8(S.REG_PHASE_SEQ))

    # ---- energy ---------------------------------------------------------

    def read_energy(self):
        """Read the on-device life-time energy snapshot (:class:`RbAmpStdEnergy`).

        Requires CAPABILITIES bit13 ON_DEVICE_ENERGY. The µWh/µvarh fields come
        from the module when bit15 ENERGY_UWH is set and the firmware is new
        enough (``fine`` / ``fine_reactive``), otherwise they are scaled from
        the coarse counters.
        """
        if not (self.caps & S.CAP_ONDEVICE_ENERGY):
            raise RbAmpNotSupportedError("module has no on-device energy counters")
        e = RbAmpStdEnergy()
        base_len = S.REG_LT_GEN + 4 - S.ENERGY_BASE  # Wh + mWh totals + LT_GEN
        b = self._read(S.ENERGY_BASE, base_len)
        up = self._read_u32(S.ENERGY_BASE + S.ENERGY_UPTIME)
        if up == 0xFFFFFFFF:
            raise RbAmpIOError("STD energy block unprogrammed / unmapped (uptime=0xFFFFFFFF)")
        e.act_import_wh = _i64(b, S.ENERGY_E_ACT_IMP)
        e.act_export_wh = _i64(b, S.ENERGY_E_ACT_EXP)
        e.act_net_wh = _i64(b, S.ENERGY_E_ACT_NET)
        e.rea_import_varh = _i64(b, S.ENERGY_E_REA_IMP)
        e.rea_export_varh = _i64(b, S.ENERGY_E_REA_EXP)
        e.generation = _u32(b, S.REG_LT_GEN - S.ENERGY_BASE)
        e.uptime_s = up
        have_mwh = self.map_version >= 0x36
        fine = have_mwh and bool(self.caps & S.CAP_ENERGY_UWH)
        fine_rea = fine and self.fw_ver >= FW_UVARH_MIN
        n = 0
        if have_mwh:
            e.act_import_mwh = _i64(b, S.ENERGY_E_ACT_IMP_MWH)
            e.act_export_mwh = _i64(b, S.ENERGY_E_ACT_EXP_MWH)
            e.act_net_mwh = _i64(b, S.ENERGY_E_ACT_NET_MWH)
            n = self.channels if self.channels <= S.GROUP_ENERGY_MWH_MAX else S.GROUP_ENERGY_MWH_MAX
            if n:
                g = self._read(S.GROUP_ENERGY_MWH_BASE, n * S.GROUP_ENERGY_MWH_STRIDE)
                for c in range(n):
                    p = c * S.GROUP_ENERGY_MWH_STRIDE
                    e.ch_import_mwh[c] = _i64(g, p + S.GROUP_ENERGY_MWH_E_IMP)
                    e.ch_export_mwh[c] = _i64(g, p + S.GROUP_ENERGY_MWH_E_EXP)
                e.channels = n
            e.group_status = self._read_u8(S.REG_LTG_STATUS)
        if fine:
            fine_len = S.ENERGY_E_REA_EXP_UVARH + 8 - S.ENERGY_E_ACT_IMP_UWH  # 40
            f = self._read(S.ENERGY_BASE + S.ENERGY_E_ACT_IMP_UWH, fine_len)
            energy_decode_fine(e, f, fine_rea)
            if n:
                gu = self._read(S.GROUP_ENERGY_UWH_BASE, n * S.GROUP_ENERGY_UWH_STRIDE)
                energy_decode_group_fine(e, gu, n)
        energy_fill_coarse(e, fine, fine_rea, have_mwh)
        return e

    # ---- config ---------------------------------------------------------

    def _has_start_ua(self):
        return bool(self.caps & S.CAP_ENERGY_UWH) and self.fw_ver >= FW_START_UA_MIN

    def read_config(self):
        """Read the STD configuration block (:class:`RbAmpStdConfig`)."""
        b = self._read(S.REG_CFG_CLASS, S.REG_CFG_HARM - S.REG_CFG_CLASS + 1)
        out = RbAmpStdConfig()
        out.sensor_class = b[0]
        out.ct_model = b[1]
        out.tier = b[2]
        out.i2c_addr = b[3]
        out.rate_ms = _u16(b, 4)
        out.harmonics = (b[6] & 0x01) != 0
        out.start_ua_supported = self._has_start_ua()
        if out.start_ua_supported:
            out.start_ua = self._read_u16(S.REG_CFG_START_UA)
        return out

    def get_start_current(self):
        """CFG_START_UA in µA (0 = off). NOT_SUPPORTED below firmware 0x7B."""
        if not self._has_start_ua():
            raise RbAmpNotSupportedError("CFG_START_UA requires firmware 0x7B+ with ENERGY_UWH")
        return self._read_u16(S.REG_CFG_START_UA)

    def set_start_current(self, ua):
        """Set CFG_START_UA (0 = off; 10..2540 µA in 10 µA steps). Applies at
        once; persist with :meth:`save_config`. Out-of-range → ParamError with
        no bus write. Verifies by read-back."""
        if not self._has_start_ua():
            raise RbAmpNotSupportedError("CFG_START_UA requires firmware 0x7B+ with ENERGY_UWH")
        if not start_ua_valid(ua):
            raise RbAmpParamError(
                "start current must be 0 (off) or 10..2540 µA in 10 µA steps, got {}".format(ua))
        self._write(S.REG_CFG_START_UA, struct.pack("<H", ua))
        if self._read_u16(S.REG_CFG_START_UA) != ua:
            raise RbAmpError("CFG_START_UA read-back mismatch after write")

    def save_config(self):
        """Persist the writable config (SAVE_CONFIG). Blocking."""
        self._cmd_sync(S.CMD_SAVE_CONFIG, S.SETTLE_MS_SAVE_CONFIG)

    def set_time(self, unix_epoch):
        """Set the device RTC (TIME_SET + CMD_SET_TIME). Requires map 0x35+.
        Epoch before 2020 → ParamError."""
        if self.map_version < 0x35:
            raise RbAmpNotSupportedError("set_time requires STD map 0x35+")
        self._write(S.REG_TIME_SET, struct.pack("<I", unix_epoch & 0xFFFFFFFF))
        try:
            self._cmd_sync(S.CMD_SET_TIME, SETTLE_SET_TIME_MS)
        except RbAmpParamError:
            raise RbAmpParamError("set_time: module refused epoch {} (before 2020?)".format(unix_epoch))
        deadline = self._io.now_ms() + 300
        while self._io.ms_diff(deadline, self._io.now_ms()) > 0:
            if self._read_u8(S.REG_STATUS) & S.ST_TIME_VALID:
                return
            self._io.sleep_ms(20)
        raise RbAmpError("set_time accepted but TIME_VALID still clear")

    # ---- status / health -----------------------------------------------

    def read_health(self):
        """Read the STD status / health block (:class:`RbAmpStdHealth`)."""
        out = RbAmpStdHealth()
        out.status = self._read_u8(S.REG_STATUS)
        out.cal_ok = (out.status & 0x01) != 0
        out.sensor_ok = (out.status & 0x02) != 0
        out.mains_ok = (out.status & 0x04) != 0
        fc = self._read(S.REG_FRAME_CNT, 8)  # FRAME_CNT + CRC_ERR adjacent u32
        out.frame_count = _u32(fc, 0)
        out.crc_errors = _u32(fc, 4)
        if self.map_version < 0x35:
            return out
        out.time_valid = (out.status & S.ST_TIME_VALID) != 0
        out.nvm_fault = (out.status & S.ST_NVM_FAULT) != 0
        out.maint = (out.status & S.ST_MAINT) != 0
        n = self._read(S.REG_LTA_STATUS, 4)  # LTA_STATUS, LTA_SRC, CAL_SRC, LTG_STATUS
        out.lta_status = n[0]
        out.lta_src = n[1]
        out.cal_src = n[2]
        out.group_status = n[3] if self.map_version >= 0x36 else 0
        d = self._read(S.REG_RING_DROP, S.REG_LOST_FRAMES + 4 - S.REG_RING_DROP)
        out.ring_drop = _u16(d, 0)
        out.lost_frames = _u32(d, S.REG_LOST_FRAMES - S.REG_RING_DROP)
        out.uptime_s = self._read_u32(S.ENERGY_BASE + S.ENERGY_UPTIME)
        if self.fw_ver >= FW_RESET_CAUSE_MIN:
            r = self._read(S.REG_RESET_CAUSE, 4)  # RESET_CAUSE u8 + pad + BOOT_COUNT u16
            out.reset_cause = r[0]
            out.boot_count = _u16(r, 2)
        return out
