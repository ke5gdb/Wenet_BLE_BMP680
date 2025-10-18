# SPDX-FileCopyrightText: 2020 Bryan Siepert for Adafruit Industries
#
# SPDX-License-Identifier: MIT

"""
`adafruit_lis3mdl`
================================================================================

CircuitPython helper library for the LIS3MDL 3-axis magnetometer

* Author(s): Bryan Siepert

Implementation Notes
--------------------

**Hardware:**
* Adafruit `Adafruit LSM6DS33 + LIS3MDL - 9 DoF IMU
<https://www.adafruit.com/product/4485>`_ (Product ID: 4485)

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://circuitpython.org/downloads


* Adafruit's Bus Device library: https://github.com/adafruit/Adafruit_CircuitPython_BusDevice
* Adafruit's Register library: https://github.com/adafruit/Adafruit_CircuitPython_Register
"""

from time import sleep
import struct

from micropython import const
from machine import I2C

try:
    from typing import Iterable, Optional, Tuple, Union

except ImportError:
    pass

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_LIS3MDL.git"


_LIS3MDL_DEFAULT_ADDRESS = const(0x1C)

_LIS3MDL_CHIP_ID = const(0x3D)

_LIS3MDL_WHOAMI = const(0xF)

_LIS3MDL_WHO_AM_I = const(0x0F)  # Register that contains the part ID
_LIS3MDL_CTRL_REG1 = const(0x20)  # Register address for control 1
_LIS3MDL_CTRL_REG2 = const(0x21)  # Register address for control 2
_LIS3MDL_CTRL_REG3 = const(0x22)  # Register address for control 3
_LIS3MDL_CTRL_REG4 = const(0x23)  # Register address for control 3
_LIS3MDL_OUT_X_L = const(0x28)  # Register address for X axis lower byte
_LIS3MDL_TEMP_L = const(0x2E)  # Register address for X axis lower byte
_LIS3MDL_INT_CFG = const(0x30)  # Interrupt configuration register
_LIS3MDL_INT_THS_L = const(0x32)  # Low byte of the irq threshold

_GAUSS_TO_UT = 100


class CV:
    """struct helper"""

    @classmethod
    def add_values(cls, value_tuples: Iterable[Tuple[str, int, Union[int, str], Optional[int]]]):
        "creates CV entires"
        cls.string = {}
        cls.lsb = {}

        for value_tuple in value_tuples:
            name, value, string, lsb = value_tuple
            setattr(cls, name, value)
            cls.string[value] = string
            cls.lsb[value] = lsb

    @classmethod
    def is_valid(cls, value: int) -> bool:
        "Returns true if the given value is a member of the CV"
        return value in cls.string


class Range(CV):
    """Options for ``accelerometer_range``"""


Range.add_values(
    (
        ("RANGE_4_GAUSS", 0, 4, 6842),
        ("RANGE_8_GAUSS", 1, 8, 3421),
        ("RANGE_12_GAUSS", 2, 12, 2281),
        ("RANGE_16_GAUSS", 3, 16, 1711),
    )
)


class PerformanceMode(CV):
    """Options for `performance_mode`"""


PerformanceMode.add_values(
    (
        ("MODE_LOW_POWER", 0, "Low Power", None),
        ("MODE_MEDIUM", 1, "Medium Performance", None),
        ("MODE_HIGH", 2, "High Performance", None),
        ("MODE_ULTRA", 3, "Ultra-high Performance", None),
    )
)


class Rate(CV):
    """Options for `data_rate`

    =============================  ============================================
    Rate                           Meaning
    =============================  ============================================
    ``RATE_0_625_HZ``              0.625 HZ
    ``RATE_1_25_HZ``               1.25 HZ
    ``RATE_2_5_HZ``                2.5 HZ
    ``RATE_5_HZ``                  5 HZ
    ``RATE_10_HZ``                 10 HZ
    ``RATE_20_HZ``                 20 HZ
    ``RATE_40_HZ``                 40 HZ
    ``RATE_80_HZ``                 80 HZ
    ``RATE_155_HZ``                155 HZ ( Sets ``PerformanceMode`` to ``MODE_ULTRA``)
    ``RATE_300_HZ``                300 HZ ( Sets ``PerformanceMode`` to ``MODE_HIGH``)
    ``RATE_560_HZ``                560 HZ ( Sets ``PerformanceMode`` to ``MODE_MEDIUM``)
    ``RATE_1000_HZ``               1000 HZ ( Sets ``PerformanceMode`` to ``MODE_LOW_POWER``)
    =============================  ============================================

    """


# The magnetometer data rate, includes FAST_ODR bit
Rate.add_values(
    (
        ("RATE_0_625_HZ", 0b0000, 0.625, None),
        ("RATE_1_25_HZ", 0b0010, 1.25, None),
        ("RATE_2_5_HZ", 0b0100, 2.5, None),
        ("RATE_5_HZ", 0b0110, 5.0, None),
        ("RATE_10_HZ", 0b1000, 10.0, None),
        ("RATE_20_HZ", 0b1010, 20.0, None),
        ("RATE_40_HZ", 0b1100, 40.0, None),
        ("RATE_80_HZ", 0b1110, 80.0, None),
        ("RATE_155_HZ", 0b0001, 155.0, None),
        ("RATE_300_HZ", 0b0011, 300.0, None),
        ("RATE_560_HZ", 0b0101, 560.0, None),
        ("RATE_1000_HZ", 0b0111, 1000.0, None),
    )
)


class OperationMode(CV):
    """Options for `operation_mode`

    =============================  ============================================
    Operation Mode                 Meaning
    =============================  ============================================
    ``OperationMode.CONTINUOUS``     Measurements are made continuously at the given `data_rate`
    ``OperationMode.SINGLE``         Setting to ``SINGLE`` takes a single measurement.
    ``OperationMode.POWER_DOWN``     Halts measurements. `magnetic` will return the last measurement
    =============================  ============================================
    """


OperationMode.add_values(
    (
        ("CONTINUOUS", 0b00, "Continuous", None),
        ("SINGLE", 0b01, "Single", None),
        ("POWER_DOWN", 0b11, "Power Down", None),
    )
)
# /** The magnetometer operation mode */
# typedef enum {
#   LIS3MDL_CONTINUOUSMODE = , ///< Continuous conversion
#   LIS3MDL_SINGLEMODE = ,     ///< Single-shot conversion
#   LIS3MDL_POWERDOWNMODE = ,  ///< Powered-down mode
# } lis3mdl_operationmode_t;


class LIS3MDL:
    """Driver for the LIS3MDL 3-axis magnetometer.

    :param ~busio.I2C i2c_bus: The I2C bus the LIS3MDL is connected to.
    :param int address: The I2C device address. Defaults to :const:`0x1C`

    **Quickstart: Importing and using the device**

        Here is an example of using the :class:`LIS3MDL` class.
        First you will need to import the libraries to use the sensor

        .. code-block:: python

            import board
            import adafruit_lis3mdl

        Once this is done you can define your `board.I2C` object and define your sensor object

        .. code-block:: python

            i2c = board.I2C()
            sensor = adafruit_lis3mdl.LIS3MDL(i2c)

        Now you have access to the :attr:`magnetic` attribute

        .. code-block:: python

            mag_x, mag_y, mag_z = sensor.magnetic


    """

    # _chip_id = ROUnaryStruct(_LIS3MDL_WHOAMI, "<b")

    # _perf_mode = RWBits(2, _LIS3MDL_CTRL_REG1, 5)
    # _z_perf_mode = RWBits(2, _LIS3MDL_CTRL_REG4, 2)

    # _operation_mode = RWBits(2, _LIS3MDL_CTRL_REG3, 0)

    # _data_rate = RWBits(4, _LIS3MDL_CTRL_REG1, 1)

    # _raw_mag_data = Struct(_LIS3MDL_OUT_X_L, "<hhh")

    # _range = RWBits(2, _LIS3MDL_CTRL_REG2, 5)
    
    # _reset = RWBit(_LIS3MDL_CTRL_REG2, 2)

    def __init__(
        self,
        i2c_bus: I2C,
        address: int = _LIS3MDL_DEFAULT_ADDRESS,
        performance_mode: PerformanceMode = PerformanceMode.MODE_ULTRA,
        data_rate: Rate = Rate.RATE_2_5_HZ,
        range_: Range = Range.RANGE_4_GAUSS,
        operation_mode: OperationMode = OperationMode.CONTINUOUS,
    ) -> None:
        self.i2c_bus = i2c_bus
        self.address = address

        if self._read_reg(_LIS3MDL_WHOAMI) != _LIS3MDL_CHIP_ID:
            raise RuntimeError("Failed to find LIS3MDL - check your wiring!")

        self.reset()
        self.performance_mode = performance_mode
        self.data_rate = data_rate
        self.range = range_
        self.operation_mode = operation_mode

        # Enable temperature sensor
        self._write_reg(_LIS3MDL_CTRL_REG1, self._read_reg(_LIS3MDL_CTRL_REG1) & 0b01111111 | (1 << 7))

        sleep(0.010)

    def _read_reg(self, reg, size=1):
        buf = self.i2c_bus.readfrom_mem(self.address, reg, size)
        if size == 1:
            return int(buf[0])
        return [int(x) for x in buf]

    def _write_reg(self, reg, val):
        self.i2c_bus.writeto_mem(self.address, reg, bytes([val]))

    def _read_reg_into(self, reg, buf):
        self.i2c_bus.readfrom_mem_into(self.address, reg, buf)

    def reset(self) -> None:
        """Reset the sensor to the default state set by the library"""
        self._write_reg(_LIS3MDL_CTRL_REG2, 0x04)
        sleep(0.010)

    @property
    def magnetic(self) -> Tuple[float, float, float]:
        """The processed magnetometer sensor values.
        A 3-tuple of X, Y, Z axis values in microteslas that are signed floats.
        """
        _raw_mag_data = bytearray(6)
        self._read_reg_into(_LIS3MDL_OUT_X_L, _raw_mag_data)
        raw_mag_data = struct.unpack("<hhh", _raw_mag_data)

        x = self._scale_mag_data(raw_mag_data[0])
        y = self._scale_mag_data(raw_mag_data[1])
        z = self._scale_mag_data(raw_mag_data[2])

        return (x, y, z)

    def _scale_mag_data(self, raw_measurement: int) -> float:
        return (raw_measurement / Range.lsb[self.range]) * _GAUSS_TO_UT
    
    @property
    def temperature(self) -> float:
        """Temperature measurement from the sensor"""
        _temp = bytearray(2)
        self._read_reg_into(_LIS3MDL_TEMP_L, _temp)
        temp = struct.unpack("<h", _temp)[0] / 8.0 + 25.0

        return temp

    @property
    def range(self) -> int:
        """The measurement range for the magnetic sensor. Must be a ``Range``"""
        buf = self._read_reg(_LIS3MDL_CTRL_REG2, size=1)
        if isinstance(buf, list):
            buf = buf[0]
        buf = (buf >> 5) & 0x03
        return buf

    @range.setter
    def range(self, value: int) -> None:
        if not Range.is_valid(value):
            raise AttributeError("``range`` must be a ``Range``")

        self._write_reg(_LIS3MDL_CTRL_REG2, (value << 5))

        sleep(0.010)

    @property
    def data_rate(self) -> int:
        """The rate at which the sensor takes measurements. Must be a ``Rate``"""
        buf = self._read_reg(_LIS3MDL_CTRL_REG1, size=1)
        if isinstance(buf, list):
            buf = buf[0]
        buf = (buf >> 1) & 0x0F
        return buf

    @data_rate.setter
    def data_rate(self, value: int) -> None:
        if value is Rate.RATE_155_HZ:
            self.performance_mode = PerformanceMode.MODE_ULTRA
        if value is Rate.RATE_300_HZ:
            self.performance_mode = PerformanceMode.MODE_HIGH
        if value is Rate.RATE_560_HZ:
            self.performance_mode = PerformanceMode.MODE_MEDIUM
        if value is Rate.RATE_1000_HZ:
            self.performance_mode = PerformanceMode.MODE_LOW_POWER
        sleep(0.010)
        if not Rate.is_valid(value):
            raise AttributeError("`data_rate` must be a `Rate`")
        self._write_reg(_LIS3MDL_CTRL_REG1, self._read_reg(_LIS3MDL_CTRL_REG1) & 0b11100001 | (value << 1))

    @property
    def performance_mode(self) -> int:
        """Sets the 'performance mode' of the sensor. Must be a ``PerformanceMode``.
        Note that `performance_mode` affects the available data rate and will be
        automatically changed by setting ``data_rate`` to certain values."""

        buf = self._read_reg(_LIS3MDL_CTRL_REG1, size=1)
        if isinstance(buf, list):
            buf = buf[0]
        buf = (buf >> 5) & 0x03
        return buf

    @performance_mode.setter
    def performance_mode(self, value: int) -> None:
        if not PerformanceMode.is_valid(value):
            raise AttributeError("`performance_mode` must be a `PerformanceMode`")
        
        self._write_reg(_LIS3MDL_CTRL_REG1, self._read_reg(_LIS3MDL_CTRL_REG1) & 0b10011111 | (value << 5))
        self._write_reg(_LIS3MDL_CTRL_REG4, self._read_reg(_LIS3MDL_CTRL_REG4) & 0b11110011 | (value << 2))

    @property
    def operation_mode(self) -> int:
        """The operating mode for the sensor, controlling how measurements are taken.
        Must be an `OperationMode`. See the the `OperationMode` document for additional details
        """
        buf = self._read_reg(_LIS3MDL_CTRL_REG3)
        if isinstance(buf, list):
            buf = buf[0]
        buf = buf & 0x03
        return buf

    @operation_mode.setter
    def operation_mode(self, value: int) -> None:
        if not OperationMode.is_valid(value):
            raise AttributeError("operation mode must be a OperationMode")
        self._write_reg(_LIS3MDL_CTRL_REG3, self._read_reg(_LIS3MDL_CTRL_REG3) & 0xFC | value)

if __name__ == "__main__":
    print("Running LIS3MDL demo!")
    import time

    from machine import Pin, I2C
    # Init in I2C mode.
    lis = LIS3MDL(I2C(0, scl=Pin(5), sda=Pin(4), freq=100000))

    while (True):
        mag_x, mag_y, mag_z = lis.magnetic
        print(f"X:{mag_x:10.2f}, Y:{mag_y:10.2f}, Z:{mag_z:10.2f} uT")
        print(f"Temperature: {lis.temperature:.2f}")
        time.sleep_ms(500)