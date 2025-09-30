from machine import ADC, Pin
import network
import time

# Create WLAN object and check if it's active
wlan = network.WLAN(network.STA_IF)
wlan_active = wlan.active()

def get_vsys_voltage():
    # Calculate conversion factor (adjust if needed)
    conversion_factor = 3 * 3.3 / 65535
    vsys_adc = ADC(Pin(29))

    try:
        # Turn off Wi-Fi to allow reading VSYS
        wlan.active(False)

        # Make sure the pin is configured correctly for ADC
        # Note: It is important to set the correct alternative function for the pin
        Pin(29, Pin.ALT, pull=Pin.PULL_DOWN, alt=7)

        # Read the raw ADC value
        vsys_raw = vsys_adc.read_u16()

        # Convert to voltage
        vsys_voltage = vsys_raw * conversion_factor
        return vsys_voltage

    finally:
        # Restore the original pin state and re-activate WLAN
        Pin(29, Pin.ALT, pull=Pin.PULL_DOWN, alt=7)
        wlan.active(wlan_active)

# Example usage:
voltage = get_vsys_voltage()
print(f"VSYS Voltage: {voltage:.2f}V")