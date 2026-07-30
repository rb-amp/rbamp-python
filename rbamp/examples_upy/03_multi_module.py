"""
Example 3 (MicroPython) — multi-module fleet monitor via v1.3 General-Call
broadcast-latch (``RbAmpFleet``).

Demonstrates the v1.3+ coherent-latch pattern:

* ``RbAmpFleet.enable_gc_all`` — one-time write per module, persisted +
  reset (bit0 of ``REG_FLEET_CONFIG``).
* ``RbAmpFleet.gc_latch`` — one 5-byte General-Call frame latches every
  enabled module in-sync.
* ``RbAmpFleet.check_sync`` — read ``REG_GC_TICK`` per module and report
  which ones caught the tick.
* ``dev.read_period_snapshot(skip_latch=True)`` reads the already-latched
  values without re-latching or per-module settle.

Every module needs a unique I²C address. Assign them once with
``prepare_address_change`` / ``commit_address_change`` (persists to flash)
so they coexist on the bus.

MicroPython note: on ESP32 the ``machine.I2C`` driver wraps ESP-IDF's
``i2c_master``. Use ``freq=50_000`` there (matches SPEC §B.5) — 100 kHz
triggers intermittent NACKs at the driver level. On non-ESP32 ports
(RP2040 / STM32) 100 kHz is fine.

Run:
    mpremote run 03_multi_module.py
"""

import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpFleet, RbAmpError, RbAmpStaleError


ADDRESSES = (0x50, 0x51, 0x52)
GC_GROUP = 0


def main():
    # ESP32 wants 50 kHz; RP2040/STM32 can run 100 kHz.
    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)

    fleet = RbAmpFleet(i2c)
    for addr in ADDRESSES:
        try:
            dev = RbAmp(i2c, addr)
            dev.begin()
        except RbAmpError as exc:
            print("skip 0x{:02X}: {}".format(addr, exc))
            continue
        fleet.add(dev)
        print("module 0x{:02X}: channels={}".format(addr, dev.channels))

    if len(fleet) == 0:
        print("no modules found")
        return

    enabled = fleet.enable_gc_all(group=GC_GROUP)
    print("GC enabled on {}/{} module(s), group={}\n".format(
        enabled, len(fleet), GC_GROUP))

    while True:
        t0 = time.ticks_ms()

        tick = fleet.gc_latch(group=GC_GROUP)
        sync = fleet.check_sync(expected_tick=tick)

        for dev, s in zip(fleet, sync):
            if not s.in_sync:
                print("  0x{:02X}  MISSED gc tick (got=0x{:04X} expected=0x{:04X})".format(
                    s.addr, s.gc_tick, tick))
                continue
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

        in_sync = sum(1 for s in sync if s.in_sync)
        print("  tick=0x{:04X}  in_sync={}/{}  fleet totE={:.4f}Wh\n".format(
            tick, in_sync, len(fleet), fleet.total_energy_wh()))

        time.sleep_ms(max(0, 60_000 - time.ticks_diff(time.ticks_ms(), t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
