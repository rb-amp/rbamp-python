"""
Example 2 (MicroPython) — 60-second period energy meter on a 128x64 SSD1306.

Demonstrates:

* ``dev.read_period_snapshot()`` — the recommended one-shot period flow.
* Library-owned energy accumulator via ``dev.energy.wh(ch)``.
* Sharing the I2C bus between the rbAmp (0x50) and SSD1306 OLED (0x3C).

Dependencies:
    mpremote mip install ssd1306

Wiring (ESP32):
    rbAmp + OLED SDA -> GPIO21
    rbAmp + OLED SCL -> GPIO22

Run:
    mpremote run 02_oled_period.py
"""

import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError
import ssd1306  # type: ignore[import-not-found]


def main():
    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    oled = ssd1306.SSD1306_I2C(128, 64, i2c)
    oled.fill(0)
    oled.text("rbAmp", 0, 0)
    oled.text("booting...", 0, 16)
    oled.show()

    with RbAmp(i2c, 0x50) as dev:
        ok = 0
        stale = 0
        while True:
            t0 = time.ticks_ms()
            try:
                snap = dev.read_period_snapshot()
                ok += 1
                p_w = snap.avg_p[0]
                wh = dev.energy.wh(0)
            except RbAmpStaleError:
                stale += 1
                p_w = float("nan")
                wh = dev.energy.wh(0)

            oled.fill(0)
            oled.text("rbAmp Energy", 0, 0)
            oled.text("P0: {:6.1f} W".format(p_w), 0, 16)
            oled.text("Wh: {:8.3f}".format(wh), 0, 28)
            oled.text("ok:  {}".format(ok), 0, 44)
            oled.text("bad: {}".format(stale), 0, 54)
            oled.show()

            # Sleep until the next minute boundary
            elapsed_ms = time.ticks_diff(time.ticks_ms(), t0)
            remaining = 60_000 - elapsed_ms
            if remaining > 0:
                time.sleep_ms(remaining)


if __name__ == "__main__":
    main()
