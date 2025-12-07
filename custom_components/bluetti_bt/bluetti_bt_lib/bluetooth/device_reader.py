"""Device reader."""

import asyncio
import logging
from typing import Any, Callable, List, cast
import async_timeout
from bleak import BleakClient, BleakError

from custom_components.bluetti_bt.bluetti_bt_lib.bluetooth.encryption import BluettiEncryption, Message, MessageType

from ..base_devices.BluettiDevice import BluettiDevice
from ..const import NOTIFY_UUID, RESPONSE_TIMEOUT, WRITE_UUID
from ..exceptions import BadConnectionError, ConnectionRecoveryError, ModbusError, ParseError
from ..utils.commands import ReadHoldingRegisters

_LOGGER = logging.getLogger(__name__)


class DeviceReader:
    def __init__(
        self,
        bleak_client: BleakClient,
        bluetti_device: BluettiDevice,
        future_builder_method: Callable[[], asyncio.Future[Any]],
        persistent_conn: bool = False,
        polling_timeout: int = 45,
        max_retries: int = 5,
        encrypted: bool = False,
    ) -> None:
        self.client = bleak_client
        self.bluetti_device = bluetti_device
        self.create_future = future_builder_method
        self.persistent_conn = persistent_conn
        self.polling_timeout = polling_timeout
        self.max_retries = max_retries
        self.encrypted = encrypted

        self.has_notifier = False
        self.notify_future: asyncio.Future[Any] | None = None
        self.current_command = None
        self.notify_response = bytearray()

        # polling mutex to guard against switches
        self.polling_lock = asyncio.Lock()

        self.encryption = BluettiEncryption()

    async def read_data(
        self, filter_registers: List[ReadHoldingRegisters] | None = None
    ) -> dict | None:
        _LOGGER.info("Reading data")

        if self.bluetti_device is None:
            _LOGGER.error("Device is None")
            return None

        polling_commands = self.bluetti_device.polling_commands
        pack_commands = self.bluetti_device.pack_polling_commands
        if filter_registers is not None:
            polling_commands = filter_registers
            pack_commands = []
        _LOGGER.info("Polling commands: " + ",".join([f"{c.starting_address}-{c.starting_address + c.quantity - 1}" for c in polling_commands]))
        _LOGGER.info("Pack comands: " + ",".join([f"{c.starting_address}-{c.starting_address + c.quantity - 1}" for c in pack_commands]))

        parsed_data: dict = {}

        async with self.polling_lock:
            try:
                async with async_timeout.timeout(self.polling_timeout):
                    # Reconnect if not connected (with timeout protection)
                    for attempt in range(1, self.max_retries + 1):
                        try:
                            if not self.client.is_connected:
                                _LOGGER.info("Client not connected, attempting to connect (attempt %d/%d)", attempt, self.max_retries)
                                # Reset notification state since we're reconnecting
                                self.has_notifier = False
                                if self.encrypted:
                                    self.encryption.reset()
                                await asyncio.wait_for(self.client.connect(), timeout=10.0)
                                _LOGGER.info("Connected successfully")
                            break
                        except asyncio.TimeoutError:
                            _LOGGER.warning(f"Connect timed out (attempt {attempt}). Retrying...")
                            if attempt < self.max_retries:
                                # Try to force disconnect before retry
                                try:
                                    await asyncio.wait_for(self.client.disconnect(), timeout=2.0)
                                except Exception:
                                    pass
                                await asyncio.sleep(2)
                            else:
                                raise Exception("Failed to connect after all retry attempts")
                        except Exception as e:
                            if attempt == self.max_retries:
                                raise e # pass exception on max_retries attempt
                            else:
                                _LOGGER.warning(
                                    f"Connect unsucessful (attempt {attempt}): {e}. Retrying..."
                                )
                                # Try to force disconnect before retry
                                try:
                                    await asyncio.wait_for(self.client.disconnect(), timeout=2.0)
                                except Exception:
                                    pass
                                await asyncio.sleep(2)

                    # Attach notifier if needed
                    if not self.has_notifier:
                        try:
                            await self.client.start_notify(
                                NOTIFY_UUID, self._notification_handler
                            )
                            self.has_notifier = True
                        except ValueError as e:
                            if "already started" in str(e):
                                # Notifications already registered - sync our state
                                _LOGGER.warning("Notifications already started, syncing state")
                                self.has_notifier = True
                            else:
                                raise

                    # Wait for encryption handshake if needed
                    if self.encrypted and not self.encryption.is_ready_for_commands:
                        # Wait up to 15 seconds for handshake with shorter intervals
                        for _ in range(30):
                            if self.encryption.is_ready_for_commands:
                                break
                            await asyncio.sleep(0.5)
                            _LOGGER.debug("Waiting for encryption handshake...")
                        
                        if not self.encryption.is_ready_for_commands:
                            _LOGGER.error("Encryption handshake timed out")
                            return None

                    # Execute polling commands
                    consecutive_failures = 0
                    for command in polling_commands:
                        try:
                            response = await self._async_send_command(command)
                            
                            # Check if command failed completely (empty response after retries)
                            if not response:
                                consecutive_failures += 1
                                _LOGGER.warning("Command %s returned empty response (failure %d)", command, consecutive_failures)
                                
                                # If we get multiple consecutive failures, force reconnect
                                if consecutive_failures >= 3:
                                    _LOGGER.error("Multiple consecutive command failures, forcing reconnect")
                                    # Force disconnect and raise to trigger reconnect
                                    try:
                                        if self.has_notifier:
                                            await asyncio.wait_for(self.client.stop_notify(NOTIFY_UUID), timeout=2.0)
                                            self.has_notifier = False
                                    except Exception:
                                        pass
                                    try:
                                        await asyncio.wait_for(self.client.disconnect(), timeout=3.0)
                                    except Exception:
                                        pass
                                    if self.encrypted:
                                        self.encryption.reset()
                                    raise BleakError("Too many consecutive command failures")
                                continue
                            
                            consecutive_failures = 0  # Reset on success
                            body = command.parse_response(response)
                            _LOGGER.debug("Raw data: %s", body)
                            parsed = self.bluetti_device.parse(
                                command.starting_address, body
                            )
                            _LOGGER.debug("Parsed data: %s", parsed)
                            parsed_data.update(parsed)
                        except ParseError:
                            _LOGGER.warning("Got a parse exception")

                    # Execute pack polling commands
                    if len(pack_commands) > 0 and len(self.bluetti_device.pack_num_field) == 1:
                        _LOGGER.debug("Polling battery packs")
                        for pack in range(1, self.bluetti_device.pack_num_max + 1):
                            _LOGGER.debug("Setting pack_num to %i", pack)

                            # Set current pack number
                            command = self.bluetti_device.build_setter_command(
                                "pack_num", pack
                            )
                            body = command.parse_response(
                                await self._async_send_command(command)
                            )
                            _LOGGER.debug("Raw data set: %s", body)

                            # Check set pack_num
                            set_pack = int.from_bytes(body, byteorder='big')
                            if set_pack is not pack:
                                _LOGGER.warning("Pack polling failed (pack_num %i doesn't match expected %i)", set_pack, pack)
                                continue

                            if self.bluetti_device.pack_num_max > 1:
                                # We need to wait after switching packs 
                                # for the data to be available
                                await asyncio.sleep(5)
                            
                            for command in pack_commands:
                                # Request & parse result for each pack
                                try:
                                    body = command.parse_response(
                                        await self._async_send_command(command)
                                    )
                                    parsed = self.bluetti_device.parse(
                                        command.starting_address, body
                                    )
                                    _LOGGER.debug("Parsed data: %s", parsed)

                                    for key, value in parsed.items():
                                        # Ignore likely unavailable pack data
                                        if value != 0:
                                            parsed_data.update({key + str(pack): value})

                                except ParseError:
                                    _LOGGER.warning("Got a parse exception...")

            except TimeoutError as err:
                _LOGGER.error(f"Polling timed out ({self.polling_timeout}s). Trying again later", exc_info=err)
                # Force a reconnect on timeout to recover from stuck state
                try:
                    if self.has_notifier:
                        await self.client.stop_notify(NOTIFY_UUID)
                        self.has_notifier = False
                except Exception:
                    pass
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                # Reset encryption state if we were using encryption
                if self.encrypted:
                    self.encryption.reset()
                    _LOGGER.info("Reset encryption state after timeout")
                return None
            except BleakError as err:
                _LOGGER.error("Bleak error: %s", err)
                # Force a reconnect on BleakError too
                try:
                    if self.has_notifier:
                        await self.client.stop_notify(NOTIFY_UUID)
                        self.has_notifier = False
                except Exception:
                    pass
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                if self.encrypted:
                    self.encryption.reset()
                return None
            finally:
                # Disconnect if connection not persistant
                if not self.persistent_conn:
                    if self.has_notifier:
                        try:
                            await self.client.stop_notify(NOTIFY_UUID)
                        except:
                            # Ignore errors here
                            pass
                        self.has_notifier = False
                    await self.client.disconnect()

            # Check if dict is empty
            if not parsed_data:
                return None

            # Reset Encryption keys only if not using persistent connection
            # For persistent connections, keep the encryption session alive
            if not self.persistent_conn:
                self.encryption.reset()

            return parsed_data

    async def _async_send_command(self, command: ReadHoldingRegisters) -> bytes:
        """Send command and return response"""
        import time
        
        # Track total time spent in retries - limit to ~30 seconds
        max_retry_time = 30.0
        retry_start_time = time.time()
        
        # We'll attempt the command with retries and reconnects on transient errors
        for attempt in range(1, self.max_retries + 1):
            try:
                # Prepare to make request
                self.current_command = command
                self.notify_future = self.create_future()
                self.notify_response = bytearray()

                # Make request
                _LOGGER.debug("Requesting %s (attempt %d/%d)", command, attempt, self.max_retries)

                command_bytes = bytes(command)

                # Encrypt command
                if self.encrypted is True:
                    if not self.encryption.is_ready_for_commands:
                        return bytes()
                    command_bytes = self.encryption.aes_encrypt(command_bytes, self.encryption.secure_aes_key, None)

                await self.client.write_gatt_char(WRITE_UUID, command_bytes)

                # Wait for response
                res = await asyncio.wait_for(self.notify_future, timeout=RESPONSE_TIMEOUT)

                # Process data
                _LOGGER.debug("Got %s bytes", len(res))
                return cast(bytes, res)

            except asyncio.CancelledError as err:
                # Bleak/CoreBluetooth sometimes cancels pending writes; try to reconnect and retry
                _LOGGER.warning("Write was cancelled (attempt %d/%d): %s", attempt, self.max_retries, err)
                # Clean up the future to avoid leaking
                try:
                    if self.notify_future and not self.notify_future.done():
                        self.notify_future.cancel()
                except Exception:
                    pass

                # Force a hard disconnect and reconnect with timeout protection
                try:
                    if self.has_notifier:
                        try:
                            await asyncio.wait_for(self.client.stop_notify(NOTIFY_UUID), timeout=2.0)
                        except Exception:
                            pass
                        self.has_notifier = False
                except Exception:
                    pass

                try:
                    await asyncio.wait_for(self.client.disconnect(), timeout=3.0)
                except Exception:
                    pass

                # Reset encryption state if using encryption
                if self.encrypted:
                    self.encryption.reset()
                    _LOGGER.debug("Reset encryption state after CancelledError")

                # Check if we've exceeded retry time limit before wasting time on backoff
                elapsed_time = time.time() - retry_start_time
                if elapsed_time > max_retry_time:
                    _LOGGER.error("Retry time limit exceeded (%.1fs) - forcing connection restart", elapsed_time)
                    raise ConnectionRecoveryError(f"Retry operations exceeded {max_retry_time}s time limit")
                
                # Exponential backoff before reconnecting (longer delays for later attempts)
                backoff_delay = min(1 * (2 ** (attempt - 1)), 10)  # 1s, 2s, 4s, 8s, 10s
                _LOGGER.debug("Waiting %ds before reconnection attempt...", backoff_delay)
                await asyncio.sleep(backoff_delay)

                # Try to reconnect with timeout protection
                reconnect_success = False
                try:
                    _LOGGER.info("Attempting to reconnect (attempt %d/%d)...", attempt, self.max_retries)
                    await asyncio.wait_for(self.client.connect(), timeout=10.0)
                    _LOGGER.info("Reconnected successfully")
                    
                    # Wait for encryption handshake if needed
                    if self.encrypted:
                        _LOGGER.debug("Waiting for encryption handshake after reconnect...")
                        handshake_timeout = 20  # Give more time for handshake
                        for i in range(handshake_timeout * 2):  # Check every 0.5s
                            if self.encryption.is_ready_for_commands:
                                _LOGGER.info("Encryption handshake complete")
                                break
                            await asyncio.sleep(0.5)
                            if i % 10 == 9:  # Log every 5 seconds
                                _LOGGER.debug("Still waiting for encryption handshake... (%ds)", (i + 1) // 2)
                        
                        if not self.encryption.is_ready_for_commands:
                            _LOGGER.error("Encryption handshake timed out after %ds", handshake_timeout)
                            raise Exception("Encryption handshake timeout")
                    
                    # Restart notifier
                    try:
                        await asyncio.wait_for(
                            self.client.start_notify(NOTIFY_UUID, self._notification_handler),
                            timeout=5.0
                        )
                        self.has_notifier = True
                        reconnect_success = True
                        _LOGGER.info("Successfully restarted notifications")
                    except ValueError as e3:
                        if "already started" in str(e3):
                            _LOGGER.info("Notifications already started after reconnect, syncing state")
                            self.has_notifier = True
                            reconnect_success = True
                        else:
                            _LOGGER.error("Failed to restart notifications: %s", e3)
                            raise
                    except Exception as e3:
                        _LOGGER.error("Failed to restart notifications: %s", e3)
                        raise
                except Exception as e2:
                    _LOGGER.error("Reconnect attempt %d failed: %s", attempt, e2)
                    reconnect_success = False

                # if last attempt and reconnect failed, give up
                if attempt == self.max_retries:
                    if not reconnect_success:
                        _LOGGER.error("Failed to recover from CancelledError after %d attempts - forcing connection restart", self.max_retries)
                        raise ConnectionRecoveryError(f"Failed to recover connection after {self.max_retries} attempts")
                    else:
                        _LOGGER.error("Max retries reached for command %s after CancelledError (reconnect succeeded but command still failing)", command)
                    break
                
                # If reconnect failed, don't bother retrying the command - force restart
                if not reconnect_success:
                    _LOGGER.error("Reconnection failed - forcing connection restart")
                    raise ConnectionRecoveryError("Failed to reconnect to device")
                
                # Check if we've exceeded retry time limit even after successful reconnect
                elapsed_time = time.time() - retry_start_time
                if elapsed_time > max_retry_time:
                    _LOGGER.error("Retry time limit exceeded (%.1fs) after reconnect - forcing connection restart", elapsed_time)
                    raise ConnectionRecoveryError(f"Retry operations exceeded {max_retry_time}s time limit")
                
                # otherwise retry with the restored connection
                _LOGGER.info("Retrying command after successful reconnection...")
                continue

            except TimeoutError:
                _LOGGER.debug("Polling single command timed out")
                # On timeout, try to cancel the notify future and retry if attempts remain
                try:
                    if self.notify_future and not self.notify_future.done():
                        self.notify_future.cancel()
                except Exception:
                    pass

                if attempt == self.max_retries:
                    break
                await asyncio.sleep(0.5)
                continue

            except ModbusError as err:
                _LOGGER.debug(
                    "Got an invalid request error for %s: %s",
                    command,
                    err,
                )
                break

            except (BadConnectionError, BleakError) as err:
                _LOGGER.warning("Bleak/connection error on write (attempt %d/%d): %s", attempt, self.max_retries, err)
                # try to reconnect and retry
                try:
                    if self.notify_future and not self.notify_future.done():
                        self.notify_future.cancel()
                except Exception:
                    pass

                # Force hard disconnect with timeout protection
                try:
                    if self.has_notifier:
                        try:
                            await asyncio.wait_for(self.client.stop_notify(NOTIFY_UUID), timeout=2.0)
                        except Exception:
                            pass
                        self.has_notifier = False
                except Exception:
                    pass

                try:
                    await asyncio.wait_for(self.client.disconnect(), timeout=3.0)
                except Exception:
                    pass

                # Reset encryption state if using encryption
                if self.encrypted:
                    self.encryption.reset()
                    _LOGGER.debug("Reset encryption state after BleakError")

                # Check if we've exceeded retry time limit before wasting time on backoff
                elapsed_time = time.time() - retry_start_time
                if elapsed_time > max_retry_time:
                    _LOGGER.error("Retry time limit exceeded (%.1fs) - forcing connection restart", elapsed_time)
                    raise ConnectionRecoveryError(f"Retry operations exceeded {max_retry_time}s time limit")
                
                # Exponential backoff before reconnecting (longer delays for later attempts)
                backoff_delay = min(1 * (2 ** (attempt - 1)), 10)  # 1s, 2s, 4s, 8s, 10s
                _LOGGER.debug("Waiting %ds before reconnection attempt...", backoff_delay)
                await asyncio.sleep(backoff_delay)
                
                # Reconnect with timeout protection
                reconnect_success = False
                try:
                    _LOGGER.info("Attempting to reconnect after BleakError (attempt %d/%d)...", attempt, self.max_retries)
                    await asyncio.wait_for(self.client.connect(), timeout=10.0)
                    _LOGGER.info("Reconnected successfully")
                    
                    # Wait for encryption handshake if needed
                    if self.encrypted:
                        _LOGGER.debug("Waiting for encryption handshake after reconnect...")
                        handshake_timeout = 20  # Give more time for handshake
                        for i in range(handshake_timeout * 2):  # Check every 0.5s
                            if self.encryption.is_ready_for_commands:
                                _LOGGER.info("Encryption handshake complete")
                                break
                            await asyncio.sleep(0.5)
                            if i % 10 == 9:  # Log every 5 seconds
                                _LOGGER.debug("Still waiting for encryption handshake... (%ds)", (i + 1) // 2)
                        
                        if not self.encryption.is_ready_for_commands:
                            _LOGGER.error("Encryption handshake timed out after %ds", handshake_timeout)
                            raise Exception("Encryption handshake timeout")
                    
                    try:
                        await asyncio.wait_for(
                            self.client.start_notify(NOTIFY_UUID, self._notification_handler),
                            timeout=5.0
                        )
                        self.has_notifier = True
                        reconnect_success = True
                        _LOGGER.info("Successfully restarted notifications")
                    except ValueError as e3:
                        if "already started" in str(e3):
                            _LOGGER.info("Notifications already started after BleakError reconnect, syncing state")
                            self.has_notifier = True
                            reconnect_success = True
                        else:
                            _LOGGER.error("Failed to restart notifications: %s", e3)
                            raise
                    except Exception as e3:
                        _LOGGER.error("Failed to restart notifications: %s", e3)
                        raise
                except Exception as e2:
                    _LOGGER.error("Reconnect after BleakError failed (attempt %d): %s", attempt, e2)
                    reconnect_success = False

                if attempt == self.max_retries:
                    if not reconnect_success:
                        _LOGGER.error("Failed to recover from BleakError after %d attempts - forcing connection restart", self.max_retries)
                        raise ConnectionRecoveryError(f"Failed to recover connection after {self.max_retries} attempts")
                    else:
                        _LOGGER.error("Max retries reached for command %s after BleakError (reconnect succeeded but command still failing)", command)
                    break
                
                # If reconnect failed, don't bother retrying the command - force restart
                if not reconnect_success:
                    _LOGGER.error("Reconnection failed - forcing connection restart")
                    raise ConnectionRecoveryError("Failed to reconnect to device")
                
                # Check if we've exceeded retry time limit even after successful reconnect
                elapsed_time = time.time() - retry_start_time
                if elapsed_time > max_retry_time:
                    _LOGGER.error("Retry time limit exceeded (%.1fs) after reconnect - forcing connection restart", elapsed_time)
                    raise ConnectionRecoveryError(f"Retry operations exceeded {max_retry_time}s time limit")
                
                # otherwise retry with the restored connection
                _LOGGER.info("Retrying command after successful reconnection...")
                continue

        # caught an exception or exhausted retries, return empty bytes object
        return bytes()

    async def _notification_handler(self, _sender: int, data: bytearray):
        """Handle bt data."""

        # Handle encrypted data
        if self.encrypted is True:
            message = Message(data)

            # Handle key exchange
            if message.is_pre_key_exchange:
                message.verify_checksum()

                if message.type == MessageType.CHALLENGE:
                    challenge_response = self.encryption.msg_challenge(message)
                    await self.client.write_gatt_char(WRITE_UUID, challenge_response)
                    return

                if message.type == MessageType.CHALLENGE_ACCEPTED:
                    _LOGGER.debug("Challenge accepted")
                    return

            if self.encryption.unsecure_aes_key is None:
                _LOGGER.error("Received encrypted message before key initialization")

            key, iv = self.encryption.getKeyIv()
            decrypted = Message(self.encryption.aes_decrypt(message.buffer, key, iv))

            if decrypted.is_pre_key_exchange:
                decrypted.verify_checksum()

                if decrypted.type == MessageType.PEER_PUBKEY:
                    peer_pubkey_response = self.encryption.msg_peer_pubkey(decrypted)
                    await self.client.write_gatt_char(WRITE_UUID, peer_pubkey_response)
                    return

                if decrypted.type == MessageType.PUBKEY_ACCEPTED:
                    self.encryption.msg_key_accepted(decrypted)
                    return

            # Handle as message
            data = decrypted.buffer

        # Ignore notifications we don't expect
        if self.notify_future is None or self.notify_future.done():
            _LOGGER.warning("Unexpected notification")
            return

        # If something went wrong, we might get weird data.
        if data == b"AT+NAME?\r" or data == b"AT+ADV?\r":
            err = BadConnectionError("Got AT+ notification")
            self.notify_future.set_exception(err)
            return

        # Save data
        self.notify_response.extend(data)

        if len(self.notify_response) == self.current_command.response_size():
            if self.current_command.is_valid_response(self.notify_response):
                self.notify_future.set_result(self.notify_response)
            else:
                self.notify_future.set_exception(ParseError("Failed checksum"))
        elif self.current_command.is_exception_response(self.notify_response):
            # We got a MODBUS command exception
            msg = f"MODBUS Exception {self.current_command}: {self.notify_response[2]}"
            self.notify_future.set_exception(ModbusError(msg))
