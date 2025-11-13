from machine import I2C, RTC, Pin
import network
import ntptime
import time
from pcf8523 import PCF8523

# Wi-Fi credentials
SSID = '' # Change me!
PASSWORD = '' # Change me too!

i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)
i2c_rtc = PCF8523(i2c)

rtc_datetime = i2c_rtc.datetime
print(f"Current I2C RTC: {rtc_datetime}")

# Connect to Wi-Fi
print("Connecting to Wi-Fi")
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)
while not wlan.isconnected():
    time.sleep(1)
    print(".", end='')
print(wlan.ifconfig())
print()
print("Connected to Wi-Fi")

# Initialize RTC
rtc = RTC()

# Set NTP host (optional, defaults to pool.ntp.org)
ntptime.host = 'pool.ntp.org'

# Synchronize RTC with NTP
try:
    ntptime.settime()
    print("RTC synchronized with NTP.")
except OSError as e:
    print(f"Error synchronizing with NTP: {e}")

# Get and print current time from RTC
current_time = rtc.datetime()
print(f"Current RTC time: {current_time}")

# Disconnect from Wi-Fi
wlan.disconnect()
wlan.active(False)
print("Disconnected from Wi-Fi")

now_i2c = i2c_rtc.datetime
now_rtc = rtc.datetime()

# Sync NTP with hardware RTC
i2c_rtc.datetime = now_rtc

# Calculate difference in clocks, for fun
diff_y = now_i2c[0] - now_rtc[0]
diff_m = now_i2c[1] - now_rtc[1]
diff_d = now_i2c[2] - now_rtc[2]
diff_wd = now_i2c[3] - now_rtc[3]
diff_h = now_i2c[4] - now_rtc[4]
diff_m = now_i2c[5] - now_rtc[5]
diff_s = now_i2c[6] - now_rtc[6]

print("Time difference:")
print(f"Year : {diff_y}")
print(f"Month: {diff_m}")
print(f"Day  : {diff_d}")
print(f"WkDay: {diff_wd}")
print(f"Hour : {diff_h}")
print(f"Min  : {diff_m}")
print(f"Sec  : {diff_s}")

print("I2C RTC synchronized")

while True:
    print(f"I2C RTC: {i2c_rtc.datetime}")
    time.sleep(1)