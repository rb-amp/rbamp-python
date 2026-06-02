# SPDX-License-Identifier: MIT
"""
Example 8 (MicroPython) — multi-module home energy balance via MQTT.

Iterates a list of rbAmp slave addresses on a single I2C bus, reads each
device's period snapshot synchronously (broadcast LATCH would be ideal but
v1 firmware disables general-call — see SPEC §9), then publishes per-device
power plus a whole-house sum to MQTT under a configurable topic prefix.

Pattern fits a home-energy-monitor with one rbAmp per circuit breaker.
Topic layout:
    home/energy/<device_label>/p_w        — instantaneous P (W) per device, per ch
    home/energy/<device_label>/wh_total   — accumulated Wh, per ch
    home/energy/sum/p_w                   — whole-house P (sum across all)
    home/energy/sum/wh_total              — whole-house Wh

Hardware:
    ESP32 + Wi-Fi credentials in `secrets.py` (WIFI_SSID, WIFI_PASS,
    MQTT_HOST, optional MQTT_USER/MQTT_PASS). 1..N rbAmp slaves at
    different I2C addresses (use ``prepare_address_change``/``commit_address_change``
    flow from SPEC §10 to assign distinct addresses to each module).

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
    [cyc 1] sum 247.4W  Wh_total=0.412
    ...

See also:
    - examples_cpython/07_home_energy_balance.py (paho-mqtt twin)
    - SPEC.md §9 (general-call), §10 (address change)
"""
import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError
from rbamp._io_micropython import MachineI2CBackend


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

    bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
    backend = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=5)
    devices = []
    for label, addr in DEVICES:
        try:
            dev = RbAmp(backend, addr)
            dev.begin()
            devices.append((label, dev))
            print("[init]", label, "@ 0x{:02X} ch={}".format(addr, dev.channels))
        except Exception as exc:
            print("[init] FAIL", label, addr, exc)

    cyc = 0
    while CYCLES is None or cyc < CYCLES:
        cyc += 1
        time.sleep(INTERVAL_S)
        sum_p = 0.0
        sum_wh = 0.0
        for label, dev in devices:
            try:
                snap = dev.read_period_snapshot()
                p = sum(snap.avg_p[ch] for ch in range(dev.channels))
                wh = sum(dev.energy.wh(ch) for ch in range(dev.channels))
                sum_p += p
                sum_wh += wh
                print("[cyc {:3d}] {} P={:7.2f}W (ch0={:.2f})".format(
                    cyc, label, p, snap.avg_p[0]))
                _publish(cli, "{}/{}/p_w".format(TOPIC_PREFIX, label), p)
                _publish(cli, "{}/{}/wh_total".format(TOPIC_PREFIX, label), wh)
            except RbAmpStaleError:
                print("[cyc {:3d}] {} STALE — skip".format(cyc, label))
            except Exception as exc:
                print("[cyc {:3d}] {} ERR: {}".format(cyc, label, exc))
        _publish(cli, "{}/sum/p_w".format(TOPIC_PREFIX), sum_p)
        _publish(cli, "{}/sum/wh_total".format(TOPIC_PREFIX), sum_wh)
        print("[cyc {:3d}] SUM {:7.2f}W Wh_total={:.3f}".format(cyc, sum_p, sum_wh))

    if cli is not None:
        cli.disconnect()


if __name__ == "__main__":
    main()
