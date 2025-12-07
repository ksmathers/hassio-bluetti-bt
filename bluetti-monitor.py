#!/usr/bin/env python3
"""
Bluetti Device Monitor

Monitors Bluetti devices and reports value changes in real-time.
Can read configuration from bluetti.yaml or use command-line arguments.
Optionally records data to Avro files.

Usage:
    # Using bluetti.yaml:
    python bluetti-monitor.py

    # Using command-line arguments:
    python bluetti-monitor.py --address UUID --name DEVICE_NAME [--encrypted] [--interval SECONDS]
    
    # Record to Avro file (narrow format):
    python bluetti-monitor.py --avro output.avro
    
    # Record to Avro file (wide format):
    python bluetti-monitor.py --avro output.avro --wide
    
    # Verbose mode:
    python bluetti-monitor.py --verbose
"""

import argparse
import asyncio
import sys
import yaml
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List

sys.path.insert(0, "custom_components/bluetti_bt")

from bleak import BleakClient
from bluetti_bt_lib.utils.device_builder import build_device
from bluetti_bt_lib.bluetooth.device_reader import DeviceReader


class BluettiMonitor:
    def __init__(
        self,
        address: str,
        name: str,
        encrypted: bool = False,
        interval: float = 1.0,
        verbose: bool = False,
        avro_file: Optional[str] = None,
        avro_wide: bool = False,
        avro_flush: bool = False,
        avro_history: int = 5
    ):
        self.address = address
        self.name = name
        self.encrypted = encrypted
        self.interval = interval
        self.verbose = verbose
        self.previous_values: Dict[str, Any] = {}
        self.device = None
        self.running = False
        
        # Avro recording
        self.avro_file = avro_file
        self.avro_wide = avro_wide
        self.avro_flush = avro_flush
        self.avro_history = avro_history
        self.avro_writer = None
        self.avro_fp = None
        
        if self.avro_file:
            self._rotate_avro_files()
            self._init_avro()
    
    def _rotate_avro_files(self):
        """Rotate existing Avro files before starting new recording."""
        if not self.avro_file:
            return
        
        avro_path = Path(self.avro_file)
        
        # If the target file doesn't exist, nothing to rotate
        if not avro_path.exists():
            return
        
        # Get the stem and suffix
        stem = avro_path.stem
        suffix = avro_path.suffix
        parent = avro_path.parent
        
        # Rotate existing numbered files from highest to lowest
        # This ensures we don't overwrite any files
        for i in range(self.avro_history - 1, 0, -1):
            old_file = parent / f"{stem}.{i}{suffix}"
            new_file = parent / f"{stem}.{i+1}{suffix}"
            
            if old_file.exists():
                if new_file.exists():
                    new_file.unlink()  # Remove the old file at position i+1
                old_file.rename(new_file)
                if self.verbose:
                    print(f"Rotated: {old_file.name} → {new_file.name}")
        
        # Rotate the current file to .1
        new_file = parent / f"{stem}.1{suffix}"
        if new_file.exists():
            new_file.unlink()
        avro_path.rename(new_file)
        
        print(f"✓ Rotated existing Avro file: {avro_path.name} → {new_file.name}")
    
    def _init_avro(self):
        """Initialize Avro file writer."""
        try:
            import fastavro
            self.fastavro = fastavro
        except ImportError:
            print("Error: fastavro is required for Avro recording.")
            print("Install with: pip install fastavro")
            sys.exit(1)
        
        if self.avro_wide:
            # Wide format: one row per timestamp with all sensors as columns
            # Schema will be dynamic based on discovered sensors
            self.wide_schema = {
                'type': 'record',
                'name': 'BluettiWideRecord',
                'fields': [
                    {'name': 'timestamp', 'type': 'long'},  # Unix timestamp in milliseconds
                    {'name': 'timestamp_iso', 'type': 'string'},
                ]
            }
            self.wide_schema_finalized = False
        else:
            # Narrow format: one row per sensor reading
            self.narrow_schema = {
                'type': 'record',
                'name': 'BluettiNarrowRecord',
                'fields': [
                    {'name': 'timestamp', 'type': 'long'},  # Unix timestamp in milliseconds
                    {'name': 'timestamp_iso', 'type': 'string'},
                    {'name': 'sensor', 'type': 'string'},
                    {'name': 'value', 'type': ['null', 'boolean', 'int', 'long', 'float', 'double', 'string']},
                ]
            }
    
    def _finalize_wide_schema(self, data: Dict[str, Any]):
        """Finalize wide schema based on first data sample."""
        if self.wide_schema_finalized:
            return
        
        for key in sorted(data.keys()):
            value = data[key]
            
            # Determine Avro type based on value
            if isinstance(value, bool):
                field_type = ['null', 'boolean']
            elif isinstance(value, int):
                if abs(value) > 2**31:
                    field_type = ['null', 'long']
                else:
                    field_type = ['null', 'int']
            elif isinstance(value, (float, Decimal)):
                field_type = ['null', 'double']
            elif isinstance(value, str):
                field_type = ['null', 'string']
            else:
                field_type = ['null', 'string']
            
            self.wide_schema['fields'].append({
                'name': key,
                'type': field_type
            })
        
        self.wide_schema_finalized = True
        
        # Open file and write initial empty data to create file with schema
        with open(self.avro_file, 'wb') as fp:
            self.fastavro.writer(fp, self.wide_schema, [])
        
        if self.verbose:
            self.print_status(f"Initialized wide Avro file: {self.avro_file}", "💾")
    
    def _open_narrow_avro(self):
        """Open narrow format Avro file."""
        self.avro_fp = open(self.avro_file, 'wb')
        # Write initial empty data to create file with schema
        self.fastavro.writer(self.avro_fp, self.narrow_schema, [])
        self.avro_fp.close()
        
        if self.verbose:
            self.print_status(f"Initialized narrow Avro file: {self.avro_file}", "💾")
    
    def _convert_value_for_avro(self, value: Any) -> Any:
        """Convert value to Avro-compatible type."""
        if isinstance(value, Decimal):
            return float(value)
        return value
    
    def _write_avro_record(self, data: Dict[str, Any]):
        """Write data to Avro file."""
        if not self.avro_file:
            return
        
        now = datetime.now()
        timestamp_ms = int(now.timestamp() * 1000)
        timestamp_iso = now.isoformat()
        
        if self.avro_wide:
            # Wide format: single record with all values
            if not self.wide_schema_finalized:
                self._finalize_wide_schema(data)
            
            record = {
                'timestamp': timestamp_ms,
                'timestamp_iso': timestamp_iso,
            }
            
            # Add all sensor values
            for key in sorted(data.keys()):
                record[key] = self._convert_value_for_avro(data[key])
            
            # Append to file
            with open(self.avro_file, 'a+b') as fp:
                self.fastavro.writer(fp, self.wide_schema, [record])
        else:
            # Narrow format: one record per sensor
            if not self.avro_fp:
                self._open_narrow_avro()
            
            records = []
            for key, value in sorted(data.items()):
                records.append({
                    'timestamp': timestamp_ms,
                    'timestamp_iso': timestamp_iso,
                    'sensor': key,
                    'value': self._convert_value_for_avro(value)
                })
            
            # Append to file
            with open(self.avro_file, 'a+b') as fp:
                self.fastavro.writer(fp, self.narrow_schema, records)
    
    def _close_avro(self):
        """Close Avro file."""
        # Files are closed after each write, nothing to do here
        pass
        
    def print_status(self, message: str, prefix: str = "ℹ"):
        """Print status message with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] {prefix} {message}")
    
    def print_change(self, key: str, old_value: Any, new_value: Any):
        """Print value change."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] 🔄 {key}: {old_value} → {new_value}")
    
    def print_initial(self, key: str, value: Any):
        """Print initial value."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] ✓ {key}: {value}")
    
    async def _run_monitoring_session(self, client: BleakClient, use_encryption: bool) -> None:
        """Run a single monitoring session with the connected client."""
        if not client.is_connected:
            self.print_status("Failed to connect to device", "✗")
            return
        
        self.print_status("Connected to device", "✓")
        
        # Create device reader
        reader = DeviceReader(
            bleak_client=client,
            bluetti_device=self.device,
            future_builder_method=asyncio.get_event_loop().create_future,
            persistent_conn=True,
            polling_timeout=30,
            max_retries=3,
            encrypted=use_encryption
        )
        
        # Wait for encryption if needed
        if use_encryption:
            self.print_status("Waiting for encryption handshake...")
            
            # Manually start notifications to trigger handshake
            if not reader.has_notifier:
                from custom_components.bluetti_bt.bluetti_bt_lib.const import NOTIFY_UUID
                await client.start_notify(NOTIFY_UUID, reader._notification_handler)
                reader.has_notifier = True
            
            for i in range(30):
                if reader.encryption.is_ready_for_commands:
                    self.print_status("Encryption ready", "✓")
                    break
                await asyncio.sleep(0.5)
            else:
                self.print_status("Encryption handshake timeout", "✗")
                return
        
        self.print_status(f"Starting monitoring (Ctrl+C to stop)...")
        print()
        
        poll_count = 0
        
        # Monitoring loop
        while self.running:
            poll_count += 1
            
            if self.verbose:
                self.print_status(f"Poll #{poll_count}")
            
            try:
                # Read all data from device
                data = await reader.read_data()
                
                if data:
                    # Write to Avro if configured
                    if self.avro_file:
                        if self.avro_wide:
                            # Wide format: write all values every time
                            self._write_avro_record(data)
                        else:
                            # Narrow format: write all values (changes tracked separately)
                            self._write_avro_record(data)
                    
                    # Check for changes
                    changes_detected = False
                    
                    for key, value in sorted(data.items()):
                        if key not in self.previous_values:
                            # First time seeing this value
                            self.print_initial(key, value)
                            self.previous_values[key] = value
                            changes_detected = True
                        elif self.previous_values[key] != value:
                            # Value changed
                            self.print_change(key, self.previous_values[key], value)
                            self.previous_values[key] = value
                            changes_detected = True
                    
                    if self.verbose and not changes_detected and poll_count > 1:
                        self.print_status("No changes detected")
                else:
                    if self.verbose:
                        self.print_status("No data received", "⚠")
            
            except Exception as e:
                self.print_status(f"Error reading data: {e}", "✗")
                if self.verbose:
                    import traceback
                    traceback.print_exc()
            
            # Wait for next poll
            await asyncio.sleep(self.interval)
    
    async def monitor(self):
        """Main monitoring loop."""
        from custom_components.bluetti_bt.bluetti_bt_lib.exceptions import ConnectionRecoveryError
        
        try:
            # Build device from name
            self.device = build_device(self.address, self.name)
            
            print("\n" + "="*70)
            print(f"Bluetti Device Monitor")
            print("="*70)
            print(f"Device Type:  {self.device.type}")
            print(f"Serial:       {self.device.sn}")
            print(f"Address:      {self.address}")
            print(f"Encrypted:    {self.encrypted or self.device.requires_encryption}")
            print(f"Poll Interval: {self.interval}s")
            print("="*70)
            print()
            
            use_encryption = self.encrypted or self.device.requires_encryption
            
            # Connection retry loop - will restart on ConnectionRecoveryError
            self.running = True
            connection_attempt = 0
            while self.running:
                connection_attempt += 1
                
                try:
                    if connection_attempt > 1:
                        # Wait before retrying connection
                        retry_delay = min(30, 5 * connection_attempt)
                        self.print_status(f"Waiting {retry_delay}s before reconnection attempt #{connection_attempt}...", "⏱")
                        await asyncio.sleep(retry_delay)
                    
                    async with BleakClient(self.address, timeout=15.0) as client:
                        await self._run_monitoring_session(client, use_encryption)
                        connection_attempt = 0  # Reset on successful session
                    
                except ConnectionRecoveryError as e:
                    # Connection needs to be restarted from scratch
                    self.print_status(f"Connection recovery needed: {e}", "🔄")
                    self.print_status("Restarting connection from scratch...", "🔄")
                    # Loop will continue and reconnect
                    continue
                
        except KeyboardInterrupt:
            self.print_status("Monitoring stopped by user", "⏹")
        except Exception as e:
            self.print_status(f"Fatal error: {e}", "✗")
            if self.verbose:
                import traceback
                traceback.print_exc()
        finally:
            # Close Avro file if open
            if self.avro_file:
                self._close_avro()
                self.print_status(f"Closed Avro file: {self.avro_file}", "💾")


def load_config_from_yaml(config_file: str = "bluetti.yaml") -> Optional[Dict[str, Any]]:
    """Load configuration from YAML file."""
    config_path = Path(config_file)
    
    if not config_path.exists():
        return None
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Assuming the YAML contains a list of devices, take the first one
        if isinstance(config, list) and len(config) > 0:
            device_config = config[0]
            return {
                'address': device_config.get('address'),
                'name': device_config.get('name'),
                'encrypted': device_config.get('encryption', False),
                'interval': device_config.get('interval', 1.0)
            }
        
        return None
    except Exception as e:
        print(f"Error loading config file: {e}")
        return None


async def main():
    parser = argparse.ArgumentParser(
        description="Monitor Bluetti device and report value changes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Monitor using bluetti.yaml configuration:
  %(prog)s

  # Monitor specific device:
  %(prog)s --address 406F4FCA-89A7-2C17-D235-B57D13257738 --name AP3002526010528096 --encrypted

  # Monitor with custom interval:
  %(prog)s --interval 5

  # Record to Avro file (narrow format - one row per sensor reading):
  %(prog)s --avro output.avro

  # Record to Avro file (wide format - all sensors in one row):
  %(prog)s --avro output.avro --wide

  # Record with flush after each write:
  %(prog)s --avro output.avro --flush

  # Keep 10 rotated Avro files instead of default 5:
  %(prog)s --avro output.avro --avro-history 10

  # Verbose mode:
  %(prog)s --verbose
        """
    )
    
    parser.add_argument(
        '--address',
        help='Bluetooth address or UUID of the device'
    )
    parser.add_argument(
        '--name',
        help='Device name (e.g., AP3002526010528096)'
    )
    parser.add_argument(
        '--encrypted',
        action='store_true',
        help='Use encrypted communication'
    )
    parser.add_argument(
        '--interval',
        type=float,
        help='Polling interval in seconds (default: 1.0 or from config)'
    )
    parser.add_argument(
        '--config',
        default='bluetti.yaml',
        help='Path to YAML config file (default: bluetti.yaml)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    parser.add_argument(
        '--avro',
        metavar='FILE',
        help='Record data to Avro file'
    )
    parser.add_argument(
        '--wide',
        action='store_true',
        help='Use wide Avro format (all sensors in one row, default: narrow format)'
    )
    parser.add_argument(
        '--flush',
        action='store_true',
        help='Flush Avro file after each write'
    )
    parser.add_argument(
        '--avro-history',
        type=int,
        default=5,
        metavar='N',
        help='Number of rotated Avro files to keep (default: 5)'
    )
    
    args = parser.parse_args()
    
    # Determine configuration source
    config = None
    
    if args.address and args.name:
        # Use command-line arguments
        config = {
            'address': args.address,
            'name': args.name,
            'encrypted': args.encrypted,
            'interval': args.interval if args.interval is not None else 1.0
        }
    else:
        # Try to load from YAML
        config = load_config_from_yaml(args.config)
        
        if not config:
            print(f"Error: No configuration found.")
            print(f"Either provide --address and --name, or ensure {args.config} exists.")
            sys.exit(1)
        
        # Override with command-line args if provided
        if args.interval is not None:
            config['interval'] = args.interval
        if args.encrypted:
            config['encrypted'] = True
    
    # Create and run monitor
    monitor = BluettiMonitor(
        address=config['address'],
        name=config['name'],
        encrypted=config['encrypted'],
        interval=config['interval'],
        verbose=args.verbose,
        avro_file=args.avro,
        avro_wide=args.wide,
        avro_flush=args.flush,
        avro_history=args.avro_history
    )
    
    await monitor.monitor()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped")
        sys.exit(0)
