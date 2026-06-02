# SPDX-License-Identifier: MIT
"""
Example 9 (CPython) — Home Assistant MQTT auto-discovery, daemon-style.

CPython twin of ``examples_upy/10_ha_autodiscovery.py``. Same topic layout,
but runs as a long-lived foreground process suitable for systemd packaging
(see ``examples_cpython/10_systemd_service.py`` for the unit file).

Publishes one set of ``homeassistant/sensor/<dev_id>/<entity>/config``
discovery payloads on startup, then streams a single JSON state message
per cadence under ``rbamp/<dev_id>/state``. Entities reference the state
topic via ``value_template`` so we only publish one message per cycle.

Hardware:
    Linux SBC + 1 rbAmp slave + MQTT broker (e.g. Mosquitto installed
    alongside Home Assistant).

Dependencies:
    pip install rbamp smbus2 paho-mqtt

Run:
    MQTT_HOST=ha.local python 09_ha_autodiscovery.py --bus 1 --dev-id rbamp_main
    # Then in HA: Settings → Devices & services → MQTT → entities show up
    # under "rbAmp main" within ~30 seconds.

See also:
    - examples_upy/10_ha_autodiscovery.py (uPy umqtt twin)
    - HA docs: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
    - examples_cpython/10_systemd_service.py (deploy as systemd unit)
"""
import argparse
import json
import os
import signal
import sys
import time

from smbus2 import SMBus
from rbamp import RbAmp, RbAmpStaleError


DISCOVERY_PREFIX = "homeassistant"


def _connect_mqtt(dev_id):
    host = os.environ.get("MQTT_HOST")
    if not host:
        raise SystemExit("error: MQTT_HOST env var required")
    import paho.mqtt.client as mqtt
    cli = mqtt.Client(client_id=dev_id)
    user = os.environ.get("MQTT_USER")
    if user:
        cli.username_pw_set(user, os.environ.get("MQTT_PASS"))
    port = int(os.environ.get("MQTT_PORT", "1883"))
    cli.connect(host, port=port, keepalive=60)
    cli.loop_start()
    print("[mqtt] connected", host, port, file=sys.stderr)
    return cli


def _publish_discovery(cli, dev_id, dev_name, channels, state_topic):
    """Publish HA auto-discovery configs for every entity."""
    device_obj = {
        "identifiers": [dev_id],
        "name": dev_name,
        "manufacturer": "rbAmp",
        "model": "rbAmp module",
    }
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
            ("pf_ch{}".format(ch), "PF ch{}".format(ch), None,
             "power_factor", "{{{{ value_json.pf[{}] }}}}".format(ch)),
            ("energy_ch{}".format(ch), "Energy ch{}".format(ch), "Wh",
             "energy", "{{{{ value_json.wh[{}] }}}}".format(ch)),
        ])
    for slug, name, unit, dev_class, tmpl in entities:
        topic = "{}/sensor/{}/{}/config".format(DISCOVERY_PREFIX, dev_id, slug)
        payload = {
            "name": name,
            "state_topic": state_topic,
            "device_class": dev_class,
            "value_template": tmpl,
            "unique_id": "{}_{}".format(dev_id, slug),
            "device": device_obj,
        }
        if unit:
            payload["unit_of_measurement"] = unit
        cli.publish(topic, payload=json.dumps(payload), retain=True)
    print("[discovery] published {} entities".format(len(entities)),
          file=sys.stderr)


def _shutdown_handler(signum, frame):
    print("[signal] caught signal {}; shutting down".format(signum),
          file=sys.stderr)
    raise SystemExit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, default=1)
    ap.add_argument("--addr", type=lambda s: int(s, 0), default=0x50)
    ap.add_argument("--dev-id", default="rbamp_main")
    ap.add_argument("--dev-name", default="rbAmp main")
    ap.add_argument("--cadence", type=float, default=10.0)
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    bus = SMBus(args.bus)
    dev = RbAmp(bus, args.addr)
    dev.begin()
    state_topic = "rbamp/{}/state".format(args.dev_id)

    cli = _connect_mqtt(args.dev_id)
    _publish_discovery(cli, args.dev_id, args.dev_name, dev.channels, state_topic)

    try:
        while True:
            time.sleep(args.cadence)
            try:
                snap = dev.read_period_snapshot()
            except RbAmpStaleError:
                print("[state] STALE — skip", file=sys.stderr)
                continue
            payload = {
                "u_v": round(dev.read_voltage(), 2),
                "f_hz": round(dev.read_frequency(), 1),
                "i_a": [round(dev.read_current(ch), 3) for ch in range(dev.channels)],
                "p_w": [round(snap.avg_p[ch], 2) for ch in range(dev.channels)],
                "pf":  [round(dev.read_power_factor(ch), 3) for ch in range(dev.channels)],
                "wh":  [round(dev.energy.wh(ch), 4) for ch in range(dev.channels)],
            }
            cli.publish(state_topic, payload=json.dumps(payload))
            print("[state]", json.dumps(payload), file=sys.stderr)
    finally:
        bus.close()
        cli.loop_stop()
        cli.disconnect()


if __name__ == "__main__":
    main()
