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
from ._snapshot import (
    RbAmpSnapshot,
    RbAmpPeriodSnapshot,
    TOPOLOGY_SINGLE,
    TOPOLOGY_SPLIT_PHASE,
    TOPOLOGY_THREE_PHASE,
    topology_name,
    RbAmpSensorClass,
    RbAmpError,
    RbAmpIOError,
    RbAmpTimeoutError,
    RbAmpStaleError,
    RbAmpParamError,
    RbAmpModeError,
    RbAmpVersionError,
)
from ._energy import RbAmpEnergy


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
        version = self._io.read_byte(self._addr, R.REG_VERSION)
        if version == 0 or version == 0xFF:
            raise RbAmpVersionError(
                "REG_VERSION = 0x{:02X} — unsupported".format(version)
            )

        self._detect_variant()

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
    _SKU_MAP = {
        1: (1, TOPOLOGY_SINGLE,      True),   # UI1
        2: (2, TOPOLOGY_SPLIT_PHASE, True),   # UI2
        3: (3, TOPOLOGY_THREE_PHASE, True),   # UI3
        4: (1, TOPOLOGY_SINGLE,      False),  # I1
        5: (2, TOPOLOGY_SPLIT_PHASE, False),  # I2
        6: (3, TOPOLOGY_THREE_PHASE, False),  # I3
    }

    def _detect_variant(self):
        """Variant auto-detection — runs once in begin().

        Authoritative SKU byte from ``REG_HW_VARIANT`` (0x55) per
        truth-doc §1.2. Old NACK-probe approach (ACKing ``0xC2``/``0xC6``)
        is BROKEN — firmware returns ``0x00`` on any unmapped read and
        NEVER NACKs; the probe is incapable of distinguishing variants.

        Mapping (1..6 only — anything else means not a rbAmp module
        or firmware unknown)::

            1 = UI1 (1 ch, voltage)         4 = I1 (1 ch, no voltage)
            2 = UI2 (2 ch, voltage)         5 = I2 (2 ch, no voltage)
            3 = UI3 (3 ch, voltage)         6 = I3 (3 ch, no voltage)

        Raises:
            RbAmpVersionError: REG_HW_VARIANT not in 1..6.
        """
        hw = self._io.read_byte(self._addr, R.REG_HW_VARIANT)
        sku = self._SKU_MAP.get(hw)
        if sku is None:
            raise RbAmpVersionError(
                "REG_HW_VARIANT = 0x{:02X} — not a known rbAmp SKU (expected 1..6)"
                .format(hw)
            )
        self._channels, self._topology, self._has_voltage_hw = sku

    # =====================================================================
    # Real-time reads (SPEC §12 — RT block, 200 ms refresh on device)
    # =====================================================================

    def read_voltage(self, phase=0):
        """Read RMS voltage (wire reg 0x86, SPEC §12) in V.

        Args:
            phase (int): Phase index (only 0 supported in v1.0).

        Returns:
            float: RMS voltage in V.
        """
        if phase != 0:
            raise RbAmpParamError("phase must be 0 (only phase 0 supported)")
        return self._read_float_le(R.REG_V03_U_RMS, kind="u")

    def read_voltage_peak(self, phase=0):
        """Read peak voltage (wire reg 0x8A) in V."""
        if phase != 0:
            raise RbAmpParamError("phase must be 0")
        return self._read_float_le(R.REG_V03_U_PEAK, kind="u")

    def read_current(self, ch=0):
        """Read RMS current for one channel (wire reg 0x8E + 4·ch) in A."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_I0_RMS + ch * 4, kind="i")

    def read_current_peak(self, ch=0):
        """Read peak current for one channel (wire reg 0x9A + 4·ch) in A."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_I0_PEAK + ch * 4, kind="i")

    def read_power(self, ch=0):
        """Read real power for one channel (wire reg 0xA6 + 4·ch) in W (signed)."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_P0_REAL + ch * 4, kind="p")

    def read_power_factor(self, ch=0):
        """Read power factor for one channel (wire reg 0xB2 + 4·ch) in -1..+1."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_PF0 + ch * 4, kind="pf")

    def read_frequency(self):
        """Read mains frequency (REG_AC_FREQ, 0x20) in Hz."""
        return float(self._io.read_byte(self._addr, R.REG_AC_FREQ))

    def read_all(self):
        """One-shot read of the full RT block.

        Returns:
            RbAmpSnapshot: All fields populated; unused channels zeroed.
        """
        s = RbAmpSnapshot()
        s.topology = self._topology
        s.channels = self._channels
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
        """Read the per-channel period-average real power register (W).

        Reads from 0xDC (ch0), 0xC2 (ch1) or 0xC6 (ch2). Must be called
        after :meth:`latch_period` + 50 ms + valid check.
        """
        self._check_channel(ch)
        # Per SPEC §7: register addresses are non-contiguous.
        reg = (
            R.REG_V03_PERIOD_AVG_P,       # ch0 (0xDC)
            R.REG_V03_PERIOD_AVG_P_CH1,   # ch1 (0xC2)
            R.REG_V03_PERIOD_AVG_P_CH2,   # ch2 (0xC6)
        )[ch]
        return self._read_float_le(reg, kind="p")

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
        out = RbAmpPeriodSnapshot()
        if self._have_last_latch:
            out.master_dt_ms = self._io.ms_diff(now_ms, self._last_latch_ms)

        self._io.sleep_ms(settle_ms)

        if not self.is_period_valid():
            self._log_info("period STALE — discarded (anchor held; next success covers full interval)")
            raise RbAmpStaleError("period snapshot is stale")

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

        Writes ``REG_SENSOR_CLASS`` (0x25), issues ``CMD_SAVE_GAINS``, waits
        700 ms for the flash erase + write cycle. Blocking.

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
        """
        cls_int = int(cls)
        if cls_int < 0 or cls_int > 3:
            raise RbAmpParamError(
                "sensor class must be 0..3 (RbAmpSensorClass member), got {}".format(cls_int)
            )
        self._io.write_byte(self._addr, R.REG_SENSOR_CLASS, cls_int)
        self.save_gains()

    # Per-sensor-class accepted CT codes (v1.3 truth-doc §7 + root seed §3).
    # SCT-013 SKU map: 1=-005, 2=-010, 3=-030, 4=-050, 5=-100, 6=-020, 7=-060.
    # Codes 5 + 7 reserved/uncharacterised on v1.3 firmware → ERR_PARAM.
    # NOT contiguous — fast-fail client-side.
    _CT_ACCEPTED = {
        # RbAmpSensorClass.SCT_013    -> {1,2,3,4,6}
        # RbAmpSensorClass.WIRED_CT   -> {1,2,3}
        # RbAmpSensorClass.BUILTIN_CT -> {}   (no codes valid yet)
        1: frozenset({1, 2, 3, 4, 6}),
        2: frozenset({1, 2, 3}),
        3: frozenset(),
    }

    def _validate_ct_code(self, code):
        """v1.3 A1 fast-fail: code must be in the accepted set for the
        currently-pinned sensor class. Firmware is ultimate authority — this
        is just a client-side early reject so the user doesn't burn a flash
        write to hear ERR_PARAM back from the device.

        Skip on v1.0/v1.1 firmware (REG_VERSION < 0x03) and on v1.2 where
        the firmware accepts the full {1..5} range as legacy behaviour.
        """
        fw = self._io.read_byte(self._addr, R.REG_VERSION)
        if fw < 0x04:  # pre-v1.3
            if code < 1 or code > 5:
                raise RbAmpParamError(
                    "CT model code must be 1..5 on v1.0/v1.1/v1.2 firmware, got {}".format(code))
            return
        cls = self._io.read_byte(self._addr, R.REG_SENSOR_CLASS)
        accepted = self._CT_ACCEPTED.get(cls, frozenset())
        if code not in accepted:
            raise RbAmpParamError(
                "CT model code {} not accepted for sensor class {} on v1.3 firmware; "
                "accepted: {}".format(code, cls, sorted(accepted) or "{} (none)"))

    def set_ct_model(self, code):
        """Set the CT model on channel 0 (legacy single-arg form).

        Writes ``REG_CT_MODEL``, issues ``CMD_SET_CT_MODEL_CH0`` (v1.3+),
        waits 5 ms, then ``CMD_SAVE_GAINS``. Blocking.

        Equivalent to ``set_ct_model_ch(0, code)``. For multi-channel
        modules (UI2 / UI3 / I2 / I3) use :meth:`set_ct_model_ch` instead.

        Args:
            code (int): SCT-013 SKU code per :meth:`_validate_ct_code`.
                v1.3 accepted set depends on the pinned sensor class:
                SCT_013 → {1, 2, 3, 4, 6}; WIRED_CT → {1, 2, 3};
                BUILTIN_CT → ∅. Legacy v1.x firmware → {1..5}.

        Raises:
            RbAmpParamError: code not accepted for current class.
            RbAmpModeError: v1.2+ firmware with sensor class still UNSET.
        """
        self._check_v12_sensor_class_pinned()
        self._validate_ct_code(code)
        # Wire-canon (same as set_ct_model_ch for ch0): REG → CMD → 5 ms → SAVE
        self._io.write_byte(self._addr, R.REG_CT_MODEL, code)
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SET_CT_MODEL_CH0)
        self._io.sleep_ms(5)
        self.save_gains()

    def set_ct_model_ch(self, channel, code):
        """Set the CT model on a specific channel (v1.2+ firmware).

        Wire-canon order (truth-doc §7, sister arduino c5726bc / esp_idf
        65c572b): ``REG_CT_MODEL = code → CMD_SET_CT_MODEL_CH<N> → 5 ms
        settle → CMD_SAVE_GAINS`` (700 ms flash window). Blocking.

        **Multi-channel call order** (truth-doc A1, v1.3 firmware):
        order-INDEPENDENT. Each call binds ``code`` only to ``channel``;
        ``REG_CT_MODEL`` is pure staging on v1.3 firmware (the legacy
        direct-write side-effect on ch0 was removed). On v1.0/v1.1
        firmware the legacy clobber side-effect still applied — callers
        targeting both legacy and v1.3 should iterate channels in
        descending order as a defensive pattern.

        Args:
            channel (int): 0..2 (limited by hardware variant).
            code (int): CT model code; v1.3 firmware accepts a per-class
                subset (see :meth:`_validate_ct_code`).

        Raises:
            RbAmpParamError: channel outside 0..2 or code not accepted
                for the pinned sensor class.
            RbAmpModeError: v1.2+ firmware with sensor class still UNSET.
        """
        if channel < 0 or channel > 2:
            raise RbAmpParamError(
                "channel must be 0..2, got {}".format(channel)
            )
        self._check_v12_sensor_class_pinned()
        self._validate_ct_code(code)
        # Wire-canon: REG first (lands in callback's pending-code register),
        # CMD second (consumes the pending code and writes the per-channel
        # preset), then 5 ms settle so device-side preset application
        # completes before SAVE flashes. Inverted order silently fails to
        # apply (CMD primes with stale code) — sister arduino/esp_idf use
        # this order, HW-validated.
        self._io.write_byte(self._addr, R.REG_CT_MODEL, code)
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SET_CT_MODEL_CH0 + channel)
        self._io.sleep_ms(5)
        self.save_gains()

    def read_ct_model_ch(self, channel):
        """Read the CT model code APPLIED on the given channel (v1.3 mirror).

        Reads the verify-mirror registers ``REG_CT_MODEL_CH0/1/2`` (0x51-0x53)
        per truth-doc §7.3. Useful for read-back verification after
        :meth:`set_ct_model_ch` and for fleet health-checks.

        Args:
            channel (int): 0..2.

        Returns:
            int: Applied CT model code (0 = unset).

        Raises:
            RbAmpParamError: channel outside 0..2.
        """
        if channel < 0 or channel > 2:
            raise RbAmpParamError("channel must be 0..2, got {}".format(channel))
        return self._io.read_byte(self._addr, R.REG_CT_MODEL_CH0 + channel)

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
        """Issue CMD_SAVE_GAINS and wait 700 ms for flash erase.

        .. warning::
            Normally called internally by :meth:`set_ct_model` (and the
            v1.2 setters once they land); a bare invocation is only
            relevant if the caller has manually touched factory-calibrated
            registers — that is an out-of-warranty operation and not a
            routine library call.
        """
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SAVE_GAINS)
        self._io.sleep_ms(R.SETTLE_MS_SAVE_GAINS)

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

    def reset(self):
        """Issue CMD_RESET (0x01) and wait 100 ms."""
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_RESET)
        self._io.sleep_ms(R.SETTLE_MS_RESET)

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
