"""
Master-side per-channel energy (Wh) accumulator for the rbamp package.

Compatible with both MicroPython and CPython. Owned by the :class:`RbAmp`
class — user code accesses it via ``dev.energy`` and reads totals through
``dev.energy.wh(ch)``.

See SPEC §7 for the integration formula::

    E_Wh[ch] += avg_p_W * master_dt_s / 3600

**L9 anti-revert**: ``master_dt_s`` is the MASTER wall-clock dt computed
by the caller (``time.monotonic()`` on CPython / ``time.ticks_ms()`` on
MicroPython) across consecutive consumed reads, NOT the chip's self-
reported period (``REG_V03_PERIOD_LATCH_MS`` 0xEC). The chip clock
under-counts ~26-27% (HW-validated on every sister library bench, due
to timer-ISR starvation in the module firmware). A previous revision
wrongly used chip period_ms; reversed mid-2026. **Future porters: do
NOT revert to chip-period — the accumulator is clock-agnostic and the
master wall-clock is the billing dt.** ``RbAmpPeriodSnapshot.latch_ms``
exists in the snapshot for diagnostics only.

CPython uses 64-bit float natively; MicroPython on ESP32 also uses 64-bit
float. Older MicroPython ports (esp8266 builds) may use 32-bit float, in
which case users should reset() periodically on long-soak runs to avoid
float-precision creep.
"""


class RbAmpEnergy:
    """Per-channel running-total Wh accumulator.

    Updates are triggered by :meth:`RbAmp.read_period_snapshot`; user code
    reads totals via :meth:`wh`. Can be disabled at runtime via
    :meth:`disable` if the caller wants raw period readings only and will
    integrate energy elsewhere.

    Not thread-safe; all operations expected on the main loop.
    """

    def __init__(self):
        """Construct an empty accumulator. Enabled by default.

        The per-channel list starts at 3 (junior-SKU default) and grows on
        demand when a wider module (UI5/UI7) first integrates — see
        :meth:`tick`.
        """
        self._wh = [0.0, 0.0, 0.0]
        self._enabled = True

    def _ensure(self, n):
        """Grow the per-channel list to hold at least ``n`` channels."""
        if n > len(self._wh):
            self._wh.extend([0.0] * (n - len(self._wh)))

    def tick(self, snap, channels):
        """Integrate a period snapshot into the running totals.

        Idempotent: a snapshot with ``valid == False`` or ``master_dt_ms <= 0``
        (e.g. first tick after primer LATCH) is silently skipped. Normally
        called by :meth:`RbAmp.read_period_snapshot`, not user code.

        Args:
            snap (RbAmpPeriodSnapshot): Snapshot to integrate.
            channels (int): Number of valid channels (1..7; senior SKUs 5/7).
        """
        if not self._enabled or not snap.valid:
            return
        dt_s = snap.master_dt_ms / 1000.0
        if dt_s <= 0.0:
            # First tick after primer LATCH on begin().
            return
        # Integrate every populated channel. Senior SKUs (UI5/UI7) expose ch3+
        # period-average power through the channel window (field 13); the
        # snapshot's avg_p is sized to the channel count, so integrate all of
        # them, growing the accumulator on first sight of a wider device.
        n = channels if channels <= len(snap.avg_p) else len(snap.avg_p)
        self._ensure(n)
        for ch in range(n):
            # E_Wh += avg_p_W * dt_s / 3600 — see SPEC §7.
            self._wh[ch] += snap.avg_p[ch] * dt_s / 3600.0

    def wh(self, ch=0):
        """Read the running total Wh for one channel.

        Args:
            ch (int): Channel index 0..N-1 (senior SKUs expose up to ch6).

        Returns:
            float: Running total in Wh (signed; negative = net export).
            0.0 for a channel that has never been integrated.
        """
        if ch < 0 or ch >= len(self._wh):
            return 0.0
        return self._wh[ch]

    def reset(self, ch=0):
        """Reset one channel's accumulator to zero."""
        if 0 <= ch < len(self._wh):
            self._wh[ch] = 0.0

    def reset_all(self):
        """Reset all channels' accumulators to zero."""
        for ch in range(len(self._wh)):
            self._wh[ch] = 0.0

    def disable(self):
        """Disable automatic integration.

        Subsequent :meth:`tick` calls become no-ops. :meth:`wh` continues to
        return the frozen value at the time of disable.
        """
        self._enabled = False

    def enable(self):
        """Re-enable automatic integration after disable()."""
        self._enabled = True

    @property
    def is_enabled(self):
        """True if automatic integration is active (default)."""
        return self._enabled
