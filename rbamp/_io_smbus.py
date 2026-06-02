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
    """Thin wrapper over ``smbus2.SMBus``.

    The wrapper exists so :mod:`_rbamp_core` can stay platform-agnostic.
    All methods take the slave address explicitly so the same backend can
    serve multiple :class:`RbAmp` instances on the same bus.

    Args:
        bus: An open ``smbus2.SMBus`` instance (or any duck-typed object
             exposing ``read_byte_data``, ``write_byte_data`` and
             ``write_i2c_block_data``).
    """

    def __init__(self, bus):
        self._bus = bus

    # ----- single-byte ops -------------------------------------------------

    def read_byte(self, addr, reg):
        """Read one byte from ``reg`` on slave ``addr``.

        Raises:
            RbAmpIOError: on NACK or transport failure.
        """
        try:
            return self._bus.read_byte_data(addr, reg) & 0xFF
        except OSError as exc:
            raise RbAmpIOError(
                "read_byte addr=0x{:02X} reg=0x{:02X} failed: {}".format(addr, reg, exc)
            ) from exc

    def write_byte(self, addr, reg, val):
        """Write one byte ``val`` to ``reg`` on slave ``addr``.

        Raises:
            RbAmpIOError: on NACK or transport failure.
        """
        try:
            self._bus.write_byte_data(addr, reg, val & 0xFF)
        except OSError as exc:
            raise RbAmpIOError(
                "write_byte addr=0x{:02X} reg=0x{:02X} val=0x{:02X} failed: {}".format(
                    addr, reg, val, exc
                )
            ) from exc

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
