"""
CPython I/O backend — wraps an ``smbus2.SMBus`` instance.

Every operation issues one I2C address phase per byte to honour the no-
auto-increment contract documented in SPEC §6. Multi-byte reads (uint16,
uint32, float32) are composed in :mod:`_rbamp_core` from single-byte calls.

This module is imported only when the user passes an smbus2-shaped bus
object to :class:`RbAmp` — see ``__init__.py`` for the autodetect logic.
"""

import time

from ._snapshot import RbAmpIOError


class SMBusBackend:
    """Thin wrapper over ``smbus2.SMBus`` with bus-robustness discipline.

    The wrapper exists so :mod:`_rbamp_core` can stay platform-agnostic.
    All methods take the slave address explicitly so the same backend can
    serve multiple :class:`RbAmp` instances on the same bus.

    **Bus-robustness** (v1.3 / root seed §2): CPython hosts typically reach
    the bus through a USB-I2C adapter or a Linux SBC's `/dev/i2c-N`. Both
    paths return ``OSError`` on NACK but neither offers an in-driver spin-
    retry. The backend implements a *generic* NACK-retry layer (default
    ``retry_attempts=3``, ``retry_gap_ms=2``) plus a guard against
    infinite-block conditions — failed reads/writes raise after the budget
    is exhausted instead of looping forever. The ESP-IDF i2c_master spin
    discipline (L8) is platform-specific to the IDF and not applicable
    here; MicroPython-on-ESP32 keeps that discipline in
    :class:`MachineI2CBackend`.

    Args:
        bus: An open ``smbus2.SMBus`` instance (or any duck-typed object
             exposing ``read_byte_data``, ``write_byte_data`` and
             ``write_i2c_block_data``).
        retry_attempts: Max total attempts per single op (≥1). Default 3.
        retry_gap_ms: Sleep between retries, in ms. Default 2 ms.
    """

    def __init__(self, bus, retry_attempts=3, retry_gap_ms=2):
        self._bus = bus
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_gap_ms = max(0, int(retry_gap_ms))
        self.retry_count_total = 0          # silent recoveries logged
        self.retry_exhaustion_count = 0     # exhausted attempts (final fail)

    def reset_counters(self):
        """Zero ``retry_count_total`` + ``retry_exhaustion_count``."""
        self.retry_count_total = 0
        self.retry_exhaustion_count = 0

    # ----- single-byte ops -------------------------------------------------

    def read_byte(self, addr, reg):
        """Read one byte from ``reg`` on slave ``addr`` with retry.

        Raises:
            RbAmpIOError: after ``retry_attempts`` consecutive NACKs.
        """
        last_exc = None
        for attempt in range(self._retry_attempts):
            try:
                value = self._bus.read_byte_data(addr, reg) & 0xFF
                if attempt > 0:
                    self.retry_count_total += attempt
                return value
            except OSError as exc:
                last_exc = exc
                if attempt + 1 < self._retry_attempts and self._retry_gap_ms:
                    time.sleep(self._retry_gap_ms / 1000.0)
        self.retry_count_total += self._retry_attempts - 1
        self.retry_exhaustion_count += 1
        raise RbAmpIOError(
            "read_byte addr=0x{:02X} reg=0x{:02X} failed after {} attempts: {}".format(
                addr, reg, self._retry_attempts, last_exc
            )
        ) from last_exc

    def write_byte(self, addr, reg, val):
        """Write one byte ``val`` to ``reg`` on slave ``addr`` with retry.

        Raises:
            RbAmpIOError: after ``retry_attempts`` consecutive NACKs.
        """
        last_exc = None
        for attempt in range(self._retry_attempts):
            try:
                self._bus.write_byte_data(addr, reg, val & 0xFF)
                if attempt > 0:
                    self.retry_count_total += attempt
                return
            except OSError as exc:
                last_exc = exc
                if attempt + 1 < self._retry_attempts and self._retry_gap_ms:
                    time.sleep(self._retry_gap_ms / 1000.0)
        self.retry_count_total += self._retry_attempts - 1
        self.retry_exhaustion_count += 1
        raise RbAmpIOError(
            "write_byte addr=0x{:02X} reg=0x{:02X} val=0x{:02X} failed after {} attempts: {}".format(
                addr, reg, val, self._retry_attempts, last_exc
            )
        ) from last_exc

    # ----- NACK probe (used by variant detection) --------------------------

    def register_acks(self, addr, reg):
        """Return True if reading ``reg`` on ``addr`` is ACKed.

        Returns False on NACK without raising — used by variant-detect logic.
        """
        try:
            self._bus.read_byte_data(addr, reg)
            return True
        except OSError:
            return False

    # ----- I2C General-Call broadcast --------------------------------------

    def broadcast(self, payload):
        """Issue an I2C General-Call write of ``payload`` (a 2-byte sequence).

        For rbAmp this is always ``bytes([0x01, 0x27])`` — REG_COMMAND +
        CMD_LATCH_PERIOD.

        Returns:
            bool: True if the bus accepted the write.
        """
        try:
            # smbus2: write_i2c_block_data(addr, cmd, [data...]).
            # For general-call we pass address 0x00, first byte as cmd, rest as block.
            first = payload[0]
            rest = list(payload[1:])
            self._bus.write_i2c_block_data(0x00, first, rest)
            return True
        except OSError:
            return False

    # ----- timing abstraction (CPython monotonic clock) --------------------

    @staticmethod
    def now_ms():
        """Return a monotonic timestamp in ms (CPython ``time.monotonic``)."""
        return int(time.monotonic() * 1000.0)

    @staticmethod
    def ms_diff(later, earlier):
        """Difference ``later - earlier`` in ms (no wrap handling needed on CPython)."""
        return later - earlier

    @staticmethod
    def sleep_ms(ms):
        """Sleep for ``ms`` milliseconds."""
        time.sleep(ms / 1000.0)
