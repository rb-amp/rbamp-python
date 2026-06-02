# 04 · Hardware Setup

This chapter covers the physical connection of the **rbAmp** module
to a Python host: the LV-side pinout, power requirements, I²C-level
compatibility, bus length, and the standard default pins for each
host type. Both **CPython** on Linux SBCs and x86 + USB-I²C, and
**MicroPython** on ESP32 / RP2040 / STM32 / Pyboard are supported.

The mains side of the module is already routed inside the enclosure
and galvanically isolated — the user only works with the LV side
(power + I²C + optional `DRDY`).

## LV-side pinout

The module runs on 3.3 V I²C logic and is **5 V tolerant** on the
signal lines. All LV pins are fully galvanically isolated from mains.

| Pin | Signal | Purpose |
|---|---|---|
| `VCC` | +5 V (4.5..5.5 V) | Module power; 4.5 V minimum for correct operation |
| `GND` | Ground | Common with the host — **mandatory** |
| `SDA` | I²C SDA | I²C data |
| `SCL` | I²C SCL | I²C clock |
| `DRDY` | Data-ready (optional) | Open-drain, LOW pulse ~10 µs every ~200 ms |

## Power

### VCC parameters

- **Nominal voltage**: 5 V DC
- **Allowed range**: 4.5..5.5 V
- **Supply current**: ~15 mA typical, ~25 mA peak during a flash write
- **Ripple**: < 50 mV peak-to-peak (for ADC accuracy)

The module carries an onboard regulator, RC filter, ceramic
capacitors, and a ferrite bead — no additional filtering is required
from the user. You can power the module from the `+5V` or `5V0`
line of any host (Raspberry Pi pin 2/4, ESP32 DevKitC USB-5V,
Pyboard V+, etc.).

### I²C level compatibility

- `SDA` / `SCL` operate on **3.3 V logic** (the built-in pull-ups
  pull the lines up to 3.3 V).
- The lines are **5 V tolerant**, but all the hosts mentioned below
  (Raspberry Pi / ESP32 family / RP2040 / STM32 / Pyboard) are
  already on 3.3 V logic — no level translators are needed.
- A `pyftdi` USB-I²C dongle (FT232H / MCP2221) is also 3.3 V logic,
  for a direct connection.

### Power-on behavior

- Cold start: ~250 ms to the first valid result.
- After a reset (software or brown-out): ~250 ms to recover.
- Until the first valid result, the registers read as `0` and the
  `DATA_VALID` flag is 0.

The package waits for the module to be ready inside `dev.begin()` —
user code simply calls `begin()` (or uses the context manager
`with RbAmp(bus, 0x50) as dev:`, which calls `begin()` automatically
in `__enter__`).

### Isolation

Inside the module there's a galvanic isolation barrier between the
mains side (CT clamp, voltage divider) and the LV side (I²C). The
implications:

- The module's `GND` is **not connected** to the mains line or
  neutral.
- Connecting the LV side to a Python host is safe — there's no risk
  of a short circuit through ground.

> ⚠ Do not open the module enclosure — doing so voids the factory
> calibration.

## Onboard pull-up resistors

**Important**: every rbAmp module has **built-in 4.7 kΩ pull-up
resistors on SDA and SCL to 3.3 V**. For a **single module** on
the bus, no external pull-ups are needed — connect the host directly.

### When to disable the built-in pull-ups

If you have several rbAmp modules on the bus (or other I²C devices
with their own pull-ups), connecting them in parallel produces an
effective resistance that's too low, which:

- Increases bus current draw
- May exceed the maximum sink current of the I²C master's output
  stage
- Adds capacitive load on a long bus while doing little to improve
  noise immunity

The fix: on the underside of each module's PCB, next to the pull-up
resistors, there are **solder jumpers** (silkscreen `Pull-Up`).
Cut them with a sharp blade:

- Keep the pull-ups enabled on **just one** module (or relocate them
  to a single point near the host).
- Cut the jumpers on the remaining modules.

### Rule of thumb

| rbAmp modules on the bus | Pull-ups |
|:---:|---|
| 1 | Leave as-is (the built-in 4.7 kΩ works) |
| 2 | Keep on one, cut on the other |
| 3+ | Cut on all modules, fit a single external 2.2…4.7 kΩ pair near the host |

> **If in doubt**: measure the resistance between SDA and VCC with
> the devices powered off. It should fall in the **1.5..4.7 kΩ**
> range. Lower than that means too many active pull-ups in parallel.

## I²C bus parameters

- **Default address**: `0x50` (7-bit)
- **Address range**: `0x08..0x77` (reassigned at the factory)
- **Recommended speed on MicroPython + ESP32**: **50 kHz** —
  mitigates the baseline NACK pattern with v1 firmware (see the
  section below).
- **Recommended speed on CPython + Linux SBC**: **100 kHz**
  (the NACK pattern does not appear through the kernel I²C driver —
  Linux smbus2 doesn't have the retry problem described for ESP-IDF).
- **Pointer auto-increment**: **NO**. Each byte read is a separate
  transaction with an explicit register address. The package handles
  this correctly; user code never sees it.

### Bus length

I²C is a short bus (~30 cm by default). Topologies validated for
rbAmp:

| Cable | Max length | Speed |
|---|---|---|
| Standard JST / flat 4-conductor | up to 0.3 m | 100 kHz |
| Twisted pair UTP (cat-5/5e/6) — SDA+GND and SCL+GND in **separate pairs** | up to 1 m | 100 kHz |
| Twisted pair + I²C buffer (PCA9515 / TCA9617) | up to 3 m | 100 kHz |
| Differential bus (PCA9615 / LTC4332) | up to 100 m | 100 kHz |

> For lengths over 0.3 m a **twisted pair is mandatory**: SDA and
> SCL must be in **different** pairs, each with its own ground. SDA
> and SCL in the same pair create cross-capacitive coupling that
> distorts the edges.

### Multi-module bus

Several rbAmp modules can share a single I²C bus:

- **Number of modules**: up to ~16 (limited by total bus
  capacitance — ≤ 400 pF at 100 kHz)
- **Addresses**: each module has its own 7-bit address. All modules
  ship from the factory at `0x50` — readdress them one at a time
  before connecting them in parallel (a factory / installer
  operation).
- **Pull-ups**: follow the rule above — cut the built-in ones on
  all modules except one, or relocate them to a single point.
- **Syncing peak periods**: see [06 · Examples](06_examples.md),
  the "Monitoring multiple modules" scenario — a sequential
  `dev.latch_period()` + a shared `time.sleep(0.050)` settle, then
  `dev.read_period_snapshot(skip_latch=True)` on each device.
  Skew between modules at 100 kHz is ~1 ms per device, < 0.2 % of a
  60-second period.

> **Changing the address** is a factory / installer operation and is
> not part of the normal user API. See the `RbAmpModeError` warning
> in [09 · API Reference](09_api_reference.md) on
> `dev.prepare_address_change` / `dev.commit_address_change`. If a
> deployed module needs a different address, contact your supplier.

## CPython hosts (Linux SBC + x86)

### Raspberry Pi (any model)

I²C bus 1 is routed to the 40-pin header. On most models (1B+ /
2 / 3 / 4 / 5) the pins are the same:

| rbAmp | RPi 40-pin header | GPIO (BCM) |
|---|---|---|
| `VCC` | pin 2 (+5V) | — |
| `GND` | pin 6 (or any GND) | — |
| `SDA` | pin 3 | GPIO 2 (SDA1) |
| `SCL` | pin 5 | GPIO 3 (SCL1) |
| `DRDY` | (optional) | any free GPIO with irq support |

Enable the kernel I²C driver: `sudo raspi-config` →
**3 Interface Options** → **I2C** → **Yes**. Or edit
`/boot/config.txt` manually:

```text
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=100000   # 100 kHz (default 100 kHz)
```

Usage from Python:

```python
from smbus2 import SMBus
from rbamp import RbAmp

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        print(dev.voltage, "V")
```

Installing `smbus2`: `pip install smbus2` (or the system
`sudo apt install python3-smbus`).

### Orange Pi 5 / Rock Pi 5 / other Linux SBCs

The same `smbus2.SMBus(N)` where N is the bus number from
`/dev/i2c-N`. Usually `/dev/i2c-1` or `/dev/i2c-0`. Check with:

```sh
ls /dev/i2c-*
i2cdetect -y 1     # should show 0x50
```

The pinout depends on the specific board — consult its datasheet.

### x86 / Windows / macOS — via a USB-I²C dongle

x86 hosts have no native I²C. The solution is a USB-I²C dongle
(FT232H, MCP2221, Bus Pirate v4, CH341). On its own,
`pyftdi.i2c.I2cController` has **neither** an `smbus2`-compatible
signature (`read_byte_data` / `write_byte_data`) nor a MicroPython
signature (`readfrom_mem` / `writeto_mem`) — so you can't pass it
directly to `RbAmp(...)`; bus autodetect will raise `RbAmpParamError`.

x86 dongles require a **thin wrapper class** implementing the
duck-typed backend interface (`read_byte` / `write_byte` /
`register_acks` / `now_ms` — see
[09 · API Reference](09_api_reference.md), the "Bus autodetect"
section). Sample wrapper code for various dongles is planned for the
`rbamp.adapters.*` submodule in v1.3+ — until then, write your own
wrapper following the `_io_smbus.py` / `_io_micropython.py` example.

## MicroPython hosts

### ESP32 / ESP32-S2 / ESP32-S3 / ESP32-C3

```python
from machine import I2C, Pin
from rbamp import RbAmp

# ESP32 DevKitC default I²C pinning
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)   # 50 kHz per SPEC §B.5
with RbAmp(i2c, 0x50) as dev:
    print(dev.voltage, "V")
```

| Chip | Default SDA / SCL (recommended) | I²C port |
|---|---|---|
| ESP32 (original / WROOM / DevKitC) | GPIO 21 / GPIO 22 | `I2C(0, ...)` |
| ESP32-S2 | GPIO 21 / GPIO 22 (same convention) | `I2C(0, ...)` |
| ESP32-S3 | GPIO 8 / GPIO 9 (default on N16R8 boards) | `I2C(0, ...)` |
| ESP32-C3 | GPIO 5 / GPIO 6 | `I2C(0, ...)` |

For other chips, check your ESP32's MicroPython firmware
(`mpremote eval 'help(machine.I2C)'`).

### Raspberry Pi Pico / Pico W (RP2040)

```python
from machine import I2C, Pin
from rbamp import RbAmp

# Pico — two hardware I²C blocks (I2C0 and I2C1)
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=100_000)   # 100 kHz OK on RP2040
with RbAmp(i2c, 0x50) as dev:
    print(dev.voltage, "V")
```

The Pico doesn't suffer from the baseline NACK pattern —
`MachineI2CBackend` auto-detects the RP2040 and lowers the retry
default to 1 attempt.

### STM32 / Pyboard / Nucleo (via MicroPython)

```python
from machine import I2C
from rbamp import RbAmp

# Pyboard — soft-I²C or a hardware I²C block
i2c = I2C(1, freq=100_000)   # bus 1 — X9/X10 on the Pyboard
with RbAmp(i2c, 0x50) as dev:
    print(dev.voltage, "V")
```

STM32 doesn't suffer from the baseline NACK pattern —
`MachineI2CBackend` lowers the retry default to 1 attempt on these
boards.

## MicroPython baseline NACK pattern on ESP32 — 50 kHz

The current rbAmp firmware, when running with MicroPython on
**ESP32**, exhibits a **~20 % NACK rate at 100 kHz** and **< 5 % at
50 kHz**. This is specific to the ESP-IDF I²C stack that underlies
MicroPython on ESP32. On other MicroPython ports (RP2040 / STM32 /
Pyboard) the problem does not appear.

The package addresses this in two layers:

1. **A default speed of 50 kHz** — we recommend passing
   `freq=50_000` to `machine.I2C(...)` on ESP32.
2. **Per-byte retry** in `MachineI2CBackend` — by default 3 attempts
   × a 5 ms gap. Configurable via the advanced API:

   ```python
   from rbamp._io_micropython import MachineI2CBackend
   from rbamp import RbAmp

   backend = MachineI2CBackend(i2c, retry_attempts=5, retry_gap_ms=5)
   dev = RbAmp(backend, 0x50)   # the address is a parameter of RbAmp, not the backend
   ```

Once firmware v1.1+ ships with the slave-side NACK fix, 100 kHz
becomes a working speed on ESP32 too:

```python
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100_000)
backend = MachineI2CBackend(i2c, retry_attempts=1)
dev = RbAmp(backend, 0x50)
```

For more on diagnosing bus-level problems, see
[10 · Troubleshooting](10_troubleshooting.md). The
`MachineI2CBackend.retry_exhaustion_count` and
`RbAmp.sanity_reject_count` counters are available for long-soak
observability.

## DATA_READY (DRDY)

An optional pin for polling optimization. If your application polls
the module no more than once every 200 ms, `DRDY` can be left
unconnected.

### Electrical parameters

- **Output type**: open-drain (no active pull-up to VCC)
- **Idle level**: HIGH (requires a pull-up on the host side — the
  GPIO's built-in pull-up or an external resistor; 10 kΩ to 3.3 V
  recommended)
- **Ready pulse**: LOW for ~10 µs after the RT registers are
  updated with fresh data
- **Pulse rate**: ~5 Hz (one pulse per ~200 ms RT window)

### Semantics

A falling edge on `DRDY` guarantees that **all RT registers are
synchronized and published** (the firmware updates them atomically
in the ISR before pulling the pin low). After the falling edge, the
master can read the RT block with no risk of getting a split sample.

### Interrupt pattern on MicroPython

```python
from machine import Pin
import uasyncio as asyncio
from rbamp import RbAmp

DRDY_PIN = 15
drdy_event = asyncio.Event()

def drdy_irq(pin):
    drdy_event.set()   # ISR-safe — Event.set() is atomic on uPy

async def bidir_task(dev):
    while True:
        await drdy_event.wait()
        drdy_event.clear()
        try:
            p = dev.read_power(0)
            # ...integrate + publish...
        except Exception as e:
            print("read failed:", e)

# In main:
drdy = Pin(DRDY_PIN, Pin.IN, Pin.PULL_UP)
drdy.irq(trigger=Pin.IRQ_FALLING, handler=drdy_irq)
asyncio.run(bidir_task(dev))
```

### Pattern on CPython (Linux SBC)

On a Raspberry Pi via `gpiozero` or `RPi.GPIO`:

```python
from gpiozero import Button
from threading import Event
from rbamp import RbAmp

drdy_event = Event()
drdy = Button(15, pull_up=True, bounce_time=None)   # GPIO 15 as an example
drdy.when_pressed = lambda: drdy_event.set()       # falling edge

while True:
    drdy_event.wait()
    drdy_event.clear()
    try:
        p = dev.read_power(0)
        # ...integrate + publish...
    except Exception as e:
        print("read failed:", e)
```

`DRDY` is **optional** — polling at any rate ≤ 5 Hz works without
it. The package doesn't depend on `DRDY` in any of its paths.

## Links

- [05 · Quickstart](05_quickstart.md) — your first working script
  for both backends
- [06 · Examples](06_examples.md) — working scenarios (including the
  multi-module bus + async streaming)
- [09 · API Reference](09_api_reference.md) — backend details and
  retry/sanity counter accessors
- [10 · Troubleshooting](10_troubleshooting.md) — bus-level debug,
  the NACK pattern, retry+sanity discipline


---

[← Sensor Selection](03_sensor_selection.md) | [Contents](README.md) | [Quickstart →](05_quickstart.md)
