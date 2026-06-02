"""
Example 6 (MicroPython) — battery-friendly deep-sleep logger.

Single-shot pattern: boot, latch, snapshot, log, deep-sleep N minutes.

Persists per-channel Wh across deep-sleep cycles via ESP32 RTC slow-RAM
(``esp32.RMT.RTC_DATA_ATTR`` equivalent: a regular module-level variable
saved/restored manually since MicroPython does not expose RTC_DATA_ATTR).

We use the simpler approach: store a small JSON file in flash.

ESP32 power-down current during deep sleep: ~10 µA.

Run:
    mpremote run 06_deep_sleep.py
"""

import json
import time

import machine  # type: ignore[import-not-found]
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError


STATE_FILE = "/rbamp_state.json"
SLEEP_MS = 5 * 60 * 1000  # 5 minutes


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"wh": [0.0, 0.0, 0.0], "boots": 0}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError as exc:
        print("save_state failed:", exc)


def main():
    state = load_state()
    state["boots"] += 1
    print("boot #{}".format(state["boots"]))

    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    try:
        with RbAmp(i2c, 0x50) as dev:
            snap = dev.read_period_snapshot()
            for ch in range(dev.channels):
                dwh = snap.avg_p[ch] * snap.master_dt_ms / 3_600_000.0
                state["wh"][ch] += dwh
                print("ch{} P={:6.1f}W  Wh={:9.4f}".format(ch, snap.avg_p[ch], state["wh"][ch]))
    except RbAmpStaleError:
        print("STALE — skipping integration this cycle")
    except OSError as exc:
        print("rbAmp error:", exc)

    save_state(state)

    print("sleeping {} ms".format(SLEEP_MS))
    time.sleep_ms(50)  # allow Serial to flush
    machine.deepsleep(SLEEP_MS)
    # never returns; ESP32 reboots after the timer.


if __name__ == "__main__":
    main()
