# SPDX-License-Identifier: MIT
"""
Example 7 (CPython) — multi-module home energy balance via MQTT (paho),
coherent-latched with the v1.3 General-Call fleet path (``RbAmpFleet``).

CPython twin of ``examples_upy/08_home_energy_balance.py``. Iterates a list
of rbAmp slave addresses on a single SMBus, latches every module together
via one General-Call broadcast, publishes per-device power / Wh and a
whole-house sum under a configurable MQTT topic prefix. The coherent
latch is what makes the whole-house sum honest — every module integrates
the same wall-clock window, no per-module drift.

Topic layout (mirror of the uPy example):
    home/energy/<device_label>/p_w        — instantaneous P (W) per device
    home/energy/<device_label>/wh_total   — accumulated Wh, per device
    home/energy/sum/p_w                   — whole-house P
    home/energy/sum/wh_total              — whole-house Wh
    home/energy/sync/in_sync              — how many modules caught the last tick

Hardware:
    Linux SBC (RPi, Orange Pi, etc.) + 1..N rbAmp slaves at distinct
    addresses. Assign addresses once with the two-step
    ``prepare_address_change`` / ``commit_address_change`` flow (persists
    to flash) so they coexist on the shared bus.

Dependencies:
    pip install rbamp smbus2 paho-mqtt

Run:
    MQTT_HOST=mqtt.local python 07_home_energy_balance.py \\
        --bus 1 --addresses 0x50,0x51,0x52 --cadence 10

If ``MQTT_HOST`` env var is unset the script runs in print-only mode
(useful for first-time bench bring-up).

See also:
    - examples_upy/08_home_energy_balance.py (umqtt.simple twin)
    - examples_cpython/03_multi_module_broadcast.py (fleet GC primer, print-only)
"""
import argparse
import os
import time

from smbus2 import SMBus
from rbamp import (
    RbAmp,
    RbAmpFleet,
    RbAmpError,
    RbAmpStaleError,
)


TOPIC_PREFIX = "home/energy"
GC_GROUP = 0


def _make_mqtt():
    host = os.environ.get("MQTT_HOST")
    if not host:
        print("[mqtt] MQTT_HOST not set — print-only mode")
        return None
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[mqtt] paho-mqtt not installed — print-only mode")
        return None
    cli = mqtt.Client(client_id="rbamp_home_cpython")
    user = os.environ.get("MQTT_USER")
    if user:
        cli.username_pw_set(user, os.environ.get("MQTT_PASS"))
    port = int(os.environ.get("MQTT_PORT", "1883"))
    cli.connect(host, port=port, keepalive=60)
    cli.loop_start()
    print("[mqtt] connected", host, port)
    return cli


def _publish(cli, topic, payload):
    if cli is None:
        return
    cli.publish(topic, payload=str(payload), retain=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, default=1, help="SMBus number")
    ap.add_argument("--addresses", default="0x50",
                    help="comma-separated list, e.g. 0x50,0x51,0x52")
    ap.add_argument("--cadence", type=float, default=10.0)
    ap.add_argument("--cycles", type=int, default=6, help="0 = run forever")
    args = ap.parse_args()

    addrs = [int(a, 0) for a in args.addresses.split(",")]
    cli = _make_mqtt()

    bus = SMBus(args.bus)
    fleet = RbAmpFleet(bus)
    labels = {}       # addr -> label
    for addr in addrs:
        try:
            dev = RbAmp(bus, addr)
            dev.begin()
        except RbAmpError as exc:
            print("[init] FAIL 0x{:02X}: {}".format(addr, exc))
            continue
        label = "dev_{:02X}".format(addr)
        labels[addr] = label
        fleet.add(dev)
        print("[init] {} ch={} topology={}".format(label, dev.channels, dev.topology))

    if len(fleet) == 0:
        print("[init] no modules enrolled; exiting")
        return

    enabled = fleet.enable_gc_all(group=GC_GROUP)
    print("[init] GC enabled on {}/{} module(s), group={}".format(
        enabled, len(fleet), GC_GROUP))

    try:
        cyc = 0
        while args.cycles == 0 or cyc < args.cycles:
            cyc += 1
            time.sleep(args.cadence)

            # One coherent latch across the whole fleet.
            tick = fleet.gc_latch(group=GC_GROUP)
            sync = fleet.check_sync(expected_tick=tick)

            sum_p = 0.0
            sum_wh = 0.0
            in_sync = 0
            for dev, s in zip(fleet, sync):
                label = labels[dev.address]
                if not s.in_sync:
                    print("[cyc {:3d}] {} MISSED tick".format(cyc, label))
                    continue
                in_sync += 1
                try:
                    snap = dev.read_period_snapshot(settle_ms=0, skip_latch=True)
                except RbAmpStaleError:
                    print("[cyc {:3d}] {} STALE".format(cyc, label))
                    continue
                except RbAmpError as exc:
                    print("[cyc {:3d}] {} ERR: {}".format(cyc, label, exc))
                    continue
                p  = sum(snap.avg_p[ch] for ch in range(dev.channels))
                wh = sum(dev.energy.wh(ch) for ch in range(dev.channels))
                sum_p  += p
                sum_wh += wh
                print("[cyc {:3d}] {} P={:7.2f}W Wh={:8.4f}".format(
                    cyc, label, p, wh))
                _publish(cli, "{}/{}/p_w".format(TOPIC_PREFIX, label), p)
                _publish(cli, "{}/{}/wh_total".format(TOPIC_PREFIX, label), wh)

            _publish(cli, "{}/sum/p_w".format(TOPIC_PREFIX), sum_p)
            _publish(cli, "{}/sum/wh_total".format(TOPIC_PREFIX), sum_wh)
            _publish(cli, "{}/sync/in_sync".format(TOPIC_PREFIX), in_sync)
            print("[cyc {:3d}] SUM {:7.2f}W Wh_total={:.3f}  in_sync={}/{}".format(
                cyc, sum_p, sum_wh, in_sync, len(fleet)))
    finally:
        bus.close()
        if cli is not None:
            cli.loop_stop()
            cli.disconnect()


if __name__ == "__main__":
    main()
