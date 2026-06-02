"""
Example 1 — smoke test on Raspberry Pi / Linux SBC.

Reads U / I / P / PF / freq once per second. Equivalent to the Arduino
``01_QuickRead.ino`` example.

Wiring (Raspberry Pi):
    rbAmp SDA -> GPIO2  (Pi pin 3)
    rbAmp SCL -> GPIO3  (Pi pin 5)
    rbAmp GND -> any GND pin
    rbAmp 3V3 -> 3V3   (pin 1 or 17)

Enable I2C: ``sudo raspi-config`` -> Interface Options -> I2C.

Run:
    pip install rbamp
    python 01_quick_read.py
"""

import time

from smbus2 import SMBus

from rbamp import RbAmp, topology_name


def main(bus_no=1, addr=0x50):
    with SMBus(bus_no) as bus:
        with RbAmp(bus, addr) as dev:
            print(
                f"rbAmp @ 0x{dev.address:02X}  fw=0x{dev.firmware_version:02X}  "
                f"topology={topology_name(dev.topology)}  channels={dev.channels}  "
                f"voltage_hw={'yes' if dev.has_voltage_hw else 'no'}"
            )
            while True:
                s = dev.read_all()
                line = f"U={s.voltage:6.1f}V f={s.frequency:4.1f}Hz   "
                for ch in range(s.channels):
                    line += (
                        f"I{ch}={s.current[ch]:5.2f}A "
                        f"P{ch}={s.power[ch]:7.1f}W "
                        f"PF{ch}={s.power_factor[ch]:+.2f}  "
                    )
                print(line)
                time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
