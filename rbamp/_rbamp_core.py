"""
rbamp — core RbAmp client class (shared MicroPython + CPython).

This module implements the canonical API surface defined in
``libs/spec/SPEC.md`` §12. It is platform-agnostic: I/O is delegated to a
backend object passed in at construction. See :mod:`_io_smbus` (CPython)
and :mod:`_io_micropython` (MicroPython) for backend implementations.

The protocol invariants from SPEC §6, §7, §10, §11 are enforced here:

* one I2C address phase per byte (no auto-increment);
* 50 ms settle after CMD_LATCH_PERIOD;
* 700 ms settle after CMD_SAVE_GAINS;
* the period-valid status bit (0x07 bit0) is checked before consuming a snapshot;
* two-step address-change with 5 s arm window.
"""

import math
import struct

from . import _registers_v2 as R
from . import _sensor_models as _SM
from . import _sct_rating as _SCT
from ._snapshot import (
    RbAmpSnapshot,
    RbAmpPeriodSnapshot,
    TOPOLOGY_SINGLE,
    TOPOLOGY_SPLIT_PHASE,
    TOPOLOGY_THREE_PHASE,
    TOPOLOGY_FIVE_CH,
    TOPOLOGY_SEVEN_CH,
    topology_name,
    RbAmpSensorClass,
    RbAmpWindowField,
    RbAmpError,
    RbAmpIOError,
    RbAmpTimeoutError,
    RbAmpStaleError,
    RbAmpParamError,
    RbAmpModeError,
    RbAmpVersionError,
)
from ._energy import RbAmpEnergy


# ---------------------------------------------------------------------------
# Read-before-write policy for sensor class / CT model (parity with esp-idf
# rbampi_*_write_policy). Pure functions — host-testable with
# no backend. The firmware clears the per-channel CT models on ANY sensor-class
# write and re-applies the generic preset on ANY CT-model write, so a rewrite
# of the SAME value is destructive; and a factory-paired module (class 2/3,
# WIRED_CT / BUILTIN_CT) must not be silently reconfigured.
WRITE_NOOP = 1      # same value already on the module: no bus write
WRITE_PAIRED = 2    # factory-paired module: refuse (RbAmpModeError)
WRITE_PROCEED = 3   # write it


def _sensor_class_paired(cls):
    return cls == _SM.SENSOR_CLASS_WIRED_CT or cls == _SM.SENSOR_CLASS_BUILTIN_CT


def sensor_class_write_policy(current_cls, new_cls):
    """WRITE_NOOP / WRITE_PAIRED / WRITE_PROCEED for a sensor-class write."""
    if current_cls == new_cls:
        return WRITE_NOOP
    if _sensor_class_paired(current_cls):
        return WRITE_PAIRED
    return WRITE_PROCEED


def ct_model_write_policy(current_cls, current_model, new_model, force):
    """WRITE_NOOP / WRITE_PAIRED / WRITE_PROCEED for a CT-model write."""
    if force:
        return WRITE_PROCEED
    if current_model == new_model:
        return WRITE_NOOP
    if _sensor_class_paired(current_cls):
        return WRITE_PAIRED
    return WRITE_PROCEED


class _ChannelProxy:
    """Indexable proxy for per-channel convenience properties.

    Returned by :attr:`RbAmp.current`, :attr:`RbAmp.power`, etc. Supports
    ``proxy[ch]`` and iteration over all populated channels.
    """

    def __init__(self, owner, reader):
        self._owner = owner
        self._reader = reader

    def __getitem__(self, ch):
        return self._reader(ch)

    def __iter__(self):
        for ch in range(self._owner.channels):
            yield self._reader(ch)

    def __len__(self):
        return self._owner.channels

    def __repr__(self):
        try:
            return repr([self._reader(ch) for ch in range(self._owner.channels)])
        except Exception:
            return "_ChannelProxy(unreadable)"


class RbAmp:
    """Python client for one rbAmp slave device.

    Instances are bound at construction to an I/O backend wrapping a bus
    object and an I2C address. Multiple :class:`RbAmp` instances may share
    the same bus to talk to several rbAmp modules; use the static
    :meth:`broadcast_latch` to synchronise their period boundaries.

    Args:
        bus: A bus object — either ``smbus2.SMBus`` (CPython) or
            ``machine.I2C`` (MicroPython). The constructor auto-detects
            which backend to use based on the object's attributes.
        addr (int): 7-bit slave address (default 0x50, range 0x08..0x77).

    Example:
        Synchronous, with-statement::

            with RbAmp(i2c, 0x50) as dev:
                print(dev.voltage, dev.current[0], dev.power[0])
                snap = dev.read_period_snapshot()
                print(dev.energy.wh(0))

    See Also:
        ``libs/spec/SPEC.md`` §12 — Unified API surface.
    """

    # Re-export topology constants on the class for ``RbAmp.SINGLE`` access.
    SINGLE      = TOPOLOGY_SINGLE
    SPLIT_PHASE = TOPOLOGY_SPLIT_PHASE
    THREE_PHASE = TOPOLOGY_THREE_PHASE

    def __init__(self, bus, addr=0x50):
        # Lazy import — avoids ImportError for the platform we're NOT on.
        if hasattr(bus, "readfrom_mem") and hasattr(bus, "writeto_mem"):
            from ._io_micropython import MachineI2CBackend as _Backend
        elif hasattr(bus, "read_byte_data") and hasattr(bus, "write_byte_data"):
            from ._io_smbus import SMBusBackend as _Backend
        elif hasattr(bus, "read_byte") and hasattr(bus, "write_byte") and \
                hasattr(bus, "register_acks") and hasattr(bus, "now_ms"):
            # The bus already looks like a backend (used by tests / mocks).
            self._io = bus
            _Backend = None
        else:
            raise RbAmpParamError(
                "Unrecognised bus object: must expose smbus2-style "
                "(read_byte_data/write_byte_data) or machine.I2C-style "
                "(readfrom_mem/writeto_mem) methods"
            )
        if _Backend is not None:
            self._io = _Backend(bus)

        self._addr = addr
        self._topology = TOPOLOGY_SINGLE
        self._channels = 1
        self._has_voltage_hw = False
        self._energy = RbAmpEnergy()
        # STD line (16-bit map): set by begin() when the 'ST' identity probe
        # succeeds; None = BASIC (8-bit) module. See :mod:`rbamp._std`.
        self._std = None

        # SCT-013 rating-switch correction (BASIC fw <= 1.4.19). Per-channel
        # scale applied to I/P/energy; 1.0 = no correction. Computed at begin()
        # and after a detected restart. See :mod:`rbamp._sct_rating` and
        # libs/spec/SCT_RATING_SWITCH.md.
        self._ct_scale = {}          # ch -> float scale (absent => 1.0)
        self._ct_rating_warn = {}    # ch -> (action, detail) for the last compute
        self._ct_rating_enabled = True

        # Period-metering wall-clock state
        self._last_latch_ms = 0
        self._have_last_latch = False

        # Two-step address-change state
        self._pending_addr = 0
        self._pending_armed_ms = 0
        self._addr_change_armed = False

        # Optional log sink — set via set_logger().
        self._log = None

        # Diagnostic counter — incremented every time `_read_float_le` rejects
        # a value via the SPEC §B.5 loose sanity filter (NaN / Inf / |x|>10000).
        # In steady state this should remain 0 — any non-zero count signals
        # that the retry layer is leaking bad data through. Resettable via
        # :meth:`reset_counters`.
        self.sanity_reject_count = 0

    def reset_counters(self):
        """Reset diagnostic counters on both this client and its backend.

        Convenience for long-soak harnesses that want a clean baseline
        after begin() (which itself issues some reads that could in theory
        bump the counters).
        """
        self.sanity_reject_count = 0
        reset = getattr(self._io, "reset_counters", None)
        if reset is not None:
            reset()

    # =====================================================================
    # Lifecycle (SPEC §12)
    # =====================================================================

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Nothing to release; bus ownership stays with the caller.
        return False

    def begin(self):
        """Probe the device, detect variant, run primer LATCH.

        Performs:
            1. REG_VERSION read — raises :class:`RbAmpIOError` if NACK.
            2. Variant detection via NACK probe + U_rms threshold.
            3. CMD_LATCH_PERIOD primer write + 50 ms settle.
            4. Records master_t_last for subsequent energy integration.

        Idempotent — safe to call multiple times.

        Raises:
            RbAmpIOError: device did not ACK.
            RbAmpVersionError: device returned 0 or 0xFF for REG_VERSION.
        """
        # STD line (16-bit map) probe first — only if the backend can do a
        # 16-bit transaction. A BASIC module (or a mock without std_read)
        # yields None and falls through to the 8-bit path below.
        if getattr(self._io, "std_read", None) is not None:
            from ._std import _StdClient
            std = _StdClient.detect(self._io, self._addr)
            if std is not None:
                self._std = std
                self._channels = std.channels
                self._has_voltage_hw = std.n_voltage > 0
                self._topology = std.channels  # count may sit outside the BASIC enum
                self._log_info(
                    "begin: STD module map=0x{:02X} fw=0x{:08X} channels={} V={}",
                    std.map_version, std.fw_ver, std.channels, std.n_voltage)
                return
        version = self._io.read_byte(self._addr, R.REG_VERSION)
        if version == 0 or version == 0xFF:
            raise RbAmpVersionError(
                "REG_VERSION = 0x{:02X} — unsupported".format(version)
            )

        self._detect_variant()

        # SCT-013 rating-switch correction (BASIC fw <= 1.4.19) — read the
        # applied gain per channel and compute the per-channel scale.
        self._refresh_ct_scales()

        # Primer LATCH — discard first accumulator window. We record
        # last_latch_ms RIGHT AFTER the bus write (before the settle) so the
        # first user-visible read_period_snapshot() reports a master_dt_ms
        # consistent with subsequent cycles — both sides of the diff are
        # captured immediately after their respective LATCH writes.
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_LATCH_PERIOD)
        self._last_latch_ms = self._io.now_ms()
        self._have_last_latch = True
        self._io.sleep_ms(R.SETTLE_MS_LATCH_PERIOD)
        self._log_info("begin: channels={} has_voltage_hw={}",
                       self._channels, self._has_voltage_hw)

    def probe(self):
        """Lightweight alive check — single REG_VERSION read.

        Returns:
            bool: True if slave ACKs and reports a supported firmware version.
        """
        try:
            v = self._io.read_byte(self._addr, R.REG_VERSION)
        except RbAmpError:
            return False
        return v != 0 and v != 0xFF

    def wait_ready(self, timeout_ms=1000):
        """Poll the status register (0xCE) bit 0 until the device reports valid data.

        Args:
            timeout_ms (int): Maximum wait in ms.

        Raises:
            RbAmpTimeoutError: bit 0 did not assert within ``timeout_ms``.
        """
        deadline = self._io.now_ms() + timeout_ms
        while self._io.ms_diff(deadline, self._io.now_ms()) > 0:
            try:
                status = self._io.read_byte(self._addr, R.REG_V03_STATUS)
            except RbAmpError:
                status = 0
            if status & 0x01:
                return
            self._io.sleep_ms(10)
        raise RbAmpTimeoutError("wait_ready timed out after {} ms".format(timeout_ms))

    @property
    def firmware_version(self):
        """Device firmware version byte (REG_VERSION). Read on access."""
        return self._io.read_byte(self._addr, R.REG_VERSION)

    @property
    def topology(self):
        """Variant topology constant (one of TOPOLOGY_SINGLE/SPLIT_PHASE/THREE_PHASE)."""
        return self._topology

    @property
    def topology_name(self):
        """Human-readable variant name (e.g. ``"SINGLE"``)."""
        return topology_name(self._topology)

    @property
    def channels(self):
        """Number of valid current channels (1..3)."""
        return self._channels

    @property
    def has_voltage_hw(self):
        """True if voltage sensing hardware was detected."""
        return self._has_voltage_hw

    @property
    def address(self):
        """Current I2C slave address — updates after commit_address_change()."""
        return self._addr

    # SKU map per truth-doc §1.2 — authoritative source for variant id.
    # (channels, topology_const, has_voltage_hw)
    # v1.4: firmware added 7=UI5, 8=UI7 variant codes.
    # Older lab boards reporting raw 5/7 in HW_VARIANT will be reflashed
    # before release (root A5); library does not support the transitional
    # numbering — HW_VARIANT=5 remains I2.
    _SKU_MAP = {
        1: (1, TOPOLOGY_SINGLE,      True),   # UI1
        2: (2, TOPOLOGY_SPLIT_PHASE, True),   # UI2
        3: (3, TOPOLOGY_THREE_PHASE, True),   # UI3
        4: (1, TOPOLOGY_SINGLE,      False),  # I1
        5: (2, TOPOLOGY_SPLIT_PHASE, False),  # I2
        6: (3, TOPOLOGY_THREE_PHASE, False),  # I3
        7: (5, TOPOLOGY_FIVE_CH,     True),   # UI5 (v1.4 senior SKU)
        8: (7, TOPOLOGY_SEVEN_CH,    True),   # UI7 (v1.4 senior SKU)
    }

    def _detect_variant(self):
        """Variant auto-detection — runs once in begin().

        v1.4 (senior SKU support): reads two ROM bytes.

        1. ``REG_TOPOLOGY`` (0x24) — I-channel count {1,2,3,5,7}.
           0x00 means v1.0-era firmware without this register; we fall
           back to REG_HW_VARIANT only.
        2. ``REG_HW_VARIANT`` (0x55) — BUILD_VARIANT byte per truth-doc
           §1.2 + v1.4 root A5: 1..6 junior + 7=UI5, 8=UI7.

        Senior SKU byte 5/7 in REG_TOPOLOGY selects the UI5/UI7 branch of
        _SKU_MAP directly (both UI-class, ``has_voltage_hw=True``). Junior
        SKUs (topology 1/2/3) still cross-check REG_HW_VARIANT so we tell
        UI from I variants apart.

        Raises:
            RbAmpVersionError: neither register yields a known SKU.
        """
        topology_byte = self._io.read_byte(self._addr, R.REG_TOPOLOGY)
        if topology_byte in (5, 7):
            # Senior UI SKU — HW_VARIANT should be 7 (UI5) or 8 (UI7).
            hw = self._io.read_byte(self._addr, R.REG_HW_VARIANT)
            sku = self._SKU_MAP.get(hw)
            if sku is None or sku[0] != topology_byte:
                raise RbAmpVersionError(
                    "senior SKU: REG_TOPOLOGY=0x{:02X} vs REG_HW_VARIANT=0x{:02X} — "
                    "inconsistent (expected HW_VARIANT 7 for UI5 or 8 for UI7)".format(
                        topology_byte, hw))
            self._channels, self._topology, self._has_voltage_hw = sku
            return

        # Junior path (topology 1/2/3, or 0x00 v1.0-fallback via HW_VARIANT).
        hw = self._io.read_byte(self._addr, R.REG_HW_VARIANT)
        sku = self._SKU_MAP.get(hw)
        if sku is None:
            raise RbAmpVersionError(
                "REG_HW_VARIANT = 0x{:02X} — not a known rbAmp SKU (expected 1..8)"
                .format(hw)
            )
        self._channels, self._topology, self._has_voltage_hw = sku

    # =====================================================================
    # Real-time reads (SPEC §12 — RT block; device refreshes once per 100 ms
    # measurement window on all shipped BASIC firmware (v1.2+);
    # pre-v1.2 images used a 200 ms window)
    # =====================================================================

    def read_voltage(self, phase=0):
        """Read RMS voltage (wire reg 0x86, SPEC §12) in V.

        Args:
            phase (int): Phase index (only 0 supported in v1.0).

        Returns:
            float: RMS voltage in V.

        On an STD module ``phase`` selects the voltage input (0..n_voltage-1).
        """
        if self._std is not None:
            return self._std.read_voltage(phase).u_rms
        if phase != 0:
            raise RbAmpParamError("phase must be 0 (only phase 0 supported)")
        return self._read_float_le(R.REG_V03_U_RMS, kind="u")

    def read_voltage_peak(self, phase=0):
        """Read peak voltage (wire reg 0x8A) in V."""
        if self._std is not None:
            return self._std.read_voltage(phase).u_peak
        if phase != 0:
            raise RbAmpParamError("phase must be 0")
        return self._read_float_le(R.REG_V03_U_PEAK, kind="u")

    def read_current(self, ch=0):
        """Read RMS current for one channel in A. See :meth:`_route_channel_read`."""
        if self._std is not None:
            return self._std.read_channel(ch).i_rms
        return self._route_channel_read(ch, R.REG_V03_I0_RMS, R.CHFIELD_I_RMS, "i")

    def read_current_peak(self, ch=0):
        """Read peak current for one channel in A. See :meth:`_route_channel_read`."""
        if self._std is not None:
            return self._std.read_channel(ch).i_peak
        return self._route_channel_read(ch, R.REG_V03_I0_PEAK, R.CHFIELD_I_PEAK, "i")

    def read_power(self, ch=0):
        """Read real power for one channel in W (signed). See :meth:`_route_channel_read`."""
        if self._std is not None:
            return self._std.read_channel(ch).p
        return self._route_channel_read(ch, R.REG_V03_P0_REAL, R.CHFIELD_P_REAL, "p")

    def read_power_factor(self, ch=0):
        """Read power factor for one channel in -1..+1. See :meth:`_route_channel_read`."""
        if self._std is not None:
            return self._std.read_channel(ch).pf
        return self._route_channel_read(ch, R.REG_V03_PF0, R.CHFIELD_PF, "pf")

    def read_period_avg_p(self, ch=0):
        """Read the latched period-average P for one channel in W.

        v1.4 senior-SKU energy path — reads the same latched value that
        :meth:`read_period_snapshot` returns in ``avg_p[ch]`` for ch 0..2
        (flat regs 0xDC / 0xC2 / 0xC6), but works for ch 3..N-1 through
        the channel-access window (field 13 = ``PERIOD_AVG_P``). The
        latched value updates atomically across all channels on
        ``CMD_LATCH_PERIOD`` (root A1/A2) — this reader gives per-channel
        access without re-latching.

        Master computes ``E_Wh = avg_p * master_dt_s / 3600`` for every
        channel; :class:`RbAmpEnergy` does this for all populated channels in
        :meth:`read_period_snapshot` (including ch3+ on senior SKUs).

        Alias of :meth:`read_period_avg_power` (kept for the v1.4 senior-SKU
        naming); both resolve ch0-2 on the flat registers and ch3+ through
        the channel window.
        """
        return self.read_period_avg_power(ch)

    def read_frequency(self):
        """Read mains frequency in Hz (BASIC REG_AC_FREQ 0x20; STD FREQ cHz)."""
        if self._std is not None:
            return self._std.read_frequency()
        return float(self._io.read_byte(self._addr, R.REG_AC_FREQ))

    # =====================================================================
    # STD line (16-bit map) — on-device energy, angles, config (v1.6.0)
    #
    # These require an STD module (detected at begin()); on a BASIC module
    # they raise RbAmpNotSupportedError. The common readers above
    # (read_voltage / read_current / read_power / read_power_factor /
    # read_frequency) work on both lines.
    # =====================================================================

    @property
    def is_std(self):
        """True if the module is on the STD 16-bit line (set by begin())."""
        return self._std is not None

    @property
    def std(self):
        """The internal STD client (advanced use), or None on a BASIC module."""
        return self._std

    def _require_std(self):
        if self._std is None:
            from ._snapshot import RbAmpNotSupportedError
            raise RbAmpNotSupportedError(
                "STD-line call on a BASIC (8-bit) module — not supported")
        return self._std

    def read_std_voltage(self, phase=0):
        """Full STD voltage block (RMS/peak/THD/angle) for ``phase``."""
        return self._require_std().read_voltage(phase)

    def read_voltage_angle(self, phase=0):
        """Voltage phase angle vs phase 0, as ``(degrees, valid)``.

        ``valid`` is False on a single-voltage module or firmware < 0x7A."""
        v = self._require_std().read_voltage(phase)
        return v.angle_deg, v.angle_valid

    def read_phase_sequence(self):
        """Phase-sequence code: 0 n/a · 1 forward · 2 reverse · 3 abnormal.

        Raises RbAmpNotSupportedError on BASIC or firmware < 0x7A."""
        return self._require_std().read_phase_seq()

    def read_std_channel(self, ch=0):
        """Full STD current-channel block (I/P/Q/S/PF + harmonics)."""
        return self._require_std().read_channel(ch)

    def read_std_totals(self, k=0):
        """STD TOTALS block ``k`` (0 = grand total; per-phase need map 0x36+)."""
        return self._require_std().read_totals(k)

    def read_reactive(self, ch=0):
        """Reactive power for one channel in var (STD only)."""
        return self._require_std().read_channel(ch).q

    def read_apparent(self, ch=0):
        """Apparent power for one channel in VA (STD only)."""
        return self._require_std().read_channel(ch).s

    def read_std_energy(self):
        """On-device life-time energy snapshot (:class:`rbamp._std.RbAmpStdEnergy`).

        Carries active Wh/mWh/µWh import/export/net and reactive varh/µvarh,
        plus per-channel mWh/µWh. The µ fields are always populated (scaled
        from the coarse counters when the module lacks the fine ones — check
        ``.fine`` / ``.fine_reactive``). Requires CAPABILITIES ON_DEVICE_ENERGY."""
        return self._require_std().read_energy()

    @staticmethod
    def std_energy_delta(prev, now):
        """Energy consumed between two :meth:`read_std_energy` snapshots.

        Returns ``(delta, module_restarted)``; ``delta`` is None if the
        generation counter changed (life-time reset / adopt). See
        :func:`rbamp._std.energy_delta`."""
        from ._std import energy_delta
        return energy_delta(prev, now)

    def read_std_config(self):
        """STD configuration block (:class:`rbamp._std.RbAmpStdConfig`)."""
        return self._require_std().read_config()

    def read_std_health(self):
        """STD status / health block (:class:`rbamp._std.RbAmpStdHealth`)."""
        return self._require_std().read_health()

    def get_start_current(self):
        """CFG_START_UA in µA (0 = off). RbAmpNotSupportedError below fw 0x7B."""
        return self._require_std().get_start_current()

    def set_start_current(self, ua):
        """Set CFG_START_UA (0 = off; 10..2540 µA in 10 µA steps).

        Below this current a channel's energy does not count into import/export
        (readings I/P/Q are unaffected; default 400 µA). Applies at once; call
        :meth:`save_config` to persist. Out-of-range → RbAmpParamError (no bus
        write). RbAmpNotSupportedError below firmware 0x7B."""
        return self._require_std().set_start_current(ua)

    def save_config(self):
        """Persist the writable STD config to flash (SAVE_CONFIG). Blocking."""
        return self._require_std().save_config()

    def set_time(self, unix_epoch):
        """Set the device RTC epoch (STD map 0x35+). Epoch < 2020 → ParamError."""
        return self._require_std().set_time(unix_epoch)

    def read_all(self):
        """One-shot read of the full RT block.

        Returns:
            RbAmpSnapshot: All fields populated; unused channels zeroed.
        """
        s = RbAmpSnapshot(channels=self._channels)
        s.topology = self._topology
        s.has_voltage_hw = self._has_voltage_hw
        s.voltage = self.read_voltage()
        s.voltage_peak = self.read_voltage_peak()
        for ch in range(self._channels):
            s.current[ch] = self.read_current(ch)
            s.current_peak[ch] = self.read_current_peak(ch)
            s.power[ch] = self.read_power(ch)
            s.power_factor[ch] = self.read_power_factor(ch)
        s.frequency = self.read_frequency()
        return s

    # ----- Pythonic property shortcuts ------------------------------------

    @property
    def voltage(self):
        """Convenience alias for ``read_voltage(0)``."""
        return self.read_voltage(0)

    @property
    def voltage_peak(self):
        """Convenience alias for ``read_voltage_peak(0)``."""
        return self.read_voltage_peak(0)

    @property
    def current(self):
        """Indexable proxy: ``dev.current[ch]`` -> RMS current (A)."""
        return _ChannelProxy(self, self.read_current)

    @property
    def current_peak(self):
        """Indexable proxy: ``dev.current_peak[ch]`` -> peak current (A)."""
        return _ChannelProxy(self, self.read_current_peak)

    @property
    def power(self):
        """Indexable proxy: ``dev.power[ch]`` -> real power (W, signed)."""
        return _ChannelProxy(self, self.read_power)

    @property
    def power_factor(self):
        """Indexable proxy: ``dev.power_factor[ch]`` -> PF (-1..+1)."""
        return _ChannelProxy(self, self.read_power_factor)

    @property
    def frequency(self):
        """Convenience alias for ``read_frequency()``."""
        return self.read_frequency()

    # =====================================================================
    # Period metering (SPEC §7)
    # =====================================================================

    def latch_period(self):
        """Issue CMD_LATCH_PERIOD (write 0x27 to REG_COMMAND).

        Does not wait. For most usage prefer :meth:`read_period_snapshot`
        which encapsulates the full sequence.

        Raises:
            RbAmpIOError: on transport failure.
        """
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_LATCH_PERIOD)

    def is_period_valid(self):
        """Read the period-valid status bit (0x07 bit0).

        Returns:
            bool: True if the latched snapshot at 0xDC/0xE0/0xEC is fresh.
        """
        return (self._io.read_byte(self._addr, R.REG_V03_PERIOD_VALID) & 0x01) != 0

    def read_period_avg_power(self, ch=0):
        """Read the per-channel period-average real power (W).

        ch0-2 read the flat registers 0xDC / 0xC2 / 0xC6. On senior SKUs
        (UI5/UI7) ch3..N-1 read the same latched value through the channel
        window (field 13 = ``PERIOD_AVG_P``) — the firmware latches every
        channel atomically under IRQ-off on ``CMD_LATCH_PERIOD``, so a read
        after one latch is coherent across all channels.

        Must be called after :meth:`latch_period` + 50 ms + valid check.
        """
        self._check_channel(ch)
        if ch > 2:
            value = self._read_window_float(R.CHFIELD_PERIOD_AVG_P, ch, kind="p")
        else:
            # Per SPEC §7: flat register addresses are non-contiguous.
            reg = (
                R.REG_V03_PERIOD_AVG_P,       # ch0 (0xDC)
                R.REG_V03_PERIOD_AVG_P_CH1,   # ch1 (0xC2)
                R.REG_V03_PERIOD_AVG_P_CH2,   # ch2 (0xC6)
            )[ch]
            value = self._read_float_le(reg, kind="p")
        # SCT rating-switch correction — avg_p feeds the Wh accumulator, so the
        # energy path inherits the same scale as live power.
        if self._ct_scale:
            value *= self._ct_scale.get(ch, 1.0)
        return value

    def read_period_max_power(self):
        """Read the channel-0 peak-power-per-period register (0xE0) in W."""
        return self._read_float_le(R.REG_V03_PERIOD_MAX_P, kind="p")

    def read_period_latch_ms(self):
        """Read the period-latch-duration register (0xEC) — diagnostic (ms).

        Device's view of the period duration. Use the master's own
        wall-clock (``master_dt_ms`` from :meth:`read_period_snapshot`)
        for energy integration; this is diagnostic only.
        """
        return self._read_u32_le(R.REG_V03_PERIOD_LATCH_MS)

    def read_period_snapshot(self, settle_ms=50, skip_latch=False):
        """One-shot period snapshot: latch, settle, valid-check, read, integrate.

        Recommended entry point for period metering. Sequence:

        1. Skip the latch if ``skip_latch`` (use after :meth:`broadcast_latch`).
        2. Else write CMD_LATCH_PERIOD.
        3. Sleep ``settle_ms`` (default 50 ms per SPEC).
        4. Read the period-valid status bit (0x07 bit0); raise :class:`RbAmpStaleError` if 0.
        5. Read avg_p for each populated channel + max_p + latch_ms.
        6. Compute master_dt_ms from now() since previous successful snapshot.
        7. Integrate into per-channel Wh totals via ``self.energy.tick``.

        Args:
            settle_ms (int): Wait after latch before reading (default 50).
            skip_latch (bool): If True, assume an external party already
                latched and skip the write — only read.

        Returns:
            RbAmpPeriodSnapshot: Populated snapshot with ``valid == True``.

        Raises:
            RbAmpStaleError: period-valid status bit == 0.
            RbAmpIOError: any underlying transport failure.
        """
        if not skip_latch:
            self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_LATCH_PERIOD)

        # L9 anti-revert: master_dt_ms is the MASTER wall-clock interval
        # since the last CONSUMED-read (successful latch+read), NOT chip-
        # reported latch_ms (0xEC). Chip latch_ms under-counts ~26-27%
        # (HW-validated on every sister library bench, due to timer-ISR
        # starvation in the module firmware) and is DIAGNOSTIC-ONLY —
        # never feed it into energy integration. Stale-hold: on
        # PERIOD_VALID=0 we do NOT advance _last_latch_ms, so the next
        # successful latch's master_dt covers the full multi-period
        # interval. Firmware preserves the accumulator across empty
        # latches, so avg_p reported then is the average power over that
        # full interval → integration is correct. Do NOT revert to chip
        # period_ms here — see L9 callout.
        now_ms = self._io.now_ms()
        out = RbAmpPeriodSnapshot(self._channels)
        if self._have_last_latch:
            out.master_dt_ms = self._io.ms_diff(now_ms, self._last_latch_ms)

        self._io.sleep_ms(settle_ms)

        if not self.is_period_valid():
            self._log_info("period STALE — discarded (anchor held; next success covers full interval)")
            raise RbAmpStaleError("period snapshot is stale")

        # Fill every channel's latched period-average power. avg_p is sized to
        # the channel count; ch0-2 read the flat registers, ch3+ (senior SKUs)
        # read window field 13 — same coherent latch (read_period_avg_power).
        for ch in range(self._channels):
            out.avg_p[ch] = self.read_period_avg_power(ch)
        out.max_p = self.read_period_max_power()
        out.latch_ms = self.read_period_latch_ms()
        out.valid = True

        # Advance the anchor only on a CONSUMED read. Stale-hold keeps the
        # previous anchor, letting energy.tick() integrate the full multi-
        # period dt once we recover.
        self._last_latch_ms = now_ms
        self._have_last_latch = True
        self._energy.tick(out, self._channels)
        return out

    # =====================================================================
    # Energy
    # =====================================================================

    @property
    def energy(self):
        """The per-device :class:`RbAmpEnergy` accumulator.

        Updated automatically by :meth:`read_period_snapshot`. Call
        ``dev.energy.disable()`` to opt out.
        """
        return self._energy

    # =====================================================================
    # Async streaming (opt-in)
    # =====================================================================

    async def stream_period(self, interval_s=60.0, skip_stale=True):
        """Async generator yielding period snapshots at fixed intervals.

        Requires ``asyncio`` (CPython) or ``uasyncio`` (MicroPython).

        Args:
            interval_s (float): Seconds between latches.
            skip_stale (bool): If True (default), stale snapshots are
                swallowed silently; if False, they propagate as
                :class:`RbAmpStaleError`.

        Yields:
            RbAmpPeriodSnapshot

        Example::

            async for snap in dev.stream_period(interval_s=60):
                print(snap.avg_p[0], dev.energy.wh(0))
        """
        try:
            import asyncio  # type: ignore[import-not-found]
        except ImportError:
            import uasyncio as asyncio  # type: ignore[import-not-found]
        while True:
            await asyncio.sleep(interval_s)
            try:
                yield self.read_period_snapshot()
            except RbAmpStaleError:
                if not skip_stale:
                    raise

    # =====================================================================
    # Configuration (SPEC §10, §11)
    # =====================================================================

    def set_sensor_class(self, cls):
        """Pin the per-channel-uniform sensor class (v1.2+ firmware).

        Writes ``REG_SENSOR_CLASS`` (0x25), issues ``CMD_SAVE_USER_CONFIG``,
        waits 700 ms for the flash erase + write cycle. Blocking.
        (v1.5.0: switched from ``CMD_SAVE_GAINS`` — the latter is
        develop-gated in production and silently no-ops for user persist.)

        On v1.2+ firmware this is a precondition for :meth:`set_ct_model` and
        :meth:`set_ct_model_ch` — calling either with the class still
        :attr:`RbAmpSensorClass.UNSET` raises :class:`RbAmpModeError`.
        Pinning the class also resets ``REG_CT_MODEL`` to 0 device-side,
        preventing stale class/model bleed across a two-step provisioning
        sequence. On v1.0 / v1.1 firmware the register has no functional
        effect but the call still completes without error.

        .. warning::
            Persists to flash. Sensor-class change resets the per-channel
            CT model to 0 device-side — the caller MUST follow up with
            :meth:`set_ct_model_ch` (or :meth:`set_ct_model` for ch0-only)
            before metering is correctly calibrated again.

        Args:
            cls: An :class:`RbAmpSensorClass` value, or any integer in
                0..3 matching the wire encoding (UNSET=0, SCT_013=1,
                WIRED_CT=2, BUILTIN_CT=3).

        Raises:
            RbAmpParamError: ``cls`` outside 0..3.
            RbAmpModeError: the module is factory-paired (current class 2/3,
                WIRED_CT / BUILTIN_CT) and ``cls`` differs — reconfiguring it
                would clear the fitted-sensor calibration. Refused.
        """
        cls_int = int(cls)
        if cls_int < 0 or cls_int > 3:
            raise RbAmpParamError(
                "sensor class must be 0..3 (RbAmpSensorClass member), got {}".format(cls_int)
            )
        # Read-before-write: the firmware clears the CT models on ANY class
        # write, so an unchanged value is a destructive no-op we skip; a
        # factory-paired class is never silently rewritten.
        cur = self.read_sensor_class()
        policy = sensor_class_write_policy(cur, cls_int)
        if policy == WRITE_NOOP:
            return
        if policy == WRITE_PAIRED:
            raise RbAmpModeError(
                "set_sensor_class refused: the module is calibrated with its "
                "fitted sensor (class {}); changing it would clear that "
                "calibration".format(cur))
        self._io.write_byte(self._addr, R.REG_SENSOR_CLASS, cls_int)
        self._save_user_config()

    # v1.5.0: (class, code) pairs are looked up via the auto-generated
    # :mod:`rbamp._sensor_models` registry. The old hand-maintained
    # ``_CT_ACCEPTED`` dict is gone: it drifted six models behind in two
    # days, exactly what the registry exists to kill.
    #
    # Client-side validation philosophy (per root spec: "which codes a given
    # module ACCEPTS is runtime truth"):
    #   * unknown pair (not in registry)   -> raise; module will ERR_PARAM
    #     anyway, and a bad hand-typed code shouldn't burn a flash write
    #   * status=reserved                   -> raise; registry allocates the
    #     slot but has no preset row -> guaranteed ERR_PARAM
    #   * status=legacy or characterized    -> forward to module; module
    #     verdict wins (ERR_PARAM after the write, surfaced by the caller)

    def _validate_ct_code(self, code):
        """Fast-fail: pair must be a known non-reserved row in the registry.

        Client-side hint only. The module is the ultimate authority: on the
        window write path the caller surfaces post-write ``REG_ERROR ==
        DEV_ERR_PARAM`` as :class:`RbAmpParamError`.

        Skip on pre-v1.3 firmware (``REG_VERSION < 0x04``); those builds
        accepted the legacy ``1..5`` range without a per-class filter and
        predate the registry contract.
        """
        fw = self._io.read_byte(self._addr, R.REG_VERSION)
        if fw < 0x04:  # pre-v1.3
            if code < 1 or code > 5:
                raise RbAmpParamError(
                    "CT model code must be 1..5 on v1.0/v1.1/v1.2 firmware, got {}".format(code))
            return
        cls = self._io.read_byte(self._addr, R.REG_SENSOR_CLASS)
        entry = _SM.lookup(cls, code)
        if entry is None:
            raise RbAmpParamError(
                "CT model code {} unknown for sensor class {} — no row in "
                "sensor_models.yaml (registry is authoritative for the "
                "meaning of a code; module returns ERR_PARAM).".format(code, cls))
        _name, _desc, status = entry
        if status == "reserved":
            raise RbAmpParamError(
                "CT model code {} (class {}) is RESERVED in the registry — "
                "no preset row exists yet, module will return ERR_PARAM.".format(code, cls))

    def set_ct_model(self, code):
        """Set the CT model on channel 0 (legacy single-arg form).

        Writes ``REG_CT_MODEL``, issues ``CMD_SET_CT_MODEL_CH0`` (v1.3+),
        waits 5 ms, then ``CMD_SAVE_USER_CONFIG``. Blocking.
        (v1.5.0: persist opcode switched from ``CMD_SAVE_GAINS``, which
        is develop-gated in production and silently no-ops user persist.)

        Equivalent to ``set_ct_model_ch(0, code)``. For multi-channel
        modules (UI2 / UI3 / I2 / I3) use :meth:`set_ct_model_ch` instead.

        Args:
            code (int): SCT-013 SKU code per :meth:`_validate_ct_code`.
                v1.3 accepted set depends on the pinned sensor class:
                SCT_013 → {1, 2, 3, 4, 6}; WIRED_CT → {1, 2, 3};
                BUILTIN_CT → ∅. Legacy v1.x firmware → {1..5}.

        Raises:
            RbAmpParamError: code not accepted for current class.
            RbAmpModeError: v1.2+ firmware with sensor class still UNSET, or
                the module is factory-paired (class 2/3) — see
                :meth:`set_ct_model_ch`.
        """
        # Identical wire-canon to the ch0 branch of set_ct_model_ch; delegating
        # keeps a single guarded write path (read-before-write, paired refuse).
        self.set_ct_model_ch(0, code)

    def set_ct_model_ch(self, channel, code):
        """Set the CT model on a specific channel.

        v1.5.0 (silicon-signed v1.4.18 firmware): all channels ``0..N-1``
        supported, where ``N`` is the variant's channel count. Blocking.

        * **ch 0-2** — legacy wire-canon (truth-doc §7): ``REG_CT_MODEL =
          code → CMD_SET_CT_MODEL_CH<N> → 5 ms settle → CMD_SAVE_USER_CONFIG``.
          Flat mirror registers ``0x51-0x53`` reflect the applied value.
        * **ch 3+** — channel-access window field 15 (RW, u8). Wire order:
          ``REG_CHANNEL_SELECT = (15<<4)|ch → 0x3C = code → 0x3D = 0 →
          0x3E = 0 → 0x3F = 0`` — each byte a separate transaction (burst
          is not supported by firmware). Preset (NF+GAIN+shape) is applied
          IMMEDIATELY on the write; ``CMD_SAVE_USER_CONFIG`` persists it
          across reset. Unknown ``(class, code)`` -> ``ERR_PARAM 0xFE``
          and the channel keeps its previous model — the library surfaces
          that as :class:`RbAmpParamError` and does NOT persist.

        (v1.5.0: persist opcode is ``CMD_SAVE_USER_CONFIG`` in both paths.
        Prior draft used ``CMD_SAVE_GAINS`` which is develop-gated in
        production and silently no-ops user persist — root audit 2026-09-04.)

        **Multi-channel call order** (v1.4+): order-INDEPENDENT.

        Args:
            channel (int): 0..channels-1 (from variant detection).
            code (int): CT model code. Validated against
                :mod:`rbamp._ct_models` (registry from
                ``libs/spec/sensor_models.yaml``); module has the final
                say (returns ERR_PARAM if its own preset table lacks the
                row, even for a registry-accepted pair).

        A CT-model write replaces the channel's calibration with the generic
        preset, so (v1.5.0+) the write is guarded read-before-write: a code
        equal to the model already applied is a no-op (no bus traffic); a
        factory-paired module (sensor class 2/3, WIRED_CT / BUILTIN_CT) is
        refused with :class:`RbAmpModeError` — use :meth:`set_ct_model_ch_force`
        in a service context to override.

        Raises:
            RbAmpParamError: channel outside 0..channels-1; code not
                shipping in the registry for the pinned class; or module
                returned ERR_PARAM after the window write (window path
                only).
            RbAmpModeError: v1.2+ firmware with sensor class still UNSET, or
                the module is factory-paired (class 2/3) and the code differs.
        """
        self._set_ct_model_ch_impl(channel, code, force=False)

    def set_ct_model_ch_force(self, channel, code):
        """Service-path CT-model write that bypasses the read-before-write guard.

        Same wire sequence as :meth:`set_ct_model_ch` but writes even when the
        module is factory-paired (class 2/3) or already holds the code. For
        technicians re-sensoring a unit; the module still validates the
        (class, code) preset and raises :class:`RbAmpParamError` on a bad pair.
        """
        self._set_ct_model_ch_impl(channel, code, force=True)

    def _set_ct_model_ch_impl(self, channel, code, force):
        if channel < 0 or channel >= self._channels:
            raise RbAmpParamError(
                "channel must be 0..{}, got {}".format(self._channels - 1, channel)
            )
        self._check_v12_sensor_class_pinned()
        # Client-side fast-fail on an unwritable code (unset/reserved/unknown)
        # before the read-before-write guard, so a bad code is reported as
        # such regardless of the module's paired state.
        self._validate_ct_code(code)
        # Read-before-write: skip an unchanged model (re-applying would wipe
        # the channel's calibration), refuse a factory-paired module unless
        # forced.
        if self._ct_model_guard(channel, code, force):
            return

        if channel <= 2:
            # Wire-canon: REG first (lands in callback's pending-code register),
            # CMD second (consumes the pending code and writes the per-channel
            # preset), 5 ms settle so device-side preset application completes
            # before SAVE flashes. Inverted order silently fails to apply.
            self._io.write_byte(self._addr, R.REG_CT_MODEL, code)
            self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SET_CT_MODEL_CH0 + channel)
            self._io.sleep_ms(5)
            self._save_user_config()
            return

        # ch >= 3 — v1.4.9+ window field 15. Firmware requires each byte
        # its own transaction (burst not supported). Preset applied on
        # write; ERR_PARAM surfaces after if the module rejected the pair.
        self._select_channel_window(R.CHFIELD_CT_MODEL, channel)
        self._io.write_byte(self._addr, R.REG_CHANNEL_DATA + 0, code & 0xFF)
        self._io.write_byte(self._addr, R.REG_CHANNEL_DATA + 1, 0)
        self._io.write_byte(self._addr, R.REG_CHANNEL_DATA + 2, 0)
        self._io.write_byte(self._addr, R.REG_CHANNEL_DATA + 3, 0)
        err = self._io.read_byte(self._addr, R.REG_ERROR)
        if err == R.DEV_ERR_PARAM:
            # Clear so subsequent ops start clean; then surface to caller.
            try:
                self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_CLEAR_ERROR)
            except RbAmpError:
                pass
            raise RbAmpParamError(
                "device rejected (class,model)=({},{}) for channel {} — "
                "REG_ERROR=DEV_ERR_PARAM (0xFE). Channel model unchanged. "
                "Registry admits the pair; module's preset table disagrees "
                "(module is authoritative).".format(
                    self._io.read_byte(self._addr, R.REG_SENSOR_CLASS),
                    code, channel))
        self._save_user_config()

    def read_ct_model_ch(self, channel):
        """Read the CT model code APPLIED on the given channel.

        Coverage per v1.5.0 canon (silicon-signed v1.4.18):
          * **ch 0-2** — flat mirrors ``REG_CT_MODEL_CH0/1/2`` (0x51-0x53).
          * **ch 3+** — channel-access window field 15 (u8, low byte of
            the u32 read is significant; upper 3 bytes are zero).

        Useful for readback verification after :meth:`set_ct_model_ch` and
        for fleet health-checks.

        Args:
            channel (int): 0..channels-1.

        Returns:
            int: Applied CT model code (0 = unset).

        Raises:
            RbAmpParamError: channel outside 0..channels-1.
        """
        if channel < 0 or channel >= self._channels:
            raise RbAmpParamError(
                "channel must be 0..{}, got {}".format(self._channels - 1, channel))
        if channel <= 2:
            return self._io.read_byte(self._addr, R.REG_CT_MODEL_CH0 + channel)
        self._select_channel_window(R.CHFIELD_CT_MODEL, channel)
        # Only low byte of 0x3C..0x3F carries the value; upper bytes zero.
        return self._io.read_byte(self._addr, R.REG_CHANNEL_DATA)

    def read_sensor_class(self):
        """Read the pinned sensor class (``REG_SENSOR_CLASS`` 0x25).

        Returns:
            int: wire encoding — 0 UNSET, 1 SCT_013, 2 WIRED_CT, 3 BUILTIN_CT.
            On pre-v1.2 firmware the register is unmapped and reads 0.
        """
        return self._io.read_byte(self._addr, R.REG_SENSOR_CLASS)

    # =====================================================================
    # SCT-013 rating-switch correction (BASIC fw <= 1.4.19)
    # =====================================================================
    #
    # Canon: libs/spec/SCT_RATING_SWITCH.md. SCT batches are calibrated on
    # model 3 (SCT-013-030). On a model switch to X != 3, fw <= 1.4.19 keeps
    # the 030 gain after a restart but shows X, so I/P read at the 030 scale.
    # The library reads the applied gain (user-mode read; the gain write is
    # factory-gated and NEVER issued here) and scales the reading by
    #   s = preset(X) * c(X) / preset(3).
    # PF and U are never scaled. fw >= 1.4.20 self-corrects -> s = 1.

    # Channel-access window field for the applied gain (field 5). Not in the
    # generated register map (which exposes fields 0-3/13/15); flagged to root
    # for codegen. Used only for ch >= 3 on senior SKUs.
    _CHFIELD_GAIN = 5

    # REG_CAPABILITY (0x57/0x58, u16 LE) bit 12 = CAP_SCT_RATING_SELF: the
    # firmware applies the SCT rating-switch scale itself (reserved in fw
    # 1.4.20). Detect the feature by this bit, NOT by VERSION (the register-
    # model rule). Not yet in the generated map (highest generated bit is
    # CAP_IAP = 1<<11); flagged to root for codegen.
    _CAP_SCT_RATING_SELF = 1 << 12

    def _sct_self_corrects(self):
        """True if the firmware applies the rating-switch scale itself, so the
        library must NOT (s = 1). Gated on REG_CAPABILITY bit 12
        (CAP_SCT_RATING_SELF); reads 0 (absent) on fw <= 1.4.19."""
        try:
            return bool(self.read_capability() & self._CAP_SCT_RATING_SELF)
        except RbAmpError:
            return False

    def _read_ct_gain(self, ch):
        """Read the current gain the module applies on a channel (float32 LE).

        ch0-2 read the flat gain registers 0xF4 / 0xF8 / 0xFC; ch3+ read
        channel window field 5. Read-only — the library never writes gain.
        """
        if ch <= 2:
            reg = R.REG_V03_I0_GAIN + ch * 4
            buf = bytes([self._io.read_byte(self._addr, reg + i) for i in range(4)])
        else:
            self._select_channel_window(self._CHFIELD_GAIN, ch)
            buf = bytes([self._io.read_byte(self._addr, R.REG_CHANNEL_DATA + i)
                         for i in range(4)])
        return struct.unpack("<f", buf)[0]

    def _compute_ct_scale(self, ch, cls):
        """Compute (and apply the one-shot reset if needed) the rating scale
        for one SCT channel. Returns the scale (1.0 if no correction)."""
        model = self.read_ct_model_ch(ch)
        g_a = self._read_ct_gain(ch)
        s, action, detail = _SCT.rating_correction(model, g_a)
        if action == _SCT.RATING_NEEDS_RESET:
            # Same-boot raw-preset state: someone wrote the model this boot, so
            # the per-unit trim k is unknown. One CMD_RESET recovers the flash
            # (factory-gain) state; re-read and re-evaluate once.
            self._log_info("ct_scale ch{}: {} — issuing CMD_RESET", ch, detail)
            self._raw_reset()
            model = self.read_ct_model_ch(ch)
            g_a = self._read_ct_gain(ch)
            s, action, detail = _SCT.rating_correction(model, g_a, post_reset=True)
        self._ct_rating_warn[ch] = (action, detail)
        if action in (_SCT.RATING_WARN_GAIN, _SCT.RATING_WARN_UNCAL):
            self._log_info("ct_scale ch{}: WARNING {}", ch, detail)
        elif action == _SCT.RATING_OK:
            self._log_info("ct_scale ch{}: model {} s={:.4f} ({})", ch, model, s, detail)
        return s

    def _refresh_ct_scales(self):
        """Recompute the per-channel rating scale. SCT (class 1) only, on a
        BASIC module, when correction is enabled and the fw does not self-
        correct. Called at :meth:`begin` and after a detected restart."""
        self._ct_scale = {}
        if self._std is not None or not self._ct_rating_enabled:
            return
        if self._sct_self_corrects():
            return
        try:
            cls = self.read_sensor_class()
        except RbAmpError:
            return
        if cls != _SCT.SENSOR_CLASS_SCT_013:
            return  # class 2/3 are factory-paired — never scaled; model untouched
        for ch in range(self._channels):
            try:
                s = self._compute_ct_scale(ch, cls)
            except RbAmpError as exc:
                self._log_info("ct_scale ch{}: read failed ({}) — s=1", ch, exc)
                s = 1.0
            if s != 1.0:
                self._ct_scale[ch] = s

    def refresh_rating_correction(self):
        """Public: re-read model + gain and recompute the SCT rating scale.

        Call after the module restarts for a reason the library did not drive
        (e.g. the user set a new sensor model, saved, and reset externally).
        :meth:`begin` and :meth:`reset` already refresh automatically.
        """
        self._refresh_ct_scales()

    def set_rating_correction(self, enabled):
        """Enable/disable the SCT rating-switch correction (default enabled).

        Disable on a module whose firmware already self-corrects (fw >= 1.4.20)
        or to read the raw 030-scale values. Recomputes immediately.
        """
        self._ct_rating_enabled = bool(enabled)
        self._refresh_ct_scales()

    @property
    def rating_correction_enabled(self):
        """True if the SCT rating-switch correction is active."""
        return self._ct_rating_enabled

    def ct_rating_scale(self, ch=0):
        """The rating scale currently applied to I/P/energy on a channel."""
        return self._ct_scale.get(ch, 1.0)

    def ct_rating_status(self, ch=0):
        """``(action, detail)`` of the last rating compute for a channel, or
        ``None`` if the channel was never evaluated (e.g. not SCT)."""
        return self._ct_rating_warn.get(ch)

    def _ct_model_guard(self, channel, code, force):
        """Read-before-write guard for one channel's CT model.

        Returns ``True`` if the write should be SKIPPED (the module already
        holds this model — re-applying would wipe the channel's calibration).
        Raises :class:`RbAmpModeError` if the module is factory-paired
        (class 2/3) and ``force`` is ``False``.
        """
        if force:
            return False  # service path: proceed, skip the read-before-write probe
        cls = self.read_sensor_class()
        try:
            cur = self.read_ct_model_ch(channel)
        except RbAmpError:
            cur = 0xFF  # unreadable mirror: never mistaken for "same"
        policy = ct_model_write_policy(cls, cur, code, force)
        if policy == WRITE_NOOP:
            return True
        if policy == WRITE_PAIRED:
            raise RbAmpModeError(
                "set_ct_model refused on channel {}: the module is calibrated "
                "with its fitted sensor (class {}); use "
                "set_ct_model_ch_force(...) in a service context".format(channel, cls))
        return False

    def _check_v12_sensor_class_pinned(self):
        """Raise RbAmpModeError if v1.2+ firmware has REG_SENSOR_CLASS == UNSET.

        Mirrors the esp_idf ``_check_sensor_class_set()`` helper. On v1.0 /
        v1.1 firmware (REG_VERSION < 0x03) the guard is skipped — there's
        no device-side precondition to enforce.
        """
        fw = self._io.read_byte(self._addr, R.REG_VERSION)
        if fw >= 0x03:
            cls = self._io.read_byte(self._addr, R.REG_SENSOR_CLASS)
            if cls == 0:
                raise RbAmpModeError(
                    "REG_SENSOR_CLASS is UNSET on v1.2+ firmware; "
                    "call dev.set_sensor_class(RbAmpSensorClass.SCT_013) first"
                )

    def save_gains(self):
        """Issue CMD_SAVE_GAINS, wait 700 ms, then post-hoc wait-ready.

        v1.5.0 IMPORTANT (root audit 2026-09-04): ``CMD_SAVE_GAINS`` is
        DEVELOP-GATED in production firmware — factory tooling can invoke
        it, end-user firmware silently blocks. Do NOT use to persist user
        config (CT_MODEL, SENSOR_CLASS, GROUP_ID, LABEL); use
        :meth:`_save_user_config` for those. Kept here for factory / gain-
        cal harnesses only.

        v1.4 policy (root A3): erase+program blackout window starts ~1-2 ms
        AFTER the CMD ACK (deferred main-loop) and lasts ≤20 ms. The 700 ms
        settle covers that ×30. Post-sleep :meth:`_wait_ready_after_save`
        probe absorbs the tail if it ever appears.

        .. warning::
            **Factory/cal use only.** Regular library callers should never
            invoke this — the model / sensor-class setters now persist via
            :meth:`_save_user_config` (production-ungated).
        """
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SAVE_GAINS)
        self._io.sleep_ms(R.SETTLE_MS_SAVE_GAINS)
        self._wait_ready_after_save(timeout_ms=30)

    def _save_user_config(self):
        """Issue CMD_SAVE_USER_CONFIG, wait 700 ms, then post-hoc wait-ready.

        v1.5.0 canonical persist opcode for user config (SENSOR_CLASS,
        CT_MODEL / CT_MODEL_CH<n>, GROUP_ID, FLEET_CONFIG, LABEL,
        I2C_ADDRESS). Ungated in production firmware — this is the
        opcode a released device actually honours. See root's audit note
        (2026-09-04, bus 14:50Z): ``CMD_SAVE_GAINS`` also writes CT_MODEL
        mirrors but is develop-gated, so in production it is a silent
        no-op for user persist. Sister libraries (esp-idf) hit this
        latent bug and switched.
        """
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SAVE_USER_CONFIG)
        self._io.sleep_ms(R.SETTLE_MS_SAVE_USER_CONFIG)
        self._wait_ready_after_save(timeout_ms=30)

    def _wait_ready_after_save(self, timeout_ms=30):
        """Probe REG_STATUS after a SAVE; tolerate NACK for up to timeout_ms.

        Root A3 tail-window safety net. Returns silently on the first
        successful read; raises :class:`RbAmpIOError` only if the device
        keeps NACKing past the budget — which in practice means the SAVE
        actually failed and the caller should surface that.
        """
        deadline = self._io.now_ms() + timeout_ms
        last_exc = None
        while self._io.ms_diff(deadline, self._io.now_ms()) >= 0:
            try:
                self._io.read_byte(self._addr, R.REG_V03_STATUS)
                return
            except RbAmpError as exc:
                last_exc = exc
                self._io.sleep_ms(2)
        raise RbAmpIOError(
            "post-SAVE probe still NACK after {} ms: {}".format(timeout_ms, last_exc)
        ) from last_exc

    def prepare_address_change(self, new_addr):
        """Arm an I2C address change (step 1 of 2) — v1.3 two-phase commit.

        Validates the new address range and records the arm timestamp
        internally. Caller must call :meth:`commit_address_change`
        within 5 seconds.

        v1.3 canon (truth-doc §6.1): two-phase commit is **PRODUCTION-OK**
        — no provisioning-mode requirement. The arm consists of two
        atomic device writes performed by :meth:`commit_address_change`:

        1. ``REG_I2C_ADDRESS = candidate`` (lands in RAM, not persisted).
        2. ``REG_ADDR_COMMIT_MAGIC = 0xA5`` (arms commit; magic byte is
           consumed/cleared by the device on commit attempt).
        3. ``CMD_COMMIT_ADDR`` opcode 0x30 (persists to flash; opcode
           refuses without armed magic = anti-fat-finger interlock).
        4. ``CMD_RESET`` (new address active after reboot).

        Raises:
            RbAmpParamError: address out of range or equal to current.
        """
        if new_addr < 0x08 or new_addr > 0x77 or new_addr == self._addr:
            raise RbAmpParamError(
                "new_addr must be in 0x08..0x77 and != current ({:#04x})".format(self._addr)
            )
        self._pending_addr = new_addr
        self._pending_armed_ms = self._io.now_ms()
        self._addr_change_armed = True

    def commit_address_change(self):
        """Commit the previously prepared address change (step 2 of 2).

        Must be called within 5 seconds of :meth:`prepare_address_change`.
        Performs the v1.3 two-phase commit sequence (truth-doc §6.1):
        candidate → magic → CMD_COMMIT_ADDR → reset. Updates the internal
        address field on success. Bus unavailable for ~1 s.

        .. warning::
            Persists to flash. Production-OK (no mode gate); the magic
            byte ``0xA5`` written to ``REG_ADDR_COMMIT_MAGIC`` (0x31) is
            the anti-fat-finger interlock — without it the opcode is
            refused by firmware.

        Raises:
            RbAmpParamError: no change armed.
            RbAmpTimeoutError: arm older than 5 s.
        """
        if not self._addr_change_armed:
            raise RbAmpParamError("no address change armed; call prepare_address_change first")
        elapsed = self._io.ms_diff(self._io.now_ms(), self._pending_armed_ms)
        if elapsed > 5000:
            self._addr_change_armed = False
            raise RbAmpTimeoutError(
                "address change arm expired ({} ms > 5000 ms)".format(elapsed)
            )
        # v1.3 two-phase commit (truth-doc §6.1):
        # (1) write candidate to REG_I2C_ADDRESS  → RAM only, not persisted
        # (2) write magic 0xA5 to REG_ADDR_COMMIT_MAGIC  → arm commit
        # (3) issue CMD_COMMIT_ADDR opcode 0x30  → persist + consume magic
        # (4) issue CMD_RESET → reboot, new address active
        # Arm state cleared in finally so a partial failure (NACK between
        # writes) does NOT leave the library armed for an out-of-context
        # retry that could overshoot — caller must re-prepare cleanly.
        try:
            self._io.write_byte(self._addr, R.REG_I2C_ADDRESS, self._pending_addr)
            self._io.write_byte(self._addr, R.REG_ADDR_COMMIT_MAGIC, 0xA5)
            self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_COMMIT_ADDR)
            self._io.sleep_ms(R.SETTLE_MS_COMMIT_ADDR)
            self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_RESET)
            self._io.sleep_ms(R.SETTLE_MS_RESET)
            self._addr = self._pending_addr
        finally:
            self._addr_change_armed = False

    def factory_reset(self):
        """Issue a factory reset and wait 1500 ms for the module to reboot.

        .. warning::
            Erases ALL persisted parameters — current calibration profile,
            I²C address, configured CT model. Bus unavailable for 1500 ms.
            Module returns to factory defaults; recalibration MUST be done
            by qualified personnel or by re-applying
            :meth:`set_sensor_class` + :meth:`set_ct_model` once they are
            available on the firmware. **Not a routine operation.**
        """
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_FACTORY_RESET)
        self._io.sleep_ms(R.SETTLE_MS_FACTORY_RESET)

    def _raw_reset(self):
        """Issue CMD_RESET (0x01) and wait, WITHOUT recomputing the rating
        scale (used internally by the rating compute, which drives its own
        reset — see :meth:`_compute_ct_scale`)."""
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_RESET)
        self._io.sleep_ms(R.SETTLE_MS_RESET)

    def reset(self):
        """Issue CMD_RESET (0x01), wait 100 ms, and recompute the SCT rating
        correction (a reset is a restart — see SCT_RATING_SWITCH.md §2)."""
        self._raw_reset()
        self._refresh_ct_scales()

    # =====================================================================
    # Identity / capability (v1.3, truth-doc §1)
    # =====================================================================

    def read_variant(self):
        """Read REG_HW_VARIANT (0x55) — authoritative SKU byte.

        Returns:
            int: 1=UI1, 2=UI2, 3=UI3, 4=I1, 5=I2, 6=I3; other = unknown.
        """
        return self._io.read_byte(self._addr, R.REG_HW_VARIANT)

    def read_capability(self):
        """Read REG_CAPABILITY (0x57, u16 LE) — feature bitmap.

        Branch on capability bits, never on firmware-version heuristics
        (truth-doc §1.4). Returns 0 on read failure.
        """
        return self._read_u16_le(R.REG_CAPABILITY)

    def read_product_id(self):
        """Read REG_PRODUCT_ID (0x54) — family byte.

        Returns 0x01 = rbAmp sensor (the only family this lib supports).
        Other values = different device family; library does not interpret.
        """
        return self._io.read_byte(self._addr, R.REG_PRODUCT_ID)

    def read_uid(self):
        """Read REG_UID (0x5C, 12 bytes) — 96-bit chip UID.

        Returns:
            bytes: 12-byte UID payload (little-endian 3×u32 from UID_BASE).
        """
        return bytes(
            self._io.read_byte(self._addr, R.REG_UID + i) for i in range(12)
        )

    def read_label(self):
        """Read REG_LABEL (0x68, 8 bytes) — user module label, ASCII zero-padded.

        Returns:
            str: Label as utf-8 string with trailing NULs stripped.
                Empty string = unset.
        """
        raw = bytes(
            self._io.read_byte(self._addr, R.REG_LABEL + i) for i in range(R.REG_LABEL_SIZE)
        ).rstrip(b"\x00")
        try:
            return raw.decode("ascii", "replace")
        except Exception:
            return ""

    def set_label(self, label):
        """Set REG_LABEL (0x68, 8 bytes) — user module label.

        Per L-006 + truth-doc §4: multi-byte register writes do NOT
        auto-increment (F.13 hw-confirmed asymmetry). Must use a byte-loop
        — block-write would only land the first byte.

        Args:
            label (str): ASCII label up to 8 chars. Longer truncated;
                shorter is zero-padded.

        Raises:
            RbAmpParamError: label contains non-ASCII bytes.
        """
        try:
            data = label.encode("ascii")
        except UnicodeEncodeError:
            raise RbAmpParamError("label must be ASCII, got {!r}".format(label))
        if len(data) > R.REG_LABEL_SIZE:
            data = data[: R.REG_LABEL_SIZE]
        else:
            data = data + b"\x00" * (R.REG_LABEL_SIZE - len(data))
        # F.13 byte-loop: writes do NOT auto-increment. One transaction
        # per byte. Block-write would land only data[0].
        for i, b in enumerate(data):
            self._io.write_byte(self._addr, R.REG_LABEL + i, b)
        # Persist via CMD_SAVE_USER_CONFIG (production-OK per §8.1).
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SAVE_USER_CONFIG)
        self._io.sleep_ms(R.SETTLE_MS_SAVE_USER_CONFIG)

    # =====================================================================
    # Error / event channel (v1.3)
    # =====================================================================

    def read_last_error(self):
        """Read REG_ERROR (0x02) — outcome of the last write op.

        Returns:
            int: 0x00 = OK, 0xFA..0xFF error classes.
        """
        return self._io.read_byte(self._addr, R.REG_ERROR)

    def read_event_flags(self):
        """Read REG_EVENT_FLAGS (0x2A) — sticky event bitmap."""
        return self._io.read_byte(self._addr, R.REG_EVENT_FLAGS)

    def clear_event_flags(self, mask):
        """Clear sticky event flags by write-1-to-clear mask."""
        self._io.write_byte(self._addr, R.REG_EVENT_FLAGS, mask & 0xFF)

    def has_error(self):
        """True if EVENT_ERROR (bit3) is latched in REG_EVENT_FLAGS."""
        return (self.read_event_flags() & R.EVENT_ERROR) != 0

    def clear_error(self):
        """Issue CMD_CLEAR_ERROR (0x31 opcode) — clears REG_ERROR + EVENT_ERROR bit."""
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_CLEAR_ERROR)

    # =====================================================================
    # Fleet / GC (v1.3, truth-doc §5)
    # =====================================================================

    def read_fleet_config(self):
        """Read REG_FLEET_CONFIG (0x27) — bit0=GC_ENABLE.

        Effective only after CMD_RESET (GC ISR wired at boot).
        """
        return self._io.read_byte(self._addr, R.REG_FLEET_CONFIG)

    def enable_gc(self, enable=True):
        """Enable or disable General-Call latch reception, persisted (v1.3).

        Read-modify-writes ``REG_FLEET_CONFIG`` (0x27) bit0, issues
        ``CMD_SAVE_USER_CONFIG`` (production-OK), then ``CMD_RESET`` — the
        GC ISR is wired only at boot, so a reset is mandatory for the
        change to take effect. Blocking (~1 s: save 700 ms + reset
        settle).

        Args:
            enable (bool): True to receive GC latches; False to opt out.
        """
        current = self.read_fleet_config()
        new = (current | 0x01) if enable else (current & ~0x01)
        if new == current:
            # Bit already correct — no-op (avoids burning a flash write +
            # 1 s bus-unavailable window on repeated calls).
            return
        self._io.write_byte(self._addr, R.REG_FLEET_CONFIG, new & 0xFF)
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SAVE_USER_CONFIG)
        self._io.sleep_ms(R.SETTLE_MS_SAVE_USER_CONFIG)
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_RESET)
        self._io.sleep_ms(R.SETTLE_MS_RESET)

    def set_group_id(self, group):
        """Write REG_GROUP_ID (0x28). Persist via CMD_SAVE_USER_CONFIG to survive reset.

        Args:
            group (int): 0..255. 0 = respond to all-call only.

        Raises:
            RbAmpParamError: group outside 0..255.
        """
        if group < 0 or group > 255:
            raise RbAmpParamError("group must be 0..255, got {}".format(group))
        self._io.write_byte(self._addr, R.REG_GROUP_ID, group)

    def read_group_id(self):
        """Read REG_GROUP_ID (0x28)."""
        return self._io.read_byte(self._addr, R.REG_GROUP_ID)

    def read_gc_tick(self):
        """Read REG_GC_TICK (0x59, u16 LE) — last accepted GC tick witness.

        ``0xFFFF`` means no GC frame has been received since boot. Match
        against the master's last broadcast tick to verify per-module
        fleet sync.
        """
        return self._read_u16_le(R.REG_GC_TICK)

    # =====================================================================
    # Static / multi-module
    # =====================================================================

    @staticmethod
    def broadcast_latch_group(bus, group=0, tick=0):
        """I2C General-Call broadcast LATCH (v1.3, truth-doc §5.4).

        Transmits the 5-byte frame ``{0xA5, CMD_LATCH_PERIOD=0x27, group,
        tick_lo, tick_hi}`` to general-call address ``0x00``. Every rbAmp
        on the bus with ``REG_FLEET_CONFIG.bit0`` set (see
        :meth:`enable_gc`) AND ``REG_GROUP_ID`` matching ``group`` (or
        ``group == 0`` = all-call) latches its period accumulator
        atomically and stores ``tick`` in ``REG_GC_TICK`` (0x59).

        After the broadcast the master sleeps its settle window (≥50 ms
        per SPEC §7) then calls
        ``read_period_snapshot(..., skip_latch=True)`` on each device.
        Sync verification via :meth:`read_gc_tick` per module — must
        match ``tick``; ``0xFFFF`` means that module did NOT receive the
        frame (GC disabled, wrong group, or bus glitch).

        Latch-only by firmware design: destructive opcodes
        (``CMD_SAVE_*``, ``CMD_COMMIT_ADDR``, ``CMD_FACTORY_RESET``)
        never honoured over General-Call.

        Args:
            bus: An smbus2.SMBus (CPython) or machine.I2C (MicroPython)
                bus — NOT an :class:`RbAmp` instance.
            group (int): Group filter (0 = all-call). Module's
                ``REG_GROUP_ID`` must equal this OR ``group == 0``.
            tick (int): 16-bit window/tick counter stored in
                ``REG_GC_TICK`` of each latching module.

        Returns:
            bool: True if the frame was transmitted. Some host I²C
            implementations refuse address 0x00 — verify on your
            platform.

        Raises:
            RbAmpParamError: bus object not recognised, or group/tick
                outside 0..255 / 0..0xFFFF.
        """
        if group < 0 or group > 255:
            raise RbAmpParamError("group must be 0..255, got {}".format(group))
        if tick < 0 or tick > 0xFFFF:
            raise RbAmpParamError("tick must be 0..0xFFFF, got {}".format(tick))
        if hasattr(bus, "readfrom_mem"):
            from ._io_micropython import MachineI2CBackend
            backend = MachineI2CBackend(bus)
        elif hasattr(bus, "read_byte_data"):
            from ._io_smbus import SMBusBackend
            backend = SMBusBackend(bus)
        else:
            raise RbAmpParamError("Unrecognised bus object for broadcast_latch_group")
        # Canon frame: A5 27 group tick_lo tick_hi (5 bytes)
        frame = bytes([0xA5, R.CMD_LATCH_PERIOD, group & 0xFF,
                       tick & 0xFF, (tick >> 8) & 0xFF])
        return backend.broadcast(frame)

    @staticmethod
    def broadcast_latch(bus):
        """Legacy GC latch wrapper — calls :meth:`broadcast_latch_group` with
        group=0, tick=0 (all-call, anonymous tick).

        Prefer :meth:`broadcast_latch_group` for new code so witness checks
        via :meth:`read_gc_tick` can verify per-module sync.

        Returns:
            bool: Result of the underlying 5-byte GC frame transmission.
        """
        return RbAmp.broadcast_latch_group(bus, group=0, tick=0)

    # =====================================================================
    # Diagnostics / logging
    # =====================================================================

    def set_logger(self, log_callable):
        """Set an optional log sink for diagnostic messages.

        Args:
            log_callable: Any callable that accepts one string argument, e.g.
                ``print`` or ``logging.getLogger("rbamp").info``. Pass
                ``None`` to disable.
        """
        self._log = log_callable

    def _log_info(self, fmt, *args):
        if self._log is None:
            return
        try:
            self._log(("[rbamp] " + fmt).format(*args))
        except Exception:
            # Logging must never break the caller.
            pass

    # =====================================================================
    # Low-level helpers (one I2C address phase per byte — SPEC §6)
    # =====================================================================

    def _check_channel(self, ch):
        if ch < 0 or ch >= self._channels:
            raise RbAmpParamError(
                "channel {} out of range (device has {})".format(ch, self._channels)
            )

    def _read_u16_le(self, reg):
        lo = self._io.read_byte(self._addr, reg)
        hi = self._io.read_byte(self._addr, reg + 1)
        return lo | (hi << 8)

    def _read_u32_le(self, reg):
        b0 = self._io.read_byte(self._addr, reg)
        b1 = self._io.read_byte(self._addr, reg + 1)
        b2 = self._io.read_byte(self._addr, reg + 2)
        b3 = self._io.read_byte(self._addr, reg + 3)
        return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)

    # Per-quantity loose-sanity ceilings (SPEC §B.5 + truth-doc):
    # 230 V × 150 A < 30 kW headroom; PF allows transient overshoot
    # to ±1.5 for noisy/edge cycles. Q reserved for v1.4 (reactive
    # power) but limit shipped now to avoid future re-bump.
    _SANITY_LIMIT = {
        "u":  500.0,    # RMS voltage (V)
        "i":  150.0,    # RMS current (A)
        "p":  30000.0,  # Real power (W, signed)
        "pf": 1.5,      # Power factor
        "q":  30000.0,  # Reactive power (VAR, signed) — v1.4
    }

    def _read_float_le(self, reg, kind="p"):
        buf = bytes([
            self._io.read_byte(self._addr, reg),
            self._io.read_byte(self._addr, reg + 1),
            self._io.read_byte(self._addr, reg + 2),
            self._io.read_byte(self._addr, reg + 3),
        ])
        value = struct.unpack("<f", buf)[0]
        # SPEC §B.5 + truth-doc loose sanity — per-quantity ceilings.
        # Catches NaN / Inf / exotic ghost patterns surviving per-byte retry
        # (e.g. IDF i2c_master buffer-leak 0x3C2FFB3F = 1.962 V). NO physical
        # lower bounds — brownout / disconnect / off-grid pass through
        # unfiltered (those are critical user-visible conditions).
        # Per-quantity rather than a single |x|>10000 — the old uniform limit
        # rejected legitimate P readings on ≥10 kW loads (230 V × 45 A ≈
        # 10.35 kW). Defensive at the sensor boundary, backend-agnostic.
        limit = self._SANITY_LIMIT.get(kind, 30000.0)
        if not math.isfinite(value) or math.fabs(value) > limit:
            self.sanity_reject_count += 1
            raise RbAmpIOError(
                "_read_float_le reg=0x{:02X} kind={} returned non-physical {!r}".format(
                    reg, kind, value
                )
            )
        return value

    # =====================================================================
    # v1.4 channel-access window (senior SKUs, ch 3..N-1)
    # =====================================================================
    #
    # Wire contract (root A6, firmware bd2ad8e):
    #   1. Write REG_CHANNEL_SELECT (0x32) with (field << 4) | ch
    #   2. Read four bytes from REG_CHANNEL_DATA (0x3C..0x3F) as float32 LE
    #
    # Rule #1: RE-SELECT BEFORE EVERY READ. The data buffer snapshots the
    # selected value at select-time; reading the four data bytes again
    # without re-selecting returns the SAME snapshot. The library re-selects
    # per _read_window_float call — invariant by construction.

    def _select_channel_window(self, field, ch):
        """Write ``(field << 4) | ch`` to REG_CHANNEL_SELECT (0x32)."""
        selector = ((field & 0x0F) << 4) | (ch & 0x0F)
        self._io.write_byte(self._addr, R.REG_CHANNEL_SELECT, selector)

    def _route_channel_read(self, ch, flat_reg, window_field, kind):
        """Central per-channel float dispatch (v1.5.0 API canon).

        Consolidates the flat-block-vs-window branching in one place so the
        flat-block address arithmetic (``flat_reg + 4*ch``) is only ever
        reached for ``ch <= 2``, structurally. Under PUBLIC_API_TRUTH's
        "single-vote guard" recommendation, every public per-channel float
        reader delegates here; there is no per-call-site place to forget
        the ch>=3 branch.

        Args:
            ch (int): 0..channels-1 (validated by :meth:`_check_channel`).
            flat_reg (int): flat-block base register (ch0's address).
            window_field (int): CHFIELD_* code for the ch>=3 window path.
            kind (str): sanity-limit family (``"i"``/``"p"``/``"pf"``/``"u"``).
        """
        self._check_channel(ch)
        if ch <= 2:
            value = self._read_float_le(flat_reg + ch * 4, kind=kind)
        else:
            value = self._read_window_float(window_field, ch, kind=kind)
        # SCT rating-switch correction scales current and real power (not PF/U).
        if kind in ("i", "p") and self._ct_scale:
            value *= self._ct_scale.get(ch, 1.0)
        return value

    def _read_window_float(self, field, ch, kind="p"):
        """Select + read one float from the channel-access window.

        Re-selects on every call (rule #1). Reads 0x3C..0x3F as four
        single-byte reads, unpacks LE float, and passes through the same
        per-quantity sanity filter as :meth:`_read_float_le`.
        """
        self._select_channel_window(field, ch)
        buf = bytes([
            self._io.read_byte(self._addr, R.REG_CHANNEL_DATA + 0),
            self._io.read_byte(self._addr, R.REG_CHANNEL_DATA + 1),
            self._io.read_byte(self._addr, R.REG_CHANNEL_DATA + 2),
            self._io.read_byte(self._addr, R.REG_CHANNEL_DATA + 3),
        ])
        value = struct.unpack("<f", buf)[0]
        limit = self._SANITY_LIMIT.get(kind, 30000.0)
        if not math.isfinite(value) or math.fabs(value) > limit:
            self.sanity_reject_count += 1
            raise RbAmpIOError(
                "_read_window_float field={} ch={} kind={} non-physical {!r}".format(
                    field, ch, kind, value))
        return value

    def read_seq_digest(self):
        """Read the digest SEQ byte (REG_DIGEST + 1 = 0x71).

        The SEQ byte increments every 20 ms RT-commit (wraps 255→0). Two
        successive reads with unchanged SEQ are guaranteed to be from the
        same commit — useful for coherence checks on user-side batched
        polling. There is NO CRC in the digest (root A7); this is a
        commit-change marker only.

        Returns:
            int: SEQ byte, 0..255.
        """
        return self._io.read_byte(self._addr, R.REG_DIGEST + 1)
