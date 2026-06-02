"""
Example 4 (MicroPython) — WiFi + MQTT publisher.

Publishes per-channel power and energy every minute. Use the umqtt.simple
client shipped with MicroPython-ESP32.

Configure WIFI_SSID, WIFI_PASSWORD, BROKER_HOST and DEVICE_ID below.

Run:
    mpremote run 04_mqtt.py
"""

import time

import network  # type: ignore[import-not-found]
from machine import I2C, Pin
from umqtt.simple import MQTTClient  # type: ignore[import-not-found]
from rbamp import RbAmp, RbAmpStaleError


WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"
BROKER_HOST = "192.168.1.10"
BROKER_PORT = 1883
DEVICE_ID = b"rbamp-esp32-01"


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan
    print("WiFi connecting...")
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(60):
        if wlan.isconnected():
            break
        time.sleep(0.5)
    if not wlan.isconnected():
        raise OSError("WiFi connect failed")
    print("WiFi", wlan.ifconfig())
    return wlan


def main():
    connect_wifi()
    mqtt = MQTTClient(DEVICE_ID, BROKER_HOST, port=BROKER_PORT, keepalive=120)
    mqtt.connect()
    print("MQTT connected")

    i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
    with RbAmp(i2c, 0x50) as dev:
        while True:
            t0 = time.ticks_ms()
            try:
                snap = dev.read_period_snapshot()
            except RbAmpStaleError:
                print("STALE")
            else:
                for ch in range(dev.channels):
                    mqtt.publish(
                        b"rbamp/" + DEVICE_ID + b"/ch" + str(ch).encode() + b"/power_w",
                        "{:.2f}".format(snap.avg_p[ch]).encode(),
                    )
                    mqtt.publish(
                        b"rbamp/" + DEVICE_ID + b"/ch" + str(ch).encode() + b"/energy_wh",
                        "{:.4f}".format(dev.energy.wh(ch)).encode(),
                    )
                print("pub @", time.ticks_ms() // 1000, "Wh0=", dev.energy.wh(0))

            remaining = 60_000 - time.ticks_diff(time.ticks_ms(), t0)
            if remaining > 0:
                time.sleep_ms(remaining)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
