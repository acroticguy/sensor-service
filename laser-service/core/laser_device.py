import asyncio
import socket
import struct
import re
import time
from typing import Optional, Dict, Any
from datetime import datetime
from core.logger import get_logger, get_device_logger, log_command, log_measurement
from models.laser_models import (
    LaserStatus, MeasurementResult, ErrorCode, LaserConfig,
    MeasurementMode, DataFormat, LaserOperatingMode
)

logger = get_logger(__name__)
device_logger = get_device_logger()


class LaserDevice:
    def __init__(self, laser_id: int, host: str = "localhost", port: int = 2030, timeout: float = 1.0, berth_id: Optional[int] = None):
        self.laser_id = laser_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self.berth_id = berth_id
        self.socket: Optional[socket.socket] = None
        self.status = LaserStatus.DISCONNECTED
        self.last_heartbeat = None
        self.error_count = 0
        self.measurements_count = 0
        self._lock = asyncio.Lock()
        self._heartbeat_task = None
        self._continuous_mode = False
        self._continuous_task = None
        self._continuous_data_queue = asyncio.Queue(maxsize=100)
        self._is_reading_continuous = False
        self._device_in_continuous = False  # Track actual device state
        self._last_continuous_data_time = None  # Track when we last received data
        self._berthing_mode = False  # Track if device is in berthing mode
        self._berthing_id = None  # Current berthing session ID
        self._operating_mode = LaserOperatingMode.OFF  # Track current operating mode
        self._drift_mode = False  # Track if device is in drift mode

    async def connect(self) -> bool:
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))
            self.status = LaserStatus.CONNECTED
            self.last_heartbeat = datetime.now()
            logger.info(f"Connected to laser device via TCP bridge at {self.host}:{self.port}")

            # Send escape sequence first to clear any stuck continuous mode
            await self._clear_device_state()

            # Check if device is in continuous mode by looking for incoming data
            await self._check_continuous_state()

            return True
        except Exception as e:
            self.status = LaserStatus.ERROR
            logger.error(f"Failed to connect to laser device at {self.host}:{self.port}: {str(e)}")
            return False

    async def disconnect(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._continuous_task:
            self._continuous_task.cancel()
        # Don't change _device_in_continuous here - device may still be in continuous mode
        self._continuous_mode = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.status = LaserStatus.DISCONNECTED
        logger.info(f"Disconnected from laser device at {self.host}:{self.port}")

    async def send_command(self, command: str, parameters: str = "") -> str:
        async with self._lock:
            if not self.socket:
                raise ConnectionError("Not connected to laser device")

            full_command = f"{command}{parameters}\r"
            logger.debug(f"Sending command: {full_command.strip()}")
            device_logger.info(f"SEND: {command} {parameters}".strip())

            # Measurement commands that return data
            measurement_commands = ['VM', 'DM', 'DF', 'DT', 'VT']
            is_measurement = command in measurement_commands

            try:
                # Clear any pending data in the buffer
                self.socket.settimeout(0.05)
                try:
                    while self.socket.recv(4096):
                        pass
                except socket.timeout:
                    pass

                # Send command
                self.socket.send(full_command.encode())

                # For measurements, we need to wait for device to complete measurement
                if command == 'VM':
                    # VM takes 25 measurements - needs much more time
                    await asyncio.sleep(0.1)
                elif command in ['DM', 'DF']:
                    # DM/DF single measurement - don't wait, just start reading immediately
                    await asyncio.sleep(0.05)
                elif is_measurement:
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.sleep(0.05)

                # Read response
                response = b''

                # For measurement commands, we need to read until we get actual data
                if is_measurement:
                    start_time = asyncio.get_event_loop().time()
                    max_wait = 7.0 if command == 'VM' else 5.0  # Device can take 2.5+ seconds for DM
                    has_measurement_data = False

                    # Use shorter timeout for faster polling
                    self.socket.settimeout(0.1)

                    while (asyncio.get_event_loop().time() - start_time) < max_wait and not has_measurement_data:
                        try:
                            chunk = self.socket.recv(4096)
                            if chunk:
                                response += chunk
                                # Check if we have measurement data
                                resp_str = response.decode('latin-1', errors='ignore')

                                # Look for D-format data or error codes
                                if 'D ' in resp_str or 'D-' in resp_str or resp_str.find('E0') != -1 or resp_str.find('E9') != -1:
                                    # Check if the line seems complete (should have at least 3-4 values)
                                    lines = resp_str.strip().split('\n')
                                    last_line = lines[-1] if lines else ''

                                    # For D format, check if we have enough data
                                    for check_line in lines:
                                        check_line = check_line.strip()
                                        if check_line.startswith('D '):
                                            parts = check_line.split()
                                            # VM should have 5 parts: D speed distance signal temp
                                            # DM should have 4 parts: D distance signal temp
                                            expected_parts = 5 if command == 'VM' else 4

                                            if len(parts) < expected_parts:
                                                # Incomplete data, keep reading
                                                logger.debug(f"Incomplete D-format data: {check_line} (got {len(parts)} parts, expected {expected_parts})")
                                                has_measurement_data = False
                                                break
                                            else:
                                                # Complete data found
                                                has_measurement_data = True

                                    if has_measurement_data:
                                        # We have complete measurement data, return immediately
                                        # Just do a quick non-blocking read to get any trailing data
                                        self.socket.settimeout(0.01)
                                        try:
                                            more = self.socket.recv(4096)
                                            if more:
                                                response += more
                                        except socket.timeout:
                                            pass
                                        # Exit the outer loop immediately
                                        break
                                    else:
                                        # Incomplete data, continue reading
                                        continue
                                elif response == b'\r' or response == b'\r\n':
                                    # Just got a carriage return, device might be processing
                                    # Clear this and wait for actual data
                                    response = b''
                                    continue
                        except socket.timeout:
                            # Check what we have so far
                            if response:
                                resp_str = response.decode('latin-1', errors='ignore')
                                # If we only have echo or carriage return, keep waiting
                                if resp_str.strip() in [command, '']:
                                    continue
                                # If we have any D-format or error, we're done
                                if 'D ' in resp_str or 'D-' in resp_str or 'E0' in resp_str or 'E9' in resp_str:
                                    has_measurement_data = True
                                    break

                    # If we still don't have measurement data, try one more aggressive read
                    if not has_measurement_data and response:
                        resp_str = response.decode('latin-1', errors='ignore')
                        if resp_str.strip() in [command, '']:
                            # We only have echo, try to get more data
                            self.socket.settimeout(1.0)
                            try:
                                more = self.socket.recv(4096)
                                if more:
                                    response += more
                            except socket.timeout:
                                pass
                else:
                    # Regular commands
                    self.socket.settimeout(1.0)
                    consecutive_timeouts = 0
                    while consecutive_timeouts < 2:
                        try:
                            chunk = self.socket.recv(4096)
                            if chunk:
                                response += chunk
                                consecutive_timeouts = 0
                                self.socket.settimeout(0.1)
                            else:
                                break
                        except socket.timeout:
                            consecutive_timeouts += 1
                            if response:
                                break

                if not response:
                    raise TimeoutError(f"No response from device for command {command}")

                response_str = response.decode('latin-1', errors='ignore')
                logger.debug(f"Raw response bytes: {response.hex()}")
                logger.debug(f"Decoded response: {repr(response_str)}")

                # Log the command and response
                device_logger.info(f"RECV: {response_str.strip()}")
                log_command(
                    command=command,
                    parameters=parameters,
                    response=response_str.strip(),
                    device_port=f"{self.host}:{self.port}",
                    success=True
                )

                # Log specific info for measurement commands
                if is_measurement:
                    # For debugging, show both normalized and raw
                    norm_lines = response_str.replace('\r\n', '\n').replace('\r', '\n').split('\n')
                    logger.debug(f"Measurement command {command} returned {len(norm_lines)} lines")
                    # Only log first few lines to avoid spam
                    for i, line in enumerate(norm_lines[:5]):
                        if line.strip():
                            logger.debug(f"  Line {i}: {repr(line.strip())}")
                    if len(norm_lines) > 5:
                        logger.debug(f"  ... and {len(norm_lines) - 5} more lines")

                return response_str

            except socket.timeout:
                self.error_count += 1
                logger.error(f"Command timeout: {command}")
                device_logger.error(f"TIMEOUT: {command} {parameters}".strip())
                log_command(
                    command=command,
                    parameters=parameters,
                    response="TIMEOUT",
                    device_port=f"{self.host}:{self.port}",
                    success=False
                )
                raise TimeoutError(f"Command {command} timed out")
            except Exception as e:
                self.error_count += 1
                logger.error(f"Command failed: {command} - {str(e)}")
                device_logger.error(f"ERROR: {command} {parameters} - {str(e)}".strip())
                log_command(
                    command=command,
                    parameters=parameters,
                    response=f"ERROR: {str(e)}",
                    device_port=f"{self.host}:{self.port}",
                    success=False
                )
                raise

    async def measure_distance(self, mode: MeasurementMode = MeasurementMode.DM) -> MeasurementResult:
        """Perform distance measurement. Returns immediately with measurement data."""
        if mode == MeasurementMode.DT:
            raise ValueError("Use _start_continuous_mode() for continuous measurements")

        # Retry logic for cases where device only returns CR
        max_retries = 2
        for attempt in range(max_retries):
            response = await self.send_command(mode.value)
            result = self._parse_measurement(response, command=mode.value)

            # If we got valid data, return it
            if result.distance is not None or result.error_code is not None:
                return result

            # If we only got empty response, try again
            if attempt < max_retries - 1:
                logger.debug(f"Retrying {mode.value} command, attempt {attempt + 2}")
                await asyncio.sleep(0.1)

        # Final attempt failed
        logger.error(f"Failed to get valid measurement data for {mode.value} after {max_retries} attempts")
        result.error_code = ErrorCode.E99
        return result

    async def measure_speed(self, mode: MeasurementMode = MeasurementMode.VM) -> MeasurementResult:
        """Perform speed measurement. Returns immediately with measurement data."""
        if mode == MeasurementMode.VT:
            raise ValueError("Use _start_continuous_mode() for continuous measurements")

        # Retry logic for cases where device only returns CR
        max_retries = 2
        for attempt in range(max_retries):
            response = await self.send_command(mode.value)
            result = self._parse_measurement(response, command=mode.value)

            # If we got valid data, return it
            if result.speed is not None or result.error_code is not None:
                return result

            # If we only got empty response, try again
            if attempt < max_retries - 1:
                logger.debug(f"Retrying {mode.value} command, attempt {attempt + 2}")
                await asyncio.sleep(0.1)

        # Final attempt failed
        logger.error(f"Failed to get valid measurement data for {mode.value} after {max_retries} attempts")
        result.error_code = ErrorCode.E99
        return result

    async def stop_continuous(self):
        """Stop continuous measurements"""
        if self._continuous_task:
            self._continuous_task.cancel()
            self._continuous_task = None

        # Send Escape command (0x1B) to stop continuous measurement as per PDF
        escape_char = b'\x1b'
        try:
            async with self._lock:
                if self.socket:
                    # Send escape twice for reliability without delays
                    self.socket.send(escape_char)
                    self.socket.send(escape_char)
        except:
            pass

        self._continuous_mode = False
        self._is_reading_continuous = False
        self._device_in_continuous = False  # Device is no longer in continuous mode
        self._last_continuous_data_time = None  # Reset data timestamp

        # Clear the queue
        while not self._continuous_data_queue.empty():
            try:
                self._continuous_data_queue.get_nowait()
            except:
                break

        logger.info("Stopped continuous measurement")

    async def _start_continuous_mode(self, mode: MeasurementMode):
        """Start continuous measurement mode"""
        # Stop any existing continuous measurement
        await self.stop_continuous()

        # Start the measurement mode
        await self.send_command(mode.value)
        self._continuous_mode = True
        self._device_in_continuous = True  # Device is now in continuous mode
        self._last_continuous_data_time = None  # Reset data timestamp

        # Start background task to read continuous data
        self._continuous_task = asyncio.create_task(self._read_continuous_data())
        logger.info(f"Started continuous mode: {mode.value}")

    async def _read_continuous_data(self):
        """Background task to read continuous measurement data"""
        self._is_reading_continuous = True
        consecutive_errors = 0
        incomplete_buffer = b''  # Buffer for incomplete lines

        while self._continuous_mode and self._is_reading_continuous:
            try:
                # Don't use the lock here to avoid blocking other operations
                self.socket.settimeout(0.5)  # Short timeout for continuous reading
                data = self.socket.recv(4096)

                if data:
                    consecutive_errors = 0

                    # Clean corrupt bytes before processing
                    data = data.replace(b'\xff', b'').replace(b'\x14', b'').replace(b'\x02', b'').replace(b'\x08', b'')

                    # Add to incomplete buffer
                    data = incomplete_buffer + data
                    incomplete_buffer = b''

                    # Process the data - it might contain multiple measurements
                    text = data.decode('latin-1', errors='ignore')

                    # Split by line endings but keep track of incomplete lines
                    lines = text.split('\n')

                    # If the data doesn't end with a newline, last line is incomplete
                    if not text.endswith('\n') and not text.endswith('\r'):
                        if lines:
                            # Save the incomplete line for next iteration
                            incomplete_buffer = lines[-1].encode('latin-1')
                            lines = lines[:-1]

                    # Process complete lines
                    for line in lines:
                        line = line.replace('\r', '').strip()
                        # Skip empty lines, command echoes, and single character lines
                        if line and line not in ['?', 'OK', '', 'DT', 'VT', 'D']:
                            # Skip lines that are just a single D or obviously incomplete
                            if line == 'D' or len(line) < 10:
                                logger.debug(f"Skipping too short line: {repr(line)}")
                                continue

                            # Skip lines with invalid characters for D-format data
                            if line.startswith('D ') or line.startswith('D-'):
                                if 'r' in line or '#' in line or 'h' in line:
                                    logger.debug(f"Skipping line with invalid characters: {repr(line)}")
                                    continue

                            # Determine the command based on which continuous mode we're in
                            if self._continuous_mode:
                                # Check what type of continuous mode based on expected data
                                # VT has 4 values after D: speed distance signal temp
                                # DT has 3 values after D: distance signal temp
                                parts = line.split()
                                if parts and (parts[0] == 'D' or parts[0].startswith('D-')):
                                    # Count the numeric values after D
                                    if parts[0].startswith('D-'):
                                        # D- prefix means first value includes the minus
                                        num_values = len(parts)  # All parts including D-xxx
                                    else:
                                        num_values = len(parts) - 1  # Exclude the 'D' part

                                    if num_values >= 4:
                                        cmd = 'VT'  # Speed continuous mode (4 values)
                                    else:
                                        cmd = 'DT'  # Distance continuous mode (3 values)
                                else:
                                    cmd = 'DT'  # Default to distance mode
                            else:
                                cmd = 'DM'

                            measurement = self._parse_measurement(line, command=cmd)
                            # Always add measurements to queue, including nulls
                            if measurement:  # Only check if measurement object exists
                                # Add timestamp
                                measurement.timestamp = time.time()
                                # Log null measurements for debugging
                                if measurement.distance is None and measurement.speed is None:
                                    logger.info(f"Queuing NULL measurement from laser {self.laser_id}: {measurement.raw_data}")
                                # Add to queue, drop old data if full
                                if self._continuous_data_queue.full():
                                    try:
                                        self._continuous_data_queue.get_nowait()
                                    except:
                                        pass
                                await self._continuous_data_queue.put(measurement)
                                # Update last data time when we receive valid measurement
                                self._last_continuous_data_time = time.time()

            except socket.timeout:
                # Timeout is normal in continuous mode
                pass
            except Exception as e:
                consecutive_errors += 1
                logger.debug(f"Error reading continuous data: {str(e)}")
                if consecutive_errors > 5:
                    logger.error("Too many consecutive errors in continuous mode, stopping")
                    break

            await asyncio.sleep(0.01)  # Small delay to prevent CPU spinning

        self._is_reading_continuous = False
        logger.info("Stopped reading continuous data")

    async def get_continuous_data(self, timeout: float = 1.0) -> Optional[MeasurementResult]:
        """Get the LATEST measurement from continuous mode"""
        if not self._continuous_mode:
            return None

        latest_measurement = None
        queue_drain_start = asyncio.get_event_loop().time()

        # First, aggressively drain the entire queue to discard old data
        drained_count = 0
        while not self._continuous_data_queue.empty():
            try:
                old_measurement = self._continuous_data_queue.get_nowait()
                drained_count += 1
                # Keep the last one (including nulls) in case we don't get a fresh one
                if old_measurement:
                    latest_measurement = old_measurement
            except asyncio.queues.QueueEmpty:
                break

        if drained_count > 0:
            logger.debug(f"Drained {drained_count} old measurements from queue")

        # Now wait for a fresh measurement
        # Give a bit more time since we want the freshest data
        fresh_timeout = timeout + 0.5
        try:
            fresh_measurement = await asyncio.wait_for(
                self._continuous_data_queue.get(),
                timeout=fresh_timeout
            )
            if fresh_measurement:
                latest_measurement = fresh_measurement
                logger.debug(f"Got fresh measurement (including nulls) with timestamp {fresh_measurement.timestamp}")
        except asyncio.TimeoutError:
            logger.debug(f"No fresh measurement received within {fresh_timeout}s")
            # For real laser data, always use the latest measurement we have
            # Don't discard based on age - real lasers can have 30+ second intervals

        return latest_measurement

    def is_continuous_mode(self) -> bool:
        """Check if device is in continuous measurement mode"""
        # If we're actively reading continuous data, return that state
        if self._continuous_mode:
            return True
        # Otherwise, return the last known device state (for reconnection scenarios)
        return self._device_in_continuous

    async def set_config(self, config: LaserConfig) -> bool:
        try:
            if config.measurement_frequency is not None:
                await self.send_command("MF", str(config.measurement_frequency))

            if config.averaging is not None:
                await self.send_command("AV", str(config.averaging))

            if config.moving_average is not None:
                await self.send_command("MA", str(config.moving_average))

            if config.offset is not None:
                await self.send_command("OF", f"{config.offset:.3f}")

            if config.scale_factor is not None:
                await self.send_command("SF", f"{config.scale_factor:.6f}")

            if config.output_format is not None:
                format_cmd = "OA" if config.output_format == DataFormat.ASCII else "OB"
                await self.send_command(format_cmd)

            if config.external_trigger is not None:
                trigger_cmd = "ET1" if config.external_trigger else "ET0"
                await self.send_command(trigger_cmd)

            if config.laser_power is not None:
                power_cmd = "L1" if config.laser_power else "L0"
                await self.send_command(power_cmd)

            return True
        except Exception as e:
            logger.error(f"Failed to set configuration: {str(e)}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        try:
            response = await self.send_command("ST")
            return self._parse_status(response)
        except:
            return {}

    async def _clear_device_state(self):
        """Clear any stuck continuous mode or partial data on device"""
        try:
            # Send escape sequence multiple times to ensure device exits continuous mode
            escape_char = b'\x1b'
            for _ in range(3):
                self.socket.send(escape_char)
                await asyncio.sleep(0.05)

            # Clear any pending data in the buffer
            self.socket.settimeout(0.1)
            try:
                while self.socket.recv(4096):
                    pass
            except socket.timeout:
                pass

            logger.debug("Cleared device state with escape sequence")
        except Exception as e:
            logger.debug(f"Error clearing device state: {str(e)}")

    async def _check_continuous_state(self):
        """Check if device is in continuous mode after connection"""
        try:
            # Set a short timeout to check for incoming data
            self.socket.settimeout(0.5)
            data = self.socket.recv(1024)

            if data:
                # Device is sending data - it's in continuous mode
                logger.info("Device is in continuous mode - data detected")
                self._device_in_continuous = True
                self._continuous_mode = True

                # Process the received data
                text = data.decode('latin-1', errors='ignore')
                lines = text.strip().split('\n')

                for line in lines:
                    if line.strip():
                        measurement = self._parse_measurement(line)
                        # Always add measurements to queue, including nulls
                        if measurement:  # Only check if measurement object exists
                            await self._continuous_data_queue.put(measurement)
                            # Update last data time when we receive any measurement
                            self._last_continuous_data_time = time.time()

                # Start the continuous reading task
                self._continuous_task = asyncio.create_task(self._read_continuous_data())
            else:
                self._device_in_continuous = False

        except socket.timeout:
            # No data received - device is not in continuous mode
            self._device_in_continuous = False
        except Exception as e:
            logger.debug(f"Error checking continuous state: {str(e)}")
            self._device_in_continuous = False

    async def detect_and_resume_continuous_mode(self) -> bool:
        """Detect if device is sending continuous data and resume reading if so"""
        if self._continuous_mode:
            # Already in continuous mode - but check if we're still getting data
            if self._last_continuous_data_time:
                time_since_data = time.time() - self._last_continuous_data_time
                if time_since_data > 5.0:
                    # Haven't received data in 5 seconds, continuous mode likely stopped
                    logger.info(f"No data for {time_since_data:.1f}s, continuous mode appears to have stopped")
                    await self.stop_continuous()
                    return False
            return True

        try:
            # Check for incoming data with a slightly longer timeout
            async with self._lock:
                self.socket.settimeout(1.0)
                data = self.socket.recv(4096)

                if data:
                    # Clean corrupt bytes
                    data = data.replace(b'\xff', b'').replace(b'\x14', b'').replace(b'\x02', b'').replace(b'\x08', b'')
                    text = data.decode('latin-1', errors='ignore')

                    # Look for D-format data which indicates measurements
                    if 'D ' in text or 'D-' in text:
                        logger.info(f"Detected continuous data stream: {repr(text[:100])}")

                        # Check if this is partial/invalid data
                        # Partial data patterns: "D 0000.0", "D 0000", "D 00", "D 0"
                        partial_patterns = [
                            r'^D\s+0+\.?0*$',  # Matches D followed by zeros
                            r'^D\s+\d{1,3}$',   # Matches D followed by 1-3 digits only
                            r'^D\s+\d+\.\d{1}$' # Matches D followed by incomplete decimal
                        ]

                        is_partial = False
                        for pattern in partial_patterns:
                            if re.search(pattern, text.strip()):
                                is_partial = True
                                break

                        if is_partial:
                            logger.warning(f"Detected partial continuous data: {repr(text[:50])}")
                            # Send escape to clear partial continuous mode
                            try:
                                escape_char = b'\x1b'
                                for _ in range(3):
                                    self.socket.send(escape_char)
                                    await asyncio.sleep(0.05)
                            except:
                                pass
                            self._device_in_continuous = False
                            self._continuous_mode = False
                            return False

                        # Device might be in continuous mode - try to parse data
                        self._device_in_continuous = True
                        self._continuous_mode = True

                        # Parse any complete measurements from the received data
                        lines = text.strip().split('\n')
                        valid_data_found = False
                        for line in lines:
                            line = line.strip()
                            if line and line not in ['?', 'OK', '', 'DT', 'VT', 'D']:
                                if len(line) >= 10:  # Ensure it's not too short
                                    # Determine if it's DT or VT based on number of values
                                    parts = line.split()
                                    if parts and (parts[0] == 'D' or parts[0].startswith('D-')):
                                        num_values = len(parts) - 1 if parts[0] == 'D' else len(parts)
                                        cmd = 'VT' if num_values >= 4 else 'DT'
                                    else:
                                        cmd = 'DT'

                                    measurement = self._parse_measurement(line, command=cmd)
                                    # Always add measurements to queue, including nulls
                                    if measurement:  # Only check if measurement object exists
                                        measurement.timestamp = time.time()
                                        await self._continuous_data_queue.put(measurement)
                                        # Update last data time when we receive any measurement
                                        self._last_continuous_data_time = time.time()
                                        valid_data_found = True

                        # Check if we actually found valid data
                        if valid_data_found:
                            # Start the continuous reading task
                            self._continuous_task = asyncio.create_task(self._read_continuous_data())
                            return True
                        else:
                            # Found D prefix but no valid recent data
                            logger.warning("Found D prefix but no valid measurement data")
                            self._device_in_continuous = False
                            self._continuous_mode = False
                            return False
                    else:
                        logger.debug(f"No measurement data found in received data: {repr(text[:100])}")
                        self._device_in_continuous = False
                        self._continuous_mode = False
                        return False
                else:
                    self._device_in_continuous = False
                    self._continuous_mode = False
                    return False

        except socket.timeout:
            logger.debug("No data received - device not in continuous mode")
            self._device_in_continuous = False
            self._continuous_mode = False
            return False
        except Exception as e:
            logger.error(f"Error detecting continuous mode: {str(e)}")
            self._device_in_continuous = False
            self._continuous_mode = False
            return False

    async def heartbeat(self) -> bool:
        try:
            # Try a simple command that should always work
            # Some devices might not support ST, so we'll just check connection
            self.socket.settimeout(0.5)  # Quick timeout for heartbeat
            self.socket.send(b"\r")  # Send empty command
            self.socket.recv(1024)  # Just read any response
            self.last_heartbeat = datetime.now()
            return True
        except Exception as e:
            logger.debug(f"Heartbeat failed: {str(e)}")
            # Only mark as error if socket is actually broken
            try:
                # Test if socket is still connected
                self.socket.getpeername()
            except (socket.error, OSError):
                self.status = LaserStatus.DISCONNECTED
            return False

    def _parse_measurement(self, response: str, command: str = None) -> MeasurementResult:
        """Parse measurement response with smart logic for various formats"""
        result = MeasurementResult(raw_data=response)

        # Detect if this is a VM or VT response (both have speed data)
        is_vm_response = command == 'VM' or 'VM' in response
        is_vt_response = command == 'VT' or 'VT' in response

        # Handle various response formats
        # First, normalize line endings - handle \r\n, \n\r, \r, \n
        response = response.replace('\r\n', '\n').replace('\n\r', '\n').replace('\r', '\n')

        # Split response and clean up
        all_lines = response.split('\n')

        # Filter out command echo and empty lines
        data_lines = []
        command_echo_found = False
        for line in all_lines:
            line = line.strip()
            if line in ['DM', 'VM', 'DF', 'DT', 'VT']:
                command_echo_found = True
                continue
            if line and line not in ['OK', '']:
                # "?" response typically means unknown command or error
                if line == '?':
                    logger.warning("Device returned '?' - unknown command or syntax error")
                    result.error_code = ErrorCode.E99
                    return result
                data_lines.append(line)

        lines = data_lines

        # If no data lines found, return empty result
        if not lines:
            logger.warning(f"No data lines found in response: {repr(response)}")
            return result

        # Filter out lines that are just "D" without any data
        lines = [line for line in lines if line != 'D']

        if not lines:
            logger.warning(f"No valid data lines after filtering: {repr(response)}")
            return result

        # For VM command, we might have multiple D lines to process
        vm_measurements = []

        # Process each line
        for line in lines:
            # Check for error codes
            if line.startswith('E') and len(line) >= 3:
                try:
                    result.error_code = ErrorCode(line[:3])
                except:
                    result.error_code = ErrorCode.E99
                logger.debug(f"Parsed error code: {result.error_code}")
                return result

            # Try different parsing strategies

            # Strategy 1: D-format
            # Format can be:
            # - DM/DF: "D distance signal temperature"
            # - VM: "D speed distance signal temperature" or "D-speed distance signal temperature"
            if line.startswith('D ') or line.startswith('D-'):
                # First validate that this looks like valid D-format data
                # Should contain only valid characters: D, -, numbers, dots, spaces, +
                if not re.match(r'^D[\s\-][\d\s\.\+\-]+$', line):
                    logger.debug(f"Invalid characters in D-format line: {repr(line)}")
                    continue

                # Handle both "D " and "D-" prefixes
                if line.startswith('D-'):
                    # For D- format, the minus is part of the first value
                    # So we include it: "D-0000.007  0004.000 01679 +45.7"
                    parts = re.split(r'\s+', line[1:].strip())
                else:
                    # For "D " format, skip the "D " prefix
                    parts = re.split(r'\s+', line[2:].strip())

                if len(parts) >= 1:
                    try:
                        # Check if this looks like VM/VT data (has 4 values) or DM/DT data (has 3 values)
                        if len(parts) >= 4 and (is_vm_response or is_vt_response):
                            # VM/VT format: D speed distance signal temperature (4 values)
                            result.speed = float(parts[0])  # m/s
                            result.distance = float(parts[1]) * 1000  # m to mm
                            result.signal_strength = int(parts[2])
                            result.temperature = float(parts[3])
                            logger.debug(f"Parsed as VM/VT format (4 values)")
                        elif len(parts) >= 3:
                            # Could be either format - check if it's VM/VT by command
                            if is_vm_response or is_vt_response:
                                # Still VM/VT but maybe missing temperature
                                result.speed = float(parts[0])  # m/s
                                result.distance = float(parts[1]) * 1000  # m to mm
                                result.signal_strength = int(parts[2])
                                logger.debug(f"Parsed as VM/VT format (3 values, no temp)")
                            else:
                                # DM/DT/DF format: D distance signal temperature (3 values)
                                result.distance = float(parts[0]) * 1000  # m to mm
                                result.signal_strength = int(parts[1])
                                result.temperature = float(parts[2])
                                logger.debug(f"Parsed as DM/DT format (3 values)")
                        else:
                            # Minimal format with just distance
                            result.distance = float(parts[0]) * 1000  # m to mm
                            if len(parts) > 1:
                                # Parse signal strength - handle formats like "03421"
                                result.signal_strength = int(parts[1])
                            if len(parts) > 2:
                                # Parse temperature - handle formats like "+40.4"
                                result.temperature = float(parts[2])

                        self.measurements_count += 1
                        result.timestamp = time.time()
                        logger.info(f"Parsed D-format: distance={result.distance}, speed={result.speed}, signal={result.signal_strength}, temp={result.temperature}")
                        continue
                    except Exception as e:
                        logger.debug(f"Failed to parse D-format '{line}': {e}")

            # Strategy 2: Space/tab separated numeric values
            parts = line.replace('\t', ' ').split()
            if parts and self._is_numeric(parts[0]):
                try:
                    values = []
                    for part in parts:
                        if self._is_numeric(part):
                            values.append(float(part))

                    if values:
                        # Determine what the values represent based on command context
                        if is_vm_response or is_vt_response:
                            # Speed measurement (VM/VT have 4 values: speed distance signal temp)
                            if len(values) >= 4:
                                result.speed = values[0]  # m/s
                                result.distance = values[1] * 1000  # m to mm
                                result.signal_strength = int(values[2])
                                result.temperature = values[3]
                            elif len(values) >= 3:
                                # Missing temperature
                                result.speed = values[0]
                                result.distance = values[1] * 1000  # m to mm
                                result.signal_strength = int(values[2])
                            elif len(values) >= 2:
                                # Just speed and distance
                                result.speed = values[0]
                                result.distance = values[1] * 1000  # m to mm
                            else:
                                # Just speed
                                result.speed = values[0]
                        else:
                            # Distance measurement (DM, DF, DT have 3 values: distance signal temp)
                            result.distance = values[0] * 1000  # m to mm
                            if len(values) > 1:
                                result.signal_strength = int(values[1])
                            if len(values) > 2:
                                result.temperature = values[2]

                        self.measurements_count += 1
                        logger.debug(f"Parsed numeric values: distance={result.distance}, speed={result.speed}, signal={result.signal_strength}, temp={result.temperature}")
                        continue
                except Exception as e:
                    logger.debug(f"Failed to parse numeric line '{line}': {e}")

            # Strategy 3: Values with units
            if any(unit in line for unit in ['mm', 'm/s', '°C', 'C']):
                try:
                    # Extract numeric values before units
                    # Distance in mm
                    dist_match = re.search(r'([+-]?\d+\.?\d*)\s*mm', line)
                    if dist_match:
                        result.distance = float(dist_match.group(1))

                    # Speed in m/s
                    speed_match = re.search(r'([+-]?\d+\.?\d*)\s*m/s', line)
                    if speed_match:
                        result.speed = float(speed_match.group(1))

                    # Temperature in °C or C
                    temp_match = re.search(r'([+-]?\d+\.?\d*)\s*[°]?C', line)
                    if temp_match:
                        result.temperature = float(temp_match.group(1))

                    # Signal strength (number between 100-9999 without unit)
                    signal_match = re.search(r'\b(\d{3,4})\b(?!\s*[a-zA-Z])', line)
                    if signal_match:
                        val = int(signal_match.group(1))
                        if 100 <= val <= 9999:
                            result.signal_strength = val

                    if result.distance is not None or result.speed is not None:
                        self.measurements_count += 1
                        logger.debug(f"Parsed with units: distance={result.distance}, speed={result.speed}, signal={result.signal_strength}, temp={result.temperature}")
                        continue
                except Exception as e:
                    logger.debug(f"Failed to parse line with units '{line}': {e}")

            # Strategy 4: Comma or semicolon separated values
            if ',' in line or ';' in line:
                separator = ',' if ',' in line else ';'
                parts = line.split(separator)
                numeric_parts = []
                for part in parts:
                    part = part.strip()
                    if self._is_numeric(part):
                        numeric_parts.append(float(part))

                if numeric_parts:
                    try:
                        if is_vm_response or is_vt_response:
                            # VM/VT format: speed, distance, signal, temperature
                            result.speed = numeric_parts[0]
                            if len(numeric_parts) > 1:
                                result.distance = numeric_parts[1] * 1000  # m to mm
                            if len(numeric_parts) > 2:
                                result.signal_strength = int(numeric_parts[2])
                            if len(numeric_parts) > 3:
                                result.temperature = numeric_parts[3]
                        else:
                            # DM/DT/DF format: distance, signal, temperature
                            result.distance = numeric_parts[0] * 1000  # m to mm
                            if len(numeric_parts) > 1:
                                result.signal_strength = int(numeric_parts[1])
                            if len(numeric_parts) > 2:
                                result.temperature = numeric_parts[2]

                        self.measurements_count += 1
                        logger.debug(f"Parsed CSV format: distance={result.distance}, speed={result.speed}")
                        continue
                    except Exception as e:
                        logger.debug(f"Failed to parse CSV line '{line}': {e}")

        # Log if we couldn't parse any measurement data
        if result.distance is None and result.speed is None and result.error_code is None:
            logger.warning(f"Could not parse any measurement data from response: {repr(response)}")
        else:
            # Log successful measurement
            measurement_type = "UNKNOWN"
            if command:
                measurement_type = command
            elif result.speed is not None and result.distance is not None:
                measurement_type = "VM/VT"
            elif result.distance is not None:
                measurement_type = "DM/DF/DT"
            elif result.speed is not None:
                measurement_type = "SPEED"

            log_measurement(
                measurement_type=measurement_type,
                value=result.distance if result.distance is not None else result.speed,
                signal_strength=result.signal_strength,
                temperature=result.temperature,
                error_code=result.error_code.value if result.error_code else None,
                device_port=f"{self.host}:{self.port}"
            )

        return result

    def _is_numeric(self, value: str) -> bool:
        """Check if a string represents a numeric value"""
        try:
            float(value.replace(',', '.'))
            return True
        except:
            return False

    def _parse_status(self, response: str) -> Dict[str, Any]:
        # Parse status response format
        status = {}
        try:
            parts = response.split(',')
            for part in parts:
                if '=' in part:
                    key, value = part.split('=')
                    status[key.strip()] = value.strip()
        except:
            pass
        return status

    def _parse_binary_data(self, data: bytes) -> MeasurementResult:
        # Binary format: 4 bytes distance, 2 bytes signal, 2 bytes temperature
        result = MeasurementResult()
        try:
            if len(data) >= 8:
                distance = struct.unpack('<I', data[0:4])[0]  # Little-endian unsigned int
                signal = struct.unpack('<H', data[4:6])[0]    # Little-endian unsigned short
                temp = struct.unpack('<h', data[6:8])[0]      # Little-endian signed short

                result.distance = distance / 1000.0  # Convert to mm
                result.signal_strength = signal
                result.temperature = temp / 10.0  # Convert to °C
        except Exception as e:
            logger.error(f"Failed to parse binary data: {str(e)}")

        return result

    async def start_berthing_mode(self, berthing_id: int, max_retries: int = 3) -> bool:
        """
        Start berthing mode for this laser device with retry logic
        
        Args:
            berthing_id: The berthing session ID
            max_retries: Maximum number of retry attempts
            
        Returns:
            Success status
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Starting berthing mode for laser {self.laser_id} with berthing {berthing_id} (attempt {attempt + 1}/{max_retries})")
                
                # Check connection and reconnect if needed
                if self.status == LaserStatus.DISCONNECTED or not self.socket:
                    logger.info(f"Laser {self.laser_id} is disconnected, attempting to reconnect...")
                    if not await self.connect():
                        logger.error(f"Failed to reconnect to laser {self.laser_id}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(0.5)  # Reduced reconnection delay
                            continue
                        else:
                            raise ConnectionError("Could not establish connection to laser device")
                
                # Send escape character to ensure device is in standby
                await self._send_escape_sequence()
                await asyncio.sleep(0.1)  # Minimal wait for device to settle
                
                # Start VT (speed measurement) continuous mode with retry
                success = await self._start_continuous_mode_with_retry(MeasurementMode.VT)
                
                if success:
                    # VT mode successfully started and already confirmed to be receiving data
                    # For real laser data with authentic intervals, trust the initial confirmation
                    self._berthing_mode = True
                    self._berthing_id = berthing_id
                    self._operating_mode = LaserOperatingMode.BERTHING
                    logger.info(f"Berthing mode started successfully for laser {self.laser_id} - VT mode confirmed active")
                    return True
                else:
                    logger.warning(f"Failed to start VT mode for laser {self.laser_id}, retrying...")
                    continue
                    
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed for laser {self.laser_id}: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)  # Reduced main retry delay
                    continue
                
        logger.error(f"Failed to start berthing mode for laser {self.laser_id} after {max_retries} attempts")
        return False
    
    async def stop_berthing_mode(self) -> bool:
        """
        Stop berthing mode and return laser to standby
        
        Returns:
            Success status
        """
        try:
            logger.info(f"Stopping berthing mode for laser {self.laser_id}")
            
            # If connection is broken, skip sending escape sequence but still clean up state
            if self.status == LaserStatus.DISCONNECTED or not self.socket:
                logger.info(f"Laser {self.laser_id} is disconnected, cleaning up berthing state without sending escape sequence")
            else:
                # Send escape character to stop continuous mode and return to standby
                try:
                    await self._send_escape_sequence()
                    await asyncio.sleep(0.5)
                except (ConnectionResetError, ConnectionAbortedError, OSError):
                    # Connection broken during escape sequence - that's fine, simulator was restarted
                    logger.info(f"Connection to laser {self.laser_id} was broken during stop - this is expected when simulator restarts")
            
            # Stop any continuous reading tasks
            await self.stop_continuous()
            
            # Always clean up the berthing state regardless of connection status
            self._berthing_mode = False
            self._berthing_id = None
            self._operating_mode = LaserOperatingMode.OFF
            
            logger.info(f"Berthing mode stopped successfully for laser {self.laser_id}")
            return True
            
        except Exception as e:
            # Clean up state even if there was an error
            self._berthing_mode = False
            self._berthing_id = None
            self._operating_mode = LaserOperatingMode.OFF
            logger.error(f"Failed to stop berthing mode for laser {self.laser_id}: {str(e)}")
            return False
    
    async def _send_escape_sequence(self) -> None:
        """Send escape sequence to put device in standby mode"""
        if not self.socket:
            raise ConnectionError("Not connected to laser device")
        
        try:
            # Send ESC character multiple times to ensure device receives it
            escape_char = b'\x1b'
            for _ in range(3):
                self.socket.send(escape_char)
                await asyncio.sleep(0.1)
            
            logger.debug(f"Sent escape sequence to laser {self.laser_id}")
            
        except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
            # Connection was broken (e.g., simulator restarted) - mark as disconnected
            logger.warning(f"Connection to laser {self.laser_id} was broken while sending escape sequence: {str(e)}")
            self.status = LaserStatus.DISCONNECTED
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
            # Don't raise - this is expected when simulator restarts
            
        except Exception as e:
            logger.error(f"Failed to send escape sequence: {str(e)}")
            raise
    
    def is_in_berthing_mode(self) -> bool:
        """Check if device is currently in berthing mode"""
        return self._berthing_mode
    
    def get_berthing_id(self) -> Optional[int]:
        """Get current berthing session ID"""
        return self._berthing_id
    
    async def set_mode_off(self) -> bool:
        """
        Set laser to OFF mode - sends ESC character to stop all operations
        
        Returns:
            Success status
        """
        try:
            logger.info(f"Setting laser {self.laser_id} to OFF mode")
            
            # Send escape character to stop any continuous mode
            await self._send_escape_sequence()
            await asyncio.sleep(0.5)
            
            # Update mode tracking
            self._operating_mode = LaserOperatingMode.OFF
            self._berthing_mode = False
            self._drift_mode = False
            self._berthing_id = None
            
            logger.info(f"Laser {self.laser_id} set to OFF mode successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set laser {self.laser_id} to OFF mode: {str(e)}")
            return False
    
    async def start_drift_mode(self) -> bool:
        """
        Start drift mode for this laser device
        In drift mode, continuous VT measurement is started but data is only streamed via websockets
        No database insertion occurs
        
        Returns:
            Success status
        """
        try:
            logger.info(f"Starting drift mode for laser {self.laser_id}")
            
            # Send escape character to ensure device is in standby
            await self._send_escape_sequence()
            await asyncio.sleep(1.0)
            
            # Start continuous VT mode
            success = await self._start_continuous_mode_with_retry(MeasurementMode.VT)
            
            if success:
                # Verify data is flowing (accept nulls as valid data)
                test_data = await self.get_continuous_data(timeout=3.0)
                if test_data:  # Accept any data including nulls
                    self._drift_mode = True
                    self._operating_mode = LaserOperatingMode.DRIFT
                    logger.info(f"Drift mode started successfully for laser {self.laser_id}")
                    return True
                else:
                    logger.error(f"Failed to verify data flow for laser {self.laser_id} in drift mode")
                    return False
            else:
                logger.error(f"Failed to start continuous VT mode for laser {self.laser_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error starting drift mode for laser {self.laser_id}: {str(e)}")
            return False
    
    async def stop_drift_mode(self) -> bool:
        """
        Stop drift mode and return laser to standby
        
        Returns:
            Success status
        """
        try:
            logger.info(f"Stopping drift mode for laser {self.laser_id}")
            
            # Send escape character to stop continuous mode
            await self._send_escape_sequence()
            await asyncio.sleep(0.5)
            
            self._drift_mode = False
            self._operating_mode = LaserOperatingMode.OFF
            
            logger.info(f"Drift mode stopped successfully for laser {self.laser_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop drift mode for laser {self.laser_id}: {str(e)}")
            return False
    
    def is_in_drift_mode(self) -> bool:
        """Check if device is currently in drift mode"""
        return self._drift_mode
    
    def get_operating_mode(self) -> LaserOperatingMode:
        """Get current operating mode"""
        return self._operating_mode
    
    async def _start_continuous_mode_with_retry(self, mode: MeasurementMode, max_retries: int = 3) -> bool:
        """Start continuous mode with retry logic"""
        for attempt in range(max_retries):
            try:
                logger.info(f"Attempting to start {mode} mode for laser {self.laser_id} (attempt {attempt + 1}/{max_retries})")
                
                # Clear device state completely
                await self._clear_device_state()
                await asyncio.sleep(0.05)  # Minimal state clear delay
                
                # Start the measurement mode
                await self._start_continuous_mode(mode)
                
                # Wait for data to start flowing
                await asyncio.sleep(0.2)  # Reduced data flow wait
                
                # Check if we're receiving data (accept nulls as valid data)
                test_data = await self.get_continuous_data(timeout=3.0)  # Reduced timeout
                if test_data:  # Accept any data including nulls
                    logger.info(f"Successfully started {mode} mode for laser {self.laser_id} and receiving data")
                    logger.info(f"Test data: distance={test_data.distance}, speed={test_data.speed}")
                    return True
                else:
                    logger.warning(f"Started {mode} for laser {self.laser_id} but no data received, retrying...")
                    await self.stop_continuous()
                    
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for laser {self.laser_id}: {str(e)}")
                try:
                    await self.stop_continuous()
                except:
                    pass
                    
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)  # Reduced retry delay
        
        logger.error(f"Failed to start {mode} mode for laser {self.laser_id} after {max_retries} attempts")
        return False