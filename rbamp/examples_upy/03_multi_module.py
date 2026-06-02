"""
Example 3 (MicroPython) — multi-module monitor via I2C General-Call.

Demonstrates :meth:`RbAmp.broadcast_latch` synchronising several modules at
once, then per-device :meth:`read_period_snapshot` with ``skip_latch=True``.

Each module must have a unique I2C address. See ``05_address_change.py`` to
assign new addresses while the device is in factory provisioning mode.

Run:
    mpremote run 03_multi_module.py
"""

import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError


ADDRESSES = (0x50, 0x51, 0x52)


def main():
    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    modules = []
    for addr in ADDRESSES:
        dev = RbAmp(i2c, addr)
        try:
            dev.begin()
        except OSError as exc:
            print("skip 0x{:02X}: {}".format(addr, exc))
            continue
        modules.append(dev)
        print("module 0x{:02X}: channels={}".format(addr, dev.channels))

    if not modules:
        print("no modules found")
        return

    while True:
        t0 = time.ticks_ms()
        if not RbAmp.broadcast_latch(i2c):
            print("broadcast LATCH failed")
        time.sleep_ms(50)
        for dev in modules:
            try:
                snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)
            except RbAmpStaleError:
                print("  0x{:02X}  STALE".format(dev.address))
                continue
            p_str = " ".join(
                "P{}={:5.0f}W".format(ch, snap.avg_p[ch]) for ch in range(dev.channels)
            )
            print("  0x{:02X}  dt={}ms  {}  Wh0={:.3f}".format(
                dev.address, snap.master_dt_ms, p_str, dev.energy.wh(0)
            ))
        print()
        time.sleep_ms(max(0, 60_000 - time.ticks_diff(time.ticks_ms(), t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
