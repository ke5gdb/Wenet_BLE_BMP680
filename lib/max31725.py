from machine import I2C
from micropython import const

_DEFAULT_ADDR = const(0x48)
_TEMP_REG = const(0x00)

class MAX31725:
    def __init__(self, i2c: I2C, address=_DEFAULT_ADDR):
        self._i2c = i2c
        self._address = address

    @property
    def temperature(self) -> float:
        """Return the current temperature in Celsius."""
        data = self._i2c.readfrom_mem(self._address, _TEMP_REG, 2)
        raw = (data[0] << 8) | data[1]
        if raw > 32767:
            raw -= 65536
        return raw / 256.0

if __name__ == "__main__":
    from machine import Pin
    import time

    i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
    sensor = MAX31725(i2c)

    while True:
        print("Temperature: {:.4f} C".format(sensor.temperature))
        time.sleep(1)