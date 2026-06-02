# SPDX-License-Identifier: MIT
"""
Example 10 (CPython) — production deployment template for systemd.

This example bundles three things needed to run rbAmp on a Linux SBC as a
managed background service:

1. A minimal Python entrypoint (this file's ``main()``) that wraps a
   long-lived polling loop with proper SIGTERM handling, exit-code
   discipline, and structured logging via stdlib ``logging``.
2. An embedded ``UNIT_FILE`` constant — the systemd unit text — printable
   via ``--print-unit``. Suitable for ``--user`` or ``--system`` install.
3. A ``--install`` helper that writes the unit to the right place and
   prints the ``systemctl`` commands to enable + start it.

This is the "what does production look like" example. For real deployment
on a busy bench, prefer wrapping ``09_ha_autodiscovery.py`` (or your own
specialised daemon) the same way.

Hardware:
    Any Linux SBC where systemd is available (RPi OS, Orange Pi OS,
    Ubuntu, Debian, Fedora — basically anything except macOS / Windows).

Dependencies:
    pip install rbamp smbus2  (no extra deps for the daemon itself)

Workflows:

    # 1. Print the unit file (inspect before installing):
    python 10_systemd_service.py --print-unit

    # 2. Install the unit and follow printed instructions:
    sudo python 10_systemd_service.py --install --system \\
        --exec-path $(pwd)/10_systemd_service.py

    # 3. Run the daemon foreground (for testing / debugging):
    python 10_systemd_service.py --bus 1 --addr 0x50 --cadence 60

Expected console output:
    [rbamp.daemon] starting cadence=60s bus=1 addr=0x50
    [rbamp.daemon] cyc 1 U=226.30 V P=[110.4] W Wh=[0.0307]
    [rbamp.daemon] cyc 2 U=226.27 V P=[112.1] W Wh=[0.0613]
    ...

See also:
    - examples_cpython/08_rotating_file_logger.py (log-to-file companion)
    - examples_cpython/09_ha_autodiscovery.py (a daemon worth packaging this way)
"""
import argparse
import logging
import os
import signal
import sys
import time

UNIT_FILE = """\
# rbAmp polling daemon — installed by examples_cpython/10_systemd_service.py
#
# After install:
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now rbamp.service
#   journalctl -u rbamp.service -f

[Unit]
Description=rbAmp module polling daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# ExecStart path is substituted at install time via --exec-path.
ExecStart=/usr/bin/env python3 __EXEC_PATH__ --bus __BUS_NO__ --addr __ADDR__ --cadence __CADENCE_S__
# Group/User must be able to access /dev/i2c-N. On RPi: usually `i2c`.
# Adjust as needed; we leave defaults so install works out of the box for
# root-installed system units (the default --system path).
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Hardening: read-only filesystem, no new privileges. Adjust if your script
# needs to write to disk (e.g. file-rotating logger).
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


def _shutdown_handler(signum, frame):
    raise SystemExit(0)


def _run_daemon(args):
    """The actual polling loop — what systemd ends up running."""
    from smbus2 import SMBus
    from rbamp import RbAmp, RbAmpStaleError

    log = logging.getLogger("rbamp.daemon")
    logging.basicConfig(level=logging.INFO,
                        format="[%(name)s] %(message)s",
                        stream=sys.stderr)
    log.info("starting cadence=%ss bus=%s addr=0x%02X", args.cadence,
             args.bus, args.addr)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    bus = SMBus(args.bus)
    try:
        dev = RbAmp(bus, args.addr)
        dev.begin()
        log.info("device ready: topology=%s channels=%d", dev.topology, dev.channels)
        cyc = 0
        while True:
            time.sleep(args.cadence)
            cyc += 1
            try:
                snap = dev.read_period_snapshot()
            except RbAmpStaleError:
                log.warning("cyc %d STALE — skipping integration", cyc)
                continue
            except Exception as exc:
                log.error("cyc %d failed: %s", cyc, exc)
                continue
            u = dev.read_voltage()
            p = [round(snap.avg_p[ch], 1) for ch in range(dev.channels)]
            wh = [round(dev.energy.wh(ch), 4) for ch in range(dev.channels)]
            log.info("cyc %d U=%.2f V P=%s W Wh=%s", cyc, u, p, wh)
    finally:
        bus.close()
        log.info("daemon stopped")
    return 0


def _print_unit(args):
    """Print the unit file with placeholders substituted."""
    text = UNIT_FILE.replace(
        "__EXEC_PATH__", args.exec_path or os.path.abspath(__file__)
    ).replace(
        "__BUS_NO__", str(args.bus)
    ).replace(
        "__ADDR__", "0x{:02X}".format(args.addr)
    ).replace(
        "__CADENCE_S__", str(args.cadence)
    )
    sys.stdout.write(text)
    return 0


def _install_unit(args):
    text = UNIT_FILE.replace(
        "__EXEC_PATH__", args.exec_path or os.path.abspath(__file__)
    ).replace(
        "__BUS_NO__", str(args.bus)
    ).replace(
        "__ADDR__", "0x{:02X}".format(args.addr)
    ).replace(
        "__CADENCE_S__", str(args.cadence)
    )
    if args.user:
        unit_dir = os.path.expanduser("~/.config/systemd/user")
    else:
        unit_dir = "/etc/systemd/system"
    if not os.path.isdir(unit_dir):
        os.makedirs(unit_dir, exist_ok=True)
    path = os.path.join(unit_dir, "rbamp.service")
    with open(path, "w") as f:
        f.write(text)
    print("[install] wrote", path)
    scope = "--user " if args.user else ""
    print("[install] next steps:")
    print("    systemctl {}daemon-reload".format(scope))
    print("    systemctl {}enable --now rbamp.service".format(scope))
    print("    journalctl {}-u rbamp.service -f".format(scope))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, default=1)
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x50)
    ap.add_argument("--cadence", type=float, default=60.0)
    ap.add_argument("--print-unit", action="store_true",
                    help="Print the systemd unit file and exit.")
    ap.add_argument("--install", action="store_true",
                    help="Install the unit file (default: --system).")
    ap.add_argument("--user", action="store_true",
                    help="Install as --user unit instead of --system.")
    ap.add_argument("--system", action="store_true",
                    help="(default) install as system unit. Mutex with --user.")
    ap.add_argument("--exec-path",
                    help="Absolute path to this script (defaults to argv[0])")
    args = ap.parse_args()

    if args.print_unit:
        return _print_unit(args)
    if args.install:
        if args.user and args.system:
            print("error: --user and --system are mutually exclusive",
                  file=sys.stderr)
            return 2
        return _install_unit(args)
    return _run_daemon(args)


if __name__ == "__main__":
    sys.exit(main())
