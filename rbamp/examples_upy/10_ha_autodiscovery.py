# SPDX-License-Identifier: MIT
"""
Example 10 (MicroPython) — Home Assistant MQTT auto-discovery.

Publishes the canonical HA discovery payloads (per-channel U/I/P/PF + Wh)
under ``homeassistant/sensor/<dev_id>/<entity>/config`` so HA picks them up
on first MQTT scan, then streams telemetry to a single state topic that
each entity references via ``value_template``.

This is the production deployment pattern for a self-hosted HA setup —
no YAML editing required on the HA side once the device is on the LAN.

Hardware:
    ESP32 + Wi-Fi + an MQTT broker reachable from the LAN. Defaults assume
    a Home Assistant broker at ``ha.local`` with no auth; override via a
    `secrets.py` next to this file (WIFI_SSID/WIFI_PASS/MQTT_HOST/MQTT_USER/
    MQTT_PASS — same shape as example 08).

Install + run:
    mpremote cp libs/python/rbamp/*.py :rbamp/
    mpremote cp libs/python/secrets.py :secrets.py
    mpremote run libs/python/rbamp/examples_upy/10_ha_autodiscovery.py

Expected output:
    [wifi] up: 192.168.1.42
    [mqtt] connected
    [discovery] published 6 entities
    [state] U=226.30 I0=0.81 P0=110.4 PF0=0.60 Wh0=0.012
    [state] U=226.27 I0=0.84 P0=112.1 PF0=0.61 Wh0=0.024
    ...

See also:
    - examples_cpython/09_ha_autodiscovery.py (paho-mqtt + daemon twin)
    - HA docs: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""
import time

from machine import I2C, Pin
from rbamp import RbAmp, RbAmpStaleError
from rbamp._io_micropython import MachineI2CBackend


DEV_ID = "rbamp_main"
DEV_NAME = "rbAmp main"
DISCOVERY_PREFIX = "homeassistant"
STATE_TOPIC = "rbamp/{}/state".format(DEV_ID)
INTERVAL_S = 10.0
CYCLES = 6


def _connect_wifi():
    try:
        import secrets   # type: ignore[import-not-found]
    except ImportError:
        return None
    import network   # type: ignore[import-not-found]
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        sta.connect(secrets.WIFI_SSID, secrets.WIFI_PASS)
        for _ in range(40):
            if sta.isconnected():
                break
            time.sleep(0.5)
    if sta.isconnected():
        print("[wifi] up:", sta.ifconfig()[0])
        return True
    print("[wifi] failed")
    return False


def _connect_mqtt():
    try:
        import secrets   # type: ignore[import-not-found]
        from umqtt.simple import MQTTClient   # type: ignore[import-not-found]
    except ImportError:
        print("[mqtt] no umqtt / secrets; offline-only")
        return None
    cli = MQTTClient(client_id=DEV_ID, server=secrets.MQTT_HOST,
                     user=getattr(secrets, "MQTT_USER", None),
                     password=getattr(secrets, "MQTT_PASS", None))
    cli.connect()
    print("[mqtt] connected")
    return cli


def _publish_discovery(cli, channels):
    """Emit HA auto-discovery configs. One per entity."""
    base_dev_info = (
        '"device":{{"identifiers":["{}"],"name":"{}","manufacturer":"rbAmp",'
        '"model":"rbAmp module"}}'
    ).format(DEV_ID, DEV_NAME)
    entities = [
        ("voltage", "Voltage", "V", "voltage", "{{ value_json.u_v }}"),
        ("frequency", "Frequency", "Hz", "frequency", "{{ value_json.f_hz }}"),
    ]
    for ch in range(channels):
        entities.extend([
            ("current_ch{}".format(ch), "Current ch{}".format(ch), "A",
             "current", "{{{{ value_json.i_a[{}] }}}}".format(ch)),
            ("power_ch{}".format(ch), "Power ch{}".format(ch), "W",
             "power", "{{{{ value_json.p_w[{}] }}}}".format(ch)),
            ("pf_ch{}".format(ch), "PF ch{}".format(ch), "",
             "power_factor", "{{{{ value_json.pf[{}] }}}}".format(ch)),
            ("energy_ch{}".format(ch), "Energy ch{}".format(ch), "Wh",
             "energy", "{{{{ value_json.wh[{}] }}}}".format(ch)),
        ])
    for slug, name, unit, dev_class, tmpl in entities:
        topic = "{}/sensor/{}/{}/config".format(DISCOVERY_PREFIX, DEV_ID, slug)
        payload = (
            '{{"name":"{}","state_topic":"{}","unit_of_measurement":"{}",'
            '"device_class":"{}","value_template":"{}",'
            '"unique_id":"{}_{}","{}}}'
        ).format(name, STATE_TOPIC, unit, dev_class, tmpl,
                 DEV_ID, slug, base_dev_info)
        if cli is not None:
            cli.publish(topic.encode(), payload.encode(), retain=True)
        print("[discovery]", topic)
    print("[discovery] published {} entities".format(len(entities)))


def _publish_state(cli, snap, dev):
    """Pack one JSON state message covering all entities."""
    i_list = [round(dev.read_current(ch), 3) for ch in range(dev.channels)]
    pf_list = [round(dev.read_power_factor(ch), 3) for ch in range(dev.channels)]
    wh_list = [round(dev.energy.wh(ch), 4) for ch in range(dev.channels)]
    p_list = [round(snap.avg_p[ch], 2) for ch in range(dev.channels)]
    # Tiny ad-hoc JSON to avoid ujson dep churn — fields are all scalar.
    payload = (
        '{{"u_v":{:.2f},"f_hz":{:.1f},"i_a":{},"p_w":{},"pf":{},"wh":{}}}'
    ).format(
        dev.read_voltage(), dev.read_frequency(),
        i_list, p_list, pf_list, wh_list,
    )
    if cli is not None:
        cli.publish(STATE_TOPIC.encode(), payload.encode())
    return payload


def main():
    _connect_wifi()
    cli = _connect_mqtt()

    bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
    backend = MachineI2CBackend(bus, retry_attempts=3, retry_gap_ms=5)
    dev = RbAmp(backend, 0x50)
    dev.begin()

    _publish_discovery(cli, dev.channels)

    cyc = 0
    while CYCLES is None or cyc < CYCLES:
        cyc += 1
        time.sleep(INTERVAL_S)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            print("[state] STALE — skip")
            continue
        payload = _publish_state(cli, snap, dev)
        print("[state]", payload)

    if cli is not None:
        cli.disconnect()


if __name__ == "__main__":
    main()
