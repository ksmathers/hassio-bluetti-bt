# hassio-bluetti-bt
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate with hassfest](https://github.com/Patrick762/hassio-bluetti-bt/actions/workflows/hassfest_validation.yml/badge.svg)](https://github.com/Patrick762/hassio-bluetti-bt/actions/workflows/hassfest_validation.yml)
[![HACS Action](https://github.com/Patrick762/hassio-bluetti-bt/actions/workflows/HACS.yml/badge.svg)](https://github.com/Patrick762/hassio-bluetti-bt/actions/workflows/HACS.yml)

Bluetti Integration for Home Assistant

## Disclaimer
This integration is provided without any warranty or support by Bluetti (unfortunately). I do not take responsibility for any problems it may cause in all cases. Use it at your own risk.

## Installation
To install this integration, you first need [HACS](https://hacs.xyz/) installed.
After the installation, you can use this button to install the integration:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Patrick762&repository=hassio-bluetti-bt&category=integration)

### Supported devices:

- AC2A
- AC60 (tested with one external battery B80)
- AC60P (untested)
- AC70 (basic data)
- AC70P (untested)
- AC180 (basic data)
- AC180P (tested)
- AC200L (untested)
- AC200M
- AC200PL (untested)
- AC300 (tested)
- AC500 (tested)
- EB3A
- EP500
- EP500P
- EP600 (tested)
- EP760 
- EP800 (basic data)

### Available controls:
If enabled in the Integration options (you need to reload the integration if you change this option):
AC and DC outputs

## Development Tools

### Device Protocol Probe Utility

For developers adding support for new device models, a protocol probe utility is included:

```bash
# Scan for nearby devices
python probe-device.py --scan

# Auto-detect protocol for a device
python probe-device.py XX:XX:XX:XX:XX:XX

# Test specific configuration
python probe-device.py XX:XX:XX:XX:XX:XX --protocol v2 --encrypted
```

See [PROBE_DEVICE_GUIDE.md](PROBE_DEVICE_GUIDE.md) for detailed usage instructions.

This utility helps identify:
- Which protocol version (V1 or V2) a device uses
- Whether encryption is required
- Which register addresses contain valid data
- Sample data from the device
