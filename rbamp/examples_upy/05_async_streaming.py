"""
Example 5 (MicroPython) — async streaming with ``uasyncio``.

Demonstrates the optional async API: :meth:`RbAmp.stream_period` is an
async generator that yields snapshots at a fixed interval, leaving the
event loop free for other tasks (e.g. WiFi, web server, sensors).

The same code runs under CPython ``asyncio`` if you replace
``machine.I2C`` with ``smbus2.SMBus`` — see CPython example 04.

Requires MicroPython v1.21+ (async generator support).

Run:
    mpremote run 05_async_streaming.py
"""

import uasyncio as asyncio  # type: ignore[import-not-found]

from machine import I2C, Pin
from rbamp import RbAmp


async def power_streamer(dev):
    """Print per-channel power and Wh every 30 s."""
    async for snap in dev.stream_period(interval_s=30):
        line = "P:"
        for ch in range(dev.channels):
            line += " ch{}={:7.1f}W (Wh={:.3f})".format(ch, snap.avg_p[ch], dev.energy.wh(ch))
        print(line)


async def heartbeat():
    """Print a heartbeat every 5 s to prove the loop is still alive."""
    n = 0
    while True:
        await asyncio.sleep(5)
        n += 1
        print(f"heartbeat #{n}")


async def main():
    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    with RbAmp(i2c, 0x50) as dev:
        await asyncio.gather(power_streamer(dev), heartbeat())


asyncio.run(main())
