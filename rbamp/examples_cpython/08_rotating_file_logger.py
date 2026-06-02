# SPDX-License-Identifier: MIT
"""
Example 8 (CPython) — rotating per-cycle logger with stdlib logging.handlers.

Periodically samples the device and writes one CSV row per cycle to a
size-rotating log file. Pattern fits long-running deployments on RPi /
Orange Pi where the SD card has limited free space — log rotation prevents
unbounded growth.

CPython twin of ``examples_upy/09_event_detection_logger.py`` (event-based)
but here we log *every* cycle, not just events — more common production
shape for time-series ingestion (Grafana Loki, Elasticsearch, etc.).

Hardware:
    Linux SBC + 1 rbAmp slave. No specific channel count assumption.

Dependencies:
    pip install rbamp smbus2  (stdlib `logging.handlers.RotatingFileHandler`
    provides the rotation logic — no extra dep)

Run:
    python 08_rotating_file_logger.py --out /var/log/rbamp.csv \\
        --bus 1 --cadence 10 --max-mb 5 --keep 3

The CSV is human-readable and rotates at ``--max-mb`` MB, keeping ``--keep``
backups (rbamp.csv, rbamp.csv.1, ..., rbamp.csv.N). systemd unit file in
``10_systemd_service.py`` shows the production deployment shape.

Expected output (stderr):
    [logger] writing to /var/log/rbamp.csv max=5MB keep=3
    [cyc   1] logged 1 row
    [cyc   2] logged 1 row
    ...

See also:
    - examples_upy/09_event_detection_logger.py (uPy SPIFFS twin — event-based)
    - examples_cpython/10_systemd_service.py (deploy this script as a service)
"""
import argparse
import csv
import io
import logging
import logging.handlers
import os
import sys
import time

from smbus2 import SMBus
from rbamp import RbAmp, RbAmpStaleError


def _setup_logger(path, max_mb, keep):
    log = logging.getLogger("rbamp_csv")
    log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_mb * 1024 * 1024, backupCount=keep, encoding="utf-8"
    )
    # Format is just "{message}" — we feed pre-formatted CSV rows in.
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.propagate = False  # don't echo CSV to root logger
    return log


def _csv_row(cols, vals):
    buf = io.StringIO()
    csv.writer(buf).writerow(vals)
    return buf.getvalue().rstrip("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="CSV output path (will rotate)")
    ap.add_argument("--bus", type=int, default=1)
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x50)
    ap.add_argument("--cadence", type=float, default=10.0,
                    help="seconds between samples")
    ap.add_argument("--max-mb", type=int, default=5,
                    help="max size per file in MB (rotation threshold)")
    ap.add_argument("--keep", type=int, default=3,
                    help="number of rotated backups to keep")
    ap.add_argument("--cycles", type=int, default=6,
                    help="0 = run forever")
    args = ap.parse_args()

    bus = SMBus(args.bus)
    dev = RbAmp(bus, args.addr)
    dev.begin()

    csv_log = _setup_logger(args.out, args.max_mb, args.keep)
    cols = (
        ["ts", "cyc", "u_v", "f_hz"]
        + ["i{}_a".format(ch) for ch in range(dev.channels)]
        + ["p{}_w".format(ch) for ch in range(dev.channels)]
        + ["pf{}".format(ch) for ch in range(dev.channels)]
        + ["wh{}".format(ch) for ch in range(dev.channels)]
    )
    # Write header line if the file is empty.
    if os.path.getsize(args.out) == 0 if os.path.exists(args.out) else True:
        csv_log.info(_csv_row(cols, cols))
    print("[logger] writing to {} max={}MB keep={}".format(
        args.out, args.max_mb, args.keep), file=sys.stderr)

    try:
        cyc = 0
        while args.cycles == 0 or cyc < args.cycles:
            cyc += 1
            time.sleep(args.cadence)
            try:
                snap = dev.read_period_snapshot()
            except RbAmpStaleError:
                print("[cyc {:3d}] STALE — skip".format(cyc), file=sys.stderr)
                continue
            except Exception as exc:
                print("[cyc {:3d}] ERR {}".format(cyc, exc), file=sys.stderr)
                continue
            vals = [
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                cyc,
                round(dev.read_voltage(), 2),
                round(dev.read_frequency(), 1),
            ]
            for ch in range(dev.channels):
                vals.append(round(dev.read_current(ch), 3))
            for ch in range(dev.channels):
                vals.append(round(snap.avg_p[ch], 2))
            for ch in range(dev.channels):
                vals.append(round(dev.read_power_factor(ch), 3))
            for ch in range(dev.channels):
                vals.append(round(dev.energy.wh(ch), 4))
            csv_log.info(_csv_row(cols, vals))
            print("[cyc {:3d}] logged 1 row".format(cyc), file=sys.stderr)
    finally:
        bus.close()


if __name__ == "__main__":
    main()
