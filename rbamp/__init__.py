"""
rbamp — unified Python client for the rbAmp I2C AC sensor / dimmer module.

The same package runs on both MicroPython (via ``machine.I2C``) and CPython
(via ``smbus2``). The backend is selected automatically based on the bus
object you pass to :class:`RbAmp` — no platform flag or import switch needed.

Quick start (CPython on Raspberry Pi)::

    from smbus2 import SMBus
    from rbamp import RbAmp

    with SMBus(1) as bus:
        with RbAmp(bus, 0x50) as dev:
            print(dev.voltage, "V")
            snap = dev.read_period_snapshot()
            print(dev.energy.wh(0), "Wh")

Quick start (MicroPython on ESP32)::

    from machine import I2C, Pin
    from rbamp import RbAmp

    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    with RbAmp(i2c, 0x50) as dev:
        print(dev.voltage, "V")
        snap = dev.read_period_snapshot()
        print(dev.energy.wh(0), "Wh")

The public API surface conforms to ``libs/spec/SPEC.md`` §12 — every
per-platform rbAmp library (Arduino, STM32 HAL, ESP-IDF, Python) exposes
the same operations under platform-idiomatic names.
"""

# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------
from ._rbamp_core import RbAmp
from ._energy import RbAmpEnergy
from ._snapshot import (
    RbAmpSnapshot,
    RbAmpPeriodSnapshot,
    TOPOLOGY_SINGLE,
    TOPOLOGY_SPLIT_PHASE,
    TOPOLOGY_THREE_PHASE,
    topology_name,
    RbAmpSensorClass,
    # Exception hierarchy
    RbAmpError,
    RbAmpIOError,
    RbAmpTimeoutError,
    RbAmpNotReadyError,
    RbAmpStaleError,
    RbAmpParamError,
    RbAmpModeError,
    RbAmpVersionError,
)
from ._registers import (
    RBAMP_REG_SCHEMA_CRC32,
    RBAMP_PROTOCOL_VERSION,
    REGISTERS,
    COMMANDS,
)

#: Package version (PEP 440). Library SemVer is independent of protocol SemVer.
__version__ = "1.1.0"

#: Re-exported for convenience — protocol version this library was built for.
__protocol_version__ = RBAMP_PROTOCOL_VERSION

__all__ = (
    "RbAmp",
    "RbAmpEnergy",
    "RbAmpSnapshot",
    "RbAmpPeriodSnapshot",
    "TOPOLOGY_SINGLE",
    "TOPOLOGY_SPLIT_PHASE",
    "TOPOLOGY_THREE_PHASE",
    "topology_name",
    "RbAmpSensorClass",
    "RbAmpError",
    "RbAmpIOError",
    "RbAmpTimeoutError",
    "RbAmpNotReadyError",
    "RbAmpStaleError",
    "RbAmpParamError",
    "RbAmpModeError",
    "RbAmpVersionError",
    "RBAMP_REG_SCHEMA_CRC32",
    "RBAMP_PROTOCOL_VERSION",
    "REGISTERS",
    "COMMANDS",
    "__version__",
    "__protocol_version__",
)
