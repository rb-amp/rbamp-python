"""Smoke tests for the rbamp package — imports and re-exports."""

import pytest

import rbamp


def test_version_string():
    assert isinstance(rbamp.__version__, str)
    parts = rbamp.__version__.split(".")
    assert len(parts) == 3
    for p in parts:
        int(p)  # all integer-parseable


def test_protocol_version_tuple():
    assert rbamp.RBAMP_PROTOCOL_VERSION == (1, 3)
    assert rbamp.__protocol_version__ == (1, 3)


def test_schema_crc_present():
    assert isinstance(rbamp.RBAMP_REG_SCHEMA_CRC32, int)
    assert 0 <= rbamp.RBAMP_REG_SCHEMA_CRC32 <= 0xFFFFFFFF


def test_registers_table_nonempty():
    assert len(rbamp.REGISTERS) > 50
    sample = rbamp.REGISTERS[0]
    for key in ("name", "addr", "size", "type", "access"):
        assert key in sample


def test_commands_table_nonempty():
    assert len(rbamp.COMMANDS) >= 10


def test_exception_hierarchy():
    # All rbamp exceptions inherit from OSError so existing OSError handlers
    # continue to catch them.
    for cls in (
        rbamp.RbAmpError,
        rbamp.RbAmpIOError,
        rbamp.RbAmpTimeoutError,
        rbamp.RbAmpStaleError,
        rbamp.RbAmpParamError,
        rbamp.RbAmpModeError,
        rbamp.RbAmpVersionError,
    ):
        assert issubclass(cls, OSError)
    # Param errors also inherit from ValueError for ergonomic catch blocks.
    assert issubclass(rbamp.RbAmpParamError, ValueError)


def test_topology_constants():
    assert rbamp.TOPOLOGY_SINGLE == 1
    assert rbamp.TOPOLOGY_SPLIT_PHASE == 2
    assert rbamp.TOPOLOGY_THREE_PHASE == 3
    assert rbamp.topology_name(2) == "SPLIT_PHASE"
    assert rbamp.topology_name(99) == "UNKNOWN"


def test_cannot_construct_with_random_object():
    with pytest.raises(rbamp.RbAmpParamError):
        rbamp.RbAmp(object(), 0x50)
