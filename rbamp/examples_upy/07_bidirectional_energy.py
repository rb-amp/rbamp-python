# SPDX-License-Identifier: MIT
"""
Example 7 (MicroPython) — bidirectional energy accounting with ASCII bar chart.

Tracks per-channel **import** (P > 0, consumption) and **export** (P < 0,
generation back to grid) Wh separately. Useful when the rbAmp is wired to
a circuit with bidirectional flow: PV inverter return, battery storage,
EV V2G, three-phase reverse-flow on one leg.

Hardware:
    Any rbAmp variant. For a clean demo: install on a circuit with both
    consumption (lamp/heater) and a source (PV / battery bridge / signal
    generator with phase reversal). Without a real bidirectional load,
    the export bar will stay empty — that's expected.

Wiring (default ESP32 I2C(0) @ 50 kHz per SPEC §B.5):
    rbAmp SDA -> GPIO21
    rbAmp SCL -> GPIO22

Install + run:
    # one-time package deploy:
    mpremote cp libs/python/rbamp/*.py :rbamp/
    mpremote run libs/python/rbamp/examples_upy/07_bidirectional_energy.py

Expected output (after 5 minutes on a fluctuating load):
    cyc  1   import: 0.012Wh import_bar:|=                |
                    export: 0.000Wh export_bar:|                  |
    cyc  2   import: 0.029Wh import_bar:|====             |
                    export: 0.005Wh export_bar:|=                 |
    ...

See also:
    - README.md (examples table)
    - SPEC.md §7 (period state machine)
    - examples_cpython/05_bidirectional_energy.py (CPython twin)
"""
import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError
from rbamp._io_micropython import MachineI2CBackend


BAR_WIDTH = 18           # ASCII bar width in characters
BAR_FULL_WH = 0.500      # scale: Wh value that fills the bar
INTERVAL_S = 10.0
CYCLES = 6               # 6 cycles × 10 s = ~60 s smoke; set None for forever


def bar(value, full=BAR_FULL_WH, width=BAR_WIDTH):
    frac = min(1.0, max(0.0, value / full))
    n = int(round(frac * width))
    return "|" + "=" * n + " " * (width - n) + "|"


def main():
    bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
    backend = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=5)
    dev = RbAmp(backend, 0x50)
    dev.begin()
    print("topology={} channels={} has_voltage={}".format(
        dev.topology, dev.channels, dev.has_voltage_hw))

    # Track signed Wh per direction
    import_wh = [0.0] * dev.channels
    export_wh = [0.0] * dev.channels

    cyc = 0
    while CYCLES is None or cyc < CYCLES:
        cyc += 1
        time.sleep(INTERVAL_S)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            print("cyc {:3d}  STALE — skip".format(cyc))
            continue
        for ch in range(dev.channels):
            p = snap.avg_p[ch]
            dwh = p * snap.master_dt_ms / 3_600_000.0
            if dwh >= 0:
                import_wh[ch] += dwh
            else:
                export_wh[ch] += -dwh
            print(
                "cyc {:3d} ch{}  P={:+7.2f}W  import: {:6.3f}Wh {}\n"
                "              export: {:6.3f}Wh {}".format(
                    cyc, ch, p,
                    import_wh[ch], bar(import_wh[ch]),
                    export_wh[ch], bar(export_wh[ch])
                )
            )

    print("\nFINAL  retry_exhaustion={}  sanity_reject={}".format(
        backend.retry_exhaustion_count, dev.sanity_reject_count))


if __name__ == "__main__":
    main()
