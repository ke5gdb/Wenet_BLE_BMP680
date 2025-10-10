from machine import Pin, SPI

import sdcard
import vfs

class SD_Card:
    def __init__(self) -> None:
        try:
            spi = SPI(0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
            cs = Pin(17)

            self._sd = sdcard.SDCard(spi=spi, cs=cs)

            vfs.mount(self._sd, '/sd')

        except Exception as e:
            print("Unable to set up SD card!")
            print(e)
            self._sd = None

    def write(self, data) -> bool:
        write_ok = False
        if self._sd:
            try:
                with open('/sd/data_log.csv', 'a') as f:
                    f.write(f"{data}\n")
                    write_ok = True
            except Exception as e:
                print(e)
        return write_ok