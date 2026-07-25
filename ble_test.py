import asyncio
from bleak import BleakScanner, BleakClient
import cbor2
from datetime import datetime

_WENET_SERVICE_UUID = 'fb63feb8-31ad-451d-a587-9fc20f9c8add'

# Edit this function to display the data in a useful manner
def response_handler(sender, data: bytes):
    _data = cbor2.loads(data)
    time = datetime.fromisoformat(_data['time']).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    count = _data['count']
    id = _data['id']
    print(f"{time},{count},{id},", end='')
    for key, value in _data.items():
        # Skip items printed above
        if key == 'time' or key == 'count' or key == 'id':
            continue

        if isinstance(value, float):
            _value = f"{value:.03f}"
        else:
            _value = value

        print(f"{key},{_value},", end='')
    print()

async def connect_to_device(address):
    rx_charac = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

    print(f"Connecting to device at {address}...")
    try:
        async with BleakClient(address) as client:
            if client.is_connected:
                print("Connected successfully!")
                # Perform operations like reading/writing characteristics here
            else:
                print("Failed to connect.")
                return

            await client.start_notify(rx_charac, response_handler)
            while client.is_connected:
                await asyncio.sleep(0.1)
    except Exception:
        print("Bluetooth connection failed!")

async def main():
    while True:
        print("Scanning for Wenet BLE devices...")
        devices = await BleakScanner.discover(service_uuids=[_WENET_SERVICE_UUID], timeout=5)
        if len(devices) == 0:
            print("No devices found!")
        else:
            print(f"Found {len(devices)} devices!")
            for d in devices:
                print(f"Address: {d.address}, Name: {d.name}")

            # Connect to devices
            await connect_to_device(devices[0].address)

asyncio.run(main())