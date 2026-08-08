import asyncio
import os
import json
import aioble
import bluetooth
import cbor2
import time
from micropython import const
from machine import Pin, ADC, mem32, RTC, I2C, SPI
import rp2
import sdcard
import vfs

from onewire import OneWire
from ds18x20 import DS18X20

from pcf8523 import PCF8523
from lsm6dsox import LSM6DSOX
from lis3mdl import LIS3MDL
from bmp280 import BMP280
from bme680 import BME680_I2C
from honeywell import HSC
from max31725 import MAX31725
from hdc302x import HDC302x
from bme280 import BME280

payload_name = "RAB_HAT"

# ADC pin configuration
adc0_name = 'adc0'
adc0_scale = 1
adc0_offset = 0
adc1_name = 'adc1'
adc1_scale = 1
adc1_offset = 0
adc2_name = 'adc2'
adc2_scale = 1
adc2_offset = 0

# BLE Update rate (in ms)
update_interval = 500

# Max name length. 32 is reasonable, but when you actually configure the name, less is more. 
_MAX_NAME_LEN = 32

# Limit advertisement name to this many characters to prevent aioble failure
_ADV_NAME_LEN = 8

def _truncate(name, limit):
    # aioble encodes the name as UTF-8, so the budget is bytes, not characters.
    while len(name.encode()) > limit:
        name = name[:-1]
    return name

# Optional overrides written by the web configurator (configurator.html). Anything
# missing, unreadable, or out of range falls back to the values above, so a bad config
# can't stop the payload from booting.
def _load_config():
    global payload_name, update_interval

    try:
        with open('config.json') as f:
            cfg = json.load(f)
    except OSError:
        return  # no config file, defaults stand
    except ValueError as e:
        print(f"config.json is not valid JSON ({e}) -- using defaults")
        return

    if not isinstance(cfg, dict):
        print("config.json is not an object -- using defaults")
        return

    name = cfg.get('payload_name')
    if isinstance(name, str) and name:
        trimmed = _truncate(name, _MAX_NAME_LEN)
        if trimmed != name:
            print(f"payload_name '{name}' exceeds {_MAX_NAME_LEN} bytes, truncating to '{trimmed}'")
        payload_name = trimmed

    interval = cfg.get('update_interval')
    if isinstance(interval, int) and not isinstance(interval, bool):
        if 50 <= interval <= 60000:
            update_interval = interval
        else:
            print(f"update_interval {interval} out of range (50-60000) -- ignoring")

_load_config()
print(f"Config: payload_name={payload_name} update_interval={update_interval}ms")

_WENET_SERVICE_UUID = bluetooth.UUID('fb63feb8-31ad-451d-a587-9fc20f9c8add')
_WENET_CHAR_UUID = bluetooth.UUID('3d235f0e-61f8-4455-89c6-2f7d73c33178')

_NUS_SERVICE_UUID = bluetooth.UUID('6E400001-B5A3-F393-E0A9-E50E24DCCA9E')
_NUS_TX_CHAR_UUID = bluetooth.UUID('6E400003-B5A3-F393-E0A9-E50E24DCCA9E')

# How frequently to send advertising beacons.
_ADV_INTERVAL_MS = 250_000

# Register Wenet Service
temp_service = aioble.Service(_WENET_SERVICE_UUID)
temp_characteristic = aioble.Characteristic(
    temp_service, _WENET_CHAR_UUID, read=True, notify=True
)

# Register Nordic UART Service
nus_service = aioble.Service(_NUS_SERVICE_UUID)
nus_tx_characteristic = aioble.Characteristic(
    nus_service, _NUS_TX_CHAR_UUID, read=False, notify=True
)

aioble.register_services(temp_service, nus_service)

# Initialize interfaces
rtc = RTC()
i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
led = Pin('LED', Pin.OUT)
ow = OneWire(Pin(21))
ds = DS18X20(ow)
ow_roms = []

sd_queue = []

lsm = None
lis = None
bmp = None
bme = None
hsc = None
max31725 = None
hdc302x = None
bme280 = None

debug = False

def build_packet(packet_dict):
    packet = cbor2.dumps(packet_dict)
    if len(packet) > 228:
        print(f">90% of packet length used ({len(packet)} of 254 bytes)! Consider splitting into two packets.")
    # Need binary packet to be less than 254 bytes to fit in wenet telemetry frame
    assert(len(packet) <= 254)
    return packet

def get_timestamp():
    now = rtc.datetime()
    ms = time.time_ns() // 1_000_000 % 1000
    return (f"{now[0]}{now[1]:02}{now[2]:02}T{now[4]:02}{now[5]:02}{now[6]:02}.{ms:03}Z",
            f"{now[0]}-{now[1]:02}-{now[2]:02} {now[4]:02}:{now[5]:02}:{now[6]:02}.{ms:03}")

# ref https://github.com/raspberrypi/pico-micropython-examples/blob/master/adc/temperature.py
def get_core_temp():     
    conversion_factor = 3.3 / (65535)
    reading = ADC(4).read_u16() * conversion_factor

    # The temperature sensor measures the Vbe voltage of a biased bipolar diode, connected to the fifth ADC channel
    # Typically, Vbe = 0.706V at 27 degrees C, with a slope of -1.721mV (0.001721) per degree.
    return 27 - (reading - 0.706) / 0.001721

def get_adc(adc):     
    return ADC(adc).read_u16() >> 4

def _setPad(gpio, value):
    mem32[0x4001c000 | (4 + (4 * gpio))] = value
    
def _getPad(gpio):
    return mem32[0x4001c000 | (4 + (4 * gpio))]

def get_batt():
    conversion_factor = 3 * (3.3 / (65535))

    oldpad = _getPad(29)
    if "Pico 2" in os.uname().machine:
        Pin(29, Pin.ALT, pull=None, alt=7)
    else:
        _setPad(29,128)  #no pulls, no output, no input
    reading = ADC(3).read_u16()
    _setPad(29,oldpad)

    reading = reading * conversion_factor
    return reading 

async def sensor_task():
    sensor_name = 'ENV'
    
    count = -1
    global debug
    global sd_queue

    task_update_interval = update_interval

    # vbatt_scale = (3.3*(2.68+11.97))/(2.68*(4096)) # 11.97k over 2.68k divider

    # OneWire device stuff, if available
    if len(ow_roms) > 0:
        if task_update_interval < 750:
            task_update_interval = 750
        ds.convert_temp()
        await asyncio.sleep_ms(750)

    now = time.time_ns() // 1_000_000
    update_at = now - (now % task_update_interval) + task_update_interval

    while True:
        timestamp_iso, timestamp_csv = get_timestamp()

        # Offer a mechanism to send JSON strings via BLE UART for debugging using nRF app
        if rp2.bootsel_button():
            for i in range(10):
                led.toggle()
                await asyncio.sleep_ms(100)
            debug = ~debug

        # Change power supply mode to reduce ripple
        Pin('WL_GPIO1', Pin.OUT).on()
        
        # Get ADC values, scale as needed
        adc0 = get_adc(0) * adc0_scale + adc0_offset
        adc1 = get_adc(1) * adc1_scale + adc1_offset
        adc2 = get_adc(2) * adc2_scale + adc2_offset

        # vbatt = (get_adc(0) + 60) * vbatt_scale

        batt = get_batt()
        core_temp = int(get_core_temp())

        packet_dict = {
            # Universal
            'time' : timestamp_iso,
            'id' : payload_name + '_' + sensor_name,
            'count' : count,
            'v_in' : batt, 
            'pi_temp' : core_temp,

            # ADC inputs
            adc0_name : adc0,
            adc1_name : adc1,
            adc2_name : adc2
            # 'vbatt' : vbatt
        }

        csv_data = f"{timestamp_csv},{payload_name}_{sensor_name},{count},{batt},{core_temp},{adc0},{adc1},{adc2},"

        # Get data from LIS3MDL magnetometer/compass, if available 
        if lis:
            try:
                (mag_x, mag_y, mag_z) = lis.magnetic

                lis_dict = {
                    'mag_x' : mag_x,
                    'mag_y' : mag_y,
                    'mag_z' : mag_z
                }
                
                csv_data += f"{mag_x},{mag_y},{mag_z},"

                packet_dict.update(lis_dict)
            except:
                print("LIS3MDL communications error!")

        # Get data from BMP280, if available
        if bmp:
            try:
                bmp_dict = {
                    'bmp_temp' : bmp.temperature,
                    'bmp_pres' : bmp.pressure,
                }

                csv_data += f"{bmp.temperature},{bmp.pressure},"

                packet_dict.update(bmp_dict)
            except:
                print("BMP280 communications error!")

        # Get data from BME680, if available
        if bme:
            try:
                bme._perform_reading()
                bme_temp = bme.temperature
                bme_pressure = bme.pressure
                bme_humidity = bme.humidity
                # bme_gas = bme.gas

                bme_dict = {
                    'bme_temp' : bme_temp,
                    'bme_pres' : bme_pressure,
                    'bme_humi' : bme_humidity,
                    # 'gas' : bme_gas
                }

                csv_data += f"{bme_temp},{bme_pressure},{bme_humidity},"

                packet_dict.update(bme_dict)
            except:
                print("BME680 communications error!")

        # Get data from BME280, if available
        if bme280:
            try:
                bme280_temp = bme280.temperature
                bme280_pres = bme280.pressure
                bme280_humi = bme280.humidity

                packet_dict.update({
                    'bme280_temp': bme280_temp,
                    'bme280_pres': bme280_pres,
                    'bme280_humi': bme280_humi,
                })
                csv_data += f"{bme280_temp},{bme280_pres},{bme280_humi},"
            except Exception as e:
                print("BME280 communications error!")
                print(e)

        # Get data from HDC302x, if available
        if hdc302x:
            try:
                hdc_temp, hdc_humi = hdc302x.measurements()

                packet_dict.update({'hdc_temp': hdc_temp, 'hdc_humi': hdc_humi})
                csv_data += f"{hdc_temp},{hdc_humi},"
            except Exception as e:
                print("HDC302x communications error!")
                print(e)

        # Get data from MAX31725, if available
        if max31725:
            try:
                max_temp = max31725.temperature

                packet_dict.update({'max31725_temp': max_temp})
                csv_data += f"{max_temp},"
            except Exception as e:
                print("MAX31725 communications error!")
                print(e)

        # Get data from Honeywell HSC, if available
        if hsc:
            try:
                hsc_temp = hsc.temperature
                hsc_pres = hsc.pressure

                hsc_dict = {
                    'hsc_temp' : hsc_temp,
                    'hsc_pres' : hsc_pres,
                }

                csv_data += f"{hsc_temp},{hsc_pres},"

                packet_dict.update(hsc_dict)
            except Exception as e:
                print("Honeywell HSC communications error!")
                print(e)

        if len(ow_roms) > 0:
            for rom in ow_roms:
                addr = ''.join(f'{byte:02x}' for byte in rom[-2:])
                temp = ds.read_temp(rom)
                csv_data += f"temp-{addr},{temp},"
                packet_dict.update({f'temp-{addr}' : temp})

        # Revert PSU mode to more efficient mode
        Pin('WL_GPIO1', Pin.OUT).off()
        
        cbor_packet = build_packet(packet_dict)

        nus_tx_characteristic.write(cbor_packet, send_update=True)

        # Remove the trailing comma
        csv_data = csv_data.rstrip(',')

        print(csv_data)

        sd_queue.append((packet_dict['id'], csv_data + '\n'))

        count = (count + 1) % 65536

        # If debug, blip LED
        if debug:
            led.on()
            await asyncio.sleep_ms(int(task_update_interval / 5))
            led.off()
            await asyncio.sleep_ms(int(task_update_interval * 4 / 5))
        else:
            led.toggle()
            
            now = time.time_ns() // 1_000_000
            if now < update_at:
                await asyncio.sleep_ms(update_at - now)
            else:
                await asyncio.sleep_ms(1)

        update_at += task_update_interval

async def sensor_task_lsm6dso():
    sensor_name = 'LSM6DSOX'

    inner_loop_delay = 50

    global debug
    global sd_queue

    count = 0
    lsm_free_fall_count = 0

    now = time.time_ns() // 1_000_000
    update_at = now - (now % update_interval) + update_interval

    if not lsm:
        return

    while True:
        loop = True
        while loop: 
            timestamp_iso, timestamp_csv = get_timestamp()

            packet_dict = {
                # Universal
                'time' : timestamp_iso,
                'id' : payload_name + '_' + sensor_name,
                'count' : count,
            }

            csv_data = f"{timestamp_csv},{payload_name}_{sensor_name},{count},"

            try:
                if lsm.free_fall():
                    print("-----> FREE FALL DETECTED <-----")
                    lsm_free_fall_count += 1
                    loop = False

                (accel_x, accel_y, accel_z) = lsm.accel()
                (gyro_x, gyro_y, gyro_z) = lsm.gyro()
                lsm_temperature = lsm.temperature()
                
                lsm_dict = {
                    'a_x' : accel_x,
                    'a_y' : accel_y,
                    'a_z' : accel_z,
                    'g_x' : gyro_x,
                    'g_y' : gyro_y, 
                    'g_z' : gyro_z,
                    'fall_cnt' : lsm_free_fall_count,
                    'imu_temp' : lsm_temperature
                }

                csv_data += f"{accel_x},{accel_y},{accel_z},{gyro_x},{gyro_y},{gyro_z},{lsm_free_fall_count},{lsm_temperature}"

                packet_dict.update(lsm_dict)
            except:
                print("LSM6DSO communications error!")

            # This task's data block has no trailing comma, but the header above does,
            # so a comms error leaves one behind. Same strip, same reason.
            csv_data = csv_data.rstrip(',')

            print(csv_data)

            sd_queue.append((packet_dict['id'], csv_data + '\n'))
            
            time_delta = update_at - (time.time_ns() // 1_000_000)
            if time_delta > inner_loop_delay:
                await asyncio.sleep_ms(inner_loop_delay)
            else:
                loop = False

        cbor_packet = build_packet(packet_dict)

        nus_tx_characteristic.write(cbor_packet, send_update=True)

        count = (count + 1) % 65536
        
        now = time.time_ns() // 1_000_000
        if now < update_at:
            await asyncio.sleep_ms(update_at - now)
        else:
            await asyncio.sleep_ms(1) # yield to other tasks if needed

        update_at += update_interval

# Serially wait for connections. Don't advertise while a central is
# connected.
async def peripheral_task():
    adv_name = _truncate(payload_name, _ADV_NAME_LEN)
    if adv_name != payload_name:
        print(f"Advertising as '{adv_name}'")

    while True:
        async with await aioble.advertise(
            _ADV_INTERVAL_MS,
            name=adv_name,
            services=[_WENET_SERVICE_UUID],
        ) as connection:
            print("Connection from", connection.device)
            await connection.disconnected(timeout_ms=None)

_SD_FLUSH_INTERVAL_MS = 5000

# SD card writer task
async def sd_write_task():
    global sd_queue
    while True:
        try:
            spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16), baudrate=24000000)
            cs = Pin(17)

            _sd = sdcard.SDCard(spi=spi, cs=cs, baudrate=24000000)
            print("sd init complete, moutning vfs")

            vfs.mount(_sd, '/sd')

        except Exception as e:
            print("Unable to set up SD card!")
            print(e)
            sd_queue.clear()

        files = {}
        last_flush = time.time_ns() // 1_000_000

        try:
            while True:
                if sd_queue:
                    batch = sd_queue
                    sd_queue = []
                    grouped = {}
                    for dest, data in batch:
                        grouped.setdefault(dest, []).append(data)

                    for dest, parts in grouped.items():
                        if dest not in files:
                            filename = f"/sd/data_log_{dest}.csv"
                            files[dest] = open(filename, 'a')
                            print(f"Opened file {filename}")

                        files[dest].write(''.join(parts))

                        # Yield between files so a big batch can't starve BLE for its
                        # whole duration.
                        await asyncio.sleep_ms(0)

                now = time.time_ns() // 1_000_000
                if now - last_flush >= _SD_FLUSH_INTERVAL_MS:
                    for file in files.values():
                        file.flush()
                        await asyncio.sleep_ms(0)
                    last_flush = now

                await asyncio.sleep(1)

        except Exception as e:
            print(e)
            sd_queue.clear()
            await asyncio.sleep(1)

        finally:
            # .values(), not the dict itself -- iterating a dict yields its keys, and
            # calling .close() on the filename string raises AttributeError out of a
            # finally, which kills this task and every other one through gather().
            for file in files.values():
                file.close()
    


async def main():
    global lsm
    global lis
    global bmp
    global bme
    global hsc
    global max31725
    global hdc302x
    global bme280
    global ow_roms

    print("Checking for I2C devices...")
    devices = i2c.scan()
    print(devices)
    if devices:
        for d in devices:
            if d == 0x1c:
                print("LIS3MDL detected!")
                lis = LIS3MDL(i2c)
            elif d == 0x28:
                print("Honeywell HSC detected!")
                hsc = HSC(i2c)
            elif 0x44 <= d <= 0x45:
                print(f"HDC302x detected at 0x{d:02x}!")
                hdc302x = HDC302x(i2c, address=d)
            elif 0x48 <= d <= 0x4f:
                print(f"MAX31725 detected at 0x{d:02x}!")
                max31725 = MAX31725(i2c, address=d)
            elif d == 0x68:
                print("PCF8523 detected!")
                try:
                    i2c_rtc = PCF8523(i2c)
                    rtc.datetime(i2c_rtc.datetime)
                    now = rtc.datetime()
                    print(f"Synchronized CPU clock -- {now[0]}-{now[1]:02}-{now[2]:02} {now[4]:02}:{now[5]:02}:{now[6]:02}")
                except Exception as e:
                    print(f"PCF8523 sync failed: {e}")
            elif d == 0x6a:
                print("LSM6DSOX detected!")
                lsm = LSM6DSOX(i2c)
            elif d == 0x76 or d == 0x77:
                chip_id = i2c.readfrom_mem(d, 0xD0, 1)[0]
                if chip_id in (0x56, 0x57, 0x58):
                    print(f"BMP280 detected at 0x{d:02x}!")
                    bmp = BMP280(i2c, addr=d)
                elif chip_id == 0x60:
                    print(f"BME280 detected at 0x{d:02x}!")
                    bme280 = BME280(i2c, addr=d)
                elif chip_id == 0x61:
                    print(f"BME680 detected at 0x{d:02x}!")
                    bme = BME680_I2C(i2c, address=d)
                else:
                    print(f"Unknown Bosch chip 0x{chip_id:02x} at 0x{d:02x}")
            else:
                print(f"Unknown device detected at 0x{d:02x}")

    print("Checking for OneWire devices...")
    ow_roms = ds.scan()
    if len(ow_roms) > 0:
        for rom in ow_roms:
            addr = ''.join(f'{byte:02x}' for byte in rom)
            print(f"0x{addr}")
    else:
        print("No OneWire devices found!")

    task_list = []

    task_list.append(asyncio.create_task(sensor_task()))
    task_list.append(asyncio.create_task(peripheral_task()))
    task_list.append(asyncio.create_task(sd_write_task()))
    if lsm:
        task_list.append(asyncio.create_task(sensor_task_lsm6dso()))

    await asyncio.gather(*task_list)

try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()
    bluetooth.BLE().active(False)