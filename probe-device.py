#!/usr/bin/env python3
"""
Bluetti Device Protocol Probe Utility

This utility helps test and identify the correct protocol and encryption settings
for Bluetti devices that may not yet be fully supported. It can:

- Test different protocol versions (V1, V2)
- Test with/without encryption
- Attempt to read device information and basic status
- Display raw register data for analysis

Usage:
    # Auto-detect (try all combinations)
    python probe-device.py XX:XX:XX:XX:XX:XX

    # Test specific protocol version
    python probe-device.py XX:XX:XX:XX:XX:XX --protocol v2

    # Test with encryption
    python probe-device.py XX:XX:XX:XX:XX:XX --protocol v2 --encrypted

    # Scan for nearby Bluetti devices
    python probe-device.py --scan

    # Verbose output
    python probe-device.py XX:XX:XX:XX:XX:XX --verbose

Example Output:
    ======================================================================
                      DETECTION SUCCESSFUL!
    ======================================================================
    
    ✓ Device responds to: Protocol V2 (Encrypted)
    
    ======================================================================
                   RECOMMENDED CONFIGURATION:
    ======================================================================
      Protocol: V2
      Encrypted: True
    ======================================================================
    
    Sample Data Received:
    
    Device Type (110-115):
      device_type: EP600
    
    Serial Number (116-119):
      serial_number: 2406123456
    
    Next Steps:
    1. Use this configuration in your device class
    2. Inherit from ProtocolV2Device
    3. Enable encryption when creating DeviceReader (encrypted=True)
    4. Define field mappings for the registers that returned data
"""

import argparse
import asyncio
import logging
import sys
from typing import Optional, Dict, Any, List, Tuple

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except ImportError:
    print("Error: bleak package not found. Install with: pip install bleak")
    sys.exit(1)

# Add the custom_components path to import our library
sys.path.insert(0, "custom_components/bluetti_bt")

try:
    from bluetti_bt_lib.bluetooth.device_reader import DeviceReader
    from bluetti_bt_lib.base_devices.ProtocolV1Device import ProtocolV1Device
    from bluetti_bt_lib.base_devices.ProtocolV2Device import ProtocolV2Device
    from bluetti_bt_lib.utils.commands import ReadHoldingRegisters
    from bluetti_bt_lib.const import NOTIFY_UUID, WRITE_UUID
except ImportError as e:
    print(f"Error: Could not import Bluetti library: {e}")
    print("Make sure you're running this from the repository root.")
    sys.exit(1)

# Color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text:^70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*70}{Colors.ENDC}\n")

def print_success(text: str):
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")

def print_error(text: str):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")

def print_info(text: str):
    print(f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}")

def print_warning(text: str):
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")

# Test configurations to try
PROTOCOL_TESTS = [
    ("V2", False, "Protocol V2 (Unencrypted)"),
    ("V2", True, "Protocol V2 (Encrypted)"),
    ("V1", False, "Protocol V1 (Unencrypted)"),
    ("V1", True, "Protocol V1 (Encrypted)"),
]

# Common register addresses to probe
V1_PROBE_REGISTERS = [
    (10, 10, "Device Info (10-19)"),
    (36, 4, "Power I/O (36-39)"),
    (43, 1, "Battery Percent (43)"),
    (48, 2, "Output State (48-49)"),
]

V2_PROBE_REGISTERS = [
    (102, 1, "Battery Percent (102)"),
    (110, 6, "Device Type (110-115)"),
    (116, 4, "Serial Number (116-119)"),
    (140, 8, "Power I/O (140-147)"),
]

class DeviceProbe:
    """Handles probing a single device with different protocol configurations."""
    
    def __init__(self, address: str, verbose: bool = False):
        self.address = address
        self.verbose = verbose
        self.logger = logging.getLogger(__name__)
        
    async def test_connection(self) -> bool:
        """Test basic BLE connectivity."""
        print_info(f"Testing basic BLE connection to {self.address}...")
        
        try:
            async with BleakClient(self.address, timeout=10.0) as client:
                if client.is_connected:
                    print_success("BLE connection successful")
                    
                    # Try to read device name
                    try:
                        services = await client.get_services()
                        print_info(f"Found {len(services)} GATT services")
                        
                        # Check for Bluetti UUIDs
                        has_write = any(WRITE_UUID.lower() in str(s).lower() for s in services)
                        has_notify = any(NOTIFY_UUID.lower() in str(s).lower() for s in services)
                        
                        if has_write and has_notify:
                            print_success("Found expected Bluetti GATT characteristics")
                        else:
                            print_warning("Expected Bluetti characteristics not found")
                            if self.verbose:
                                for service in services:
                                    print(f"  Service: {service.uuid}")
                                    for char in service.characteristics:
                                        print(f"    - {char.uuid} ({char.properties})")
                        
                    except Exception as e:
                        print_warning(f"Could not enumerate services: {e}")
                    
                    return True
                else:
                    print_error("Connection failed")
                    return False
                    
        except BleakError as e:
            print_error(f"BLE error: {e}")
            return False
        except Exception as e:
            print_error(f"Unexpected error: {e}")
            return False
    
    async def test_protocol(
        self, 
        protocol: str, 
        encrypted: bool
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Test a specific protocol/encryption combination."""
        
        config_name = f"Protocol {protocol.upper()}" + (" (Encrypted)" if encrypted else " (Unencrypted)")
        print_header(f"Testing: {config_name}")
        
        # Create appropriate device instance
        if protocol.upper() == "V1":
            device = ProtocolV1Device(self.address, "PROBE", "00000000", encrypted=encrypted)
            probe_registers = V1_PROBE_REGISTERS
        else:
            device = ProtocolV2Device(self.address, "PROBE", "00000000", encrypted=encrypted)
            probe_registers = V2_PROBE_REGISTERS
        
        try:
            async with BleakClient(self.address, timeout=15.0) as client:
                if not client.is_connected:
                    print_error("Failed to connect")
                    return False, None
                
                # Create device reader
                reader = DeviceReader(
                    bleak_client=client,
                    bluetti_device=device,
                    future_builder_method=asyncio.get_event_loop().create_future,
                    persistent_conn=True,
                    polling_timeout=30,
                    max_retries=3,
                    encrypted=encrypted
                )
                
                # Start notifications (required for encryption handshake)
                if not reader.has_notifier:
                    from custom_components.bluetti_bt.bluetti_bt_lib.const import NOTIFY_UUID
                    await client.start_notify(NOTIFY_UUID, reader._notification_handler)
                    reader.has_notifier = True
                
                # Wait for encryption handshake if needed
                if encrypted:
                    print_info("Waiting for encryption handshake...")
                    max_wait = 30  # Increased to 15 seconds (0.5s intervals)
                    for i in range(max_wait):
                        if reader.encryption.is_ready_for_commands:
                            print_success("Encryption handshake complete")
                            break
                        await asyncio.sleep(0.5)
                        if self.verbose and i % 2 == 0:
                            print(f"  Waiting... ({i//2 + 1}/{max_wait//2}s)")
                    
                    if not reader.encryption.is_ready_for_commands:
                        print_error("Encryption handshake timed out")
                        return False, None
                
                # Try to read data from common registers
                results = {}
                successful_reads = 0
                
                for start_addr, quantity, description in probe_registers:
                    try:
                        print_info(f"Reading {description}...")
                        
                        data = await reader.read_data(
                            filter_registers=[ReadHoldingRegisters(start_addr, quantity)]
                        )
                        
                        if data:
                            successful_reads += 1
                            results[description] = data
                            print_success(f"Successfully read {len(data)} fields")
                            
                            if self.verbose:
                                for key, value in data.items():
                                    print(f"    {key}: {value}")
                        else:
                            if self.verbose:
                                print_warning(f"No data returned for {description}")
                            
                    except Exception as e:
                        if self.verbose:
                            print_warning(f"Failed to read {description}: {e}")
                
                # Evaluate success
                if successful_reads > 0:
                    print_success(f"Successfully read {successful_reads}/{len(probe_registers)} register sets")
                    return True, results
                else:
                    print_error("No successful reads")
                    return False, None
                    
        except BleakError as e:
            print_error(f"BLE error: {e}")
            return False, None
        except Exception as e:
            print_error(f"Error during protocol test: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()
            return False, None
    
    async def auto_detect(self) -> Optional[Tuple[str, bool, Dict[str, Any]]]:
        """Try all protocol/encryption combinations to find working configuration."""
        
        print_header("Auto-Detecting Protocol Configuration")
        print_info(f"Will try {len(PROTOCOL_TESTS)} different configurations...")
        
        for protocol, encrypted, description in PROTOCOL_TESTS:
            print(f"\n{Colors.BOLD}Trying: {description}{Colors.ENDC}")
            
            success, data = await self.test_protocol(protocol, encrypted)
            
            if success and data:
                print_header("DETECTION SUCCESSFUL!")
                print_success(f"Device responds to: {description}")
                return (protocol, encrypted, data)
            
            # Small delay between attempts
            await asyncio.sleep(2)
        
        print_header("Auto-Detection Failed")
        print_error("Device did not respond to any known protocol configuration")
        return None

async def scan_devices(timeout: int = 30):
    """Scan for nearby Bluetti devices."""
    
    print_header("Scanning for Bluetti Devices")
    print_info(f"Scanning for {timeout} seconds...")
    
    devices = await BleakScanner.discover(timeout=timeout)
    
    bluetti_devices = []
    
    for device in devices:
        # Look for devices that might be Bluetti
        name = device.name or "Unknown"
        print_info(f"Found device: {name} [{device.address}] (RSSI: {device.rssi} dBm)")
        
        # Common Bluetti naming patterns
        is_bluetti = any([
            name.startswith("AC"),
            name.startswith("AP"),
            name.startswith("EP"),
            name.startswith("EB"),
            name.startswith("PBOX"),
            "bluetti" in name.lower(),
        ])
        
        if is_bluetti:
            bluetti_devices.append(device)
    
    if bluetti_devices:
        print_success(f"Found {len(bluetti_devices)} potential Bluetti device(s):\n")
        
        for i, device in enumerate(bluetti_devices, 1):
            print(f"{Colors.BOLD}{i}. {device.name or 'Unknown'}{Colors.ENDC}")
            print(f"   Address: {device.address}")
            print(f"   RSSI: {device.rssi} dBm")
            print()
    else:
        print_warning("No Bluetti devices found")
        print_info("Make sure your device is powered on and in range")
    
    return bluetti_devices

async def main():
    parser = argparse.ArgumentParser(
        description="Probe Bluetti devices to identify protocol and encryption requirements",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "address",
        nargs="?",
        help="Bluetooth MAC address of the device (XX:XX:XX:XX:XX:XX)"
    )
    
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan for nearby Bluetti devices"
    )
    
    parser.add_argument(
        "--protocol",
        choices=["v1", "v2", "V1", "V2"],
        help="Test specific protocol version (v1 or v2)"
    )
    
    parser.add_argument(
        "--encrypted",
        action="store_true",
        help="Test with encryption enabled"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Demo mode - simulate device responses (for testing the tool)"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Scan mode
    if args.scan:
        await scan_devices()
        return 0
    
    # Validate address
    if not args.address:
        parser.print_help()
        print(f"\n{Colors.FAIL}Error: Address required (or use --scan to find devices){Colors.ENDC}")
        return 1
    
    # Validate address format
    if not all(c in "0123456789ABCDEFabcdef:-" for c in args.address):
        print_error(f"Invalid MAC address format: {args.address}")
        return 1
    
    print_header(f"Bluetti Device Protocol Probe")
    print(f"Target Device: {Colors.BOLD}{args.address}{Colors.ENDC}\n")
    
    # Demo mode - simulate a successful detection
    if args.demo:
        print_warning("DEMO MODE - Simulating device responses")
        print_info("In real mode, this would connect to the actual device\n")
        
        print_header("Simulated Auto-Detection Process")
        
        # Simulate V2 unencrypted attempt
        print(f"\n{Colors.BOLD}Trying: Protocol V2 (Unencrypted){Colors.ENDC}")
        print_info("Testing basic BLE connection to CC:BA:97:01:C0:E6...")
        await asyncio.sleep(0.5)
        print_success("BLE connection successful")
        print_success("Found expected Bluetti GATT characteristics")
        
        print_info("Reading Battery Percent (102)...")
        await asyncio.sleep(0.3)
        print_success("Successfully read 1 fields")
        
        print_info("Reading Device Type (110-115)...")
        await asyncio.sleep(0.3)
        print_success("Successfully read 1 fields")
        
        print_info("Reading Serial Number (116-119)...")
        await asyncio.sleep(0.3)
        print_success("Successfully read 1 fields")
        
        print_info("Reading Power I/O (140-147)...")
        await asyncio.sleep(0.3)
        print_success("Successfully read 4 fields")
        
        print_success("Successfully read 4/4 register sets")
        
        print_header("DETECTION SUCCESSFUL!")
        print_success("Device responds to: Protocol V2 (Unencrypted)")
        
        print("\n" + "="*70)
        print(f"{Colors.OKGREEN}{Colors.BOLD}RECOMMENDED CONFIGURATION:{Colors.ENDC}")
        print(f"  Protocol: {Colors.BOLD}V2{Colors.ENDC}")
        print(f"  Encrypted: {Colors.BOLD}False{Colors.ENDC}")
        print("="*70)
        
        print(f"\n{Colors.BOLD}Sample Data Received:{Colors.ENDC}")
        
        print(f"\n{Colors.OKCYAN}Battery Percent (102):{Colors.ENDC}")
        print(f"  total_battery_percent: 78")
        
        print(f"\n{Colors.OKCYAN}Device Type (110-115):{Colors.ENDC}")
        print(f"  {Colors.BOLD}device_type: AC70{Colors.ENDC}")
        
        print(f"\n{Colors.OKCYAN}Serial Number (116-119):{Colors.ENDC}")
        print(f"  {Colors.BOLD}serial_number: 2411234567{Colors.ENDC}")
        
        print(f"\n{Colors.OKCYAN}Power I/O (140-147):{Colors.ENDC}")
        print(f"  dc_output_power: 45")
        print(f"  ac_output_power: 0")
        print(f"  dc_input_power: 120")
        print(f"  ac_input_power: 0")
        
        print(f"\n{Colors.OKGREEN}Next Steps:{Colors.ENDC}")
        print(f"1. Use this configuration in your device class")
        print(f"2. Inherit from ProtocolV2Device")
        print(f"3. Define field mappings for the registers that returned data")
        
        return 0
    
    probe = DeviceProbe(args.address, verbose=args.verbose)
    
    # Test basic connectivity first
    if not await probe.test_connection():
        print_error("Basic connectivity test failed. Cannot proceed.")
        print_info("Make sure:")
        print("  - The device is powered on")
        print("  - Bluetooth is enabled on your computer")
        print("  - The MAC address is correct")
        print("  - The device is in range")
        return 1
    
    # Specific protocol test or auto-detect
    if args.protocol:
        success, data = await probe.test_protocol(
            args.protocol.upper(),
            args.encrypted
        )
        
        if success:
            print_header("Test Successful!")
            print_success(f"Device responds to Protocol {args.protocol.upper()}" + 
                         (" with encryption" if args.encrypted else " without encryption"))
            
            if data:
                print("\nReceived Data:")
                for section, fields in data.items():
                    print(f"\n{Colors.BOLD}{section}:{Colors.ENDC}")
                    for key, value in fields.items():
                        print(f"  {key}: {value}")
            
            return 0
        else:
            print_header("Test Failed")
            print_error(f"Device did not respond to Protocol {args.protocol.upper()}" +
                       (" with encryption" if args.encrypted else " without encryption"))
            return 1
    
    else:
        # Auto-detect mode
        result = await probe.auto_detect()
        
        if result:
            protocol, encrypted, data = result
            
            print("\n" + "="*70)
            print(f"{Colors.OKGREEN}{Colors.BOLD}RECOMMENDED CONFIGURATION:{Colors.ENDC}")
            print(f"  Protocol: {Colors.BOLD}{protocol}{Colors.ENDC}")
            print(f"  Encrypted: {Colors.BOLD}{encrypted}{Colors.ENDC}")
            print("="*70)
            
            if data:
                print(f"\n{Colors.BOLD}Sample Data Received:{Colors.ENDC}")
                for section, fields in data.items():
                    print(f"\n{Colors.OKCYAN}{section}:{Colors.ENDC}")
                    for key, value in fields.items():
                        # Highlight potentially useful fields
                        if "type" in key.lower() or "serial" in key.lower():
                            print(f"  {Colors.BOLD}{key}: {value}{Colors.ENDC}")
                        else:
                            print(f"  {key}: {value}")
            
            print(f"\n{Colors.OKGREEN}Next Steps:{Colors.ENDC}")
            print(f"1. Use this configuration in your device class")
            print(f"2. Inherit from Protocol{protocol}Device")
            if encrypted:
                print(f"3. Enable encryption when creating DeviceReader (encrypted=True)")
            print(f"4. Define field mappings for the registers that returned data")
            
            return 0
        else:
            return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}Interrupted by user{Colors.ENDC}")
        sys.exit(130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
