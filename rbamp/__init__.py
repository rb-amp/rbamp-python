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
    TOPOLOGY_FIVE_CH,
    TOPOLOGY_SEVEN_CH,
    topology_name,
    RbAmpSensorClass,
    RbAmpWindowField,
    # Exception hierarchy
    RbAmpError,
    RbAmpIOError,
    RbAmpTimeoutError,
    RbAmpNotReadyError,
    RbAmpStaleError,
    RbAmpParamError,
    RbAmpModeError,
    RbAmpVersionError,
    RbAmpNotSupportedError,
)
from ._std import (
    RbAmpStdEnergy,
    RbAmpStdVoltage,
    RbAmpStdChannel,
    RbAmpStdConfig,
    RbAmpStdHealth,
    energy_delta as std_energy_delta,
    phase_seq_name,
    PHASE_SEQ_NA,
    PHASE_SEQ_FORWARD,
    PHASE_SEQ_REVERSE,
    PHASE_SEQ_ABNORMAL,
)
from ._fleet import (
    RbAmpFleet,
    RbAmpFleetPoll,
    RbAmpFleetSync,
    RBAMP_FLEET_MAX_MODULES,
)
from ._registers_v2 import (
    RBAMP_REG_SCHEMA_CRC32_V2 as RBAMP_REG_SCHEMA_CRC32,
    RBAMP_PROTOCOL_VERSION_V2 as RBAMP_PROTOCOL_VERSION,
    REGISTERS,
    COMMANDS,
)
from ._sct_rating import (
    rating_correction as sct_rating_correction,
    PRESET_G as SCT_PRESET_G,
    RATING_C as SCT_RATING_C,
)

#: Package version (PEP 440). Library SemVer is independent of protocol SemVer.
__version__ = "1.6.0"

#: Re-exported for convenience — protocol version this library was built for.
__protocol_version__ = RBAMP_PROTOCOL_VERSION

__all__ = (
    "RbAmp",
    "RbAmpEnergy",
    "RbAmpFleet",
    "RbAmpFleetPoll",
    "RbAmpFleetSync",
    "RBAMP_FLEET_MAX_MODULES",
    "RbAmpSnapshot",
    "RbAmpPeriodSnapshot",
    "TOPOLOGY_SINGLE",
    "TOPOLOGY_SPLIT_PHASE",
    "TOPOLOGY_THREE_PHASE",
    "TOPOLOGY_FIVE_CH",
    "TOPOLOGY_SEVEN_CH",
    "topology_name",
    "RbAmpSensorClass",
    "RbAmpWindowField",
    "RbAmpError",
    "RbAmpIOError",
    "RbAmpTimeoutError",
    "RbAmpNotReadyError",
    "RbAmpStaleError",
    "RbAmpParamError",
    "RbAmpModeError",
    "RbAmpVersionError",
    "RbAmpNotSupportedError",
    "RbAmpStdEnergy",
    "RbAmpStdVoltage",
    "RbAmpStdChannel",
    "RbAmpStdConfig",
    "RbAmpStdHealth",
    "std_energy_delta",
    "sct_rating_correction",
    "SCT_PRESET_G",
    "SCT_RATING_C",
    "phase_seq_name",
    "PHASE_SEQ_NA",
    "PHASE_SEQ_FORWARD",
    "PHASE_SEQ_REVERSE",
    "PHASE_SEQ_ABNORMAL",
    "RBAMP_REG_SCHEMA_CRC32",
    "RBAMP_PROTOCOL_VERSION",
    "REGISTERS",
    "COMMANDS",
    "__version__",
    "__protocol_version__",
)
