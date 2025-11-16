# Bluetti BT Lib

A Python library for communicating with Bluetti portable power stations and battery backup systems via Bluetooth Low Energy (BLE). This library uses reverse-engineered Modbus-over-BLE protocols to read device status and control settings.

## Overview

The Bluetti BT Lib provides a complete interface for interacting with Bluetti power stations through Bluetooth. It abstracts the complexity of the underlying Modbus protocol, device-specific register mappings, and BLE communication into a clean, device-oriented API.

### Key Features

- **Bluetooth Communication**: Asynchronous BLE communication using the Bleak library
- **Device Auto-Recognition**: Automatic device type detection from Bluetooth device name (e.g., `AC180P1234567`)
- **Protocol Abstraction**: Support for multiple protocol versions (V1 and V2), with protocol selection based on device model
- **Encrypted Communication**: AES-128 encryption with ECDH key exchange for newer devices (EP600/EP760/EP800 series)
- **Comprehensive Field Mapping**: Structured access to device registers including power metrics, battery status, and control switches
- **Read & Write Operations**: Query device state and modify settings like output switches, charging modes, and UPS configurations
- **Type-Safe Parsing**: Strongly-typed field definitions with proper data parsing and validation

## Supported Devices

The library currently supports the following Bluetti device models:

### Protocol V1 Devices (Unencrypted)
- **AC200L** - Large capacity portable system
- **AC200M** - Mobile power station
- **AC200PL** - Plus variant of AC200
- **AC300** - Modular power system (supports up to 4 battery packs)
- **AC500** - High-capacity modular system
- **EB3A** - Entry-level portable power station
- **EP500** - All-in-one home backup power station
- **EP500P** - High-power home backup system

**Note**: Protocol V1 devices can be configured to use encryption by setting `encrypted=True` when instantiating the device class, though most V1 devices do not require encryption.

### Protocol V2 Devices (Unencrypted)
- **AC2A** - Compact portable power station
- **AC60** - Portable power station with expansion capabilities
- **AC60P** - Enhanced AC60 variant
- **AC70** - Mid-size portable power station
- **AC70P** - Enhanced AC70 variant
- **AC180** - Popular portable power station
- **AC180P** - Enhanced AC180 variant

### Protocol V2 Devices (Encrypted)
- **EP600** - Professional energy storage system with 3-phase support
- **EP760** - Advanced energy storage system (single-phase variant)
- **EP800** - Industrial-grade energy storage system

## Installation

This library requires Python 3.8+ and the following dependencies:

```bash
pip install bleak crcmod cryptography pyasn1
```

**Note**: The `cryptography` and `pyasn1` packages are required for encrypted device support (EP600/EP760/EP800 series).

## Usage

### Basic Example

```python
import asyncio
from bleak import BleakClient
from bluetti_bt_lib.utils.device_builder import build_device
from bluetti_bt_lib.bluetooth.device_reader import DeviceReader

async def read_device_status():
    # Connect to device
    address = "XX:XX:XX:XX:XX:XX"  # Your device's BLE address
    device_name = "AC180P1234567"  # Device name from BLE scan
    
    # Build device instance
    device = build_device(address, device_name)
    
    async with BleakClient(address) as client:
        # Create reader
        # Set encrypted=True for EP600/EP760/EP800 devices
        reader = DeviceReader(
            bleak_client=client,
            bluetti_device=device,
            future_builder_method=asyncio.get_event_loop().create_future,
            encrypted=False  # Set to True for EP600/EP760/EP800
        )
        
        # Read device data
        data = await reader.read_data()
        
        if data:
            print(f"Battery: {data.get('total_battery_percent')}%")
            print(f"AC Output Power: {data.get('ac_output_power')}W")
            print(f"DC Output Power: {data.get('dc_output_power')}W")
            print(f"AC Input Power: {data.get('ac_input_power')}W")

asyncio.run(read_device_status())
```

### Controlling Device Outputs

```python
from bluetti_bt_lib.utils.commands import WriteSingleRegister

async def toggle_ac_output(reader, device, turn_on: bool):
    # Build setter command
    command = device.build_setter_command('ac_output_on_switch', turn_on)
    
    # Write to device
    await reader.write_data(command)
```

### Device Recognition

```python
from bluetti_bt_lib.bluetooth.device_recognizer import recognize_device

async def auto_detect_device(client):
    # Automatically detect device type by reading device registers
    device_type = await recognize_device(
        bleak_client=client,
        future_builder_method=asyncio.get_event_loop().create_future
    )
    print(f"Detected device type: {device_type}")
```

## Library Architecture

### Core Components

#### 1. Base Device Classes

- **`BluettiDevice`** - Abstract base class defining the device interface
  - Manages device address, type, and serial number
  - Defines polling commands for data collection
  - Handles field parsing and setter command generation
  - Specifies writable register ranges

- **`ProtocolV1Device`** - Base for older devices (EP/AC300/AC500 series)
  - Standard register layout with device info at registers 10-49
  - Control registers starting at 3000+
  - Support for external battery pack polling
  - Optional encryption support via constructor parameter

- **`ProtocolV2Device`** - Base for newer devices (AC60/AC70/AC180/AC200/EB3A series)
  - Different register mapping with swapped byte order
  - Device info at registers 110+
  - Simplified control structure
  - Optional encryption support via constructor parameter

#### 2. Device Structure System

The `DeviceStruct` class provides a declarative way to define device field mappings:

```python
# Define fields with specific types
struct.add_uint_field("ac_output_power", 142)
struct.add_decimal_field("ac_input_voltage", 1314, 1)  # 1 decimal place
struct.add_bool_field("ac_output_on_switch", 2011)
struct.add_enum_field("charging_mode", 2020, ChargingMode)
```

**Field Types:**
- `UintField` - Unsigned integers with optional range and multiplier
- `IntField` - Signed integers
- `DecimalField` - Fixed-point decimals
- `BoolField` - Boolean values (0/1)
- `EnumField` - Enumerated values
- `StringField` - Text strings
- `VersionField` - Firmware version numbers
- `SerialNumberField` - Device serial numbers

#### 3. Bluetooth Communication

- **`DeviceReader`** - Manages BLE communication and data polling
  - Handles connection lifecycle (persistent or one-shot)
  - Implements retry logic and timeout handling
  - Processes Modbus responses into structured data
  - Supports filtered register reads for efficiency
  - Manages encryption handshake and encrypted communication for supported devices

- **`BluettiEncryption`** - Handles encrypted communication (EP600/EP760/EP800 series)
  - Implements AES-128 encryption with ECDH key exchange
  - Performs secure handshake using elliptic curve cryptography (SECP256R1)
  - Manages both unsecure (handshake) and secure (post-handshake) encryption keys
  - Validates signatures using known public keys

- **`DeviceRecognizer`** - Identifies unknown devices
  - Reads device type from registers
  - Returns model string for device instantiation

#### 4. Modbus Commands

- **`ReadHoldingRegisters`** - Read data from device registers
  - Generates proper Modbus RTU frames
  - Validates CRC checksums
  - Parses responses into raw register data

- **`WriteSingleRegister`** - Write values to control registers
  - Supports setting outputs, modes, and configuration
  - Type conversion for booleans and enums

### Data Flow

1. **Discovery**: BLE scan finds device → Extract model from name
2. **Instantiation**: `build_device()` creates device-specific instance
3. **Connection**: `BleakClient` establishes BLE connection
4. **Reading**: `DeviceReader` sends `ReadHoldingRegisters` commands
5. **Parsing**: Device struct converts register data to typed fields
6. **Control**: User builds setter commands for writable fields
7. **Writing**: Commands sent via Modbus protocol over BLE

### Register Address Spaces

Different address ranges serve different purposes:

- **10-99**: Device information and core status (V1 protocol)
- **100-199**: Battery and power metrics (V2 protocol)  
- **1000-1999**: Extended input/output details (V2 protocol)
- **2000-2999**: Control registers for settings (V2 protocol)
- **3000-3999**: Control registers for settings (V1 protocol)

## Protocol Details

### Device Identification and Protocol Selection

The library uses a two-stage approach to identify devices and select the appropriate protocol version:

#### 1. Primary Method: Bluetooth Device Name Parsing

Bluetti devices broadcast their model type and serial number in their Bluetooth Low Energy (BLE) advertisement name.

**Device Name Format**: `[MODEL][SERIAL_NUMBER]`

Examples:
- `AC180P1234567` → Model: **AC180P**, Serial: **1234567**
- `EP5002391829` → Model: **EP500**, Serial: **2391829**
- `AC60P9876543` → Model: **AC60P**, Serial: **9876543**

The `build_device()` function uses a regex pattern to extract the model prefix:

```python
DEVICE_NAME_RE = re.compile(
    r"^(AC2A|AC60|AC60P|AC70|AC70P|AC180|AC180P|AC200L|AC200M|AC200PL|AC300|AC500|EB3A|EP500|EP500P|EP600|EP760|EP800)(\d+)$"
)
```

Once the model is identified, the library instantiates the corresponding device class. **Each device class is statically mapped to a protocol version** (V1 or V2) through its inheritance hierarchy:

```python
# Protocol V1 device
class EP500(ProtocolV1Device):
    def __init__(self, address: str, sn: str):
        super().__init__(address, "EP500", sn)

# Protocol V1 device with encryption
class EP500Encrypted(ProtocolV1Device):
    def __init__(self, address: str, sn: str):
        super().__init__(address, "EP500", sn, encrypted=True)

# Protocol V2 device
class AC180P(ProtocolV2Device):
    def __init__(self, address: str, sn: str):
        super().__init__(address, "AC180P", sn)

# Protocol V2 device with encryption
class EP600(ProtocolV2Device):
    def __init__(self, address: str, sn: str):
        super().__init__(address, "EP600", sn, encrypted=True)
```

**Key Points:**
- The Bluetooth MAC address is **NOT** used for device identification
- Device type determines protocol version (not vice versa)
- Protocol selection is **compile-time**, not runtime
- Each model always uses the same protocol version

#### 2. Fallback Method: Runtime Device Recognition

For devices with non-standard Bluetooth names (e.g., devices broadcasting as "PBOX" instead of their model name), the `recognize_device()` function performs runtime identification:

```python
async def recognize_device(bleak_client: BleakClient, ...) -> str:
    # Assumes Protocol V2 and queries register 110
    bluetti_device = ProtocolV2Device("Unknown", "Unknown", "Unknown")
    device_reader = DeviceReader(bleak_client, bluetti_device, ...)
    
    # Read 6 registers starting at address 110 (V2 device_type location)
    data = await device_reader.read_data([ReadHoldingRegisters(110, 6)])
    
    return data.get("device_type")  # Returns model string like "AC180"
```

**Process:**
1. Creates a temporary `ProtocolV2Device` instance
2. Reads register 110 (where V2 protocol stores device type string)
3. Extracts and returns the device model string
4. Returns "Unknown" if the read fails or no data is found

**Limitations:**
- Only works for Protocol V2 devices
- V1 devices with non-standard names cannot be auto-detected
- Requires active Bluetooth connection to the device

Once the device type string is obtained, it can be used with `build_device()` to create the properly typed device instance.

### Bluetooth UUIDs

- **Write UUID**: `0000ff02-0000-1000-8000-00805f9b34fb`
- **Notify UUID**: `0000ff01-0000-1000-8000-00805f9b34fb`
- **Device Name UUID**: `00002a00-0000-1000-8000-00805f9b34fb`

### Encryption Support

Newer Bluetti devices (EP600, EP760, EP800 series) require encrypted communication to protect data privacy and control access.

#### Encryption Architecture

The library implements a multi-stage encryption protocol:

**1. Initial Handshake (Unsecure Keys)**
- Device sends a 4-byte challenge
- Client derives an unsecure AES-128 key by XORing the challenge with a static local key
- Challenge is also used to derive an IV (initialization vector)
- This temporary key is used only for the key exchange process

**2. Key Exchange (ECDH)**
- Device sends its SECP256R1 public key, signed with a known private key
- Client verifies the signature using the known public key (K2)
- Client generates an ephemeral ECDH keypair
- Client signs and sends its public key back to the device
- Both parties compute a shared secret using ECDH

**3. Secure Communication**
- Shared secret becomes the secure AES-128 key for all subsequent messages
- Each encrypted message includes a random 4-byte IV seed
- IV is derived as `MD5(iv_seed)`
- Message format: `[length:2][iv_seed:4][encrypted_data:variable]`

**4. Message Encryption**
- Data is padded to AES block size (16 bytes) using PKCS7
- Encrypted using AES-128 in CBC mode
- Decryption reverses the process, removing padding and validating length

#### Security Model

The encryption uses **known static keys** for signing, which means:
- ✅ Protects against casual eavesdropping
- ✅ Prevents unauthorized device control from unknown apps
- ✅ Ensures message integrity through signatures
- ⚠️ Does not protect against reverse engineering (keys are in the app)
- ⚠️ All instances of the app share the same signing keys

This is a common approach for consumer IoT devices where the goal is to prevent casual interference while allowing the manufacturer's ecosystem to work seamlessly.

#### Enabling Encryption

```python
# For EP600/EP760/EP800 devices, enable encryption:
reader = DeviceReader(
    bleak_client=client,
    bluetti_device=device,
    future_builder_method=loop.create_future,
    encrypted=True  # Required for EP600/EP760/EP800
)
```

The encryption handshake happens automatically on connection. Commands are queued until the secure key is established.

### Modbus-over-BLE

Communication uses Modbus RTU protocol encapsulated in BLE characteristic writes/notifications:

```
[Device Address][Function Code][Register Data][CRC16]
```

- Device Address: Always `0x01`
- Function Code: `0x03` (Read Holding Registers) or `0x06` (Write Single Register)
- CRC: Modbus CRC-16 for data integrity

### Response Handling

- Default timeout: 5 seconds
- Automatic retry on failure (configurable)
- Exception response detection
- CRC validation on all responses

## Common Fields

Most devices expose these common fields:

### Status & Metrics
- `total_battery_percent` - Overall battery level (%)
- `ac_output_power` - AC output power (W)
- `dc_output_power` - DC output power (W)
- `ac_input_power` - AC input/charging power (W)
- `dc_input_power` - Solar/DC input power (W)
- `power_generation` - Total energy generated (kWh)

### Device Information
- `device_type` - Model identifier
- `serial_number` - Device serial number
- `arm_version` - ARM firmware version
- `dsp_version` - DSP firmware version

### Controls (Writable)
- `ac_output_on_switch` - Enable/disable AC outlets
- `dc_output_on_switch` - Enable/disable DC outputs
- `grid_charge_on` - Allow grid charging
- `ups_mode` - UPS operating mode
- `charging_mode` - Charging behavior (normal/silent)

## Error Handling

The library defines custom exceptions:

- **`ParseError`** - Failed to parse device response
- **`ModbusError`** - Modbus protocol error
- **`BadConnectionError`** - BLE connection failure

## Device-Specific Features

### AC200/AC300/AC500/EP500 Series (V1 Protocol)
- External battery pack support (up to 4 packs for AC300)
- Split-phase operation (AC300/AC500)
- Advanced UPS modes
- Time-controlled charging
- Auto-sleep configuration

### AC60/AC70/AC180/AC2A/EB3A Series (V2 Protocol)
- Silent charging mode
- Power lifting (increased AC output)
- Grid enhancement mode
- LED brightness control
- Eco shutdown timers

### EP600/EP760/EP800 Series (V2 Protocol with Encryption)
- **Encrypted communication required**
- 3-phase power monitoring (EP600) or single-phase (EP760)
- Home energy storage and backup
- Grid-tied operation with export control
- Advanced battery management for long-term storage
- Professional-grade features for whole-home backup

## Contributing

When adding support for new device models:

1. **Use the Protocol Probe Utility** (recommended):
   ```bash
   # From repository root
   python probe-device.py XX:XX:XX:XX:XX:XX
   ```
   This will auto-detect the protocol version and encryption requirements. See [PROBE_DEVICE_GUIDE.md](../../../../PROBE_DEVICE_GUIDE.md) for details.

2. Determine protocol version (V1 or V2) and encryption requirement
3. Create device class in `devices/` inheriting from appropriate base
4. Define field mappings using `struct.add_*_field()` methods
5. Specify `polling_commands` and `writable_ranges`
6. Add device to `device_builder.py` registry
7. Set encryption flag when creating `DeviceReader` if needed
8. Test with actual hardware if possible

## Credits

This library is based on reverse engineering work from the [bluetti_mqtt](https://github.com/warhammerkid/bluetti_mqtt) project, with modifications and extensions for Home Assistant integration.

## License

See the LICENSE file in the repository root for license information.
