# This example demonstrates a simple temperature sensor peripheral.
#
# The sensor's local value is updated, and it will notify
# any connected central every 10 seconds.

import bluetooth
import struct
import time
from ble_advertising import advertising_payload
from micropython import const
from machine import Pin, I2C, ADC, mem32

import bme680 

payload_name = "GDB 1"

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_INDICATE_DONE = const(20)

_FLAG_READ = const(0x0002)
_FLAG_NOTIFY = const(0x0010)
_FLAG_INDICATE = const(0x0020)

# org.bluetooth.service.environmental_sensing
_ENV_SENSE_UUID = bluetooth.UUID(0x181A)

# Wenet service ID
_WENET_SERVICE_UUID = bluetooth.UUID(0x181C)

# org.bluetooth.characteristic.temperature
_PRESSURE_CHAR = (
    bluetooth.UUID(0x2A6D),
    _FLAG_READ | _FLAG_NOTIFY | _FLAG_INDICATE,
)

_TEMP_CHAR = (
    bluetooth.UUID(0x2A6E),
    _FLAG_READ | _FLAG_NOTIFY | _FLAG_INDICATE,
)

_HUMIDITY_CHAR = (
    bluetooth.UUID(0x2A6F), 
    _FLAG_READ | _FLAG_NOTIFY | _FLAG_INDICATE,
)

_ENV_SENSE_SERVICE = (
    _ENV_SENSE_UUID,
    (_PRESSURE_CHAR, _TEMP_CHAR, _HUMIDITY_CHAR,),
)

_WENET_CHAR = (
    bluetooth.UUID("3d235f0e-61f8-4455-89c6-2f7d73c33178"), 
    _FLAG_READ | _FLAG_NOTIFY | _FLAG_INDICATE,
)

_WENET_SERVICE = (
    _WENET_SERVICE_UUID,
    (_WENET_CHAR,),
)

# org.bluetooth.characteristic.gap.appearance.xml
_ADV_APPEARANCE_GENERIC_THERMOMETER = const(768)

class BLE_BME680:
    def __init__(self, ble, i2c, name = None):

        self.sensor = bme680.BME680_I2C(i2c, address=0x76)

        #self.sensor.humidity_oversample(2)
        #self.sensor.pressure_oversample(4)
        #self.sensor.temperature_oversample(8)
        #self.sensor.filter_size(2)

        self._count = 0

        self._ble = ble
        self._ble.active(True)
        self._ble.irq(self._irq)
#        ((self._pressure_handle,self._temp_handle,self._humidity_handle),) = self._ble.gatts_register_services((_ENV_SENSE_SERVICE,))
        ((self._wenet_handle,),) = self._ble.gatts_register_services((_WENET_SERVICE,))
        self._connections = set()
        # if payload_name == None:
        #     payload_name = 'Pico %s' % ubinascii.hexlify(self._ble.config('mac')[1],':').decode().upper()
        print('Sensor name %s' % payload_name)
        self._payload = advertising_payload(
            name=payload_name, services=[_WENET_SERVICE_UUID]
        )
        self._advertise()

    def _irq(self, event, data):
        # Track connections so we can send notifications.
        if event == _IRQ_CENTRAL_CONNECT:
            print("Connected to new device!")
            conn_handle, _, _ = data
            self._connections.add(conn_handle)
        elif event == _IRQ_CENTRAL_DISCONNECT:
            print("Device disconnected!")
            conn_handle, _, _ = data
            self._connections.remove(conn_handle)
            # Start advertising again to allow a new connection.
            self._advertise()
        elif event == _IRQ_GATTS_INDICATE_DONE:
            conn_handle, value_handle, status = data

    def update_sensor(self, notify=True, indicate=False):
        # Write the local value, ready for a central to read.
        
        print("Starting reading...", end="")

        self.sensor._perform_reading()
        core_temp = self._get_temp()
        battery_voltage = self._get_batt()

        print("done!")

        temperature_deg_c = self.sensor.temperature
        pressure = self.sensor.pressure
        humidity = self.sensor.humidity
        gas = self.sensor.gas

        # print(f"g: {gas:0.2f} R");


        # print(f"t: {temperature_deg_c:0.2f} degC");
        # self._ble.gatts_write(self._temp_handle, struct.pack('<h', int(temperature_deg_c * 100)))
        # if notify or indicate:
        #     for conn_handle in self._connections:
        #         if notify:
        #             # Notify connected centrals.
        #             self._ble.gatts_notify(conn_handle, self._temp_handle)
        #         if indicate:
        #             # Indicate connected centrals.
        #             self._ble.gatts_indicate(conn_handle, self._temp_handle)

        # print(f"h: {humidity:0.2f} %");
        # self._ble.gatts_write(self._humidity_handle, struct.pack("<H", int(humidity * 100)))
        # if notify or indicate:
        #     for conn_handle in self._connections:
        #         if notify:
        #             # Notify connected centrals.
        #             self._ble.gatts_notify(conn_handle, self._humidity_handle)
        #         if indicate:
        #             # Indicate connected centrals.
        #             self._ble.gatts_indicate(conn_handle, self._humidity_handle)

        # print(f"p: {pressure:0.2f} mb");
        # self._ble.gatts_write(self._pressure_handle, struct.pack("<I", int(pressure * 1000)))
        # if notify or indicate:
        #     for conn_handle in self._connections:
        #         if notify:
        #             # Notify connected centrals.
        #             self._ble.gatts_notify(conn_handle, self._pressure_handle)
        #         if indicate:
        #             # Indicate connected centrals.
        #             self._ble.gatts_indicate(conn_handle, self._pressure_handle)

        wenet_data = f"{self._count},{core_temp:0.2f},{battery_voltage:0.2f}," + \
                    f"{pressure:0.2f},{temperature_deg_c:0.2f},{humidity:0.2f},{gas:0.0f}"
        self._ble.gatts_write(self._wenet_handle, wenet_data)
        if notify or indicate:
            for conn_handle in self._connections:
                if notify:
                    # Notify connected centrals.
                    self._ble.gatts_notify(conn_handle, self._wenet_handle)
                if indicate:
                    # Indicate connected centrals.
                    self._ble.gatts_indicate(conn_handle, self._wenet_handle)

        print(wenet_data)

        self._count = (self._count + 1) % 65536

    def _advertise(self, interval_us=500000):
        self._ble.gap_advertise(interval_us, adv_data=self._payload)

    # ref https://github.com/raspberrypi/pico-micropython-examples/blob/master/adc/temperature.py
    def _get_temp(self):     
        conversion_factor = 3.3 / (65535)
        reading = ADC(4).read_u16() * conversion_factor

        # The temperature sensor measures the Vbe voltage of a biased bipolar diode, connected to the fifth ADC channel
        # Typically, Vbe = 0.706V at 27 degrees C, with a slope of -1.721mV (0.001721) per degree.
        return 27 - (reading - 0.706) / 0.001721
    
    def _get_adc(self, adc):     
        return ADC(adc).read_u16()

    def setPad(self, gpio, value):
        mem32[0x4001c000 | (4+ (4 * gpio))] = value
        
    def getPad(self, gpio):
        return mem32[0x4001c000 | (4+ (4 * gpio))]


    def _get_batt(self):
        conversion_factor = 3 * 3.3 / (65535)

        oldpad = self.getPad(29)
        self.setPad(29,128)  #no pulls, no output, no input
        reading = ADC(3).read_u16()
        self.setPad(29,oldpad)

        print(reading)
        reading = reading * conversion_factor

        return reading 

if __name__ == "__main__":
    ble = bluetooth.BLE()
    i2c = I2C(1, scl=Pin(7), sda=Pin(6), freq=100000)
    temp = BLE_BME680(ble, i2c)
    counter = 0
    led = Pin('LED', Pin.OUT)
    while True:
        temp.update_sensor(notify=True, indicate=False)
        led.toggle()
        time.sleep_ms(1000)
        counter += 1

    # from machine import I2C
    # i2c = I2C(1, scl=Pin(7), sda=Pin(6), freq=100000)
    # devices = i2c.scan()

    # if devices:
    #     for d in devices:
    #         print(hex(d))