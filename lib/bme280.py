from bmp280 import BMP280, BMP280_CASE_INDOOR
from micropython import const
from ustruct import unpack as unp

_BME280_REGISTER_CTRL_HUM = const(0xF2)

class BME280(BMP280):
    def __init__(self, i2c_bus, addr=0x76, use_case=BMP280_CASE_INDOOR):
        # Pass use_case=None so parent doesn't write ctrl_meas before we set ctrl_hum
        super().__init__(i2c_bus, addr=addr, use_case=None)

        # Humidity calibration (datasheet section 4.2.2)
        self._H1 = self._read(0xA1, 1)[0]
        h = self._read(0xE1, 7)          # registers E1..E7
        self._H2 = unp('<h', h[0:2])[0]
        self._H3 = h[2]
        self._H4 = (h[3] << 4) | (h[4] & 0x0F)
        if self._H4 > 2047:
            self._H4 -= 4096
        self._H5 = (h[5] << 4) | (h[4] >> 4)
        if self._H5 > 2047:
            self._H5 -= 4096
        self._H6 = h[6] if h[6] < 128 else h[6] - 256

        self._h_raw = 0

        # ctrl_hum must be written before ctrl_meas to take effect
        self._write(_BME280_REGISTER_CTRL_HUM, bytearray([1]))  # osrs_h = 1x

        if use_case is not None:
            self.use_case(use_case)

    def _gauge(self):
        # Read press (3) + temp (3) + hum (2) in one transaction
        d = self._read(0xF7, 8)
        self._p_raw = (d[0] << 12) + (d[1] << 4) + (d[2] >> 4)
        self._t_raw = (d[3] << 12) + (d[4] << 4) + (d[5] >> 4)
        self._h_raw = (d[6] << 8) | d[7]
        self._t_fine = 0
        self._t = 0
        self._p = 0

    @property
    def humidity(self) -> float:
        """Return relative humidity in percent."""
        self._calc_t_fine()  # also calls _gauge(), populating _h_raw and _t_fine

        v = self._t_fine - 76800.0
        v = ((self._h_raw - (self._H4 * 64.0 + (self._H5 / 16384.0) * v)) *
             (self._H2 / 65536.0 * (1.0 + self._H6 / 67108864.0 * v *
              (1.0 + self._H3 / 67108864.0 * v))))
        v = v * (1.0 - self._H1 * v / 524288.0)
        return max(0.0, min(100.0, v))

if __name__ == "__main__":
    from machine import Pin, I2C
    import time

    i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
    sensor = BME280(i2c)

    while True:
        print(f"Temp: {sensor.temperature:.2f} C  Press: {sensor.pressure:.2f} hPa  Humi: {sensor.humidity:.2f} %")
        time.sleep_ms(500)
