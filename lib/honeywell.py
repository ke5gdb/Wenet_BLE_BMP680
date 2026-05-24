from machine import I2C

_DEFAULT_ADDR = const(0x28)
_DEFAULT_SCALE = const(400)

class HSC:
    def __init__(
        self,
        i2c: I2C,
        address=_DEFAULT_ADDR,
        scale=_DEFAULT_SCALE,
        temp_sense=True
    ):

        self._i2c = i2c
        self._scale = scale
        self._address = address
        self._temp_sense = temp_sense

    @property
    def pressure(self) -> float:
        """Return the current pressure in millibar."""
        data = self._i2c.readfrom_mem(self._address, 0x28, 2)
        raw_pressure = ((data[0] & 0x3F) << 8) | data[1]
        pressure = self._scale * (raw_pressure - 0x2000) / 16384
        return pressure

    @property
    def temperature(self) -> float:
        """Return the current temperature in Celsius."""
        if not self._temp_sense:
            raise AttributeError("Temperature sensing is disabled.")
        data = self._i2c.readfrom_mem(self._address, 0x28, 4)
        raw_temp = (data[2] << 3) | (data[3] >> 5)
        temperature = ((raw_temp) * 200 / 2047) - 50
        return temperature

if __name__ == "__main__":
    from machine import Pin
    import time

    i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
    honeywell_sensor = HSC(i2c)

    while True:
        print("Pressure: {:.2f} mb".format(honeywell_sensor.pressure))
        print("Temperature: {:.2f} C".format(honeywell_sensor.temperature))
        time.sleep(1)