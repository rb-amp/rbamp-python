# SPDX-License-Identifier: MIT
"""
Example 7 (CPython) — multi-module home energy balance via MQTT (paho).

CPython twin of ``examples_upy/08_home_energy_balance.py``. Iterates a list
of rbAmp slave addresses on a single SMBus, publishes per-device power and
a whole-house sum under a configurable MQTT topic prefix.

Topic layout (mirror of the uPy example):
    home/energy/<device_label>/p_w
    home/energy/<device_label>/wh_total
    home/energy/sum/p_w
    home/energy/sum/wh_total

Hardware:
    Linux SBC (RPi, Orange Pi, etc.) + 1..N rbAmp slaves at distinct
    addresses. Assign addresses via the SPEC §10 two-step flow before first
    use; once persisted to flash they stick.

Dependencies:
    pip install rbamp smbus2 paho-mqtt

Run:
    MQTT_HOST=mqtt.local python 07_home_energy_balance.py \\
        --bus 1 --addresses 0x50,0x51,0x52 --cadence 10

If ``MQTT_HOST`` env var is unset the script runs in print-only mode
(useful for first-time bench bring-up).

See also:
    - examples_upy/08_home_energy_balance.py (umqtt.simple twin)
    - SPEC.md §10 (address change)
"""
import argparse
import os
import time

from smbus2 import SMBus
from rbamp import RbAmp, RbAmpStaleError


TOPIC_PREFIX = "home/energy"


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
    devices = []
    for addr in addrs:
        try:
            dev = RbAmp(bus, addr)
            dev.begin()
            label = "dev_{:02X}".format(addr)
            devices.append((label, dev))
            print("[init] {} ch={} topology={}".format(label, dev.channels, dev.topology))
        except Exception as exc:
            print("[init] FAIL 0x{:02X}: {}".format(addr, exc))

    try:
        cyc = 0
        while args.cycles == 0 or cyc < args.cycles:
            cyc += 1
            time.sleep(args.cadence)
            sum_p = 0.0
            sum_wh = 0.0
            for label, dev in devices:
                try:
                    snap = dev.read_period_snapshot()
                    p = sum(snap.avg_p[ch] for ch in range(dev.channels))
                    wh = sum(dev.energy.wh(ch) for ch in range(dev.channels))
                    sum_p += p
                    sum_wh += wh
                    print("[cyc {:3d}] {} P={:7.2f}W Wh={:8.4f}".format(
                        cyc, label, p, wh))
                    _publish(cli, "{}/{}/p_w".format(TOPIC_PREFIX, label), p)
                    _publish(cli, "{}/{}/wh_total".format(TOPIC_PREFIX, label), wh)
                except RbAmpStaleError:
                    print("[cyc {:3d}] {} STALE".format(cyc, label))
                except Exception as exc:
                    print("[cyc {:3d}] {} ERR: {}".format(cyc, label, exc))
            _publish(cli, "{}/sum/p_w".format(TOPIC_PREFIX), sum_p)
            _publish(cli, "{}/sum/wh_total".format(TOPIC_PREFIX), sum_wh)
            print("[cyc {:3d}] SUM {:7.2f}W Wh_total={:.3f}".format(
                cyc, sum_p, sum_wh))
    finally:
        bus.close()
        if cli is not None:
            cli.loop_stop()
            cli.disconnect()


if __name__ == "__main__":
    main()
