"""
Snapshot classes and exception hierarchy for the rbamp package.

Compatible with both MicroPython and CPython — avoid features missing from
core MicroPython (no ``@dataclass``, no ``enum.Enum``, no ``typing``).

See ``libs/spec/SPEC.md`` for the protocol definitions these classes mirror.
"""

# ---------------------------------------------------------------------------
# Topology constants (integer; matches device variant)
# ---------------------------------------------------------------------------

TOPOLOGY_SINGLE      = 1  # UI1 / I1 — 1 current channel
TOPOLOGY_SPLIT_PHASE = 2  # UI2 / I2 — 2 current channels
TOPOLOGY_THREE_PHASE = 3  # UI3 / I3 — 3 current channels
TOPOLOGY_FIVE_CH     = 5  # UI5 — 5 current channels + voltage (senior SKU)
TOPOLOGY_SEVEN_CH    = 7  # UI7 — 7 current channels + voltage (senior SKU)

# Legacy value 4 (=I3 without voltage) still valid in _SKU_MAP; it uses
# TOPOLOGY_THREE_PHASE semantically. Constants above are the exposed set.

_TOPOLOGY_NAMES = {
    TOPOLOGY_SINGLE:      "SINGLE",
    TOPOLOGY_SPLIT_PHASE: "SPLIT_PHASE",
    TOPOLOGY_THREE_PHASE: "THREE_PHASE",
    TOPOLOGY_FIVE_CH:     "FIVE_CH",
    TOPOLOGY_SEVEN_CH:    "SEVEN_CH",
}


def topology_name(value):
    """Return a human-readable name for a topology integer constant."""
    return _TOPOLOGY_NAMES.get(value, "UNKNOWN")


# ---------------------------------------------------------------------------
# RbAmpSensorClass — wire-byte values for REG_SENSOR_CLASS (0x25), v1.2+
# ---------------------------------------------------------------------------
#
# Authoritative source: ``libs/spec/registers.yaml`` (auto-generated into
# ``_registers.py`` as ``REG_SENSOR_CLASS = 0x25``). The wire enum is shared
# across all client libraries (Arduino ``RbAmpSensorClass``, ESP-IDF
# ``rbamp_sensor_class_t``).
#
# On CPython we use ``IntEnum`` for the richer repr/iteration; on MicroPython
# ports that don't ship ``enum`` (firmware default) we fall back to a plain
# class with integer class attributes. Both forms expose the same API:
# ``RbAmpSensorClass.SCT_013`` evaluates to int ``1`` either way.

try:
    from enum import IntEnum

    class RbAmpSensorClass(IntEnum):
        """Sensor class — wire-byte values for ``REG_SENSOR_CLASS`` (0x25).

        Set via :meth:`RbAmp.set_sensor_class` before the first
        :meth:`RbAmp.set_ct_model` / :meth:`RbAmp.set_ct_model_ch` call on
        v1.2+ firmware. ``int(cls)`` yields the wire encoding directly.

        On v1.2+ firmware ``UNSET`` causes both setters to refuse with
        :class:`RbAmpModeError`. On v1.0 / v1.1 firmware the register has no
        functional effect and the guard is skipped (backward compatibility).

        ``WIRED_CT`` and ``BUILTIN_CT`` are reserved for future sensor-class
        SKUs (STANDARD / PRO tiers) and currently behave the same as
        ``SCT_013`` device-side.
        """
        UNSET       = 0  # Default after factory reset.
        SCT_013     = 1  # SCT-013 current transformer (shipping default).
        WIRED_CT    = 2  # Reserved — wired CT class (STANDARD tier).
        BUILTIN_CT  = 3  # Reserved — built-in CT class (PRO tier).
except ImportError:
    # MicroPython without ``enum`` — plain class with int attributes.
    # int(RbAmpSensorClass.SCT_013) → 1 (Python int identity).
    class RbAmpSensorClass:  # type: ignore[no-redef]
        """Sensor class — wire-byte values for ``REG_SENSOR_CLASS`` (0x25).

        Plain-class fallback for MicroPython ports lacking ``enum``. API
        matches the CPython :class:`enum.IntEnum` variant — both forms
        expose ``RbAmpSensorClass.SCT_013`` as int ``1``.
        """
        UNSET       = 0
        SCT_013     = 1
        WIRED_CT    = 2
        BUILTIN_CT  = 3


# ---------------------------------------------------------------------------
# RbAmpWindowField — v1.4 senior-SKU channel-access-window field codes
# ---------------------------------------------------------------------------
#
# Senior SKUs (UI5/UI7) expose channels 3..N-1 only through a
# selector/window pair: write ``(field << 4) | ch`` to REG_CHANNEL_SELECT
# (0x32); read four single-byte registers 0x3C..0x3F as float32 LE.
#
# **Re-select rule #1** (contract): the window buffer snapshots the
# selected value at the moment of the select write. Reading 0x3C..0x3F
# without re-selecting returns the SAME snapshot again — so the library
# re-selects before every read (build-time invariant).
#
# Public fields:
#   0 = I_RMS            — live RMS current (A)
#   1 = I_PEAK           — live peak current (A)
#   2 = P_REAL           — live real power (W)
#   3 = PF               — live power factor (-1..+1)
#  13 = PERIOD_AVG_P     — latched period-average P (W); v1.4 energy path
#                          for ch>=3 (mirrors flat 0xC2/0xC6/0xDC for ch0-2)
#  15 = CT_MODEL         — u8 RW per-channel CT model (v1.4.9+, silicon-
#                          validated in v1.4.18). Sole per-channel path for
#                          ch3+; ch0-2 use flat 0x51-0x53 mirrors.
#
# Fields 4..12 are reserved (device-private cal writes); library uses them
# internally, they never appear in public docs or examples.

try:
    from enum import IntEnum

    class RbAmpWindowField(IntEnum):
        """v1.4 channel-access-window field codes (public subset).

        Values matches the CHFIELD_* constants in the generated
        :mod:`rbamp._registers_v2` module.
        """
        I_RMS         = 0
        I_PEAK        = 1
        P_REAL        = 2
        PF            = 3
        PERIOD_AVG_P  = 13
        CT_MODEL      = 15
except ImportError:
    class RbAmpWindowField:  # type: ignore[no-redef]
        """v1.4 channel-access-window field codes — MicroPython fallback."""
        I_RMS         = 0
        I_PEAK        = 1
        P_REAL        = 2
        PF            = 3
        PERIOD_AVG_P  = 13
        CT_MODEL      = 15


# ---------------------------------------------------------------------------
# Snapshot POD classes — plain attributes, no decorators
# ---------------------------------------------------------------------------

class RbAmpSnapshot:
    """Real-time metering snapshot — one full read of the RT register block.

    Populated by :meth:`RbAmp.read_all`. All fields are SI units.

    Attributes:
        voltage (float):        RMS voltage in V (wire reg 0x86, SPEC §12).
        voltage_peak (float):   Peak voltage in V (wire reg 0x8A).
        current (list[float]):  RMS current per channel in A.
        current_peak (list[float]): Peak current per channel in A.
        power (list[float]):    Real power per channel in W (signed).
        power_factor (list[float]): Power factor per channel (-1..+1).
        frequency (float):      Mains frequency in Hz.
        topology (int):         TOPOLOGY_* constant.
        channels (int):         Number of valid channels (1..7; senior SKUs 5/7).
        has_voltage_hw (bool):  True if voltage hardware was detected.

    Length note (v1.4): per-channel lists are sized ``max(3, channels)`` so
    legacy code accessing ``snap.current[2]`` on a UI1 keeps working
    (returns 0.0) while UI5/UI7 modules populate the full N slots.
    """

    def __init__(self, channels=1):
        n = channels if channels > 3 else 3
        self.voltage = 0.0
        self.voltage_peak = 0.0
        self.current      = [0.0] * n
        self.current_peak = [0.0] * n
        self.power        = [0.0] * n
        self.power_factor = [0.0] * n
        self.frequency = 0.0
        self.topology = TOPOLOGY_SINGLE
        self.channels = channels
        self.has_voltage_hw = False

    def __repr__(self):
        return (
            "RbAmpSnapshot(U={:.1f}V, f={:.1f}Hz, "
            "I={}, P={}, PF={})"
        ).format(
            self.voltage, self.frequency,
            self.current[: self.channels],
            self.power[: self.channels],
            self.power_factor[: self.channels],
        )


class RbAmpPeriodSnapshot:
    """Period-metering snapshot — output of :meth:`RbAmp.read_period_snapshot`.

    The energy primitive of the rbAmp protocol. Master tracks
    ``master_dt_ms`` between successful latches and integrates Wh from
    ``avg_p`` (see SPEC §7).

    The device-reported ``latch_ms`` is diagnostic only — the master's own
    wall-clock is authoritative for energy correctness.

    Attributes:
        avg_p (list[float]): Average real power per channel over the
            latched period (W). Sized ``max(3, channels)`` — a junior SKU
            keeps the three-element shape (legacy ``avg_p[2]`` stays valid);
            senior SKUs (UI5/UI7) carry ch3+ too. Unused channels are 0.
        max_p (float):       Peak real power on channel 0 during the period (W).
        latch_ms (int):      Device-reported period duration (ms, diagnostic).
        master_dt_ms (int):  Master's wall-clock dt since previous successful latch.
        valid (bool):        True if the period-valid status bit (0x07 bit0) was set at read time.
    """

    def __init__(self, channels=1):
        n = channels if channels > 3 else 3
        self.avg_p = [0.0] * n
        self.max_p = 0.0
        self.latch_ms = 0
        self.master_dt_ms = 0
        self.valid = False

    def __repr__(self):
        return (
            "RbAmpPeriodSnapshot(valid={}, avg_p={}, "
            "max_p={:.1f}W, master_dt={}ms, latch_ms={})"
        ).format(
            self.valid, self.avg_p, self.max_p,
            self.master_dt_ms, self.latch_ms,
        )


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------
#
# All rbamp exceptions inherit from OSError so user code that already handles
# I2C OSErrors continues to catch them. The specific subclasses let callers
# distinguish between transport failures (retry possible) and protocol-state
# failures (stale snapshot, mode-restricted operation, ...).

class RbAmpError(OSError):
    """Base class for all rbamp errors."""


class RbAmpIOError(RbAmpError):
    """I2C transport failure or NACK from the device."""


class RbAmpTimeoutError(RbAmpError):
    """Timeout waiting for a condition (e.g. waitReady)."""


class RbAmpNotReadyError(RbAmpError):
    """Device responded but reports status register != ready (SPEC §12)."""


class RbAmpStaleError(RbAmpError):
    """Period-valid status bit (0x07 bit0) == 0 — the period snapshot is stale (race)."""


try:
    class RbAmpParamError(RbAmpError, ValueError):
        """Caller passed an out-of-range argument (channel > 2, address out of range).

        Inherits from both ``OSError`` (via ``RbAmpError``) and ``ValueError`` so
        existing CPython catch blocks for either parent continue to work.
        """
except TypeError:
    # MicroPython forbids multi-base inheritance when both bases have separate
    # C-level layouts. Fall back to single-base; user code on MicroPython must
    # catch RbAmpParamError or RbAmpError explicitly (not ValueError).
    class RbAmpParamError(RbAmpError):
        """Caller passed an out-of-range argument (MicroPython single-base variant)."""


class RbAmpModeError(RbAmpError):
    """Operation requires the module's factory-provisioning mode (REG_MODE),
    but the device is in normal production mode."""


class RbAmpVersionError(RbAmpError):
    """Device firmware version is not supported by this library."""


class RbAmpNotSupportedError(RbAmpError):
    """The call is not available on this device/firmware.

    Raised by STD-line methods when invoked on a BASIC (8-bit) module, and by
    STD methods gated on a capability or firmware version the module lacks
    (e.g. on-device energy, µWh counters, voltage angles, CFG_START_UA)."""
