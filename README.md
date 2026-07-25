# Wenet BLE Sensor Payload

MicroPython sensor payload for the Raspberry Pi Pico W / Pico 2 W. Reads a set of
auto-detected I2C and OneWire sensors, broadcasts CBOR-encoded telemetry over BLE for
[Wenet](https://github.com/projecthorus/wenet) high-altitude balloon downlinks, and logs
everything to CSV on an SD card.

## Quick start

### 1. What you need

- A **Pico W** or **Pico 2 W** — the wireless variant is required, not a plain Pico.
  The code uses `Pin('LED')` and `Pin('WL_GPIO1')`, which only exist on the W boards.
- A USB cable, and VS Code.
- Optionally: any of the supported sensors, an SD card breakout, and a PCF8523 RTC.
  Every sensor is optional and auto-detected — the payload runs fine with none attached.

### 2. Flash MicroPython

1. Download the UF2 for your board from the official MicroPython downloads page:
   [Pico W](https://micropython.org/download/RPI_PICO_W/) or
   [Pico 2 W](https://micropython.org/download/RPI_PICO2_W/).
2. Hold **BOOTSEL** while plugging the Pico into USB. It appears as a drive named `RPI-RP2`.
3. Copy the `.uf2` onto that drive. The board reboots into MicroPython automatically.

### 3. Install the VS Code tooling

Open this folder in VS Code and accept the recommended extensions, or install
[MicroPico](https://marketplace.visualstudio.com/items?itemName=paulober.pico-w-go)
(`paulober.pico-w-go`) manually. The `.vscode/` settings and `.micropico` marker in this
repo configure stub paths and IntelliSense for you.

With the Pico plugged in, MicroPico connects on startup — look for the status bar at the
bottom of the window. `Ctrl+Shift+P` → **MicroPico: Connect** if it doesn't.

### 4. Configure before uploading

Two things to set:

**[main.py](main.py#L26)** — name your payload. This becomes part of the BLE packet `id`
and the CSV filename:

```python
payload_name = "RAB_HAT"
```

**[ntp_sync.py](ntp_sync.py#L8-L9)** — only needed if you have a PCF8523 RTC to set:

```python
SSID = ''      # Change me!
PASSWORD = ''  # Change me too!
```

### 5. Upload to the Pico

`Ctrl+Shift+P` → **MicroPico: Upload project to Pico**.

This copies `main.py`, `ntp_sync.py`, and the whole `lib/` directory. **`lib/` is not
optional** — it holds all the sensor drivers and the vendored `cbor2` encoder. If you copy
files by hand, preserve the directory structure exactly, including `lib/cbor2/`.

MicroPython runs `main.py` automatically on every boot, so the payload starts as soon as
the upload finishes or the board is power-cycled.

### 6. Verify it's working

Open the MicroPico terminal (**MicroPico: Open REPL**) and reset the board. You should see
the I2C scan report each device it finds:

```
Checking for I2C devices...
BME680 detected at 0x77!
PCF8523 detected!
Synchronized CPU clock -- 2026-07-24 18:22:04
Checking for OneWire devices...
No OneWire devices found!
```

...followed by a JSON line of telemetry roughly every 500 ms.

To see the BLE side, install **nRF Connect** on a phone and look for your payload. It
advertises both the Wenet service and a Nordic UART service. Press the **BOOTSEL** button
on the Pico to toggle debug mode — the LED blinks ten times to confirm, and readable JSON
starts streaming over BLE UART.

### 7. Set the hardware clock (optional)

If you fitted a PCF8523, run [ntp_sync.py](ntp_sync.py) once from the REPL after filling in
your Wi-Fi credentials. It pulls the time from `pool.ntp.org` and writes it to the RTC,
which then keeps time on its backup battery. `main.py` reads it on every boot, so accurate
log timestamps no longer need a network connection.

Note the script ends in an infinite print loop — `Ctrl+C` when you're satisfied the time is
correct.

## Wiring

| Function | Interface | Pins |
|---|---|---|
| Sensors | I2C0 | SDA = **GP4**, SCL = **GP5** (100 kHz) |
| OneWire (DS18X20) | — | **GP21** |
| SD card | SPI0 | SCK = **GP18**, MOSI = **GP19**, MISO = **GP16**, CS = **GP17** |
| Analog inputs | ADC0-2 | **GP26**, **GP27**, **GP28** |
| Battery voltage | ADC3 | GP29 (internal VSYS sense) |

All of these are set at the top of [main.py](main.py#L54-L60) and in
[`sd_write_task()`](main.py#L428-L431) if your board wires them differently.

## Supported sensors

Detected by I2C address at startup; attach any subset.

| Sensor | Address | Reports |
|---|---|---|
| LIS3MDL | 0x1C | 3-axis magnetometer |
| Honeywell HSC | 0x28 | Pressure |
| HDC302x | 0x44-0x45 | Temperature, humidity |
| MAX31725 | 0x48-0x4F | Temperature |
| PCF8523 | 0x68 | Real-time clock |
| LSM6DSOX | 0x6A | Accelerometer, gyroscope (logged on its own faster task) |
| BMP280 | 0x76/0x77 | Temperature, pressure |
| BME280 | 0x76/0x77 | Temperature, pressure, humidity |
| BME680 | 0x76/0x77 | Temperature, pressure, humidity, gas |
| DS18X20 | OneWire | Temperature (multiple supported) |

The three Bosch parts share the same addresses, so they're told apart by reading the chip
ID register — you don't configure which one you have. Only one Bosch sensor per address is
tracked, so two identical parts at 0x76 and 0x77 will not both report.

## Data output

**BLE** — CBOR-encoded packets on characteristic `3d235f0e-…` under service
`fb63feb8-…`, updated every 500 ms (`update_interval` in [main.py](main.py#L29)).
Packets must stay under 254 bytes to fit a Wenet telemetry frame; the code warns at 228
bytes and asserts at the limit. If you add sensors and hit that ceiling, split the payload
across two packets.

**SD card** — one CSV per task, named from your `payload_name`:

```
/sd/data_log_RAB_HAT_ENV.csv
/sd/data_log_RAB_HAT_LSM6DSOX.csv
```

Rows are flushed after every write, so pulling power mid-flight loses at most the last
sample. If the card is missing or unreadable the payload logs the error and keeps running
on BLE alone.

## Troubleshooting

**`ImportError: no module named 'aioble'`** — some firmware builds don't bundle it. Install
it on the board from the REPL:

```python
import mip
mip.install("aioble")
```

**`ImportError` for a sensor driver** — `lib/` didn't make it across. Re-run
**Upload project to Pico** and confirm `lib/cbor2/` came with it.

**Nothing on the I2C scan** — check SDA/SCL aren't swapped, confirm pull-ups are present,
and verify the sensor is powered at the right voltage.

**Timestamps start at 2021-01-01** — no RTC was found, so the clock is unset. Fit a
PCF8523 and run `ntp_sync.py`, or accept that timestamps are relative to boot.

**`Unable to set up SD card!`** — check the SPI wiring against the table above, make sure
the card is formatted FAT32, and try a lower `baudrate` in
[`sd_write_task()`](main.py#L428) if the card is on long leads.
