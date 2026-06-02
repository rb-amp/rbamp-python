"""
Example 5 — master-side bidirectional energy accounting.

Splits a signed real-power stream into separate consumption and export
accumulators. Useful for solar-with-battery setups where the energy supplier
bills imported and exported kWh differently.

The library's built-in ``dev.energy.wh(0)`` accumulator yields the NET
balance. This script keeps two additional double-precision counters for
gross consume and gross export by sampling the real-time power register at
200 ms cadence (matching the device's commit period).

Run:
    python 05_bidirectional_energy.py
"""

import time

from smbus2 import SMBus

from rbamp import RbAmp


def main(bus_no=1, addr=0x50):
    consume_wh = 0.0
    export_wh = 0.0
    with SMBus(bus_no) as bus:
        with RbAmp(bus, addr) as dev:
            last_t = time.monotonic()
            next_print = last_t + 1.0
            while True:
                time.sleep(0.2)
                now = time.monotonic()
                dt_s = now - last_t
                last_t = now

                p_w = dev.read_power(0)
                dwh = p_w * dt_s / 3600.0
                if p_w >= 0:
                    consume_wh += dwh
                else:
                    export_wh += -dwh

                if now >= next_print:
                    next_print = now + 1.0
                    print(
                        f"P={p_w:+8.2f}W   "
                        f"consume={consume_wh:9.4f}Wh   "
                        f"export={export_wh:9.4f}Wh   "
                        f"net={consume_wh - export_wh:+9.4f}Wh"
                    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
