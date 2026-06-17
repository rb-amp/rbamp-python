# 04 · Wiring

This chapter covers the physical connection of the **rbAmp** module to
a Python host: the LV-side pinout, power requirements, I²C level
compatibility, bus length, and the standard default pins for each host
type. Supported hosts are **CPython** on Linux SBCs and x86 + USB-I²C,
and **MicroPython** on ESP32 / RP2040 / STM32 / Pyboard.

The module's mains side is already routed inside the enclosure and
galvanically isolated — the user works only with the LV side (power +
I²C + optional `DRDY`).

## LV-side pinout

The module runs on 3.3 V I²C logic and is **5 V tolerant** on the
signal lines. All LV pins are fully galvanically isolated from mains.

| Pin | Signal | Purpose |
|---|---|---|
| `VCC` | +5 V (4.5..5.5 V) | Module power; minimum 4.5 V for correct operation |
| `GND` | Ground | Common with the host — **mandatory** |
| `SDA` | I²C SDA | I²C data |
| `SCL` | I²C SCL | I²C clock |
| `DRDY` | Data-ready (optional) | Open-drain, LOW pulse ~10 µs every ~200 ms |

## Power

### VCC parameters

- **Nominal voltage**: 5 V DC
- **Permitted range**: 4.5..5.5 V
- **Supply current**: ~15 mA typical, ~25 mA peak when writing to flash
- **Ripple**: < 50 mV peak-to-peak (for ADC accuracy)

The module carries an on-board regulator, an RC filter, ceramic
capacitors and a ferrite bead — no additional filtering is required
from the user. You can power the module from the `+5V` or `5V0` rail of
any host (Raspberry Pi pin 2/4, ESP32 DevKitC USB-5V, Pyboard V+, etc.).

### I²C level compatibility

- `SDA` / `SCL` operate on **3.3 V logic** (the built-in pull-ups pull
  the lines up to 3.3 V).
- The lines are **5 V tolerant**, but all hosts mentioned below
  (Raspberry Pi / ESP32-family / RP2040 / STM32 / Pyboard) are already
  on 3.3 V logic — no level shifters are required.
- The `pyftdi` USB-I²C dongle (FT232H / MCP2221) is also 3.3 V logic —
  direct connection.

### Power-on behavior

- Cold start: ~250 ms to the first valid result.
- After a reset (software or brown-out): ~250 ms to recover.
- Until the first valid result, the registers read as `0` and the
  `DATA_VALID` flag = 0.

The package waits for the module to become ready inside `dev.begin()` —
user code simply calls `begin()` (or uses the context manager
`with RbAmp(bus, 0x50) as dev:`, which calls `begin()` automatically in
`__enter__`).

### Isolation

Inside the module there is a galvanic isolation barrier between the
mains side (CT clamp, voltage divider) and the LV side (I²C).
Consequences:

- The module's `GND` is **not connected** to mains line or neutral.
- Connecting the LV side to the Python host is safe — there is no risk
  of a short circuit through ground.

> ⚠ Do not open the module enclosure — doing so voids the factory
> calibration.

## Built-in pull-up resistors — on the board

**Important**: every rbAmp module ships with **built-in 4.7 kΩ pull-up
resistors on SDA and SCL to 3.3 V**. For a **single module** on the
bus, no external pull-ups are required — connect the host directly.

### When to disable the built-in pull-ups

If there are several rbAmp modules on the bus (or other I²C devices
with their own pull-ups), connecting them in parallel yields too low an
effective resistance, which:

- Increases bus current draw
- May exceed the maximum sink current of the I²C master's output stage
- Adds capacitive loading on a long bus while doing little to improve
  noise immunity

Solution: on the bottom side of each module's board, next to the
pull-up resistors, there are **solder jumpers** (silkscreen `Pull-Up`).
Cut them with a sharp blade:

- Leave the pull-ups enabled on **only one** module (or move them to a
  single point near the host).
- Cut the jumpers on the remaining modules.

### Rule of thumb

| rbAmp modules on the bus | Pull-ups |
|:---:|---|
| 1 | Leave as-is (built-in 4.7 kΩ works) |
| 2 | Keep on one, cut on the other |
| 3+ | Cut on all modules, fit a single external 2.2…4.7 kΩ pair near the host |

> **If in doubt**: measure the resistance between SDA and VCC with the
> devices powered off. It should be in the **1.5..4.7 kΩ** range. Less
> than that means too many active pull-ups in parallel.

## I²C bus parameters

- **Default address**: `0x50` (7-bit)
- **Address range**: `0x08..0x77` (reassignable at the factory)
- **Recommended speed on MicroPython + ESP32**: **50 kHz** — mitigates
  the baseline NACK pattern with firmware v1 (see the section below).
- **Recommended speed on CPython + Linux SBC**: **100 kHz** (the NACK
  pattern does not appear through the kernel I²C driver — the Linux
  smbus2 path does not have the retry problem described for ESP-IDF).
- **Pointer auto-increment**: **NO**. Each byte read is a separate
  transaction with an explicit register address. The package does this
  correctly on its own; user code never sees it.

### Bus length

I²C is a short bus (~30 cm by default). Tested topologies for rbAmp:

| Cable | Max length | Speed |
|---|---|---|
| Standard JST / flat 4-conductor | up to 0.3 m | 100 kHz |
| Twisted pair UTP (cat-5/5e/6) — SDA+GND and SCL+GND in **separate pairs** | up to 1 m | 100 kHz |
| Twisted pair + I²C buffer (PCA9515 / TCA9617) | up to 3 m | 100 kHz |
| Differential bus (PCA9615 / LTC4332) | up to 100 m | 100 kHz |

> For lengths over 0.3 m, **twisted pair is mandatory**: SDA and SCL
> must be in **different** pairs, each with its own ground. SDA and SCL
> in the same pair create cross-coupling capacitance that distorts the
> edges.

### Multi-module bus — the primary topology

The library's **canonical use case** is several rbAmp modules on a
single I²C bus, managed by one ESP32 / Raspberry Pi / SBC through
`RbAmpFleet`. All the rules and optimizations below are designed for
exactly this topology; a single module is a special case (the same
handle, but the fleet calls degenerate to trivial ones).

#### Typical deployments

| Scenario | Configuration | Module count |
|---|---|---|
| **80%-canonical**: home service entry + sub-meters | 1× UI1 (entry, mains-energy) + 3-6× UI1/I1 (loads) | 4-7 |
| Sub-panel with current breakdown | 1× UI1 (entry) + 1-2× I2/I3 (per-circuit current) | 3-5 |
| Industrial sub-metering | 1× UI1 (entry) + N× I2/I3 (per machine, current breakdown) | up to ~16 |

#### Electrical limits

- **Module count**: up to ~16 (total bus capacitance ≤ 400 pF at 100 kHz; at 50 kHz the margin is greater).
- **Pull-ups for the fleet bus (important)**: each module ships with built-in 4.7 kΩ. On a multi-module bus, N pull-ups work in parallel → the sink is overloaded and the bus cannot pull down to LOW. **Cut the built-in pull-ups on all modules but one** (or cut them on all and keep **only an external** ~4.7 kΩ pair to 3.3 V at one point on the bus — recommended for long runs / noise / ESP32 as master).
- The ESP32 internal pull-ups (~45 kΩ) are **too weak** for a multi-module bus with real trace length — **external** ~4.7 kΩ pull-ups are needed. This is part of the three-layer mitigation against bus-hang on a marginal bus — see [10 · Troubleshooting](10_troubleshooting.md), section "Script hangs / fails on timeout".

#### Addressing — provisioning and field-swap (production-OK)

The default factory address is `0x50`. Each new or replacement module
goes through **provisioning** (once): re-addressing from `0x50` to a
working address + optional saving of the configuration (CT models,
group_id, label) to flash. Develop-mode is **not required** — the
address change works directly in production through a two-phase commit.

**Method 1 — low-level `prepare_address_change` / `commit_address_change`** (current API):

```python
dev.prepare_address_change(0x52)   # arms candidate, 5 s window
dev.commit_address_change()        # magic 0xA5 + CMD_COMMIT_ADDR + reset
time.sleep(0.3)                     # boot window
# dev.address is now == 0x52
```

**Method 2 — the `RbAmpFleet` wrapper** (recommended for fleet deployments):

```python
from rbamp import RbAmpFleet

fleet = RbAmpFleet(bus)
fleet.scan()
fleet.assign_address(dev, 0x52)   # two-phase + reset + handle rebind
```

The wire protocol under the hood (for reference):

```text
1. write candidate_addr → REG_I2C_ADDRESS (0x30, in RAM)
2. write 0xA5 → REG_ADDR_COMMIT_MAGIC (0x31, armed; 5-second window)
3. issue CMD_COMMIT_ADDR (opcode 0x30 in REG_COMMAND)
4. wait ~700 ms (flash erase + write)
5. issue CMD_RESET (opcode 0x01) — CMD_COMMIT_ADDR does not auto-reset
6. wait ~300 ms; the module responds at the new address
```

> ✅ **v1.3 addr-boot-sync**: `REG_I2C_ADDRESS (0x30)` at boot reads the
> **active** address. After the post-commit reboot, reading 0x30 shows
> the new active one. During staging it echoes the candidate until
> commit. The library guarantees the arm-state is cleared via
> `try/finally` on partial failure — `RbAmpTimeoutError` if the window
> expired.

> ⚠ **Provisioning discipline — MUST one virgin at a time.**
>
> The most common source of provisioning failure is having **more than
> one** module with the factory address `0x50` on the bus.
> Distinguishing them over I²C is **physically impossible** (open-drain
> wired-AND — both modules ACK identically, and the data read-back will
> be "merged").
>
> **Hence the rule is strict (a normative MUST, not a recommendation):**
> at the moment `prepare_address_change()` is called, there must be
> **exactly one** module at `0x50` on the bus (a new one, or one
> returned to factory defaults via a separate bench tool).
>
> **Recovery path if you suspect a discipline violation:**
> 1. Power-cycle all modules.
> 2. Physically disconnect all but one "virgin" module.
> 3. Call `prepare_address_change()` on the single-module bus.
> 4. Add the remaining modules one at a time, provisioning each separately.

#### Bus energy budget

At 50 kHz, a single read transaction ≈ 200-400 µs. A full RT block for one channel (U + I + P + PF) ≈ 5-8 ms per channel.

| Configuration | Bus per cycle | At 50 kHz | Max polling rate |
|---|---|---|---|
| 1× UI1, RT block | ~5 ms | < 5 ms | 100+ Hz (but the module updates at 5 Hz) |
| 5× UI1, RT block | ~25 ms | ~25 ms | 5 Hz |
| 16× I3, RT + period | ~400 ms | **400 ms > 200 ms** | **cannot keep up with the module** |

> The numbers are estimates. The exact values will be confirmed by a bench test.

**Rule**: at 50 kHz, the comfortable limits are **8-10× I3** or **15-16× UI1** at 5 Hz polling.

#### Period sync — synchronizing the periods

For tariff metering, all modules must latch the **same** interval.

**Strategy 1 — sequential latch + common settle** (any firmware):

```python
import time

# Phase 1: sequential latch
for m in meters:
    m.latch_period()

# Phase 2: common settle (≥ 50 ms)
time.sleep(0.050)

# Phase 3: read the snapshots with skip_latch=True
snaps = [m.read_period_snapshot(skip_latch=True) for m in meters]
```

Skew: 16 modules × ~1 ms/latch = ~16 ms relative to a 60-s period = 0.027%. Negligible for billing.

**Strategy 2 — General-Call broadcast latch** (requires enabling on each module):

All modules latch on a single I²C frame at addr `0x00`:

```
addr=0x00 | A5 27 <group> <tick_lo> <tick_hi>   (5 bytes)
```

Skew = 0 (atomic). Pre-config (once on each fleet module):

1. `dev.write_reg(0x27, 0x01)` (FLEET_CONFIG.bit0 = 1)
2. `dev.save_user_config()`
3. `dev.reset()` (settle ~300 ms)

Witness: for each expected slave, read `REG_PERIOD_VALID (0x07)`. `!=1` → fall back to strategy 1.

> ⚠ **GC opt-in (C.10)**: if GC is disabled across the whole fleet, the GC address NACKs. The master gets a hard error (not a silent drop) — this is **detectable**, not a bug.

#### GC group filtering (multi-tenant bus)

`REG_GROUP_ID (0x28)` lets you have several GC domains on one bus:

- `group = 0x00` → all-call.
- `group = N` → only modules with `REG_GROUP_ID = N` latch.

Use case — separating tariff groups.

#### Failure modes

| Symptom | Cause | What to do |
|---|---|---|
| `i2c bus scan` shows several modules at the same address after a field add | A new module at `0x50` is conflicting | Re-address it separately before adding |
| One module NACKs more often than the others | Cable fault / VCC drop | Check VCC under load |
| After a GC frame, one module does not latch | `FLEET_CONFIG.bit0 = 0` or group mismatch | Check `REG_FLEET_CONFIG` and `REG_GROUP_ID` |

Detailed multi-module scenarios are in [06 · Examples](06_examples.md).

## CPython hosts (Linux SBC + x86)

### Raspberry Pi (any model)

I²C bus 1 is routed to the 40-pin header. On most models (1B+ / 2 / 3 /
4 / 5) the pins are the same:

| rbAmp | RPi 40-pin header | GPIO (BCM) |
|---|---|---|
| `VCC` | pin 2 (+5V) | — |
| `GND` | pin 6 (or any GND) | — |
| `SDA` | pin 3 | GPIO 2 (SDA1) |
| `SCL` | pin 5 | GPIO 3 (SCL1) |
| `DRDY` | (optional) | any free GPIO with irq support |

Enable the kernel I²C driver: `sudo raspi-config` →
**3 Interface Options** → **I2C** → **Yes**. Or edit `/boot/config.txt`
manually:

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

The same `smbus2.SMBus(N)` where N is the bus number from `/dev/i2c-N`.
Usually `/dev/i2c-1` or `/dev/i2c-0`. Check:

```sh
ls /dev/i2c-*
i2cdetect -y 1     # should show 0x50
```

The pinout depends on the specific board — check its datasheet.

### x86 / Windows / macOS — via a USB-I²C dongle

There is no native I²C on x86 hosts. The solution is a USB-I²C dongle
(FT232H, MCP2221, Bus Pirate v4, CH341). `pyftdi.i2c.I2cController` on
its own has **neither** an `smbus2`-compatible signature
(`read_byte_data` / `write_byte_data`) nor a MicroPython signature
(`readfrom_mem` / `writeto_mem`) — so you cannot pass it directly into
`RbAmp(...)`; bus autodetect will raise `RbAmpParamError`.

x86 dongles require a **thin wrapper class** that implements the
duck-typed backend interface (`read_byte` / `write_byte` /
`register_acks` / `now_ms` — see
[09 · API Reference](09_api_reference.md), section "Bus autodetect").
Sample wrapper code for various dongles is planned for release in the
`rbamp.adapters.*` submodule in v1.3+ — until then, write your own
wrapper modeled on `_io_smbus.py` / `_io_micropython.py`.

## MicroPython hosts

### ESP32 / ESP32-S2 / ESP32-S3 / ESP32-C3

```python
from machine import I2C, Pin
from rbamp import RbAmp

# ESP32 DevKitC default I²C pinning
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)   # 50 kHz recommended on ESP32
with RbAmp(i2c, 0x50) as dev:
    print(dev.voltage, "V")
```

| Chip | Default SDA / SCL (recommended) | I²C port |
|---|---|---|
| ESP32 (original / WROOM / DevKitC) | GPIO 21 / GPIO 22 | `I2C(0, ...)` |
| ESP32-S2 | GPIO 21 / GPIO 22 (same convention) | `I2C(0, ...)` |
| ESP32-S3 | GPIO 8 / GPIO 9 (default for the N16R8 board) | `I2C(0, ...)` |
| ESP32-C3 | GPIO 5 / GPIO 6 | `I2C(0, ...)` |

For other chips, check the MicroPython firmware of your ESP32
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

The Pico does not suffer from the baseline NACK pattern —
`MachineI2CBackend` auto-detects RP2040 and lowers the retry default to
1 attempt.

### STM32 / Pyboard / Nucleo (via MicroPython)

```python
from machine import I2C
from rbamp import RbAmp

# Pyboard — soft-I²C or hardware I²C block
i2c = I2C(1, freq=100_000)   # bus 1 — X9/X10 on the Pyboard
with RbAmp(i2c, 0x50) as dev:
    print(dev.voltage, "V")
```

STM32 does not suffer from the baseline NACK pattern —
`MachineI2CBackend` lowers the retry default to 1 attempt on these
boards.

## MicroPython baseline NACK pattern on ESP32 — 50 kHz

When running under MicroPython on **ESP32**, the current rbAmp firmware
exhibits a **~20 % NACK rate at 100 kHz** and **< 5 % at 50 kHz**. This
is specific to the ESP-IDF I²C stack that underlies MicroPython ESP32.
On the other MicroPython ports (RP2040 / STM32 / Pyboard) the problem
does not appear.

The package addresses this in two layers:

1. **Default speed of 50 kHz** — it is recommended to pass
   `freq=50_000` to `machine.I2C(...)` on ESP32.
2. **Per-byte retry** in `MachineI2CBackend` — 3 attempts × 5 ms gap by
   default. Configurable through the advanced API:

   ```python
   from rbamp._io_micropython import MachineI2CBackend
   from rbamp import RbAmp

   backend = MachineI2CBackend(i2c, retry_attempts=5, retry_gap_ms=5)
   dev = RbAmp(backend, 0x50)   # the address is a parameter of RbAmp, not of the backend
   ```

For more on diagnosing bus-level problems, see
[10 · Troubleshooting](10_troubleshooting.md). The
`MachineI2CBackend.retry_exhaustion_count` and
`RbAmp.sanity_reject_count` counters are available for long-soak
observability.

## DATA_READY (DRDY)

An optional pin for polling optimization. If the application polls the
module no more than once every 200 ms, `DRDY` can be left
unconnected.

### Electrical parameters

- **Output type**: open-drain (no active pull-up to VCC)
- **Idle level**: HIGH (requires a pull-up on the host side — the GPIO's
  built-in pull-up or an external resistor; 10 kΩ to 3.3 V is
  recommended)
- **Ready pulse**: LOW for ~10 µs after the RT registers have been
  updated with fresh data
- **Pulse rate**: ~5 Hz (one pulse per ~200 ms RT window)

### Semantics

A falling edge on `DRDY` guarantees that **all RT registers are
synchronized and published** (the firmware updates them atomically in
the ISR before pulling the pin low). After the falling edge, the master
can read the RT block with no risk of getting a split sample.

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

On the Raspberry Pi via `gpiozero` or `RPi.GPIO`:

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

`DRDY` is **optional** — polling at any rate ≤ 5 Hz works without it
too. The package does not depend on `DRDY` in any of its paths.

## Links

- [05 · Quickstart](05_quickstart.md) — the first working script for
  both backends
- [06 · Examples](06_examples.md) — working scenarios (including the
  multi-module bus + async streaming)
- [09 · API Reference](09_api_reference.md) — backend details and the
  retry/sanity counter accessors
- [10 · Troubleshooting](10_troubleshooting.md) — bus-level debug, the
  NACK pattern, retry+sanity discipline

