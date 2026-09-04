# SPDX-License-Identifier: MIT
"""
Example 8 (MicroPython) — multi-module home energy balance via MQTT,
coherent-latched with the v1.3 General-Call fleet path (``RbAmpFleet``).

Iterates a list of rbAmp slave addresses on a single I2C bus, latches
every module together via one General-Call broadcast, then publishes
per-device power / Wh and a whole-house sum to MQTT under a configurable
topic prefix. The coherent latch is what makes the whole-house sum
honest — every module integrates the same wall-clock window, no
per-module drift.

Topic layout:
    home/energy/<device_label>/p_w        — instantaneous P (W) per device, per ch
    home/energy/<device_label>/wh_total   — accumulated Wh, per ch
    home/energy/sum/p_w                   — whole-house P
    home/energy/sum/wh_total              — whole-house Wh
    home/energy/sync/in_sync              — modules that caught the last tick

Hardware:
    ESP32 + Wi-Fi credentials in ``secrets.py`` (WIFI_SSID, WIFI_PASS,
    MQTT_HOST, optional MQTT_USER/MQTT_PASS). 1..N rbAmp slaves at
    distinct I2C addresses (use the two-step ``prepare_address_change``
    / ``commit_address_change`` flow to assign distinct addresses once).

Dependencies on device:
    umqtt.simple (ships with ESP32 MicroPython firmware out of the box)

Install + run:
    mpremote cp libs/python/rbamp/*.py :rbamp/
    mpremote cp libs/python/secrets.py :secrets.py
    mpremote run libs/python/rbamp/examples_upy/08_home_energy_balance.py

Expected output (operator: replace MQTT_HOST + WIFI_SSID/PASS first):
    [wifi] connecting...
    [wifi] up: 192.168.1.42
    [mqtt] connecting mqtt.local
    [mqtt] connected
    [cyc 1] dev_main P=215.3W (ch0=215.3)
    [cyc 1] dev_kitch P= 32.1W (ch0=32.1)
    [cyc 1] SUM 247.4W  Wh_total=0.412  in_sync=2/2
    ...

See also:
    - examples_cpython/07_home_energy_balance.py (paho-mqtt twin)
    - examples_upy/03_multi_module.py (fleet GC primer, print-only)
"""
import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpFleet, RbAmpError, RbAmpStaleError


# --- knobs ---------------------------------------------------------------
DEVICES = [
    # (label, i2c_addr)
    ("dev_main",  0x50),
    # Add additional devices once you've used prepare_address_change /
    # commit_address_change to give them distinct addresses, e.g.:
    # ("dev_kitch", 0x51),
    # ("dev_solar", 0x52),
]
INTERVAL_S = 10.0
CYCLES = 6   # set None for forever
TOPIC_PREFIX = "home/energy"
GC_GROUP = 0


def _connect_wifi():
    try:
        import secrets   # type: ignore[import-not-found]
    except ImportError:
        print("[wifi] no secrets.py — skipping Wi-Fi + MQTT")
        return False
    import network   # type: ignore[import-not-found]
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        print("[wifi] connecting to", secrets.WIFI_SSID)
        sta.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
        for _ in range(40):
            if sta.isconnected():
                break
            time.sleep(0.5)
    if sta.isconnected():
        print("[wifi] up:", sta.ifconfig()[0])
        return True
    print("[wifi] failed; running offline")
    return False


def _connect_mqtt():
    try:
        import secrets   # type: ignore[import-not-found]
        from umqtt.simple import MQTTClient   # type: ignore[import-not-found]
    except ImportError:
        return None
    user = getattr(secrets, "MQTT_USER", None)
    pwd  = getattr(secrets, "MQTT_PASS", None)
    cli = MQTTClient(client_id="rbamp_home", server=secrets.MQTT_HOST,
                     user=user, password=pwd)
    cli.connect()
    print("[mqtt] connected", secrets.MQTT_HOST)
    return cli


def _publish(cli, topic, payload):
    if cli is None:
        return
    try:
        cli.publish(topic.encode(), str(payload).encode(), retain=True)
    except OSError as exc:
        print("[mqtt] publish err:", exc)


def main():
    have_wifi = _connect_wifi()
    cli = _connect_mqtt() if have_wifi else None

    # ESP32-Arduino wraps ESP-IDF i2c_master; SPEC §B.5 mitigation = 50 kHz.
    bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)

    fleet = RbAmpFleet(bus)
    labels = {}
    for label, addr in DEVICES:
        try:
            dev = RbAmp(bus, addr)
            dev.begin()
        except RbAmpError as exc:
            print("[init] FAIL", label, addr, exc)
            continue
        labels[addr] = label
        fleet.add(dev)
        print("[init]", label, "@ 0x{:02X} ch={}".format(addr, dev.channels))

    if len(fleet) == 0:
        print("[init] no modules; exiting")
        return

    enabled = fleet.enable_gc_all(group=GC_GROUP)
    print("[init] GC enabled on {}/{} module(s), group={}".format(
        enabled, len(fleet), GC_GROUP))

    cyc = 0
    while CYCLES is None or cyc < CYCLES:
        cyc += 1
        time.sleep(INTERVAL_S)

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
                print("[cyc {:3d}] {} STALE — skip".format(cyc, label))
                continue
            except RbAmpError as exc:
                print("[cyc {:3d}] {} ERR: {}".format(cyc, label, exc))
                continue
            p  = sum(snap.avg_p[ch] for ch in range(dev.channels))
            wh = sum(dev.energy.wh(ch) for ch in range(dev.channels))
            sum_p  += p
            sum_wh += wh
            print("[cyc {:3d}] {} P={:7.2f}W (ch0={:.2f})".format(
                cyc, label, p, snap.avg_p[0]))
            _publish(cli, "{}/{}/p_w".format(TOPIC_PREFIX, label), p)
            _publish(cli, "{}/{}/wh_total".format(TOPIC_PREFIX, label), wh)

        _publish(cli, "{}/sum/p_w".format(TOPIC_PREFIX), sum_p)
        _publish(cli, "{}/sum/wh_total".format(TOPIC_PREFIX), sum_wh)
        _publish(cli, "{}/sync/in_sync".format(TOPIC_PREFIX), in_sync)
        print("[cyc {:3d}] SUM {:7.2f}W Wh_total={:.3f}  in_sync={}/{}".format(
            cyc, sum_p, sum_wh, in_sync, len(fleet)))

    if cli is not None:
        cli.disconnect()


if __name__ == "__main__":
    main()
