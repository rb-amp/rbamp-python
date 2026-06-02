"""
Example 4 — MQTT publisher with Home Assistant auto-discovery.

Publishes per-channel power & energy under MQTT and announces the entities
to Home Assistant via the discovery prefix ``homeassistant/sensor/...``.

Dependencies:
    pip install paho-mqtt

Configure ``BROKER_HOST`` and ``DEVICE_ID`` for your installation.

Run:
    python 04_mqtt_publisher.py
"""

import json
import time

from smbus2 import SMBus

import paho.mqtt.client as mqtt
from rbamp import RbAmp, RbAmpStaleError


BROKER_HOST = "192.168.1.10"
BROKER_PORT = 1883
DEVICE_ID = "rbamp-01"
INTERVAL_S = 60


def publish_discovery(client, dev):
    """Announce sensors to Home Assistant."""
    device = {
        "identifiers": [DEVICE_ID],
        "name": f"rbAmp {DEVICE_ID}",
        "model": "rbAmp module",
        "manufacturer": "rbAmp",
        "sw_version": f"0x{dev.firmware_version:02X}",
    }
    base = f"homeassistant/sensor/{DEVICE_ID}"
    for ch in range(dev.channels):
        # Power
        payload = {
            "name": f"rbAmp ch{ch} power",
            "state_topic": f"rbamp/{DEVICE_ID}/ch{ch}/power_w",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "unique_id": f"{DEVICE_ID}_ch{ch}_power",
            "device": device,
        }
        client.publish(f"{base}_ch{ch}_power/config", json.dumps(payload), retain=True)

        # Energy
        payload = {
            "name": f"rbAmp ch{ch} energy",
            "state_topic": f"rbamp/{DEVICE_ID}/ch{ch}/energy_wh",
            "unit_of_measurement": "Wh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "unique_id": f"{DEVICE_ID}_ch{ch}_energy",
            "device": device,
        }
        client.publish(f"{base}_ch{ch}_energy/config", json.dumps(payload), retain=True)


def main(bus_no=1, addr=0x50):
    client = mqtt.Client(client_id=DEVICE_ID)
    client.connect(BROKER_HOST, BROKER_PORT)
    client.loop_start()

    with SMBus(bus_no) as bus:
        with RbAmp(bus, addr) as dev:
            publish_discovery(client, dev)

            while True:
                t_start = time.monotonic()
                try:
                    snap = dev.read_period_snapshot()
                except RbAmpStaleError:
                    print("STALE — skipping publish")
                else:
                    for ch in range(dev.channels):
                        client.publish(
                            f"rbamp/{DEVICE_ID}/ch{ch}/power_w",
                            f"{snap.avg_p[ch]:.2f}",
                        )
                        client.publish(
                            f"rbamp/{DEVICE_ID}/ch{ch}/energy_wh",
                            f"{dev.energy.wh(ch):.4f}",
                        )
                    print(f"published @ {time.strftime('%H:%M:%S')}  Wh0={dev.energy.wh(0):.3f}")

                elapsed = time.monotonic() - t_start
                if elapsed < INTERVAL_S:
                    time.sleep(INTERVAL_S - elapsed)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
