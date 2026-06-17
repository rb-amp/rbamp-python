# rbamp — Python клиентская библиотека для модуля rbAmp

> ⚠ **ЧЕРНОВИК НА РУССКОМ** (2026-05-29, round 1) — финальная
> английская версия будет после операторского ревью.

[![protocol: 1.2](https://img.shields.io/badge/protocol-1.2-blue)](docs/02_tiers.md)
[![python: micropython · cpython](https://img.shields.io/badge/python-MicroPython%20%C2%B7%20CPython-brightgreen)](docs/04_hardware.md)
[![license: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](../../../LICENSE)

Унифицированный Python-клиент для I²C AC-сенсора/диммера rbAmp.
Поддерживает **одновременно MicroPython** (`machine.I2C` на ESP32 /
RP2040 / STM32 / Pyboard) **и CPython** (`smbus2` на Linux-SBC —
RPi / Orange Pi / Rock Pi и т.д.) из одного исходника. Backend
выбирается автоматически по типу шины, переданной в `RbAmp` —
никаких флагов platform или import-switch не требуется.

```python
# CPython на Raspberry Pi
from smbus2 import SMBus
from rbamp import RbAmp, RbAmpSensorClass

with SMBus(1) as bus:
    with RbAmp(bus, 0x50) as dev:
        # На v1.2+ прошивке: один раз при первой инсталляции
        dev.set_sensor_class(RbAmpSensorClass.SCT_013)
        dev.set_ct_model(3)        # SCT-013-030

        print(dev.voltage, "V")
        snap = dev.read_period_snapshot()
        print(dev.energy.wh(0), "Wh")
```

```python
# MicroPython на ESP32  (50 кГц per SPEC §B.5)
from machine import I2C, Pin
from rbamp import RbAmp, RbAmpSensorClass

i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=50_000)
with RbAmp(i2c, 0x50) as dev:
    dev.set_sensor_class(RbAmpSensorClass.SCT_013)
    dev.set_ct_model(3)
    print(dev.voltage, "V")
    snap = dev.read_period_snapshot()
    print(dev.energy.wh(0), "Wh")
```

Соответствует кросс-платформенному API rbAmp — тот же набор операций
что в Arduino / ESP-IDF / STM32-HAL библиотеках, но с
Python-идиоматическим именованием (snake_case + свойства + исключения
вместо error-кодов).

## Установка

### CPython на Linux SBC

**Перед `pip install`** убедитесь что I²C-шина включена и доступна
без `sudo`:

```sh
# 1. Системные пакеты для работы с I²C на Linux (Raspberry Pi OS,
#    Debian, Ubuntu). На большинстве дистрибутивов уже стоят.
sudo apt install i2c-tools python3-pip python3-venv

# 2. Включить I²C на Raspberry Pi (если ещё не сделано):
sudo raspi-config nonint do_i2c 0   # включает /dev/i2c-1
# Либо через menuconfig: sudo raspi-config → Interface → I2C → Enable

# 3. Добавить пользователя в группу `i2c` — иначе `/dev/i2c-1`
#    требует sudo и pip-installed скрипты ломаются с PermissionError:
sudo usermod -aG i2c $USER
# После этого ОБЯЗАТЕЛЬНО перелогиниться (новая группа не активна
# в текущей session) либо `newgrp i2c` в текущем shell.

# 4. Проверка: модуль должен появиться в I²C-скане:
i2cdetect -y 1     # ожидаем 0x50 в строке 50:
```

После prerequisites:

```sh
pip install rbamp smbus2
# TODO: replace `rbamp` с финальным PyPI slug после публикации
# (текущее значение — placeholder; см. pyproject.toml line 56-61).
```

Пакет `rbamp` не зависит от конкретной I²C-библиотеки —
`smbus2` рекомендован как backend для большинства Linux-SBC.
Проверка:

```sh
rbamp --version           # rbamp 1.2.0
rbamp --bus 1 scan        # I²C-скан, ищет rbAmp устройства
```

### MicroPython

```sh
mpremote mip install github:rbamp/rbamp-python
# TODO: URL — placeholder; align с финальным GitHub-org после
# публикации (см. package.json). До тех пор используйте:
mpremote cp -r path/to/rbamp :rbamp/
```

Пакет self-устанавливается в `/lib/rbamp/` на устройстве.

Для frozen-as-bytecode деплойментов (меньший RAM footprint) включите
`libs/python/rbamp/` в `freeze_modules.py` сборки прошивки.

### Из исходников (editable, для разработки)

```sh
git clone https://github.com/rbamp/rbamp-python.git
pip install -e rbamp-python smbus2
```

## Поддерживаемые платформы

| Платформа | Backend | Default retry | Заметки |
|---|---|---|---|
| CPython на Linux SBC | `SMBusBackend(smbus2.SMBus)` | нет | RPi / Orange Pi / Rock Pi / NanoPi / x86 + USB-I²C |
| MicroPython на ESP32-семействе | `MachineI2CBackend(machine.I2C)` | 3 попытки × 5 мс | ESP32 / S2 / S3 / C3 |
| MicroPython на RP2040 | `MachineI2CBackend(machine.I2C)` | 3 попытки (можно снизить до 1 — NACK pattern отсутствует) | Pico / Pico W |
| MicroPython на STM32 | `MachineI2CBackend(machine.I2C)` | 3 попытки (можно снизить до 1) | Pyboard / Nucleo |
| CircuitPython | **не напрямую** | — | `busio.I2C` ≠ `machine.I2C` — оберните adapter'ом или используйте raw-протокол |

## Что вы получаете

- **Один класс `RbAmp`** — `dev = RbAmp(bus, 0x50)` авто-детектит
  backend по типу методов bus-объекта (`readfrom_mem` → MicroPython;
  `read_byte_data` → smbus2; duck-typed backends поддерживаются для
  тестов и FTDI-адаптеров).
- **Pythonic API** — `dev.voltage`, `dev.power[0]`, `dev.energy.wh(0)`
  как **properties**; method-based `dev.read_period_snapshot()` для
  energy-metering примитива; `async for snap in dev.stream_period()`
  для async-стриминга через `asyncio` / `uasyncio`.
- **Per-channel Wh-накопитель** — обновляется автоматически
  на каждом успешном `read_period_snapshot()`. Со знаком
  (отрицательное = экспорт). Можно отключить через
  `dev.energy.disable()`.
- **Конфигурация датчика** (v1.2+ прошивка): `dev.set_sensor_class(cls)`,
  `dev.set_ct_model(code)` (legacy, канал 0) и
  `dev.set_ct_model_ch(channel, code)` (per-channel) для настройки разных
  CT-клипс на разных каналах UI2 / UI3 модулей.
- **Иерархия исключений** — каждая ошибка наследует `RbAmpError`:
  `RbAmpIOError`, `RbAmpTimeoutError`, `RbAmpStaleError`,
  `RbAmpParamError`, `RbAmpModeError`, `RbAmpVersionError`. Никаких
  return-кодов — стандартный Python `try / except` паттерн.
- **Retry + sanity discipline** на MicroPython backend (применяется
  автоматически; конфигурируется через
  `MachineI2CBackend(retry_attempts=5)` для плотных нагрузок).
  CPython backend без retry-слоя — Linux-kernel не разделяет
  NACK-pattern.
- **`rbamp` CLI** (только CPython, ставится через `pip install rbamp`):
  `rbamp scan` / `read --watch 5` / `period` / `info` / `address 0x51`.

## Документация

| Документ | Назначение |
|---|---|
| [01 · Обзор](docs/01_overview.md) | Что такое rbAmp, что делает пакет, dual-backend story |
| [02 · Тиры (BASIC / STANDARD / PRO)](docs/02_tiers.md) | Какой тир под какую задачу |
| [03 · Выбор датчика тока](docs/03_sensor_selection.md) | Руководство по моделям SCT-013 |
| [04 · Подключение](docs/04_hardware.md) | Распиновка, проводка per-host (RPi / ESP32 / RP2040 / STM32) |
| [05 · Quickstart](docs/05_quickstart.md) | 5-минутный hello-world для обоих backend'ов |
| [06 · Примеры](docs/06_examples.md) | Walkthrough реальных сценариев per backend |
| [07 · DIY интеграции](docs/07_diy_integrations.md) | Home Assistant / Node-RED / OpenHAB |
| [08 · Облачные интеграции](docs/08_cloud_integrations.md) | AWS IoT / Azure / GCP / InfluxDB Cloud |
| [09 · Справочник API](docs/09_api_reference.md) | Каждый публичный класс + метод + исключение |
| [10 · Диагностика](docs/10_troubleshooting.md) | Типичные ошибки + NACK-диагностика + retry tuning |
| [11 · Changelog](docs/11_changelog.md) | Per-release notes |

Спецификация wire-протокола — в репозитории
[`rbamp-spec`](https://github.com/rbamp/rbamp-spec).

## Примеры

Два параллельных набора примеров:

### MicroPython ([`examples_upy/`](examples_upy/))

Скрипты, mirror'ящие сценарии главы [06 · Примеры](docs/06_examples.md).
Запуск:

```sh
mpremote run examples_upy/01_quick_read.py
```

### CPython ([`examples_cpython/`](examples_cpython/))

Скрипты, mirror'ящие те же сценарии для Linux-SBC. Запуск:

```sh
python examples_cpython/01_quick_read.py
```

Полные walkthrough'и сценариев — в [docs/06_examples.md](docs/06_examples.md).

## Long-soak регрессионный harness

Pytest-opt-in регрессионный тест плюс standalone-раннеры живут в
[`tests/test_long_soak.py`](tests/test_long_soak.py) и
[`../tools_bench/`](../tools_bench/). Гоняет устройство ≥ 60 с
(дефолт; ≥ 3600 с для release-gate) и проверяет шесть критериев
приёмки: per-cycle valid ratio > 99 %, нулевые retry-exhaust,
нулевые sanity-rejects, монотонный Wh, ограниченный scheduler-jitter,
ограниченный retry-rate.

```bash
# CPython runner
python tools_bench/long_soak.py --bus 1 --addr 0x50 \
    --duration 3600 --cadence 60 --out soak.csv

# MicroPython runner (через mpremote / кастомный session-driver)
# Bench-скрипты доступны после ship'a v1.2.0 как console scripts:
#   rbamp-soak, rbamp-soak-upy, rbamp-deploy
# (до v1.2.0 — `python -m rbamp.bench.long_soak` либо tools_bench/ из monorepo)
```

## Тесты

```bash
cd libs/python && pytest -v   # 93 passed, 1 skipped в v1.1.0 (~2 с)
```

Если установили пакет через `pip install -e .` — `cd` в директорию
пакета и просто `pytest`.

## Совместимость

Пакет таргетит **протокол rbAmp v1.0 / v1.1 / v1.2** с прозрачной
обратной совместимостью. Кросс-платформенный контракт документирован
в [`rbamp-spec`](https://github.com/rbamp/rbamp-spec).

| Версия пакета | Версия прошивки | Поведение |
|---|---|---|
| 1.1 | 1.0 | Конструктор-подсказка топологии. `set_sensor_class()` принимается, но прошивка регистр не использует (сам вызов всё равно блокирует ~700 мс на `CMD_SAVE_GAINS`). `set_ct_model(code)` работает (legacy путь). Per-channel `set_ct_model_ch(channel, code)` **может NACK'нуть** на pre-v1.2 прошивке (опкоды `CMD_SET_CT_MODEL_CHn` ещё не существуют) — проверьте `dev.firmware_version` перед вызовом. |
| 1.1 | 1.1 | `REG_TOPOLOGY` ещё игнорируется (форвард-совместимость v1.2+). CT-model preset auto-load на стороне прошивки. |
| 1.1 | 1.2 | Per-channel `set_ct_model_ch(channel, code)` работает (опкоды `CMD_SET_CT_MODEL_CH0` / `CH1` / `CH2`). `set_sensor_class()` обязателен **перед** `set_ct_model()` И `set_ct_model_ch()` — иначе обе функции выбросят `RbAmpParamError` (precondition violation). Per-channel signed `dev.power[ch]` отдаёт знак (отрицательное = экспорт). |
| 1.2+ (планируется) | 1.x | Расширенная diagnostics, `dev.warm_open()` для deep-sleep сценариев на MicroPython, дополнительные accessor'ы. |

## Сестринские библиотеки

- **Arduino** — [`rbamp-arduino`](https://github.com/rbamp/rbamp-arduino)
- **ESP-IDF** — [`rbamp-esp-idf`](https://github.com/rbamp/rbamp-esp-idf)
- **STM32 HAL** — [`rbamp-stm32-hal`](https://github.com/rbamp/rbamp-stm32-hal) *(coming soon)*
- **ESPHome** — [`rbamp-esphome`](https://github.com/rbamp/rbamp-esphome)

## Лицензия

MIT — см. [LICENSE](../../../LICENSE).
