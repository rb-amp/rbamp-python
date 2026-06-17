# 07 · DIY integrations

How to feed `rbamp` readings into popular self-hosted automation
systems from a Python host. For each platform: a minimal Python
script plus the matching configuration on the platform side.

Cloud / commercial integrations (AWS IoT, Azure, GCP, InfluxDB
Cloud) are covered in [08 · Cloud integrations](08_cloud_integrations.md).

| Platform | Transport | Auto-discovery | Python MQTT client |
|---|---|---|---|
| Home Assistant | MQTT | yes (HA MQTT Discovery) | `paho-mqtt` (CPython) / `umqtt.simple` (uPy) |
| Node-RED | MQTT (or HTTP) | manual flow | as above |
| OpenHAB | MQTT (or REST) | manual `.things` | as above |
| Domoticz | MQTT (auto) or HTTP | yes (MQTT plugin) | `paho-mqtt` or `requests` |
| InfluxDB OSS + Grafana | HTTP line-protocol | no | `requests` (CPython) / `urequests` (uPy) |

> Ready-made scripts for Home Assistant live in
> [`examples_cpython/09_ha_autodiscovery.py`](https://github.com/rb-amp/rbamp-python)
> (CPython, SIGTERM-aware daemon) and
> [`examples_upy/10_ha_autodiscovery.py`](https://github.com/rb-amp/rbamp-python)
> (MicroPython).

---

## Home Assistant — MQTT Auto-discovery

HA MQTT Discovery automatically creates the device and its sensors
once your Python script publishes the config topics. No YAML edits
in HA are required.

### CPython version (via `paho-mqtt`)

```python
import time, json
from smbus2 import SMBus
import paho.mqtt.client as mqtt_client
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

DEVICE_ID   = "rbamp_main"
DEVICE_NAME = "Mains rbAmp"

mqtt = mqtt_client.Client(DEVICE_ID)
mqtt.connect("192.168.1.10", 1883, keepalive=60)
mqtt.loop_start()

def publish_discovery_sensor(key, friendly, unit, device_class, state_class):
    payload = {
        "name":            f"{DEVICE_NAME} {friendly}",
        "unique_id":       f"{DEVICE_ID}_{key}",
        "state_topic":     f"rbamp/{DEVICE_ID}/state",
        "value_template":  f"{{{{ value_json.{key} }}}}",
        "state_class":     state_class,
        "device": {
            "identifiers":   [DEVICE_ID],
            "name":          DEVICE_NAME,
            "manufacturer":  "rbAmp",
            "model":         "UI*",
        },
    }
    if unit:         payload["unit_of_measurement"] = unit
    if device_class: payload["device_class"] = device_class
    topic = f"homeassistant/sensor/{DEVICE_ID}/{key}/config"
    mqtt.publish(topic, json.dumps(payload), qos=1, retain=True)

def publish_discovery_all():
    publish_discovery_sensor("voltage",      "Voltage",      "V",  "voltage",      "measurement")
    publish_discovery_sensor("current",      "Current",      "A",  "current",      "measurement")
    publish_discovery_sensor("power",        "Power",        "W",  "power",        "measurement")
    publish_discovery_sensor("energy",       "Energy",       "Wh", "energy",       "total_increasing")
    publish_discovery_sensor("frequency",    "Frequency",    "Hz", "frequency",    "measurement")
    publish_discovery_sensor("power_factor", "Power Factor", None, "power_factor", "measurement")

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)

    publish_discovery_all()

    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            continue

        state = {
            "voltage":      round(dev.voltage, 1),
            "current":      round(dev.read_current(0), 3),
            "power":        round(snap.avg_p[0], 1),
            "energy":       round(dev.energy.wh(0), 3),
            "frequency":    round(dev.frequency, 1),
            "power_factor": round(dev.read_power_factor(0), 3),
        }
        mqtt.publish(f"rbamp/{DEVICE_ID}/state", json.dumps(state), qos=0)
```

### MicroPython version (via `umqtt.simple`)

```python
import time, ujson
from machine import I2C, Pin
from umqtt.simple import MQTTClient
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

DEVICE_ID = b"rbamp_main"

def publish_discovery(mqtt, key, friendly, unit, dc, sc):
    payload = {
        "name":           f"Mains rbAmp {friendly}",
        "unique_id":      f"rbamp_main_{key}",
        "state_topic":    f"rbamp/rbamp_main/state",
        "value_template": "{{ value_json." + key + " }}",
        "state_class":    sc,
        "device": {
            "identifiers":  ["rbamp_main"],
            "name":         "Mains rbAmp",
            "manufacturer": "rbAmp",
            "model":        "UI*",
        },
    }
    if unit: payload["unit_of_measurement"] = unit
    if dc:   payload["device_class"] = dc
    topic = b"homeassistant/sensor/rbamp_main/" + key.encode() + b"/config"
    mqtt.publish(topic, ujson.dumps(payload).encode(),
                 qos=1, retain=True)

mqtt = MQTTClient(DEVICE_ID, b"192.168.1.10", port=1883, keepalive=60)
mqtt.connect()

for key, friendly, unit, dc, sc in [
    ("voltage",      "Voltage",      "V",  "voltage",      "measurement"),
    ("current",      "Current",      "A",  "current",      "measurement"),
    ("power",        "Power",        "W",  "power",        "measurement"),
    ("energy",       "Energy",       "Wh", "energy",       "total_increasing"),
    ("frequency",    "Frequency",    "Hz", "frequency",    "measurement"),
    ("power_factor", "Power Factor", None, "power_factor", "measurement"),
]:
    publish_discovery(mqtt, key, friendly, unit, dc, sc)

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)

    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            continue

        state = {
            "voltage":      round(dev.voltage, 1),
            "current":      round(dev.read_current(0), 3),
            "power":        round(snap.avg_p[0], 1),
            "energy":       round(dev.energy.wh(0), 3),
            "frequency":    round(dev.frequency, 1),
            "power_factor": round(dev.read_power_factor(0), 3),
        }
        mqtt.publish(b"rbamp/rbamp_main/state",
                     ujson.dumps(state).encode(), qos=0)
        mqtt.ping()   # keepalive, since umqtt.simple is sync with no background task
```

### Result in HA

A few seconds after the first publish, HA automatically creates a
device named "Mains rbAmp" with 6 sensors (Voltage, Current,
Power, Energy, Frequency, Power Factor). The Energy sensor carries
`state_class: total_increasing` and the correct `device_class`, so
the HA Energy Dashboard accepts it as a consumption source.

To remove the device from HA later, publish an empty payload to
`homeassistant/sensor/.../config` (the retained flag clears the entry).

### Multi-channel UI3

Repeat `publish_discovery_sensor()` with suffixed keys for channels
1 and 2:

```python
publish_discovery_sensor("current_1", "Current 1", "A",  "current", "measurement")
publish_discovery_sensor("power_1",   "Power 1",   "W",  "power",   "measurement")
publish_discovery_sensor("energy_1",  "Energy 1",  "Wh", "energy",  "total_increasing")
# ...same for _2
```

Then extend the state JSON with the fields `"current_1"`, `"power_1"`,
`"energy_1"`, populated from `dev.read_current(1)` / `snap.avg_p[1]` /
`dev.energy.wh(1)` (or from `dev.current[1]` / `dev.power[1]` via the
`_ChannelProxy` property if you prefer).

> On MicroPython, the async variant — the `mqtt_as` package instead of
> `umqtt.simple` — provides built-in keepalive + reconnect without a
> manual `mqtt.ping()`. See also Scenario 10 in
> [06 · Examples](06_examples.md) (async-streaming).

---

## Node-RED

Subscribe to the MQTT topic of the Python publisher inside a flow:

```json
[
  {
    "id": "rbamp_in",
    "type": "mqtt in",
    "topic": "rbamp/rbamp_main/state",
    "qos": "0",
    "datatype": "json"
  },
  {
    "id": "rbamp_chart",
    "type": "ui_chart",
    "label": "Mains Power",
    "chartType": "line",
    "ymin": "0",
    "ymax": "5000"
  },
  {
    "id": "extract_power",
    "type": "function",
    "func": "msg.payload = msg.payload.power; return msg;"
  }
]
```

Wire `rbamp_in → extract_power → rbamp_chart` and you get a
real-time power chart. Do the same for energy / voltage / PF.

If Node-RED runs on the same Pi as the MQTT broker, set the host to
`localhost`. For remote brokers, use `192.168.X.Y:1883` plus
credentials if the broker requires auth.

The Python side is the same script as in the Home Assistant section
above; the JSON payload is shared, only the consumer differs.

---

## OpenHAB

OpenHAB 4.x + MQTT binding:

```text
# things/rbamp.things
Bridge mqtt:broker:local "MQTT Broker" [ host="192.168.1.10", port=1883 ] {
    Thing topic rbamp_main "rbAmp Main" {
        Channels:
            Type number : voltage "Voltage" [ stateTopic="rbamp/rbamp_main/state", transformationPattern="JSONPATH:$.voltage" ]
            Type number : current "Current" [ stateTopic="rbamp/rbamp_main/state", transformationPattern="JSONPATH:$.current" ]
            Type number : power   "Power"   [ stateTopic="rbamp/rbamp_main/state", transformationPattern="JSONPATH:$.power" ]
            Type number : energy  "Energy"  [ stateTopic="rbamp/rbamp_main/state", transformationPattern="JSONPATH:$.energy" ]
    }
}
```

```text
# items/rbamp.items
Number:ElectricPotential rbAmp_Voltage "Voltage [%.1f V]"  <energy> { channel="mqtt:topic:local:rbamp_main:voltage" }
Number:ElectricCurrent   rbAmp_Current "Current [%.3f A]"  <energy> { channel="mqtt:topic:local:rbamp_main:current" }
Number:Power             rbAmp_Power   "Power   [%.1f W]"  <energy> { channel="mqtt:topic:local:rbamp_main:power" }
Number:Energy            rbAmp_Energy  "Energy  [%.3f Wh]" <energy> { channel="mqtt:topic:local:rbamp_main:energy" }
```

The Python side is the same script as in the Home Assistant section
above. The JSON payload is shared, only the consumer differs.

---

## Domoticz

The MQTT Auto-discovery plugin in Domoticz understands the same
`homeassistant/...` discovery topics. Enable the plugin in the
Domoticz settings, and the Python script from the HA section above
will start registering the device in Domoticz automatically, just
as it does in HA.

The alternative is Domoticz's native HTTP API via `requests`:

### CPython

```python
import requests
from rbamp import RbAmp, RbAmpStaleError

def publish_to_domoticz(idx, power, e_wh):
    """Domoticz svalue format for a kWh meter: 'POWER;ENERGY_WH'."""
    try:
        r = requests.get(
            "http://192.168.1.20:8080/json.htm",
            params={
                "type": "command",
                "param": "udevice",
                "idx": idx,
                "svalue": f"{power:.1f};{e_wh:.0f}",
            },
            timeout=5,
        )
        if r.status_code != 200:
            print(f"domoticz HTTP {r.status_code}")
    except requests.RequestException as e:
        print("domoticz request failed:", e)

# In your 60-second loop:
publish_to_domoticz(123 /* your idx */, snap.avg_p[0], dev.energy.wh(0))
```

### MicroPython

```python
import urequests as requests

def publish_to_domoticz(idx, power, e_wh):
    url = ("http://192.168.1.20:8080/json.htm"
           "?type=command&param=udevice"
           "&idx={}&svalue={:.1f};{:.0f}").format(idx, power, e_wh)
    try:
        r = requests.get(url)
        if r.status_code != 200:
            print("domoticz HTTP", r.status_code)
        r.close()   # MicroPython requires explicit close to free socket
    except OSError as e:
        print("domoticz request failed:", e)
```

Create a device in the Domoticz UI of type **General → kWh**
(incremental counter), grab its idx, and hard-code it into the script.

---

## InfluxDB OSS + Grafana

Write line-protocol points to InfluxDB directly from the Python host.

### CPython (via `requests`)

```python
import time, requests
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

INFLUX_HOST  = "192.168.1.30:8086"
INFLUX_ORG   = "homelab"
INFLUX_BKT   = "energy"
INFLUX_TOKEN = "your-token-here"

def push_influx(u, p, e_wh):
    url = (f"http://{INFLUX_HOST}/api/v2/write"
           f"?org={INFLUX_ORG}&bucket={INFLUX_BKT}&precision=s")
    body = f"rbamp,device=main voltage={u:.1f},power={p:.1f},energy={e_wh:.3f}"
    try:
        r = requests.post(url, data=body, headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type":  "text/plain",
        }, timeout=5)
        if r.status_code != 204:
            print(f"influx HTTP {r.status_code}: {r.text}")
    except requests.RequestException as e:
        print("influx request failed:", e)

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)

    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            continue
        push_influx(dev.voltage, snap.avg_p[0], dev.energy.wh(0))
```

### MicroPython (via `urequests`)

```python
import urequests as requests
import time

INFLUX_URL    = "http://192.168.1.30:8086/api/v2/write?org=homelab&bucket=energy&precision=s"
INFLUX_TOKEN  = "your-token-here"

def push_influx(u, p, e_wh):
    body = "rbamp,device=main voltage={:.1f},power={:.1f},energy={:.3f}".format(u, p, e_wh)
    try:
        r = requests.post(INFLUX_URL, data=body, headers={
            "Authorization": "Token " + INFLUX_TOKEN,
            "Content-Type":  "text/plain",
        })
        if r.status_code != 204:
            print("influx HTTP", r.status_code)
        r.close()
    except OSError as e:
        print("influx request failed:", e)
```

In Grafana, add an InfluxDB datasource, then a panel with a Flux query:

```text
from(bucket: "energy")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "rbamp" and r.device == "main")
  |> filter(fn: (r) => r._field == "power")
```

For a long-running soak deployment, additionally push diagnostic
counters to a separate measurement:

```python
# On MicroPython, with an explicit backend:
diag_body = (
    "rbamp_diag,device=main "
    f"sanity_reject={dev.sanity_reject_count},"
    f"retry_exhaust={backend.retry_exhaustion_count},"
    f"retry_total={backend.retry_count_total}"
)
# ...push to InfluxDB...
```

(On CPython there are no retry counters — `SMBusBackend` has no
retry layer. Push only `sanity_reject_count`.)

This gives you a bus-health chart alongside the energy data.

---

## Multi-platform — fan-out from a single Python host

If you need HA Auto-discovery, InfluxDB, and Node-RED all at once,
publish the state JSON once to MQTT and let each consumer subscribe
to `rbamp/+/state`. The Python host talks only to the MQTT broker —
the broker fans out to subscribers itself. Don't push from Python to
N HTTP endpoints directly: that couples the host to specific
consumers.

For a very high-rate stream (5 Hz RT via `dev.power[0]`), run a
sidecar Python script on the Pi that hosts the broker — subscribe to
the fast topic, decimate, and republish to the slow topics. The main
Python host (the one running `rbamp`) should stay focused on
`dev.read_*` calls without HTTP overhead.

### Asyncio variant (CPython)

For CPython with asyncio, you can combine the read + multi-target
publish into a single task:

```python
import asyncio, json
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

async def publish_mqtt(client, snap, dev): ...   # paho-mqtt async wrapper
async def push_influx(snap, dev): ...            # aiohttp instead of requests
async def push_webhook(snap, dev): ...           # aiohttp as well

async def main():
    with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        dev.set_ct_model(3)

        async for snap in dev.stream_period(interval_s=60.0):
            await asyncio.gather(
                publish_mqtt(mqtt_client, snap, dev),
                push_influx(snap, dev),
                push_webhook(snap, dev),
            )

asyncio.run(main())
```

`dev.stream_period()` is the package's async generator — see
[09 · API reference](09_api_reference.md), section "Per-period accounting".

---

## Links

- [06 · Examples](06_examples.md) — the base scripts these
  integrations build on (including Scenario 10 "async-streaming")
- [08 · Cloud integrations](08_cloud_integrations.md) — AWS IoT /
  Azure / GCP / InfluxDB Cloud / generic webhook
- [10 · Troubleshooting](10_troubleshooting.md) — MQTT-disconnect
  patterns, signal handling, TLS heap budget
