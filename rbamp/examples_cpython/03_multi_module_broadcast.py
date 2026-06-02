"""
Example 3 — multi-module monitor synchronised via I2C General-Call broadcast.

Demonstrates:

* Multiple :class:`RbAmp` instances on one bus.
* :meth:`RbAmp.broadcast_latch` — one general-call write latches all modules.
* :meth:`RbAmp.read_period_snapshot` with ``skip_latch=True`` — read the
  already-latched snapshot per device.

Each module must have a unique I2C address; use ``rbamp address`` CLI or the
05_AddressChange example to reassign devices off the default 0x50.

Run:
    python 03_multi_module_broadcast.py
"""

import time

from smbus2 import SMBus

from rbamp import RbAmp, RbAmpStaleError


ADDRESSES = (0x50, 0x51, 0x52)
INTERVAL_S = 60


def main(bus_no=1):
    with SMBus(bus_no) as bus:
        modules = []
        for addr in ADDRESSES:
            dev = RbAmp(bus, addr)
            try:
                dev.begin()
            except OSError as exc:
                print(f"skip 0x{addr:02X}: {exc}")
                continue
            modules.append(dev)
            print(f"module 0x{addr:02X}: channels={dev.channels}")

        if not modules:
            print("no modules found")
            return

        while True:
            t_start = time.monotonic()

            # ONE general-call broadcast latches every module within microseconds.
            if not RbAmp.broadcast_latch(bus):
                print("broadcast LATCH failed; falling back to per-device latch")

            # Settle once for all modules.
            time.sleep(0.05)

            # Read each module's snapshot without re-latching.
            for dev in modules:
                try:
                    snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)
                except RbAmpStaleError:
                    print(f"  0x{dev.address:02X}  STALE")
                    continue
                p_str = " ".join(f"P{ch}={snap.avg_p[ch]:6.0f}W" for ch in range(dev.channels))
                wh_str = " ".join(f"Wh{ch}={dev.energy.wh(ch):.3f}" for ch in range(dev.channels))
                print(f"  0x{dev.address:02X}  dt={snap.master_dt_ms}ms  {p_str}   {wh_str}")
            print()

            elapsed = time.monotonic() - t_start
            if elapsed < INTERVAL_S:
                time.sleep(INTERVAL_S - elapsed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
