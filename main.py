import asyncio
import os
import aioble
import bluetooth
import cbor2
import json
import time
from micropython import const
from machine import Pin, ADC, mem32, RTC, I2C
import rp2

from sd_card import SD_Card
from pcf8523 import PCF8523
from lsm6dsox import LSM6DSOX
from lis3mdl import LIS3MDL
from bme680 import BME680_I2C

sensor_name = "Sensor Name"

# BLE Update rate (in ms)
update_interval = 500

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
sd = SD_Card()
rtc = RTC()
i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
led = Pin('LED', Pin.OUT)

lsm = None
lis = None
bme = None

def build_packet(packet_dict):
    packet = cbor2.dumps(packet_dict)
    # Need binary packet to be less than 254 bytes to fit in wenet telemetry frame
    assert(len(packet) <= 254)
    return packet

def get_iso_timestamp():
    now = rtc.datetime()
    ms = time.time_ns() // 1_000_000 % 1000
    return f"{now[0]}-{now[1]:02}-{now[2]:02}T{now[4]:02}:{now[5]:02}:{now[6]:02}.{ms:03}+00:00"

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
    count = 0
    debug = False

    lsm_free_fall_count = 0

    while True:
        # Offer a mechanism to send JSON strings via BLE UART for debugging using nRF phone app
        if rp2.bootsel_button():
            for i in range(10):
                led.toggle()
                await asyncio.sleep_ms(100)
            debug = ~debug

        # Change power supply mode to reduce ripple
        Pin('WL_GPIO1', Pin.OUT).on()
        
        # Get ADC values, scale as needed
        adc0 = get_adc(0)
        adc1 = get_adc(1)
        adc2 = get_adc(2)

        packet_dict = {
            # Universal
            'time' : get_iso_timestamp(),
            'id' : sensor_name,
            'count' : count,
            'volts' : get_batt(), 
            'pi_temp' : get_core_temp(),

            # ADC inputs
            'adc0' : adc0,
            'adc1' : adc1,
            'adc2' : adc2
        }

        if lsm:
            try:
                if lsm.free_fall():
                    print("-----> FREE FALL DETECTED <-----")
                    lsm_free_fall_count += 1

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

                packet_dict.update(lsm_dict)
            except:
                print("LSM6DSO communications error!")

        # Get data from LIS3MDL magnetometer/compass, if available 
        if lis:
            try:
                (mag_x, mag_y, mag_z) = lis.magnetic

                lis_dict = {
                    'mag_x' : mag_x,
                    'mag_y' : mag_y,
                    'mag_z' : mag_z
                }

                packet_dict.update(lis_dict)
            except:
                print("LIS3MDL communications error!")

        # Get data from BME680, if available
        if bme:
            try:
                bme._perform_reading()
                bme_temp = bme.temperature
                bme_pressure = bme.pressure
                bme_humidity = bme.humidity
                bme_gas = bme.gas

                bme_dict = {
                    'temp' : bme_temp,
                    'pres' : bme_pressure,
                    'humi' : bme_humidity,
                    'gas' : bme.gas
                }

                packet_dict.update(bme_dict)
            except:
                print("BME680 communications error!")

        # Revert PSU mode to more efficient mode
        Pin('WL_GPIO1', Pin.OUT).off()
        
        cbor_packet = build_packet(packet_dict)
        json_packet = json.dumps(packet_dict)

        if not debug:
            nus_tx_characteristic.write(cbor_packet, send_update=True)
        else:
            nus_tx_characteristic.write(json_packet, send_update=True)

        print(json_packet)
        
        if not sd.write(json_packet):
            print("SD card write failure")

        count = (count + 1) % 65536

        # If debug, blip LED
        if debug:
            led.on()
            await asyncio.sleep_ms(int(update_interval / 5))
            led.off()
            await asyncio.sleep_ms(int(update_interval * 4 / 5))
        else:
            led.toggle()
            await asyncio.sleep_ms(update_interval)

# Serially wait for connections. Don't advertise while a central is
# connected.
async def peripheral_task():
    while True:
        async with await aioble.advertise(
            _ADV_INTERVAL_MS,
            name=sensor_name,
            services=[_WENET_SERVICE_UUID],
        ) as connection:
            print("Connection from", connection.device)
            await connection.disconnected(timeout_ms=None)

async def main():
    global lsm
    global lis
    global bme

    print("Checking for I2C devices...")
    devices = i2c.scan()
    if devices:
        for d in devices:
            if d == 0x1c:
                print("LIS3MDL detected!")
                lis = LIS3MDL(i2c)
            elif d == 0x68:
                print("PCF8523 detected!")
                i2c_rtc = PCF8523(i2c)
                rtc = RTC()
                rtc.datetime(i2c_rtc.datetime)
                now = rtc.datetime()
                print(f"Synchronized CPU clock -- {now[0]}-{now[1]:02}-{now[2]:02} {now[4]:02}:{now[5]:02}:{now[6]:02}")
            elif d == 0x6a:
                print("LSM6DSOX detected!")
                lsm = LSM6DSOX(i2c)
            elif d == 0x77:
                print("BME680 detected!")
                bme = BME680_I2C(i2c, address=0x77)
            else:
                print(f"Unknown device detected at 0x{d:02x}")

    t1 = asyncio.create_task(sensor_task())
    t2 = asyncio.create_task(peripheral_task())
    await asyncio.gather(t1, t2)

asyncio.run(main())