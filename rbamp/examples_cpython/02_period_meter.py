"""
Example 2 — 60-second period energy meter, console output.

Demonstrates:

* ``dev.read_period_snapshot()`` — the recommended one-shot period flow.
* Library-owned energy accumulator (``dev.energy.wh(0)``).
* Stale snapshot handling — ``RbAmpStaleError`` is caught and logged.

Run:
    python 02_period_meter.py
"""

import time

from smbus2 import SMBus

from rbamp import RbAmp, RbAmpStaleError


def main(bus_no=1, addr=0x50, interval_s=60):
    with SMBus(bus_no) as bus:
        with RbAmp(bus, addr) as dev:
            print(f"polling every {interval_s} s; Ctrl-C to stop")
            print(
                f"{'avg_p0':>10}  {'avg_p1':>10}  {'avg_p2':>10}  "
                f"{'max_p':>10}  {'Wh0':>12}  {'dt_ms':>8}"
            )
            ok = 0
            stale = 0
            while True:
                t_start = time.monotonic()
                try:
                    snap = dev.read_period_snapshot()
                    ok += 1
                except RbAmpStaleError:
                    stale += 1
                    print(f"STALE (ok={ok} stale={stale})")
                else:
                    print(
                        f"{snap.avg_p[0]:>10.2f}  "
                        f"{snap.avg_p[1]:>10.2f}  "
                        f"{snap.avg_p[2]:>10.2f}  "
                        f"{snap.max_p:>10.2f}  "
                        f"{dev.energy.wh(0):>12.4f}  "
                        f"{snap.master_dt_ms:>8d}"
                    )
                # Sleep for the rest of the interval — accounts for the time
                # we just spent polling so the cadence stays accurate.
                elapsed = time.monotonic() - t_start
                if elapsed < interval_s:
                    time.sleep(interval_s - elapsed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
