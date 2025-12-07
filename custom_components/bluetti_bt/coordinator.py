"""Coordinator for Bluetti integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from bleak import BleakClient

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)

from .bluetti_bt_lib.bluetooth.device_reader import DeviceReader
from .bluetti_bt_lib.utils.device_builder import build_device

from .utils import mac_loggable

_LOGGER = logging.getLogger(__name__)


class PollingCoordinator(DataUpdateCoordinator):
    """Polling coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        device_name: str,
        polling_interval: int,
        persistent_conn: bool,
        polling_timeout: int,
        max_retries: int,
        encrypted: bool,
    ):
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Bluetti polling coordinator",
            update_interval=timedelta(seconds=polling_interval),
        )

        self.address = address
        self.device_name = device_name
        self.persistent_conn = persistent_conn
        self.polling_timeout = polling_timeout
        self.max_retries = max_retries
        self.encrypted = encrypted
        
        self.bluetti_device = build_device(address, device_name)
        
        # Use device's requires_encryption property, but allow override from config
        self.use_encryption = encrypted if encrypted is not None else self.bluetti_device.requires_encryption
        
        # Create initial client and reader
        self.reader = None
        self._create_reader()

    def _create_reader(self):
        """Create or recreate the BleakClient and DeviceReader."""
        self.logger.debug("Creating BleakClient and DeviceReader")
        device = bluetooth.async_ble_device_from_address(self.hass, self.address)
        if device is None:
            self.logger.error("Device %s not available", mac_loggable(self.address))
            return
        
        client = BleakClient(device)
        
        self.reader = DeviceReader(
            client,
            self.bluetti_device,
            self.hass.loop.create_future,
            persistent_conn=self.persistent_conn,
            polling_timeout=self.polling_timeout,
            max_retries=self.max_retries,
            encrypted=self.use_encryption,
        )

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        from .bluetti_bt_lib.exceptions import ConnectionRecoveryError

        # Check if device is connected
        if bluetooth.async_address_present(self.hass, self.address, connectable=True) is False:
            self.logger.warning("Device not connected")
            self.last_update_success = False
            return None

        # Ensure reader is initialized
        if self.reader is None:
            self.logger.warning("Reader not initialized, creating...")
            self._create_reader()
            if self.reader is None:
                return None

        try:
            return await self.reader.read_data()
        except ConnectionRecoveryError as e:
            # Connection needs to be reinitialized
            self.logger.warning("Connection recovery needed: %s - reinitializing client", e)
            self._create_reader()
            # Return None for this update cycle - will retry on next poll
            return None
