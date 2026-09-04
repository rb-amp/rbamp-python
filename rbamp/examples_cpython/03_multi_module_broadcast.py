# SPDX-License-Identifier: MIT
"""
Example 3 (CPython) — multi-module fleet monitor synchronised via v1.3
General-Call broadcast-latch (``RbAmpFleet``).

Demonstrates the v1.3+ coherent-latch pattern:

* :class:`RbAmpFleet` manages N :class:`RbAmp` handles on one bus.
* :meth:`RbAmpFleet.enable_gc_all` — one-time write per module, persisted +
  reset (bit0 of ``REG_FLEET_CONFIG``); after this every module accepts
  broadcast latches.
* :meth:`RbAmpFleet.gc_latch` — one 5-byte General-Call frame
  (``A5 27 group tick_lo tick_hi``) latches every enabled module on the
  same clock edge.
* :meth:`RbAmpFleet.check_sync` — read ``REG_GC_TICK`` per module and
  report which ones caught the tick.
* :meth:`RbAmp.read_period_snapshot` with ``skip_latch=True`` — read the
  already-latched values with no per-module latch and no per-module settle.

Every module needs a unique I²C address. Assign them once with the
:meth:`RbAmp.prepare_address_change` / :meth:`RbAmp.commit_address_change`
flow (persists to flash) so they coexist on the bus. This example assumes
the addresses are already 0x50 / 0x51 / 0x52.

Run:
    python 03_multi_module_broadcast.py
"""

import time

from smbus2 import SMBus

from rbamp import RbAmp, RbAmpFleet, RbAmpError, RbAmpStaleError


ADDRESSES = (0x50, 0x51, 0x52)
INTERVAL_S = 60
GC_GROUP = 0     # 0 = all-call (every GC-enabled module latches)


def main(bus_no=1):
    with SMBus(bus_no) as bus:
        fleet = RbAmpFleet(bus)
        for addr in ADDRESSES:
            try:
                dev = RbAmp(bus, addr)
                dev.begin()
            except RbAmpError as exc:
                print(f"skip 0x{addr:02X}: {exc}")
                continue
            fleet.add(dev)
            print(f"module 0x{addr:02X}: channels={dev.channels}")

        if len(fleet) == 0:
            print("no modules found")
            return

        enabled = fleet.enable_gc_all(group=GC_GROUP)
        print(f"GC enabled on {enabled}/{len(fleet)} module(s), group={GC_GROUP}\n")

        while True:
            t_start = time.monotonic()

            # ONE 5-byte GC frame latches every enabled module in-sync.
            # gc_latch auto-increments an internal tick counter and returns
            # the tick that was broadcast; sleeps the settle window (50 ms).
            tick = fleet.gc_latch(group=GC_GROUP)
            sync = fleet.check_sync(expected_tick=tick)

            # Report and skip anyone who missed the tick (GC disabled,
            # different group, or bus glitch). Failure-isolation policy.
            for dev, s in zip(fleet, sync):
                if not s.in_sync:
                    print(f"  0x{s.addr:02X}  MISSED gc tick "
                          f"(got=0x{s.gc_tick:04X} expected=0x{tick:04X})")
                    continue
                try:
                    snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)
                except RbAmpStaleError:
                    print(f"  0x{dev.address:02X}  STALE")
                    continue
                p_str = " ".join(
                    f"P{ch}={snap.avg_p[ch]:6.0f}W" for ch in range(dev.channels)
                )
                wh_str = " ".join(
                    f"Wh{ch}={dev.energy.wh(ch):.3f}" for ch in range(dev.channels)
                )
                print(f"  0x{dev.address:02X}  dt={snap.master_dt_ms}ms  "
                      f"{p_str}   {wh_str}")

            in_sync = sum(1 for s in sync if s.in_sync)
            print(f"  tick=0x{tick:04X}  in_sync={in_sync}/{len(fleet)}  "
                  f"fleet totE={fleet.total_energy_wh():.4f}Wh\n")

            elapsed = time.monotonic() - t_start
            if elapsed < INTERVAL_S:
                time.sleep(INTERVAL_S - elapsed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
