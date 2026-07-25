# Wenet BLE Sensor Payload

MicroPython sensor payload for the Raspberry Pi Pico W / Pico 2 W. Reads a set of
auto-detected I2C and OneWire sensors, broadcasts CBOR-encoded telemetry over BLE for
[Wenet](https://github.com/projecthorus/wenet) high-altitude balloon downlinks, and logs
everything to CSV on an SD card.

The firmware's default pinout targets the **RAB Pi Pico HAT** — see
[the HAT](#the-rab-pi-pico-hat) below. It runs on a bare Pico W too, if you wire the
peripherals yourself.

[configurator.html](configurator.html) is a browser-based companion that reads the live
telemetry and edits the payload's settings — see [Web configurator](#web-configurator).

## Quick start

### 1. What you need

- A **Pico W** or **Pico 2 W** — the wireless variant is required, not a plain Pico.
  The code uses `Pin('LED')` and `Pin('WL_GPIO1')`, which only exist on the W boards.
- A USB cable, and VS Code.
- **Strongly recommended for flights: the RAB Pi Pico HAT.** It carries the RTC,
  SD socket, BMP280, battery input, and filtered analog inputs on one board with no
  hand-wiring. Not required — the payload runs on a bare Pico W, and every peripheral is
  optional and auto-detected. See [the HAT](#the-rab-pi-pico-hat).

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

**[main.py](main.py#L26)** — name your payload. This becomes part of the BLE packet `id`,
the advertised BLE name, and the CSV filename:

```python
payload_name = "RAB_HAT"
```

**`payload_name` cannot exceed 8 characters.** The legacy BLE advertising payload is 31
bytes: 3 for flags, 18 for the 128-bit Wenet service UUID, and 2 of header for the name,
leaving 8. A longer name makes `aioble.advertise()` fail, which propagates out of
`asyncio.gather()` and takes down every task — so it isn't a cosmetic limit. The loader
truncates rather than let that happen.

You don't have to edit the file for this: `payload_name` and `update_interval` can also be
set from [the web configurator](#web-configurator), which writes a `config.json` that
[main.py](main.py#L38-L70) reads at boot. Anything missing or invalid falls back to the
values in the source, so a bad config can't stop the payload from booting.

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

Only device-side code needs to go across, and this repo is already set up that way.
`README.md`, the schematic, and the `.code-workspace` are skipped because their extensions
aren't in `micropico.syncFileTypes`. `configurator.html` and `ble_test.py` *would* have been
uploaded — `.html` and `.py` are both sync types — but they run on your computer, not the
Pico, so [.vscode/settings.json](.vscode/settings.json) lists them in
`micropico.pyIgnore`. That leaves about 107 KB of Python for the board.

If you add another host-side file, add it to `micropico.pyIgnore` too. Note that setting
**replaces** the extension's default list rather than extending it, which is why the
defaults are repeated there — deleting them would start syncing `.git` to the board.

MicroPython runs `main.py` automatically on every boot, so the payload starts as soon as
the upload finishes or the board is power-cycled.

### 6. Verify it's working

Open the MicroPico terminal (**MicroPico: Open REPL**) and reset the board. You should see
the I2C scan report each device it finds:

```
Checking for I2C devices...
[104, 118]
PCF8523 detected!
Synchronized CPU clock -- 2026-07-24 18:22:04
BMP280 detected at 0x76!
Checking for OneWire devices...
No OneWire devices found!
```

That's a stock RAB HAT: `104` is the PCF8523 at 0x68, `118` is the onboard BMP280 at 0x76.
Addresses are reported in ascending order. Anything you've hung off J10 shows up in the same
list — if a sensor is missing here, it's a wiring or address problem, not a firmware one.

The scan is followed by a **CSV** line of telemetry roughly every 500 ms — the same text
that goes to the SD card ([main.py:306](main.py#L306)). There is no header row, and the
columns after the first eight depend on which sensors were detected.

To see the BLE side, use [the web configurator](#web-configurator), or install **nRF
Connect** on a phone. Note the payload advertises *only* the Wenet service UUID but sends
its telemetry on the Nordic UART TX characteristic — see [Data output](#data-output).

Pressing **BOOTSEL** toggles the `debug` flag: the LED blinks ten times to confirm, then
switches from a steady toggle to a short blip each cycle. Despite the comment at
[main.py:145](main.py#L145), this only changes the LED — it does not change what is
transmitted.

### 7. Set the hardware clock

On the RAB HAT the PCF8523 is already fitted. Run [ntp_sync.py](ntp_sync.py) once from the
REPL after filling in your Wi-Fi credentials. It pulls the time from `pool.ntp.org` and
writes it to the RTC, which then keeps time on its ML621 coin cell. `main.py` reads it on
every boot, so accurate log timestamps no longer need a network connection.

Note the script ends in an infinite print loop — `Ctrl+C` when you're satisfied the time is
correct.

## The RAB Pi Pico HAT

A carrier board for the Pico W that provides everything the firmware expects, already
wired. **Strongly recommended for flights** — it removes the hand-wiring that tends to fail
under vibration and cold, and gives you a proper battery input and a backed-up clock. It is
**not required**: the firmware runs on a bare Pico W, and every peripheral it adds is
optional and auto-detected.

Schematic: [RAB Pi Pico HAT.pdf](RAB%20Pi%20Pico%20HAT.pdf) (Rev A, 2025-09-20).

**Onboard:**

| Part | What it gives you |
|---|---|
| BMP280 (U2) | Temperature and pressure, no external sensor needed |
| PCF8523 (U1) + ML621 coin cell | Real-time clock that survives power-down |
| microSD socket (J1) | CSV logging, with card-detect wired up |
| Reset button (SW1) | Pulls `RUN` low — reboot without unplugging USB |
| Battery input (J3, J8) | Feeds `VSYS`, read back on ADC3 by `get_batt()` |

**Connectors:**

| Connector | Type | Purpose |
|---|---|---|
| J10 | JST SM04B-SRSS | I2C expansion — 3V3, SDA, SCL, GND. Where external sensors go. |
| J5 | JST S3B-PH | Analog in → **ADC0**, 100 Ω series + 0.1 µF. Direct 0–3.3 V. |
| J4 | JST S3B-PH | Analog in → **ADC1**, 100 Ω series + 0.1 µF. Direct 0–3.3 V. |
| J2 | JST S3B-PH | Analog in → **ADC2**, 10.2 kΩ series. 0–3.3 V as shipped; 0–13.2 V with `JP3` fitted. |
| J3 / J8 | Molex / 0532610271 | Battery input to `+BATT` |

Three things to know before you fly it:

- **`JP1` sets the onboard BMP280's I2C address** (0x76 or 0x77). The BME280 and BME680
  live at those same two addresses, and the firmware only tracks one Bosch part per
  address — so if you hang a BME680 off J10, set `JP1` to the address it isn't using or one
  of the two will be invisible.
- **All three analog inputs are 0–3.3 V as the board ships.** `JP3` is open by default, which
  leaves R7 (3.4 kΩ) disconnected from ground — so J2 is just a 10.2 kΩ series resistor into
  ADC2, with no attenuation. **Fit `JP3`** to ground R7 and turn J2 into a 4:1 divided input:
  0–13.2 V at **0.003223 V per count**. Do that before feeding it a battery voltage; with
  `JP3` open, anything over 3.3 V goes almost straight at the pin and the reading pins at
  full scale. J4 and J5 have plain 100 Ω series resistors and are always 0–3.3 V.
- **OneWire is not broken out on Rev A.** GP21 is free on the Pico header but doesn't reach a
  connector, so DS18X20 sensors need a direct tap to that pin.

### Wiring a bare Pico W

Skip this if you have the HAT — it already matches. These are the defaults in
[main.py](main.py#L54-L60) and [`sd_write_task()`](main.py#L428-L431); change them there if
your board differs.

| Function | Interface | Pins |
|---|---|---|
| Sensors | I2C0 | SDA = **GP4**, SCL = **GP5** (100 kHz) |
| OneWire (DS18X20) | — | **GP21** |
| SD card | SPI0 | SCK = **GP18**, MOSI = **GP19**, MISO = **GP16**, CS = **GP17** |
| Analog inputs | ADC0-2 | **GP26**, **GP27**, **GP28** |
| Battery voltage | ADC3 | GP29 (internal VSYS sense) |

## Supported sensors

Detected by I2C address at startup; attach any subset.

| Sensor | Address | Reports |
|---|---|---|
| LIS3MDL | 0x1C | 3-axis magnetometer |
| Honeywell HSC | 0x28 | Pressure |
| HDC302x | 0x44-0x45 | Temperature, humidity |
| MAX31725 | 0x48-0x4F | Temperature |
| PCF8523 | 0x68 | Real-time clock — **onboard on the RAB HAT** |
| LSM6DSOX | 0x6A | Accelerometer, gyroscope (logged on its own faster task) |
| BMP280 | 0x76/0x77 | Temperature, pressure — **onboard on the RAB HAT** |
| BME280 | 0x76/0x77 | Temperature, pressure, humidity |
| BME680 | 0x76/0x77 | Temperature, pressure, humidity, gas |
| DS18X20 | OneWire | Temperature (multiple supported) |

The three Bosch parts share the same addresses, so they're told apart by reading the chip
ID register — you don't configure which one you have. Only one Bosch sensor per address is
tracked, so two identical parts at 0x76 and 0x77 will not both report. On the RAB HAT this
is what `JP1` is for: it moves the onboard BMP280 clear of an external Bosch sensor.

## Data output

**BLE** — CBOR-encoded packets, updated every `update_interval` ms (500 by default).
The service and characteristic are **not the pair you'd expect**:

| | UUID | Role |
|---|---|---|
| Advertised service | `fb63feb8-31ad-451d-a587-9fc20f9c8add` | Wenet — the only UUID in the advertising payload |
| Wenet characteristic | `3d235f0e-61f8-4455-89c6-2f7d73c33178` | Registered at [main.py:42](main.py#L42), **never written** |
| NUS service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | Not advertised |
| **NUS TX characteristic** | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | **Where the CBOR actually goes** ([main.py:304](main.py#L304), [main.py:399](main.py#L399)) |

So a client must *scan* for the Wenet service but *subscribe* to the Nordic UART TX
characteristic. Under the Web Bluetooth API that means listing NUS in `optionalServices`,
or access to it is denied even after connecting.

Packets must stay under 254 bytes to fit a Wenet telemetry frame; the code warns at 228
bytes and asserts at the limit. If you add sensors and hit that ceiling, split the payload
across two packets.

One BLE caveat: a notification carries at most `ATT MTU − 3` bytes. A full packet can reach
254, so on a host that never negotiates a larger MTU the tail is silently cut off. At the
23-byte default not even the first field survives — `time` alone is a 20-character string.
Phones and desktop Chrome normally negotiate up to 517 and are fine.

**SD card** — one CSV per task, named from your `payload_name`:

```
/sd/data_log_RAB_HAT_ENV.csv
/sd/data_log_RAB_HAT_LSM6DSOX.csv
```

Rows are flushed after every write, so pulling power mid-flight loses at most the last
sample. If the card is missing or unreadable the payload logs the error and keeps running
on BLE alone.

**USB serial** — the same CSV rows are printed to the REPL, with no header.

## Web configurator

[configurator.html](configurator.html) is a single self-contained page — no build step, no
CDN, no dependencies. Open it in a Chromium browser (Chrome, Edge, Opera); Firefox and
Safari ship neither Web Bluetooth nor Web Serial. If you open it as a `file://` URL and the
buttons do nothing, serve it over localhost instead:

```
python -m http.server
# then visit http://localhost:8000/configurator.html
```

**Over Bluetooth** it decodes the CBOR packets live and shows KPI tiles, a packet-size meter
against the 228/254-byte budget, a per-field history chart, and a table of every field with
units. Truncated packets are decoded as far as they go and flagged, rather than dropped.

**Over USB serial** it parses the CSV debug lines, and — because it can reach the
MicroPython REPL — it is the only transport that can read and write `config.json`:

| Field | Range |
|---|---|
| `payload_name` | 1–8 characters (the page shows the advertising payload cost as you type) |
| `update_interval` | 50–60000 ms |

Writing interrupts the running payload with Ctrl-C, enters the raw REPL, writes the file,
reads it back to verify, and soft-reboots. The bytes go over as hex and are rebuilt with
`binascii.unhexlify`, so quoting and newlines can't corrupt the transfer.

There's also a **JP3 fitted** checkbox. Tick it when the HAT's ADC2 divider is installed and
the page converts `adc2` counts to volts at 0.003223 V/count — see
[the HAT](#the-rab-pi-pico-hat).

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
and verify the sensor is powered at the right voltage. On the RAB HAT you should always see
at least 0x68 (PCF8523) and 0x76 or 0x77 (BMP280); if you see neither, suspect the Pico
isn't seated properly on the header.

**An external BME280/BME680 never appears** — it's colliding with the HAT's onboard BMP280.
Move `JP1` to the other address.

**Timestamps start at 2021-01-01** — no RTC was found, so the clock is unset. On a bare Pico
fit a PCF8523; on the HAT check the ML621 coin cell, then run `ntp_sync.py`. Otherwise
timestamps are relative to boot.

**`Unable to set up SD card!`** — make sure the card is formatted FAT32, and try a lower
`baudrate` in [`sd_write_task()`](main.py#L428). On a bare Pico also check the SPI wiring
against the table above.

**The payload doesn't appear in the configurator's Bluetooth picker** — the picker filters on
the advertised Wenet service. If the device is powered and nothing lists, `payload_name` is
probably longer than 8 characters, so `aioble.advertise()` threw and no advertising is
happening at all. Check the REPL for a traceback.

**Bluetooth connects but every packet logs as partial** — the ATT MTU is smaller than the
packet. See the caveat under [Data output](#data-output). Nothing is wrong with the payload;
the transport is truncating it.

**The configurator's config buttons are greyed out** — they need USB serial. Bluetooth
carries telemetry only; there's no write path over BLE.

**Serial config read/write fails or hangs** — something else holds the port. Close the
MicroPico REPL (and any other terminal) first; only one program can own a serial port.

**ADC2 on J2 reads full scale, or 4× off** — check `JP3`. Open (the default) means no
attenuation, so J2 is 0–3.3 V and anything higher pins the reading. Fitted means a 4:1
divider, so multiply counts by 0.003223 to get volts. A reading 4× lower than expected means
`JP3` is in and you're scaling as though it isn't.

Either way, don't just uncomment `vbatt_scale` in [main.py](main.py#L130) — it's from a
*different* board, assuming an 11.97 kΩ/2.68 kΩ divider on ADC0 rather than the HAT's
10.2 kΩ/3.4 kΩ on ADC2.
