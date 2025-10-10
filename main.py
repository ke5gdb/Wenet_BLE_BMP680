import os
import bluetooth
import time
from ble_advertising import advertising_payload
from micropython import const
from machine import Pin, ADC, mem32, RTC, I2C

from sd_card import SD_Card

# Task list:
# * SD card error checking
# * RTC -- read from hardware if available on init
# * RTC -- write to hardware
# * RTC -- periodically update running RTC


# Must be 4 characters or less
payload_name = "Test"

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_GATTS_INDICATE_DONE = const(20)

_FLAG_READ = const(0x0002)
_FLAG_WRITE = const(0x0008)
_FLAG_NOTIFY = const(0x0010)
_FLAG_INDICATE = const(0x0020)

# Wenet service ID
_WENET_SERVICE_UUID = bluetooth.UUID(0x181C)

_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")

_UART_TX = (
    bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_NOTIFY,
)
_UART_RX = (
    bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_WRITE,
)
_UART_SERVICE = (
    _UART_UUID,
    (_UART_TX, _UART_RX),
)

_WENET_CHAR = (
    bluetooth.UUID("3d235f0e-61f8-4455-89c6-2f7d73c33178"),0x0000,
)

_WENET_SERVICE = (
    _WENET_SERVICE_UUID,
    (_WENET_CHAR,),
)

class HAB_BLE:
    def __init__(self, ble, i2c, name = None):
        self._count = 0
        self._sub_count = 0
        self._ble = ble
        self._ble.active(True)
        self._ble.irq(self._irq)
        self._rx_buffer = bytearray()
        self._handler = self.handler
        self._i2c = i2c

        ((self._wenet_handle,),(self._uart_tx, self.uart_rx,),) = self._ble.gatts_register_services((_WENET_SERVICE,_UART_SERVICE,))
        self._connections = set()
        
        # if payload_name == None:
        #     payload_name = 'Pico %s' % ubinascii.hexlify(self._ble.config('mac')[1],':').decode().upper()
        
        print('Sensor name: %s' % payload_name)
        
        self._payload = advertising_payload(
            name=payload_name, services=[_UART_UUID, _WENET_SERVICE_UUID]
        )
        self._advertise()

        # Configure SD card, if available
        self._sd = SD_Card()

        # Initialize RTC
        self._rtc = RTC()
        self._now = None

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
        elif event == _IRQ_GATTS_WRITE:
            conn_handle, value_handle = data
            if conn_handle in self._connections and value_handle == self.uart_rx:
                self._rx_buffer += self._ble.gatts_read(self.uart_rx)
                if self._handler:
                    self._handler()

    def update_sensor(self, notify=True, indicate=False):
        # Write the local value, ready for a central to read.
        
        # Change power supply mode to reduce ripple
        Pin('WL_GPIO1', Pin.OUT).on()

        core_temp = self._get_temp()
        battery_voltage = self._get_batt()

        adc0 = self._get_adc(0)
        adc1 = self._get_adc(1)
        adc2 = self._get_adc(2)

        # Revert PSU mode to more efficient mode
        Pin('WL_GPIO1', Pin.OUT).off()

        now = self._rtc.datetime()
        ms = time.time_ns() // 1_000_000 % 1000
        timestamp = f"{now[0]}-{now[1]:02}-{now[2]:02} {now[4]:02}:{now[5]:02}:{now[6]:02}.{ms:03}"

        data = f"{timestamp},{self._count},{battery_voltage:0.2f},{core_temp:0.2f},{adc0},{adc1},{adc2}"

        self._sd.write(data)
        
        self._ble.gatts_write(self._uart_tx, data)
        if notify or indicate:
            for conn_handle in self._connections:
                if notify:
                    # Notify connected centrals.
                    self._ble.gatts_notify(conn_handle, self._uart_tx)
                if indicate:
                    # Indicate connected centrals.
                    self._ble.gatts_indicate(conn_handle, self._uart_tx)

        print(data)

        self._count = (self._count + 1) % 65536

    def _read_uart(self, sz=None):
        if not sz:
            sz = len(self._rx_buffer)
        result = self._rx_buffer[0:sz]
        self._rx_buffer = self._rx_buffer[sz:]
        return result
    
    def handler(self):
        print(f"BLE RX: {self._read_uart().decode().strip()}")

    def _advertise(self, interval_us=250000):
        self._ble.gap_advertise(interval_us, adv_data=self._payload)

    # ref https://github.com/raspberrypi/pico-micropython-examples/blob/master/adc/temperature.py
    def _get_temp(self):     
        conversion_factor = 3.3 / (65535)
        reading = ADC(4).read_u16() * conversion_factor

        # The temperature sensor measures the Vbe voltage of a biased bipolar diode, connected to the fifth ADC channel
        # Typically, Vbe = 0.706V at 27 degrees C, with a slope of -1.721mV (0.001721) per degree.
        return 27 - (reading - 0.706) / 0.001721
    
    def _get_adc(self, adc):     
        return ADC(adc).read_u16() >> 4

    def _setPad(self, gpio, value):
        mem32[0x4001c000 | (4 + (4 * gpio))] = value
        
    def _getPad(self, gpio):
        return mem32[0x4001c000 | (4 + (4 * gpio))]

    def _get_batt(self):
        conversion_factor = 3 * (3.3 / (65535))

        oldpad = self._getPad(29)
        if "Pico 2" in os.uname().machine:
            Pin(29, Pin.ALT, pull=None, alt=7)
        else:
            self._setPad(29,128)  #no pulls, no output, no input
        reading = ADC(3).read_u16()
        self._setPad(29,oldpad)

        reading = reading * conversion_factor

        return reading 

if __name__ == "__main__":
    ble = bluetooth.BLE()
    i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
    hab_ble = HAB_BLE(ble, i2c)
    led = Pin('LED', Pin.OUT)
    count = 0

    print("Checking for I2C devices...")
    devices = i2c.scan()
    if devices:
        for d in devices:
            print(hex(d))
    print("done!")

    time.sleep(2)

    while True:
        notify = False
        if count % 50:
            notify = True
            led.toggle()
        hab_ble.update_sensor(notify=notify, indicate=False)
        time.sleep_ms(10)