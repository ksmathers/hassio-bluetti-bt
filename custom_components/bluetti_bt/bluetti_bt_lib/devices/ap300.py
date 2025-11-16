"""AP300 fields."""

from typing import List

from ..utils.commands import ReadHoldingRegisters
from ..base_devices.ProtocolV2Device import ProtocolV2Device


class AP300(ProtocolV2Device):
    def __init__(self, address: str, sn: str):
        super().__init__(address, "AP300", sn, encrypted=True)

        # Battery Data
        # R100: Total battery charge in Wh ✓ CONFIRMED (5334-5337 Wh observed at 98% SOC)
        #       Calculated: 98% × 5529.6 Wh (dual battery) = 5418 Wh expected
        #       Difference: ~83 Wh (1.5%) within normal variance
        self.struct.add_uint_field('total_battery_charge', 100)

        # Power I/O (registers 140-147) - CONFIRMED via live testing
        # R140: DC Output = 0W (no DC loads connected)
        # R142: AC Output varies with load (300-450W tested) ✓ CONFIRMED
        # R144: DC Input = 0W (no solar/DC input)
        # R146: AC Input varies (300-450W), drops to 0W when unplugged ✓ CONFIRMED
        self.struct.add_uint_field('dc_output_power', 140)
        self.struct.add_uint_field('ac_output_power', 142)
        self.struct.add_uint_field('dc_input_power', 144)
        self.struct.add_uint_field('ac_input_power', 146)
        
        # Additional candidate fields (to be verified):
        # Based on scan data analysis - these are best guesses
        # Register 104-105: 0x4248 ('BH' - possibly part of battery health string?)
        # Register 123: Toggles 1674↔9864 (possibly battery pack selector or state)
        # Register 152: 5695 (unknown - time/runtime related?)
        # Register 156: 6839-6840 (slow incrementing counter)

    @property
    def polling_commands(self) -> List[ReadHoldingRegisters]:
        # Include base V2 commands (102, 110-115, 116-119, 154) plus battery charge and power I/O
        return super().polling_commands + [
            ReadHoldingRegisters(100, 1),  # total_battery_charge
            ReadHoldingRegisters(140, 1),  # dc_output_power
            ReadHoldingRegisters(142, 1),  # ac_output_power
            ReadHoldingRegisters(144, 1),  # dc_input_power
            ReadHoldingRegisters(146, 1),  # ac_input_power
        ]

    @property
    def writable_ranges(self) -> List[range]:
        # Control ranges to be discovered (likely 2000+)
        return super().writable_ranges
