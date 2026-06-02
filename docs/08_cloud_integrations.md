# 08 · Cloud Integrations

How to push `rbamp` readings to cloud platforms — AWS IoT
Core, Azure IoT Hub, Google Cloud, and serverless / managed
observability pipelines. For each platform you get the Python setup
plus the cloud-side instructions.

Self-hosted DIY platforms (Home Assistant, Node-RED, InfluxDB OSS)
are covered in [07 · DIY Integrations](07_diy_integrations.md).

| Cloud | Transport | Auth | CPython | MicroPython |
|---|---|---|---|---|
| AWS IoT Core | MQTT/TLS | X.509 cert | `paho-mqtt` + `ssl` | `umqtt.simple(ssl=True)` |
| Azure IoT Hub | MQTT/TLS | SAS token | `paho-mqtt` + `ssl` | `umqtt.simple(ssl=True)` |
| Google Cloud IoT (deprecated 2023) | MQTT/TLS | JWT | (migration — see below) | same |
| InfluxDB Cloud | HTTPS line-protocol | API token | `requests` | `urequests` |
| Generic webhook / REST | HTTPS POST | API key | `requests` | `urequests` |

> ⚠ **TLS cost on MicroPython.** A TLS handshake needs
> ~30 KB of heap on an ESP32. On small targets (ESP32-C2) it can
> run out of memory if you also have WiFi + buffers running in
> parallel. On CPython it's effectively a non-issue (Pi 3+/4+ have
> 1+ GB of RAM).
>
> For memory-tight targets we recommend a **local Mosquitto
> bridge** on a Pi/SBC: the MicroPython device publishes to
> `mqtts://local-broker` (with minimal TLS, or no TLS at all
> inside the LAN), and the bridge relays to the cloud broker over TLS.

## TLS certificates — two approaches

### CPython — filesystem paths

CPython ships with system `ca-certificates` (Debian/Ubuntu:
`/etc/ssl/certs/`). For additional device certificates, place the
files next to your script and pass the paths to `ssl.SSLContext`:

```python
import ssl

ctx = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
# For mutual TLS (AWS IoT) — add the device cert + key:
ctx.load_cert_chain(certfile="device.cert.pem", keyfile="device.private.key")
```

### MicroPython — embedded bytes or filesystem

MicroPython has no full filesystem TLS storage. Two options:

1. **Embedded bytes** — cert files as Python objects in the script:

   ```python
   AWS_ROOT_CA = b"""-----BEGIN CERTIFICATE-----
   MIIDQTCC...
   -----END CERTIFICATE-----
   """

   import ssl
   ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
   ctx.load_verify_locations(cadata=AWS_ROOT_CA)
   ```

2. **Via the flash filesystem** — place the cert files in `/lib/certs/`
   on the device (using `mpremote cp`) and read them with the
   standard `open()`:

   ```python
   with open("/lib/certs/ca.pem", "rb") as f:
       ca_bytes = f.read()
   ctx.load_verify_locations(cadata=ca_bytes)
   ```

> On some MicroPython ports (especially ESP8266) `ssl.SSLContext`
> is missing — a simplified `ussl.wrap_socket(...)` with limited
> validation is used instead. For production deployments, choose an
> ESP32-family chip with a full TLS stack.

---

## AWS IoT Core

AWS IoT Core uses mutual TLS with an X.509 device certificate.

### Provisioning

1. AWS Console → **IoT Core → Manage → Things → Create things → Single thing**.
2. Generate the certificate + keys; download `device.cert.pem`,
   `device.private.key`, `AmazonRootCA1.pem`.
3. Attach a policy allowing `iot:Connect`, `iot:Publish`
   on `arn:aws:iot:<region>:<acc>:topic/rbamp/+/state`.
4. Note your AWS IoT endpoint:
   `xxxxxx-ats.iot.<region>.amazonaws.com:8883`.

### CPython version (AWS IoT)

```python
import json, ssl, time
from smbus2 import SMBus
import paho.mqtt.client as mqtt_client
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

AWS_ENDPOINT  = "xxxxxx-ats.iot.eu-west-1.amazonaws.com"
AWS_CLIENT_ID = "rbamp-main"

ctx = ssl.create_default_context(cafile="AmazonRootCA1.pem")
ctx.load_cert_chain(certfile="device.cert.pem",
                    keyfile="device.private.key")

mqtt = mqtt_client.Client(AWS_CLIENT_ID, protocol=mqtt_client.MQTTv311)
mqtt.tls_set_context(ctx)
mqtt.connect(AWS_ENDPOINT, 8883, keepalive=60)
mqtt.loop_start()

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)

    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            continue

        payload = {
            "voltage":  round(dev.voltage, 1),
            "power":    round(snap.avg_p[0], 1),
            "energy":   round(dev.energy.wh(0), 3),
            "freq":     round(dev.frequency, 1),
        }
        mqtt.publish("rbamp/main/state", json.dumps(payload), qos=1)
```

### MicroPython version (AWS IoT)

```python
import time, ujson, ssl
from machine import I2C, Pin
from umqtt.simple import MQTTClient
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

AWS_ENDPOINT  = b"xxxxxx-ats.iot.eu-west-1.amazonaws.com"

# Cert files in `/lib/certs/` (copied over with mpremote cp)
with open("/lib/certs/AmazonRootCA1.pem", "rb") as f:
    AWS_ROOT_CA = f.read()
with open("/lib/certs/device.cert.pem", "rb") as f:
    DEV_CERT = f.read()
with open("/lib/certs/device.private.key", "rb") as f:
    DEV_KEY = f.read()

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.load_verify_locations(cadata=AWS_ROOT_CA)
ctx.load_cert_chain(certfile="/lib/certs/device.cert.pem",
                    keyfile="/lib/certs/device.private.key")

mqtt = MQTTClient(b"rbamp-main", AWS_ENDPOINT, port=8883,
                  keepalive=60, ssl=ctx)
mqtt.connect()

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

        payload = ujson.dumps({
            "voltage":  round(dev.voltage, 1),
            "power":    round(snap.avg_p[0], 1),
            "energy":   round(dev.energy.wh(0), 3),
            "freq":     round(dev.frequency, 1),
        })
        mqtt.publish(b"rbamp/main/state", payload.encode(), qos=1)
        mqtt.ping()   # keepalive
```

### Cloud-side processing

- Create an **IoT Rule**:
  `SELECT *, topic(2) AS device FROM 'rbamp/+/state'` → Kinesis
  Data Firehose or Lambda for storage.
- For dashboards, use AWS IoT SiteWise (industrial historian) or
  Timestream (time-series DB) → QuickSight.
- If Home Assistant runs on a Pi and consumes data from AWS, set
  up a local Mosquitto bridge (cheaper and faster than HA → AWS
  directly).

### CPython alternative — `boto3` via the REST API

For infrequent publishing (once an hour or less) you can skip MQTT
entirely and use `boto3.client('iot-data').publish()`:

```python
import boto3, json
iot = boto3.client("iot-data", region_name="eu-west-1")
iot.publish(topic="rbamp/main/state",
            qos=1, payload=json.dumps(payload))
```

Authorization goes through the standard AWS credentials chain (env
vars / `~/.aws/credentials` / IAM role). Handy for batch cron
publishing from a Pi. MicroPython has no `boto3`.

### On cost

Publishing once a minute per device, AWS IoT comes to ~525k
messages per year → ~$2.60/year/device on the "Connectivity" +
"Messaging" tiers (2026, us-east-1). Timestream / Lambda costs
are separate.

---

## Azure IoT Hub

Azure IoT Hub supports MQTT 3.1.1 over TLS with SAS-token auth
(simpler than X.509 for home use).

### Provisioning

1. Azure Portal → **IoT Hub → Devices → New** → device ID
   `rbamp-main`, authentication = **Symmetric key**.
2. Save the connection string:
   `HostName=foo.azure-devices.net;DeviceId=rbamp-main;SharedAccessKey=…`.
3. Generate a SAS token. On CPython, use the `azure-iot-device`
   SDK or a manual HMAC:

   ```python
   import time, hmac, hashlib, urllib.parse, base64

   def generate_sas(uri, key, expiry_seconds=3600 * 24 * 365):
       expiry = int(time.time()) + expiry_seconds
       string_to_sign = urllib.parse.quote_plus(uri) + "\n" + str(expiry)
       sig = base64.b64encode(hmac.new(
           base64.b64decode(key),
           string_to_sign.encode(),
           hashlib.sha256
       ).digest())
       return ("SharedAccessSignature "
               f"sr={urllib.parse.quote_plus(uri)}"
               f"&sig={urllib.parse.quote_plus(sig.decode())}"
               f"&se={expiry}")

   sas = generate_sas("foo.azure-devices.net/devices/rbamp-main", "BASE64KEY=")
   ```

4. On MicroPython, either use `ucryptolib` + a manual HMAC (slow), or
   simply hardcode a 1-year SAS token generated on your build
   machine.

### CPython version (Azure IoT Hub)

```python
import json, ssl, time
import paho.mqtt.client as mqtt_client

AZ_HOST      = "foo.azure-devices.net"
AZ_DEVICE    = "rbamp-main"
AZ_USERNAME  = f"{AZ_HOST}/{AZ_DEVICE}/?api-version=2021-04-12"
AZ_SAS       = "SharedAccessSignature sr=...&sig=...&se=..."   # see above

ctx = ssl.create_default_context()   # Uses the system CAs

mqtt = mqtt_client.Client(AZ_DEVICE, protocol=mqtt_client.MQTTv311)
mqtt.username_pw_set(AZ_USERNAME, password=AZ_SAS)
mqtt.tls_set_context(ctx)
mqtt.connect(AZ_HOST, 8883, keepalive=60)
mqtt.loop_start()

# In your 60-second loop:
mqtt.publish(f"devices/{AZ_DEVICE}/messages/events/",
             json.dumps(payload), qos=1)
```

### MicroPython version (Azure IoT Hub)

```python
from umqtt.simple import MQTTClient
import ssl

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
# Azure root CA (Baltimore CyberTrust or DigiCert Global G2):
with open("/lib/certs/azure_ca.pem", "rb") as f:
    ctx.load_verify_locations(cadata=f.read())

mqtt = MQTTClient(b"rbamp-main", b"foo.azure-devices.net", port=8883,
                  user=AZ_USERNAME.encode(),
                  password=AZ_SAS.encode(),
                  keepalive=60, ssl=ctx)
mqtt.connect()

# In the loop:
mqtt.publish(b"devices/rbamp-main/messages/events/",
             ujson.dumps(payload).encode(), qos=1)
```

### SAS-token expiry

SAS tokens carry an `expiry` claim — typical lifetimes range from 1
hour to 1 year. For a CPython deployment, use the `azure-iot-device`
SDK; it rotates the token for you. For MicroPython, generate a
1-year token on the build machine and bake it into the script, or
implement HMAC rotation via `ucryptolib`.

### Cloud-side processing (Azure)

- Route messages to **Event Hubs** for high-throughput
  ingestion → Stream Analytics → Power BI dashboards.
- Cheaper alternative: messages → **Storage Account (blob)** →
  Synapse Serverless SQL for ad-hoc queries.

---

## Google Cloud IoT (DEPRECATED 2023)

Google shut down Cloud IoT Core in 2023. Migration paths:

- **MQTT broker on Compute Engine** (you deploy Mosquitto in a VM
  yourself) — the same pattern as in
  [07 · DIY Integrations](07_diy_integrations.md), but pointing at
  your VM's public IP.
- **HiveMQ Cloud / EMQX Cloud** — managed MQTT brokers, ~$10-20/mo
  on hobbyist tiers.
- **Pub/Sub over HTTPS** — publish directly to a Pub/Sub topic
  via the REST API. On CPython, it's easiest via the
  `google-cloud-pubsub` SDK + a service-account JSON key:

  ```python
  from google.cloud import pubsub_v1
  import json

  publisher = pubsub_v1.PublisherClient.from_service_account_json("key.json")
  topic_path = publisher.topic_path("my-project", "rbamp")
  publisher.publish(topic_path, json.dumps(payload).encode())
  ```

For MicroPython Pub/Sub over HTTPS, see the "Generic webhook / REST"
section below, substituting the Pub/Sub publish endpoint + an OAuth
bearer token.

---

## InfluxDB Cloud (TLSv1.3 + line-protocol)

InfluxDB Cloud (Serverless tier) accepts line-protocol over
HTTPS — the same form as the OSS path in
[07 · DIY Integrations](07_diy_integrations.md), but with
`cloud2.influxdata.com` as the host and an API token for auth.

### CPython version (InfluxDB Cloud)

```python
import requests, time
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

INFLUX_URL   = "https://us-east-1-1.aws.cloud2.influxdata.com/api/v2/write?org=MyOrg&bucket=energy&precision=s"
INFLUX_TOKEN = "your-rw-token"

def push_influx(u, p, e_wh):
    body = f"rbamp,device=main voltage={u:.1f},power={p:.1f},energy={e_wh:.3f}"
    try:
        r = requests.post(INFLUX_URL, data=body,
                          headers={
                              "Authorization": f"Token {INFLUX_TOKEN}",
                              "Content-Type":  "text/plain",
                          },
                          timeout=10)   # TLS handshake can take up to 3 s
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

`requests` uses the system CA bundle — on a stock Debian/Ubuntu
CPython install this "just works".

### MicroPython version (InfluxDB Cloud)

```python
import urequests as requests, ujson, ssl, time
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

INFLUX_URL   = "https://us-east-1-1.aws.cloud2.influxdata.com/api/v2/write?org=MyOrg&bucket=energy&precision=s"
INFLUX_TOKEN = "your-rw-token"

# Custom SSL context with embedded CA (DigiCert Global G2)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
with open("/lib/certs/digicert_g2.pem", "rb") as f:
    ctx.load_verify_locations(cadata=f.read())

def push_influx(u, p, e_wh):
    body = "rbamp,device=main voltage={:.1f},power={:.1f},energy={:.3f}".format(u, p, e_wh)
    try:
        r = requests.post(INFLUX_URL, data=body,
                          headers={
                              "Authorization": "Token " + INFLUX_TOKEN,
                              "Content-Type":  "text/plain",
                          })
        if r.status_code != 204:
            print("influx HTTP", r.status_code)
        r.close()   # MicroPython socket cleanup
    except OSError as e:
        print("influx request failed:", e)
```

> On most MicroPython ports `urequests` uses a built-in CA bundle
> (without verification) or the system one. For strict
> validation, use `urequests.post(..., verify=True)` if your port
> supports it; otherwise go through `ssl.SSLContext` + low-level
> `usocket`.

The free InfluxDB Cloud tier (5 GB / 30-day retention) covers
~5,000 points at a one-minute cadence per day — generous for home
use.

---

## Generic webhook / REST

Publishing to any HTTPS endpoint with an API key — works with
IFTTT webhooks, custom Flask / FastAPI services, or any cloud
function (AWS Lambda / Azure Functions / GCP Cloud Run) exposed
over HTTPS.

### CPython

```python
import requests, time, json

WEBHOOK_URL = "https://your-api.example.com/ingest"
API_KEY     = "Bearer your-token-here"

def push_webhook(u, p, e_wh):
    body = {
        "ts":      time.time(),
        "voltage": u,
        "power":   p,
        "energy":  e_wh,
    }
    try:
        r = requests.post(WEBHOOK_URL, json=body, headers={
            "Authorization": API_KEY,
        }, timeout=10)
        if not (200 <= r.status_code < 300):
            print(f"webhook HTTP {r.status_code}: {r.text}")
    except requests.RequestException as e:
        print("webhook failed:", e)
```

### MicroPython

```python
import urequests as requests, ujson, time

def push_webhook(u, p, e_wh):
    body = ujson.dumps({
        "ts":      time.time(),
        "voltage": u,
        "power":   p,
        "energy":  e_wh,
    })
    try:
        r = requests.post(WEBHOOK_URL, data=body, headers={
            "Authorization": API_KEY,
            "Content-Type":  "application/json",
        })
        if not (200 <= r.status_code < 300):
            print("webhook HTTP", r.status_code)
        r.close()
    except OSError as e:
        print("webhook failed:", e)
```

At a low rate (≤ once a minute) the overhead is acceptable. At
higher rates, batch the data on the Python side (accumulate
10 minutes in a ring buffer, publish one bulk JSON) so you don't
pay for a TLS handshake per request.

---

## Hybrid: local storage + cloud sync

For offline-tolerant deployments: log locally once a minute and
send to the cloud once an hour. Survives WiFi drops / cloud
outages with no data loss.

### CPython (rotating file + hourly sync)

```python
import logging, time, csv, requests
from logging.handlers import RotatingFileHandler
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass, RbAmpStaleError

log = logging.getLogger("rbamp.csv")
log.setLevel(logging.INFO)
handler = RotatingFileHandler("rbamp.csv", maxBytes=1_000_000, backupCount=5)
handler.setFormatter(logging.Formatter("%(asctime)s,%(message)s",
                                       datefmt="%Y-%m-%dT%H:%M:%S"))
log.addHandler(handler)

def sync_to_cloud_if_due():
    """Once an hour: read rbamp.csv, send new rows to InfluxDB Cloud."""
    # ...uses push_influx from the section above + a state file for the offset...

with SMBus(1) as bus, RbAmp(bus, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)

    last_sync = 0
    while True:
        time.sleep(60)
        try:
            snap = dev.read_period_snapshot()
        except RbAmpStaleError:
            log.warning("stale")
            continue
        log.info(f"{snap.avg_p[0]:.1f},{dev.energy.wh(0):.4f},"
                 f"{snap.master_dt_ms}")

        if time.time() - last_sync > 3600:
            sync_to_cloud_if_due()
            last_sync = time.time()
```

### MicroPython (file append + retry on connect)

```python
import time, ujson

def append_local(snap, dev):
    with open("/lib/log.csv", "a") as f:
        f.write("{:.0f},{:.1f},{:.4f},{}\n".format(
            time.time(), snap.avg_p[0], dev.energy.wh(0),
            snap.master_dt_ms))

def sync_if_due():
    """Once an hour: read the accumulated log, send it to the cloud."""
    try:
        with open("/lib/log.csv") as f:
            for line in f:
                # ...push_webhook(line) with retry on failure...
                pass
        # archive successful upload
        import os
        os.rename("/lib/log.csv", "/lib/log_pushed.csv")
    except OSError:
        pass   # offline / no file yet
```

The package's `dev.energy.wh(0)` accumulator keeps counting
throughout the offline window — no data is lost as long as the
Python host stays powered.

---

## Energy budget (for MicroPython battery deployments)

A TLS handshake is an expensive operation: ~3 s + ~30 KB of heap
per connection. For MicroPython deep-sleep scenarios (see
[06 · Examples](06_examples.md), Scenario 9):

- **Reuse the TLS session** between wakeups via TLS session
  resumption — `umqtt.simple` supports a persistent session via
  `clean_session=False`, and the broker remembers your subscriptions.
- **Batch** several measurements on local flash and publish them
  in a single bulk POST per wakeup — the pattern from the "Hybrid"
  section above.
- **MQTT keepalive** doesn't need to be held outside deep sleep —
  the device sleeps between wakeups, and the TLS handshake runs on
  every wakeup (unless resumed).

At a 10-minute wakeup interval on a 2000 mAh Li-ion cell, expect
~3 months of operation on WiFi + TLS, versus ~6 months on WiFi +
plain MQTT (see Scenario 9).

CPython on a Raspberry Pi typically runs 24/7 from a power supply —
the TLS energy budget isn't critical. For a production deployment
with Pi power-consumption optimization, see
[10 · Troubleshooting](10_troubleshooting.md), section "Script hangs /
crashes on timeout" (a signal-aware loop for `systemctl suspend`).

---

## Links

- [06 · Examples](06_examples.md) — the base projects that cloud
  integrations build on (especially Scenario 9 "deep-sleep
  logger" and Scenario 10 "async streaming")
- [07 · DIY Integrations](07_diy_integrations.md) — self-hosted
  alternatives
- [10 · Troubleshooting](10_troubleshooting.md) — TLS handshake /
  heap budget / signal handling


---

[← DIY Integrations](07_diy_integrations.md) | [Contents](README.md) | [API Reference →](09_api_reference.md)
