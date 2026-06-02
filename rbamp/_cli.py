"""
Command-line interface — installed as the ``rbamp`` shell command on CPython.

Subcommands:
    read       — one-shot or polling read of U / I / P / PF / Wh
    period     — issue CMD_LATCH_PERIOD and print the snapshot
    scan       — I2C scan to find rbAmp devices on the bus
    info       — variant / firmware / topology
    address    — change the slave address (factory-provisioning only)

Examples:
    rbamp --bus 1 --addr 0x50 read
    rbamp --bus 1 --addr 0x50 read --watch 5
    rbamp --bus 1 scan
    rbamp --bus 1 --addr 0x50 address 0x51
"""

import argparse
import sys
import time

from . import (
    RbAmp,
    RbAmpError,
    RbAmpStaleError,
    topology_name,
    __version__,
)


def _open_bus(bus_number):
    """Open an smbus2.SMBus on the given bus number."""
    try:
        from smbus2 import SMBus  # type: ignore[import-not-found]
    except ImportError:
        sys.exit("error: smbus2 not installed (pip install smbus2)")
    return SMBus(bus_number)


def _cmd_read(args, dev):
    """Print a flat one-line summary of the device state."""
    def emit():
        s = dev.read_all()
        line = (
            f"U={s.voltage:6.1f}V f={s.frequency:4.1f}Hz   "
            f"I0={s.current[0]:5.2f}A P0={s.power[0]:7.1f}W PF0={s.power_factor[0]:+.2f}"
        )
        if s.channels >= 2:
            line += f"   I1={s.current[1]:5.2f}A P1={s.power[1]:7.1f}W"
        if s.channels >= 3:
            line += f"   I2={s.current[2]:5.2f}A P2={s.power[2]:7.1f}W"
        print(line)

    if args.watch:
        try:
            while True:
                emit()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        emit()


def _cmd_period(args, dev):
    """Issue CMD_LATCH_PERIOD, print the snapshot, optionally watch."""
    def emit():
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            print("STALE (period snapshot not fresh)")
            return
        print(
            f"avg_p={snap.avg_p[:dev.channels]} W   "
            f"max_p={snap.max_p:6.1f} W   "
            f"dt_master={snap.master_dt_ms} ms   "
            f"dt_dev={snap.latch_ms} ms   "
            f"Wh={[dev.energy.wh(ch) for ch in range(dev.channels)]}"
        )

    if args.watch:
        try:
            while True:
                emit()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        emit()


def _cmd_scan(args, _dev_unused):
    """I2C scan on the given bus — independent of any single device."""
    bus = _open_bus(args.bus)
    found = []
    print(f"scanning bus {args.bus} for rbAmp devices (range 0x08..0x77)...")
    for addr in range(0x08, 0x78):
        try:
            bus.read_byte_data(addr, 0x03)  # REG_VERSION
            found.append(addr)
        except OSError:
            pass
    bus.close()
    if not found:
        print("  no devices acknowledged.")
    else:
        for addr in found:
            print(f"  0x{addr:02X}  (likely rbAmp — ACKed REG_VERSION)")


def _cmd_info(_args, dev):
    """Print variant + firmware + topology."""
    print(f"address       0x{dev.address:02X}")
    print(f"firmware      0x{dev.firmware_version:02X}")
    print(f"topology      {topology_name(dev.topology)} ({dev.topology})")
    print(f"channels      {dev.channels}")
    print(f"voltage_hw    {'yes' if dev.has_voltage_hw else 'no'}")


def _cmd_address(args, dev):
    """Change the slave address — two-step prompt."""
    new_addr = int(args.new_addr, 0)
    print(f"current address: 0x{dev.address:02X}")
    print(f"new address:     0x{new_addr:02X}")
    print("device MUST be in factory provisioning mode (REG_MODE == 1).")
    if not args.yes:
        if input("proceed? [y/N] ").strip().lower() != "y":
            print("aborted.")
            return
    dev.prepare_address_change(new_addr)
    print("armed. committing in 1 second...")
    time.sleep(1.0)
    dev.commit_address_change()
    print(f"device now at 0x{dev.address:02X}")


def main(argv=None):
    """Console-script entry point — wired in pyproject.toml."""
    parser = argparse.ArgumentParser(
        prog="rbamp",
        description="Command-line interface for the rbAmp I2C AC sensor / dimmer module.",
    )
    parser.add_argument("--version", action="version", version=f"rbamp {__version__}")
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default 1)")
    parser.add_argument(
        "--addr",
        type=lambda s: int(s, 0),
        default=0x50,
        help="Slave address (default 0x50; accepts decimal or 0xNN hex)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="Read U / I / P / PF / freq")
    p_read.add_argument("--watch", type=float, default=0,
                        help="Poll every N seconds (default: one-shot)")

    p_period = sub.add_parser("period", help="Read period snapshot (avg_p, max_p, Wh)")
    p_period.add_argument("--watch", type=float, default=0,
                          help="Poll every N seconds (default: one-shot)")

    sub.add_parser("scan", help="Scan the bus for rbAmp devices")
    sub.add_parser("info", help="Print variant / firmware / topology")

    p_addr = sub.add_parser("address", help="Change the slave address (factory-provisioning only)")
    p_addr.add_argument("new_addr", help="New address (e.g. 0x51)")
    p_addr.add_argument("--yes", action="store_true",
                        help="Don't prompt; proceed immediately")

    args = parser.parse_args(argv)

    # `scan` does not need an RbAmp instance — it talks to the bus directly.
    if args.command == "scan":
        _cmd_scan(args, None)
        return 0

    bus = _open_bus(args.bus)
    try:
        with RbAmp(bus, args.addr) as dev:
            try:
                {
                    "read":    _cmd_read,
                    "period":  _cmd_period,
                    "info":    _cmd_info,
                    "address": _cmd_address,
                }[args.command](args, dev)
            except RbAmpError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    finally:
        bus.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
