# SPDX-FileCopyrightText: 2016 Philip R. Moyer for Adafruit Industries
# SPDX-FileCopyrightText: 2016 Radomir Dopieralski for Adafruit Industries
#
# SPDX-License-Identifier: MIT

def _bcd2bin(value: int) -> int:
    """Convert binary coded decimal to Binary

    :param value: the BCD value to convert to binary (required, no default)
    """
    return value - 6 * (value >> 4)


def _bin2bcd(value: int) -> int:
    """Convert a binary value to binary coded decimal.

    :param value: the binary value to convert to BCD. (required, no default)
    """
    return value + 6 * (value // 10)


class PCF8523:
    """
    Date and time register using binary coded decimal structure.

    The byte order of the register must* be: second, minute, hour, weekday, day (1-31), month, year
    (in years after 2000).

    * Setting weekday_first=False will flip the weekday/day order so that day comes first.

    Values are `time.struct_time`

    :param int register_address: The register address to start the read
    :param bool weekday_first: True if weekday is in a lower register than the day of the month
        (1-31)
    :param int weekday_start: 0 or 1 depending on the RTC's representation of the first day of the
        week
    """

    def __init__(self, i2c, weekday_first: bool = True, weekday_start: int = 1) -> None:
        self._i2c = i2c
        self.weekday_start = weekday_start
        # Masking value list   n/a  sec min hr day wkday mon year
        self.mask_datetime = b"\xff\x7f\x7f\x3f\x3f\x07\x1f\xff"

    @property
    def datetime(self) -> tuple[int, int, int, int, int, int, int, int]:
        buffer = bytearray(7)
        # Read and return the date and time.
        self._i2c.readfrom_mem_into(0x68, 0x03, buffer)

        return (
                _bcd2bin(buffer[6] & self.mask_datetime[7]) + 2000, # Year
                _bcd2bin(buffer[5] & self.mask_datetime[6]), # Month
                _bcd2bin(buffer[3] & self.mask_datetime[4]), # Day of Month
                _bcd2bin(
                    (buffer[4] & self.mask_datetime[5])
                    - self.weekday_start
                ), # Day of Week
                _bcd2bin(buffer[2] & self.mask_datetime[3]), # Hour
                _bcd2bin(buffer[1] & self.mask_datetime[2]), # Min
                _bcd2bin(buffer[0] & self.mask_datetime[1]), # Second
                0 # Sub second
                )

    @datetime.setter
    def datetime(self, value: tuple[int, int, int, int, int, int, int, int]) -> None:
        buffer = bytearray(7)

        # Enable battery switchover, set lost_power to false
        buffer[0] = 0x02
        buffer[1] = 0b00000000
        self._i2c.writeto(0x68, buffer[0:1])

        buffer[0] = _bin2bcd(value[6]) & 0x7F  # format conversions # Second
        buffer[1] = _bin2bcd(value[5]) # Minute
        buffer[2] = _bin2bcd(value[4]) # Hour
        buffer[3] = _bin2bcd(value[2]) # Day of month
        buffer[4] = _bin2bcd(value[3] + self.weekday_start) # Day of week
        buffer[5] = _bin2bcd(value[1]) # Month
        buffer[6] = _bin2bcd(value[0] - 2000) # Year

        self._i2c.writeto_mem(0x68, 0x03, buffer)