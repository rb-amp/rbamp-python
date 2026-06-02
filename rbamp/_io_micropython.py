"""
MicroPython I/O backend — wraps a ``machine.I2C`` instance.

Every operation issues one I2C address phase per byte to honour the no-
auto-increment contract documented in SPEC §6. We deliberately avoid
``readfrom_mem(addr, reg, N)`` with N > 1 because that asks the slave to
auto-increment its pointer, which the v1 firmware does not support.

This module is imported only when the user passes a ``machine.I2C``-shaped
bus object to :class:`RbAmp` — see ``__init__.py`` for the autodetect logic.

SPEC §B.5 — ESP32-IDF NACK + buffer-leak discipline
---------------------------------------------------
MicroPython's ``machine.I2C`` on the ESP32 port wraps the same ESP-IDF v5
``i2c_master`` driver that ESPHome, arduino-esp32 and our own
``libs/esp_idf/components/rbamp`` rely on. That driver intermittently
NACKs ~20 % of single-byte reads against the rbAmp v1 firmware at
100 kHz and additionally leaks read-buffer state on NACK (reproducible
0x3C2FFB3F = 1.962 V ghost). Mitigation per SPEC §B.5: 50 kHz bus +
per-byte retry (3 attempts × 5 ms gap) + loose sanity filter in
:mod:`_rbamp_core`.

The retry layer here covers the bus side; the sanity filter sits in
``_rbamp_core._read_float_le`` and is shared by both backends.

Non-ESP32 MicroPython ports (RP2040, STM32) do not hit this NACK pattern.
The retry layer is harmless there — it only kicks in on actual OSError —
so the default ``retry_attempts=3`` ships unconditionally. Callers on
non-ESP32 ports may pass ``retry_attempts=1`` for ~3× less wall-clock
latency on every read.
"""

try:
    import time
    _HAVE_TICKS_MS = hasattr(time, "ticks_ms")
except ImportError:  # pragma: no cover — defensive
    _HAVE_TICKS_MS = False

from ._snapshot import RbAmpIOError


class MachineI2CBackend:
    """Thin wrapper over ``machine.I2C`` with SPEC §B.5 retry discipline.

    The wrapper exists so :mod:`_rbamp_core` can stay platform-agnostic.
    All methods take the slave address explicitly so the same backend can
    serve multiple :class:`RbAmp` instances on the same bus.

    Args:
        bus: An open ``machine.I2C`` instance (or any duck-typed object
             exposing ``readfrom_mem`` and ``writeto_mem``).
        retry_attempts (int): Per-byte read/write attempts on transient
             OSError (NACK / timeout). Default 3 — drops ESP32 + rbAmp
             v1 NACK rate from ~20 % to <0.8 %. Set to 1 on non-ESP32
             ports, or once v1.1 firmware fixes the NACK source upstream.
        retry_gap_ms (int): Delay between retry attempts. Default 5 ms —
             empirically the time the slave needs to flush ADDR-phase
             state.
    """

    def __init__(self, bus, retry_attempts=3, retry_gap_ms=5):
        self._bus = bus
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_gap_ms = max(0, int(retry_gap_ms))
        # Diagnostic counters — used by the long-soak harness and by users
        # running long-running monitoring deployments. Resettable via
        # :meth:`reset_counters`.
        #
        # ``retry_exhaustion_count`` — bumped when the retry loop exits
        # without success (all `retry_attempts` NACKed). Each bump is a
        # real user-visible error (RbAmpIOError raised).
        #
        # ``retry_count_total`` — bumped on every individual retry sleep
        # i.e. every time a transient NACK was recovered by a subsequent
        # attempt. Distinguishes silent recovery from outright failure:
        # rate of retries / cycle tells you bus health, while
        # ``retry_exhaustion_count`` tells you how often retry failed.
        # Per root baton 2026-05-25T00:30Z (Phase 2 ACK Q2 answer).
        self.retry_exhaustion_count = 0
        self.retry_count_total = 0

    # ----- single-byte ops -------------------------------------------------

    def read_byte(self, addr, reg):
        """Read one byte from ``reg`` on slave ``addr``.

        Retries up to ``retry_attempts`` times with ``retry_gap_ms`` delay
        between attempts (SPEC §B.5).

        Raises:
            RbAmpIOError: on persistent NACK / transport failure after
                exhausting all retry attempts.
        """
        last_exc = None
        for attempt in range(self._retry_attempts):
            try:
                b = self._bus.readfrom_mem(addr, reg, 1)
                return b[0] & 0xFF
            except OSError as exc:
                last_exc = exc
                if attempt + 1 < self._retry_attempts:
                    self.retry_count_total += 1
                    self.sleep_ms(self._retry_gap_ms)
        self.retry_exhaustion_count += 1
        raise RbAmpIOError(
            "read_byte addr=0x{:02X} reg=0x{:02X} failed after {} attempts: {}".format(
                addr, reg, self._retry_attempts, last_exc
            )
        ) from last_exc

    def write_byte(self, addr, reg, val):
        """Write one byte ``val`` to ``reg`` on slave ``addr``.

        Same retry policy as :meth:`read_byte` — NACK on the write phase
        happens less frequently than on reads but is not impossible
        (SPEC §B.5).

        Raises:
            RbAmpIOError: on persistent NACK / transport failure.
        """
        last_exc = None
        for attempt in range(self._retry_attempts):
            try:
                self._bus.writeto_mem(addr, reg, bytes([val & 0xFF]))
                return
            except OSError as exc:
                last_exc = exc
                if attempt + 1 < self._retry_attempts:
                    self.retry_count_total += 1
                    self.sleep_ms(self._retry_gap_ms)
        self.retry_exhaustion_count += 1
        raise RbAmpIOError(
            "write_byte addr=0x{:02X} reg=0x{:02X} val=0x{:02X} failed after {} attempts: {}".format(
                addr, reg, val, self._retry_attempts, last_exc
            )
        ) from last_exc

    # ----- diagnostic counters ---------------------------------------------

    def reset_counters(self):
        """Reset all diagnostic counters to 0 (long-soak test convenience)."""
        self.retry_exhaustion_count = 0
        self.retry_count_total = 0

    # ----- NACK probe (used by variant detection) --------------------------

    def register_acks(self, addr, reg):
        """Return True if reading ``reg`` on ``addr`` is ACKed.

        Returns False on NACK without raising — used by variant-detect logic.
        Single-shot by design: retry would mask the NACK signal the probe
        is testing for (SPEC §8).
        """
        try:
            self._bus.readfrom_mem(addr, reg, 1)
            return True
        except OSError:
            return False

    # ----- I2C General-Call broadcast --------------------------------------

    def broadcast(self, payload):
        """Issue an I2C General-Call write of ``payload``.

        Implemented as ``writeto_mem(0x00, payload[0], payload[1:])`` — this
        yields one start + addr 0x00 + reg byte + remaining bytes + stop,
        which is the exact wire format the device expects for a broadcast
        LATCH. Note: some MicroPython ports refuse address 0x00 — verify on
        the target.

        Args:
            payload: 2-byte sequence ``[REG_COMMAND, CMD_LATCH_PERIOD]``.

        Returns:
            bool: True on success.
        """
        try:
            first = payload[0]
            rest = bytes(payload[1:])
            self._bus.writeto_mem(0x00, first, rest)
            return True
        except OSError:
            return False

    # ----- timing abstraction (MicroPython ticks_ms + wrap-safe diff) ------

    @staticmethod
    def now_ms():
        """Return a monotonic timestamp in ms.

        Uses ``time.ticks_ms`` on MicroPython (wraps every ~12 days at 32 bit);
        falls back to ``time.monotonic() * 1000`` if ticks_ms is unavailable.
        """
        if _HAVE_TICKS_MS:
            return time.ticks_ms()
        return int(time.monotonic() * 1000.0)

    @staticmethod
    def ms_diff(later, earlier):
        """Difference ``later - earlier`` in ms with wrap handling."""
        if _HAVE_TICKS_MS:
            return time.ticks_diff(later, earlier)
        return later - earlier

    @staticmethod
    def sleep_ms(ms):
        """Sleep for ``ms`` milliseconds."""
        if _HAVE_TICKS_MS:
            time.sleep_ms(ms)
        else:
            time.sleep(ms / 1000.0)
