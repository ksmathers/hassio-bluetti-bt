#!/usr/bin/env python3
"""
Avro to CSV Converter

Converts Avro files (from bluetti-monitor.py) to CSV format.
Supports both narrow format (one row per sensor reading) and wide format (all sensors in one row).

Usage:
    # Convert Avro to CSV:
    python avro2csv.py input.avro output.csv
    
    # Use stdout (for piping):
    python avro2csv.py input.avro -
    
    # Auto-generate output filename:
    python avro2csv.py input.avro
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Dict, Any, Set, Optional
from collections import OrderedDict


def read_avro_file(avro_path: str) -> List[Dict[str, Any]]:
    """Read all records from an Avro file."""
    try:
        import fastavro
    except ImportError:
        print("Error: fastavro is required.", file=sys.stderr)
        print("Install with: pip install fastavro", file=sys.stderr)
        sys.exit(1)
    
    try:
        with open(avro_path, 'rb') as fp:
            records = list(fastavro.reader(fp))
        return records
    except FileNotFoundError:
        print(f"Error: File not found: {avro_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading Avro file: {e}", file=sys.stderr)
        sys.exit(1)


def discover_sensors(records: List[Dict[str, Any]], sample_size: int = 1000) -> Set[str]:
    """Discover all unique sensor names from the first N records."""
    sensors = set()
    for record in records[:sample_size]:
        if 'sensor' in record:
            sensors.add(record['sensor'])
    return sensors


def pivot_to_wide_format(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert narrow format (one row per sensor) to wide format (one row per timestamp).
    Forward-fills missing values with the previous value for that sensor.
    """
    if not records:
        return []
    
    # Check if data is already in wide format (has sensor columns, not a 'sensor' field)
    if 'sensor' not in records[0]:
        print("Note: Data appears to be in wide format already", file=sys.stderr)
        return records
    
    # Discover all unique sensors
    print("Discovering sensors...", file=sys.stderr)
    sensors = discover_sensors(records)
    sensor_list = sorted(list(sensors))
    print(f"Found {len(sensor_list)} unique sensors: {', '.join(sensor_list)}", file=sys.stderr)
    
    # Group records by timestamp
    timestamp_groups: Dict[Any, Dict[str, Any]] = OrderedDict()
    
    for record in records:
        timestamp = record.get('timestamp')
        timestamp_iso = record.get('timestamp_iso')
        sensor = record.get('sensor')
        value = record.get('value')
        
        if timestamp not in timestamp_groups:
            timestamp_groups[timestamp] = {
                'timestamp': timestamp,
                'timestamp_iso': timestamp_iso,
            }
        
        if sensor:
            timestamp_groups[timestamp][sensor] = value
    
    # Forward-fill missing values
    print("Applying forward-fill for missing values...", file=sys.stderr)
    last_values: Dict[str, Any] = {}
    wide_records = []
    
    for timestamp, record in timestamp_groups.items():
        # Create new record with all sensors
        wide_record = {
            'timestamp': record['timestamp'],
            'timestamp_iso': record['timestamp_iso'],
        }
        
        # Fill in sensor values, using last known value if missing
        for sensor in sensor_list:
            if sensor in record:
                # New value available
                value = record[sensor]
                last_values[sensor] = value
                wide_record[sensor] = value
            elif sensor in last_values:
                # Use last known value (forward-fill)
                wide_record[sensor] = last_values[sensor]
            else:
                # No value ever seen for this sensor
                wide_record[sensor] = None
        
        wide_records.append(wide_record)
    
    print(f"Pivoted to {len(wide_records)} wide-format records", file=sys.stderr)
    return wide_records


def write_csv(records: List[Dict[str, Any]], output_path: str):
    """Write records to CSV file."""
    if not records:
        print("Warning: No records to write", file=sys.stderr)
        return
    
    # Get all field names from first record
    fieldnames = list(records[0].keys())
    
    # Determine output
    if output_path == '-':
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    else:
        try:
            with open(output_path, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(records)
            print(f"✓ Wrote {len(records)} records to {output_path}")
        except Exception as e:
            print(f"Error writing CSV file: {e}", file=sys.stderr)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Convert Avro files to CSV format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert to CSV with auto-generated filename:
  %(prog)s ap300.avro
  
  # Convert with specific output filename:
  %(prog)s ap300.avro output.csv
  
  # Output to stdout (for piping):
  %(prog)s ap300.avro - | head -20
  
  # Pivot narrow format to wide format:
  %(prog)s ap300.avro --pivot
  
  # Pipe to other tools:
  %(prog)s ap300.avro - | column -t -s,
        """
    )
    
    parser.add_argument(
        'input',
        help='Input Avro file path'
    )
    parser.add_argument(
        'output',
        nargs='?',
        help='Output CSV file path (default: input.csv, use "-" for stdout)'
    )
    parser.add_argument(
        '--pivot',
        action='store_true',
        help='Convert narrow format to wide format (one row per timestamp with all sensors as columns). Missing values are forward-filled.'
    )
    
    args = parser.parse_args()
    
    # Determine output path
    if args.output:
        output_path = args.output
    else:
        # Auto-generate output filename
        input_path = Path(args.input)
        output_path = str(input_path.with_suffix('.csv'))
    
    # Read Avro file
    print(f"Reading {args.input}...", file=sys.stderr)
    records = read_avro_file(args.input)
    print(f"Found {len(records)} records", file=sys.stderr)
    
    # Pivot if requested
    if args.pivot:
        records = pivot_to_wide_format(records)
    
    # Write CSV file
    write_csv(records, output_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nConversion cancelled", file=sys.stderr)
        sys.exit(0)
