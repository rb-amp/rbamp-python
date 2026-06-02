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

from . import _registers as R
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

    def _detect_variant(self):
        """Variant auto-detection — runs once in begin().

        Per SPEC §8:

        - ACK on the channel-2 avg-power register (0xC6) -> THREE_PHASE
        - else ACK on the channel-1 avg-power register (0xC2) -> SPLIT_PHASE
        - else SINGLE
        - voltage RMS register (0x86) > 1.0 V -> has voltage hardware
        """
        if self._io.register_acks(self._addr, R.REG_V03_PERIOD_AVG_P_F2):
            self._channels = 3
            self._topology = TOPOLOGY_THREE_PHASE
        elif self._io.register_acks(self._addr, R.REG_V03_PERIOD_AVG_P_F1):
            self._channels = 2
            self._topology = TOPOLOGY_SPLIT_PHASE
        else:
            self._channels = 1
            self._topology = TOPOLOGY_SINGLE

        try:
            u_rms = self._read_float_le(R.REG_V03_U_RMS)
            self._has_voltage_hw = u_rms > 1.0
        except RbAmpError:
            self._has_voltage_hw = False

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
        return self._read_float_le(R.REG_V03_U_RMS)

    def read_voltage_peak(self, phase=0):
        """Read peak voltage (wire reg 0x8A) in V."""
        if phase != 0:
            raise RbAmpParamError("phase must be 0")
        return self._read_float_le(R.REG_V03_U_PEAK)

    def read_current(self, ch=0):
        """Read RMS current for one channel (wire reg 0x8E + 4·ch) in A."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_I0_RMS + ch * 4)

    def read_current_peak(self, ch=0):
        """Read peak current for one channel (wire reg 0x9A + 4·ch) in A."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_I0_PEAK + ch * 4)

    def read_power(self, ch=0):
        """Read real power for one channel (wire reg 0xA6 + 4·ch) in W (signed)."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_P0_REAL + ch * 4)

    def read_power_factor(self, ch=0):
        """Read power factor for one channel (wire reg 0xB2 + 4·ch) in -1..+1."""
        self._check_channel(ch)
        return self._read_float_le(R.REG_V03_PF0 + ch * 4)

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
            R.REG_V03_PERIOD_AVG_P_F0,
            R.REG_V03_PERIOD_AVG_P_F1,
            R.REG_V03_PERIOD_AVG_P_F2,
        )[ch]
        return self._read_float_le(reg)

    def read_period_max_power(self):
        """Read the channel-0 peak-power-per-period register (0xE0) in W."""
        return self._read_float_le(R.REG_V03_PERIOD_MAX_P_F0)

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

        now_ms = self._io.now_ms()
        out = RbAmpPeriodSnapshot()
        if self._have_last_latch:
            out.master_dt_ms = self._io.ms_diff(now_ms, self._last_latch_ms)

        self._io.sleep_ms(settle_ms)

        if not self.is_period_valid():
            self._log_info("period STALE — discarded")
            raise RbAmpStaleError("period snapshot is stale")

        for ch in range(self._channels):
            out.avg_p[ch] = self.read_period_avg_power(ch)
        out.max_p = self.read_period_max_power()
        out.latch_ms = self.read_period_latch_ms()
        out.valid = True

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

    def set_ct_model(self, code):
        """Set the SCT-013 CT model on channel 0 (legacy single-arg form).

        Writes ``REG_CT_MODEL`` (0x05), issues ``CMD_SAVE_GAINS``, waits
        700 ms for the flash erase + write cycle. Blocking.

        Equivalent to ``set_ct_model_ch(0, code)`` on v1.2+ firmware
        (sensor-class precondition applies — see :meth:`set_sensor_class`).
        On v1.0 / v1.1 firmware behaves identically to pre-v1.1.0 library
        versions (no guard).

        For multi-channel modules (UI2 / UI3 / I2 / I3) use
        :meth:`set_ct_model_ch` instead.

        Args:
            code (int): 1=SCT_013_005 .. 5=SCT_013_100.

        Raises:
            RbAmpParamError: code out of range.
            RbAmpModeError: v1.2+ firmware with sensor class still UNSET.
        """
        if code < 1 or code > 5:
            raise RbAmpParamError("CT model code must be 1..5, got {}".format(code))
        self._check_v12_sensor_class_pinned()
        self._io.write_byte(self._addr, R.REG_CT_MODEL, code)
        self.save_gains()

    def set_ct_model_ch(self, channel, code):
        """Set the SCT-013 CT model on a specific channel (v1.2+ firmware).

        Writes the per-channel ``CMD_SET_CT_MODEL_CH<N>`` opcode + the model
        code via ``REG_CT_MODEL``, issues ``CMD_SAVE_GAINS``, waits 700 ms.
        Blocking.

        .. warning::
            Multi-channel call order matters. Writing ``REG_CT_MODEL`` also
            triggers the device-side legacy direct-write callback which
            applies the preset to channel 0 unconditionally. So
            ``set_ct_model_ch(1, code)`` writes ``code``'s preset to
            channel 1 AS INTENDED, but also clobbers channel 0 to the same
            preset as a side-effect. To configure all channels with
            different models, **call the higher channel indices FIRST**::

                dev.set_ct_model_ch(2, 5)  # ch2 = SCT-013-100 (clobbers ch0 → 5)
                dev.set_ct_model_ch(1, 3)  # ch1 = SCT-013-030 (clobbers ch0 → 3)
                dev.set_ct_model_ch(0, 1)  # ch0 = SCT-013-005 (final ch0 preset)

            Final state: ch0=preset 1, ch1=preset 3, ch2=preset 5.

        Args:
            channel (int): 0..2 (limited by hardware variant).
            code (int): 1=SCT_013_005 .. 5=SCT_013_100.

        Raises:
            RbAmpParamError: channel outside 0..2 or code outside 1..5.
            RbAmpModeError: v1.2+ firmware with sensor class still UNSET.
        """
        if channel < 0 or channel > 2:
            raise RbAmpParamError(
                "channel must be 0..2, got {}".format(channel)
            )
        if code < 1 or code > 5:
            raise RbAmpParamError("CT model code must be 1..5, got {}".format(code))
        self._check_v12_sensor_class_pinned()
        # Per-channel select opcode (0x28/0x29/0x2A) primes the device-side
        # callback to apply the next REG_CT_MODEL write to the target channel.
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SET_CT_MODEL_CH0 + channel)
        self._io.write_byte(self._addr, R.REG_CT_MODEL, code)
        self.save_gains()

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
        """Arm an I2C address change (step 1 of 2).

        Validates the new address range and records the arm timestamp
        internally. Caller must call :meth:`commit_address_change`
        within 5 seconds.

        .. warning::
            The module must be in a factory-controlled provisioning mode
            for the address change to take effect. On standard production
            modules :meth:`commit_address_change` returns
            :class:`RbAmpModeError`. Use only when explicitly briefed.

        Raises:
            RbAmpParamError: address out of range or equal to current.
            RbAmpModeError: device not in the required provisioning mode.
        """
        if new_addr < 0x08 or new_addr > 0x77 or new_addr == self._addr:
            raise RbAmpParamError(
                "new_addr must be in 0x08..0x77 and != current ({:#04x})".format(self._addr)
            )
        mode = self._io.read_byte(self._addr, R.REG_MODE)
        if mode != 1:
            raise RbAmpModeError(
                "module is in production mode; address change requires "
                "the factory provisioning mode"
            )
        self._pending_addr = new_addr
        self._pending_armed_ms = self._io.now_ms()
        self._addr_change_armed = True

    def commit_address_change(self):
        """Commit the previously prepared address change (step 2 of 2).

        Must be called within 5 seconds of :meth:`prepare_address_change`.
        Persists the new address to flash, resets the device, then updates
        the internal address field. Bus unavailable for ~800 ms.

        .. warning::
            Persists to flash. Same factory-provisioning-mode requirement
            as :meth:`prepare_address_change`. Not a routine operation.

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
        self._io.write_byte(self._addr, R.REG_I2C_ADDRESS, self._pending_addr)
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_SAVE_GAINS)
        self._io.sleep_ms(R.SETTLE_MS_SAVE_GAINS)
        self._io.write_byte(self._addr, R.REG_COMMAND, R.CMD_RESET)
        self._io.sleep_ms(R.SETTLE_MS_RESET)
        self._addr = self._pending_addr
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
    # Static / multi-module
    # =====================================================================

    @staticmethod
    def broadcast_latch(bus):
        """I2C General-Call broadcast LATCH — sync multiple modules.

        Writes ``[REG_COMMAND, CMD_LATCH_PERIOD]`` to general-call address 0x00.
        All rbAmp modules on the bus latch within microseconds. Master should
        then time its own wall-clock dt and call
        ``read_period_snapshot(..., skip_latch=True)`` on each device.

        Args:
            bus: An smbus2 or machine.I2C bus object (NOT an :class:`RbAmp` instance).

        Returns:
            bool: True if the general-call write succeeded. Some host
            implementations refuse address 0x00 — verify on your platform.
        """
        if hasattr(bus, "readfrom_mem"):
            from ._io_micropython import MachineI2CBackend
            backend = MachineI2CBackend(bus)
        elif hasattr(bus, "read_byte_data"):
            from ._io_smbus import SMBusBackend
            backend = SMBusBackend(bus)
        else:
            raise RbAmpParamError("Unrecognised bus object for broadcast_latch")
        return backend.broadcast(bytes([R.REG_COMMAND, R.CMD_LATCH_PERIOD]))

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

    def _read_float_le(self, reg):
        buf = bytes([
            self._io.read_byte(self._addr, reg),
            self._io.read_byte(self._addr, reg + 1),
            self._io.read_byte(self._addr, reg + 2),
            self._io.read_byte(self._addr, reg + 3),
        ])
        value = struct.unpack("<f", buf)[0]
        # SPEC §B.5 — loose sanity filter. Catches NaN / Inf / exotic ghost
        # patterns that survive the per-byte retry (e.g. IDF i2c_master
        # buffer-leak 0x3C2FFB3F = 1.962 V). NO physical lower bounds, so
        # brownout / disconnect / off-grid states pass through unfiltered
        # (those are critical user-visible conditions). Backend-agnostic on
        # purpose — defensive at the sensor boundary.
        if not math.isfinite(value) or math.fabs(value) > 10000.0:
            self.sanity_reject_count += 1
            raise RbAmpIOError(
                "_read_float_le reg=0x{:02X} returned non-physical value {!r}".format(
                    reg, value
                )
            )
        return value
