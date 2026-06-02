"""
Example 1 (MicroPython) — smoke test on ESP32 / RP2040 / generic uPy.

Reads U / I / P / PF / freq once per second. Equivalent to the CPython
01_quick_read.py and Arduino 01_QuickRead.ino examples.

Wiring (ESP32 default I2C(0)):
    rbAmp SDA -> GPIO21
    rbAmp SCL -> GPIO22

Flash the library first:
    mpremote mip install github:rb-amp/rbamp-python
    # or copy files manually:
    mpremote cp rbamp/*.py :rbamp/

Then run:
    mpremote run 01_quick_read.py
"""

import time

from machine import I2C, Pin
from rbamp import RbAmp, topology_name


def main():
    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    with RbAmp(i2c, 0x50) as dev:
        print(
            "rbAmp @ 0x{:02X}  fw=0x{:02X}  topology={}  channels={}  voltage_hw={}".format(
                dev.address, dev.firmware_version,
                topology_name(dev.topology), dev.channels,
                "yes" if dev.has_voltage_hw else "no",
            )
        )
        while True:
            s = dev.read_all()
            line = "U={:6.1f}V f={:4.1f}Hz   ".format(s.voltage, s.frequency)
            for ch in range(s.channels):
                line += "I{}={:5.2f}A P{}={:7.1f}W PF{}={:+.2f}  ".format(
                    ch, s.current[ch], ch, s.power[ch], ch, s.power_factor[ch]
                )
            print(line)
            time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
