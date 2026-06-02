# SPDX-License-Identifier: MIT
"""
Example 9 (MicroPython) — power-event detector + persistent ring log.

Watches power on a configured channel; when |P| crosses a threshold either
upward or downward by ``EVENT_DELTA_W`` between consecutive cycles, appends
an event line to a rotating log file in SPIFFS / LittleFS.

Useful as a forensics aid: appliance switch-on / -off, motor stall, sudden
load drop, etc. Survives reboots because the log is on flash, not RAM.

Hardware:
    Any rbAmp variant. Wire some switchable load (kettle, work-light) to
    channel 0 so you can trigger events manually.

Wiring (default ESP32 I2C(0) @ 50 kHz per SPEC §B.5):
    rbAmp SDA -> GPIO21
    rbAmp SCL -> GPIO22

Storage:
    Log file at ``/rbamp_events.log``. Rotated every ``ROTATE_LINES``
    appends (default 200) to avoid runaway flash growth. Tail with:
        mpremote cp :/rbamp_events.log .
        type rbamp_events.log

Install + run:
    mpremote cp libs/python/rbamp/*.py :rbamp/
    mpremote run libs/python/rbamp/examples_upy/09_event_detection_logger.py

Expected output:
    [init] watching ch0 threshold delta=30W
    [cyc 1] P=  3.1W
    [cyc 2] P=  4.5W
    [cyc 3] P= 87.2W  EVENT_UP +82.7W -> logged
    [cyc 4] P= 92.4W
    ...

See also:
    - examples_cpython/08_rotating_file_logger.py (CPython logging.handlers twin)
"""
import os
import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError
from rbamp._io_micropython import MachineI2CBackend


WATCH_CH = 0
EVENT_DELTA_W = 30.0     # min |ΔP| between cycles to log
LOG_PATH = "/rbamp_events.log"
ROTATE_LINES = 200
INTERVAL_S = 5.0
CYCLES = 12              # 12 × 5 s = 1 min smoke; set None for forever


def _count_lines():
    try:
        n = 0
        with open(LOG_PATH, "r") as f:
            for _ in f:
                n += 1
        return n
    except OSError:
        return 0


def _rotate_if_needed(lines):
    if lines < ROTATE_LINES:
        return
    backup = LOG_PATH + ".1"
    try:
        os.remove(backup)
    except OSError:
        pass
    try:
        os.rename(LOG_PATH, backup)
        print("[log] rotated to", backup)
    except OSError as exc:
        print("[log] rotate err:", exc)


def _append(line):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line)
            f.write("\n")
    except OSError as exc:
        print("[log] append err:", exc)


def main():
    bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
    backend = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=5)
    dev = RbAmp(backend, 0x50)
    dev.begin()
    print("[init] watching ch{} threshold delta={}W".format(WATCH_CH, EVENT_DELTA_W))

    last_p = None
    cyc = 0
    line_count = _count_lines()
    while CYCLES is None or cyc < CYCLES:
        cyc += 1
        time.sleep(INTERVAL_S)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            print("[cyc {:3d}] STALE".format(cyc))
            continue
        p = snap.avg_p[WATCH_CH]
        kind = ""
        if last_p is not None:
            dp = p - last_p
            if abs(dp) >= EVENT_DELTA_W:
                kind = "EVENT_UP" if dp > 0 else "EVENT_DOWN"
                _rotate_if_needed(line_count)
                _append("{} ch{} P={:.2f}W dP={:+.2f}W".format(
                    kind, WATCH_CH, p, dp))
                line_count += 1
        print("[cyc {:3d}] P={:7.2f}W  {}".format(cyc, p, kind))
        last_p = p

    print("\n[final] log lines={} retry_exhaustion={} sanity_reject={}".format(
        line_count, backend.retry_exhaustion_count, dev.sanity_reject_count))


if __name__ == "__main__":
    main()
