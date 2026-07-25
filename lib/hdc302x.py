from machine import I2C
from micropython import const
import time

_DEFAULT_ADDR = const(0x44)

# One-shot, high repeatability, no clock stretching (HDC302x and SHT3x compatible)
_CMD_ONE_SHOT = b'\x24\x00'
_MEAS_DELAY_MS = const(20)

class HDC302x:
    def __init__(self, i2c: I2C, address=_DEFAULT_ADDR):
        self._i2c = i2c
        self._address = address

    def measurements(self):
        """Return (temperature_C, relative_humidity_%) as a tuple."""
        self._i2c.writeto(self._address, _CMD_ONE_SHOT)
        time.sleep_ms(_MEAS_DELAY_MS)
        data = self._i2c.readfrom(self._address, 6)
        raw_temp = (data[0] << 8) | data[1]
        raw_humi = (data[3] << 8) | data[4]
        temp = -45.0 + 175.0 * raw_temp / 65535.0
        humi = 100.0 * raw_humi / 65535.0
        return temp, humi

    @property
    def temperature(self) -> float:
        return self.measurements()[0]

    @property
    def humidity(self) -> float:
        return self.measurements()[1]

if __name__ == "__main__":
    from machine import Pin

    i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
    sensor = HDC302x(i2c)

    while True:
        temp, humi = sensor.measurements()
        print("Temperature: {:.4f} C  Humidity: {:.2f} %RH".format(temp, humi))
        time.sleep(1)
