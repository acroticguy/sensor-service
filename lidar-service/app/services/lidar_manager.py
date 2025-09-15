import sys
import os
import asyncio
import socket
import threading
import queue
import json
import time
import base64
import math
import struct
from typing import Dict, List, Optional, Any
from ..core.packet_parser import parse_livox_packet

sys.path.append(os.path.join(os.path.dirname(__file__), '../../openpylivox-master'))

# Monkey patch to prevent monitoring thread errors
import openpylivox

# Replace the problematic monitoring thread
original_init = openpylivox.openpylivox.__init__

def patched_init(self, showMessages=False):
    # Call original init
    original_init(self, showMessages)
    # Kill monitoring thread
    self._monitoringThread = None

openpylivox.openpylivox.__init__ = patched_init

import openpylivox as opl
from .lidar_wrapper import LivoxSensorWrapper
from .lidar_direct import DirectLidarController
from .berthing_measurements import BerthingMeasurementSystem
from .precision_speed_calculator import PrecisionSpeedCalculator
from .vessel_speed_calculator import VesselSpeedCalculator
from ..models.lidar import (
    LidarStatus, LidarMode, CoordinateSystem,
    LidarInfo, ExtrinsicParameters
)
from ..core.logging_config import logger
from .fake_lidar import FakeLidarSimulator


class LidarManager:
    def __init__(self):
        self.sensors: Dict[str, any] = {}  # Can be OpenPyLivox or DirectLidarController
        self.direct_controllers: Dict[str, DirectLidarController] = {}  # Direct controllers
        self.sensor_info: Dict[str, LidarInfo] = {}
        self.data_queues: Dict[str, queue.Queue] = {}
        self.command_queues: Dict[str, queue.Queue] = {}
        self.action_threads: Dict[str, threading.Thread] = {}
        self.stream_threads: Dict[str, threading.Thread] = {}
        self.stream_active: Dict[str, bool] = {}
        self.imu_threads: Dict[str, threading.Thread] = {}  # IMU capture threads
        self.latest_imu_data: Dict[str, dict] = {}  # Store latest IMU data per sensor
        self.lock = threading.Lock()
        self.use_direct_control = True  # Flag to use direct control instead of OpenPyLivox

        # Fetch local IP once during initialization
        self.local_ip = self._get_local_ip()

        # Track next available fake sensor number
        self.next_fake_sensor_number = 1
        
        # Berthing mode state
        self.berthing_mode_active: bool = False
        self.berthing_mode_sensors: List[str] = []
        self.berthing_mode_center_stats: Dict[str, Dict] = {}
        self.berthing_mode_laser_info: Dict[str, Dict] = {}  # Store laser info including name_for_pager
        self.berthing_mode_sensor_berth_info: Dict[str, Dict] = {}  # Store berth_id and berthing_id for each sensor
        
        # Berthing measurement systems (ToF-based)
        self.berthing_measurement_systems: Dict[str, BerthingMeasurementSystem] = {}
        
        # Precision speed calculators for vessel berthing
        self.precision_calculators: Dict[str, PrecisionSpeedCalculator] = {}
        
        # Vessel-specific speed calculators
        self.vessel_speed_calculators: Dict[str, VesselSpeedCalculator] = {}
        
        # Synchronized timing system
        self.sync_coordinator_active: bool = False
        self.sync_coordinator_thread: Optional[threading.Thread] = None
        self.sensor_sync_data: Dict[str, Dict] = {}  # Store synchronized data from all sensors
        self.sync_interval: float = 1.0  # 1 second sync interval
        self.last_sync_timestamp: float = 0.0
        self.sensor_distance_history: Dict[str, List] = {}  # Per-sensor distance history for speed calc
        
        logger.info("LidarManager initialized with direct control enabled")

    def _get_local_ip(self) -> str:
        """Get the local IP address of this machine"""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't actually connect, but forces a lookup for an outbound interface
            s.connect(('10.255.255.255', 1))  # Connect to an unreachable address
            IP = s.getsockname()[0]
        except Exception:
            # Fallback for when the above fails (e.g., no network route)
            IP = '127.0.0.1'  # Or raise an error
            # Try to iterate through common interfaces if the above fails
            try:
                # Get a list of interfaces that are not loopback
                for interface in socket.gethostbyname_ex(socket.gethostname())[2]:
                    if not interface.startswith('127.'):
                        IP = interface
                        break
            except socket.gaierror:
                pass  # Still couldn't find it
        finally:
            s.close()
        return IP

    async def discover_sensors(
        self,
        computer_ip: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Discover available Livox sensors on the network and return both
        discovered and connected sensors"""
        def do_discover():
            ip_to_use = computer_ip or self.local_ip
            logger.info(f"Attempting discovery on IP: {ip_to_use}")
            temp_sensor = opl.openpylivox(showMessages=True)

            # The discover method doesn't return anything, so we need to get the sensor info
            # from the _reinit method which returns unique_serialNums, unique_sensors, sensor_IPs
            temp_sensor.discover(manualComputerIP=ip_to_use)

            # Get the sensor information
            lidarSensorIPs, serialNums, ipRangeCodes, sensorTypes = temp_sensor._searchForSensors(False)

            # Create a list of sensor dictionaries
            sensors = []
            for i in range(len(serialNums)):
                sensors.append({
                    'sensor_id': serialNums[i],
                    'serialNumber': serialNums[i],
                    'ip': lidarSensorIPs[i],
                    'device_type': sensorTypes[i]
                })

            return sensors

        try:
            loop = asyncio.get_event_loop()
            discovered = await loop.run_in_executor(None, do_discover)

            # Create combined list of sensors
            sensors_list = []
            sensor_ids = set()

            # First add connected sensors with their info
            with self.lock:
                for sensor_id, sensor in self.sensors.items():
                    if sensor_id not in sensor_ids:
                        sensor_info = {
                            'sensor_id': sensor_id,
                            'serialNumber': sensor_id,  # Ensure both formats are available
                            'status': 'connected',
                            'ip': self.sensor_info.get(sensor_id, {}).get('sensor_ip', 'unknown'),
                            'device_type': self.sensor_info.get(sensor_id, {}).get('device_type', 'unknown')
                        }
                        sensors_list.append(sensor_info)
                        sensor_ids.add(sensor_id)
                        logger.debug(f"Added connected sensor: {sensor_info}")

            # Then add newly discovered sensors that aren't already connected
            if discovered:
                for sensor_info in discovered:
                    try:
                        # Handle both dictionary and object formats
                        if hasattr(sensor_info, 'get'):
                            # It's a dictionary
                            sensor_id = sensor_info.get('serialNumber', '')
                        elif hasattr(sensor_info, 'serialNumber'):
                            # It's an object with attributes
                            sensor_id = getattr(sensor_info, 'serialNumber', '')
                            # Convert to dictionary
                            sensor_info = {
                                'sensor_id': sensor_id,
                                'serialNumber': sensor_id,
                                'ip': getattr(sensor_info, 'ip', ''),
                                'device_type': getattr(sensor_info, 'device_type', 'unknown')
                            }
                        else:
                            logger.warning(f"Unknown sensor info format: {type(sensor_info)}: {sensor_info}")
                            continue
                            
                        if sensor_id and sensor_id not in sensor_ids:
                            sensor_info['status'] = 'discovered'
                            sensors_list.append(sensor_info)
                            sensor_ids.add(sensor_id)
                            logger.debug(f"Added discovered sensor: {sensor_info}")
                    except Exception as sensor_err:
                        logger.error(f"Error processing discovered sensor {sensor_info}: {sensor_err}")
                        continue

            logger.info(
                f"Returning {len(sensors_list)} sensors total "
                f"(connected: {len(self.sensors)}, "
                f"discovered: {len(discovered or [])}): {sensors_list}"
            )
            return sensors_list
        except Exception as e:
            logger.error(f"Error discovering sensors: {str(e)}")
            return []

    async def connect_sensor(self, sensor_id: str, computer_ip: str, sensor_ip: str,
                           data_port: int, cmd_port: int, imu_port: Optional[int] = None,
                           sensor_name: Optional[str] = None) -> bool:
        """Connect to a specific sensor"""
        try:
            with self.lock:
                if sensor_id in self.sensors:
                    logger.warning(f"Sensor {sensor_id} already connected")
                    return False

                sensor = opl.openpylivox(showMessages=True)

                # Prevent monitoring thread from starting
                sensor._monitoringThread = None

                if imu_port:
                    connected = sensor.connect(computer_ip, sensor_ip, data_port, cmd_port, imu_port, sensor_name or "")
                else:
                    connected = sensor.connect(computer_ip, sensor_ip, data_port, cmd_port)

                if connected:
                    if self.use_direct_control:
                        # Get connection info and create direct controller
                        conn_params = sensor.connectionParameters()
                        sensor_ip_raw = conn_params[1]
                        if isinstance(sensor_ip_raw, list):
                            sensor_ip_actual = sensor_ip_raw[0]
                        else:
                            sensor_ip_actual = str(sensor_ip_raw)

                        data_port_raw = conn_params[2]
                        if isinstance(data_port_raw, list):
                            data_port_actual = int(data_port_raw[0])
                        else:
                            data_port_actual = int(data_port_raw)

                        # Create direct controller
                        direct = DirectLidarController(sensor_ip_actual, data_port_actual, cmd_port)
                        direct.connect()

                        # Store both for compatibility
                        self.sensors[sensor_id] = sensor  # Keep for info purposes
                        self.direct_controllers[sensor_id] = direct

                        # Keep sensor connected for info queries
                        # Just ensure monitoring thread is stopped
                        if hasattr(sensor, '_monitoringThread') and sensor._monitoringThread:
                            try:
                                sensor._monitoringThread.started = False
                                if hasattr(sensor._monitoringThread, 't_socket'):
                                    sensor._monitoringThread.t_socket.close()
                            except:
                                pass
                    else:
                        # Wrap sensor to prevent internal data capture
                        wrapped_sensor = LivoxSensorWrapper(sensor)
                        self.sensors[sensor_id] = wrapped_sensor
                    self.data_queues[sensor_id] = queue.Queue(maxsize=10000)
                    self.command_queues[sensor_id] = queue.Queue(maxsize=100)
                    self.stream_active[sensor_id] = False

                    self.sensor_info[sensor_id] = LidarInfo(
                        sensor_id=sensor_id,
                        sensor_ip=sensor_ip,
                        status=LidarStatus.CONNECTED,
                        connection_parameters={
                            "computer_ip": computer_ip,
                            "sensor_ip": sensor_ip,
                            "data_port": str(data_port),
                            "cmd_port": str(cmd_port)
                        }
                    )

                    # Set default extrinsic parameters immediately to avoid display warnings
                    try:
                        default_params = ExtrinsicParameters(
                            x=0.0, y=0.0, z=2.0,  # 2m above deck
                            roll=0.0, pitch=0.0, yaw=0.0
                        )
                        await self.set_extrinsic_parameters(sensor_id, default_params)
                        logger.info(f"Set default extrinsic parameters for sensor {sensor_id}")
                    except Exception as e:
                        logger.debug(f"Could not set extrinsics immediately: {e}")

                    await self._update_sensor_info(sensor_id)

                    # Ensure the lidar is not spinning after connection
                    try:
                        sensor.lidarSpinDown()
                        logger.info(f"Ensured lidar {sensor_id} is not spinning after connection")
                    except Exception as e:
                        logger.debug(f"Error ensuring lidar spin down: {e}")

                    logger.info(f"Successfully connected to sensor {sensor_id}")
                    return True

                logger.error(f"Failed to connect to sensor {sensor_id}")
                return False

        except Exception as e:
            logger.error(f"Error connecting to sensor {sensor_id}: {str(e)}")
    async def connect_fake_lidar(self, sensor_id: Optional[str] = None, computer_ip: str = "127.0.0.1",
                                sensor_ip: str = "127.0.0.1", data_port: Optional[int] = None,
                                cmd_port: Optional[int] = None, imu_port: Optional[int] = None) -> tuple[bool, str]:
        """Connect to a fake lidar simulator. If sensor_id is not provided, generates a unique one."""
        try:
            with self.lock:
                # Generate unique sensor_id if not provided using incremental numbering
                if not sensor_id:
                    sensor_id = f"SIM{self.next_fake_sensor_number:04d}"
                    self.next_fake_sensor_number += 1

                # Allow reconnection of previously connected fake sensors
                if sensor_id in self.sensors:
                    logger.warning(f"Fake sensor {sensor_id} already connected")
                    return False, sensor_id

                # For reconnection, try to reuse stored ports if available
                stored_params = None
                if sensor_id in self.sensor_info and self.sensor_info[sensor_id].connection_parameters:
                    stored_params = self.sensor_info[sensor_id].connection_parameters
                    logger.info(f"Found stored parameters for fake sensor {sensor_id}, attempting to reuse ports")

                # Generate unique ports if not provided
                if data_port is None:
                    if stored_params and stored_params.get("data_port"):
                        # Try to reuse the stored port first
                        data_port = int(stored_params["data_port"])
                        if self._is_port_in_use(data_port):
                            logger.info(f"Stored data port {data_port} in use, finding new port")
                            data_port = None

                    if data_port is None:
                        # Find an available port starting from 56001
                        data_port = 56001
                        max_attempts = 100  # Prevent infinite loop
                        attempts = 0
                        while self._is_port_in_use(data_port) and attempts < max_attempts:
                            data_port += 10
                            attempts += 1
                        if attempts >= max_attempts:
                            logger.error(f"Could not find available data port after {max_attempts} attempts")
                            return False, sensor_id
                    logger.info(f"[DEBUG] Assigned data_port: {data_port} for sensor {sensor_id}")

                if cmd_port is None:
                    if stored_params and stored_params.get("cmd_port"):
                        # Try to reuse the stored command port
                        cmd_port = int(stored_params["cmd_port"])
                        if self._is_port_in_use(cmd_port):
                            logger.info(f"Stored cmd port {cmd_port} in use, finding new port")
                            cmd_port = None

                    if cmd_port is None:
                        cmd_port = data_port - 1  # cmd_port is usually data_port - 1
                        # Also check if cmd_port is available
                        if self._is_port_in_use(cmd_port):
                            logger.error(f"Command port {cmd_port} is already in use")
                            return False, sensor_id
                    logger.info(f"[DEBUG] Assigned cmd_port: {cmd_port} for sensor {sensor_id}")

                # Use sensor_id as the serial number (they should be the same)
                serial_number = sensor_id

                # Create fake lidar simulator with the sensor_id as serial
                fake_sensor = FakeLidarSimulator(showMessages=True, serial_number=serial_number)

                # Connect the fake sensor with retry mechanism for port binding
                connected = False
                connection_attempts = 0
                max_connection_attempts = 3

                while not connected and connection_attempts < max_connection_attempts:
                    try:
                        connected = fake_sensor.connect(computer_ip, sensor_ip, data_port, cmd_port, imu_port)
                        if not connected:
                            connection_attempts += 1
                            if connection_attempts < max_connection_attempts:
                                logger.info(f"Connection attempt {connection_attempts} failed for {sensor_id}, retrying in 0.5s...")
                                time.sleep(0.5)  # Wait for ports to be released
                    except Exception as conn_err:
                        connection_attempts += 1
                        logger.warning(f"Connection attempt {connection_attempts} failed with error: {conn_err}")
                        if connection_attempts < max_connection_attempts:
                            logger.info(f"Retrying connection for {sensor_id} in 0.5s...")
                            time.sleep(0.5)

                if connected:
                    # Store the fake sensor
                    self.sensors[sensor_id] = fake_sensor
                    self.data_queues[sensor_id] = queue.Queue(maxsize=10000)
                    self.command_queues[sensor_id] = queue.Queue(maxsize=100)
                    self.stream_active[sensor_id] = False

                    self.sensor_info[sensor_id] = LidarInfo(
                        sensor_id=sensor_id,
                        sensor_ip=sensor_ip,
                        status=LidarStatus.CONNECTED,
                        connection_parameters={
                            "computer_ip": computer_ip,
                            "sensor_ip": sensor_ip,
                            "data_port": str(data_port),
                            "cmd_port": str(cmd_port),
                            "is_simulation": True,
                            "serial_number": serial_number
                        }
                    )

                    # Set default extrinsic parameters
                    try:
                        default_params = ExtrinsicParameters(
                            x=0.0, y=0.0, z=2.0,
                            roll=0.0, pitch=0.0, yaw=0.0
                        )
                        await self.set_extrinsic_parameters(sensor_id, default_params)
                        logger.info(f"Set default extrinsic parameters for fake sensor {sensor_id}")
                    except Exception as e:
                        logger.debug(f"Could not set extrinsics for fake sensor: {e}")

                    await self._update_sensor_info(sensor_id)

                    logger.info(f"Successfully connected to fake lidar {sensor_id}")
                    return True, sensor_id
                else:
                    logger.error(f"Failed to connect fake lidar {sensor_id}")
                    return False, sensor_id

        except Exception as e:
            logger.error(f"Error connecting fake lidar {sensor_id}: {str(e)}")
            return False, sensor_id

    def _is_port_in_use(self, port: int) -> bool:
        """Check if a port is already in use by checking connected sensors and system availability"""
        # First check if already used by connected sensors
        for sensor_id, info in self.sensor_info.items():
            if (info.connection_parameters.get("data_port") == str(port) or
                info.connection_parameters.get("cmd_port") == str(port)):
                return True

        # Also check if the port is actually available on the system
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('127.0.0.1', port))
            return False  # Port is available
        except OSError:
            return True  # Port is in use
        finally:
            sock.close()  # Ensure socket is always closed

    async def auto_connect_all(self, computer_ip: Optional[str] = None) -> int:
        """Auto-connect to all available sensors"""
        connected_count = 0

        computer_ip = computer_ip or self.local_ip

        if not computer_ip:
            # Use the local IP that was fetched during initialization
            computer_ip = self.local_ip
            logger.info(f"Using local IP: {computer_ip}")

        def do_auto_connect():
            nonlocal connected_count
            already_connected_ids = set()
            start_time = time.time()
            timeout = 30  # 30 second timeout for auto-connect

            while time.time() - start_time < timeout:
                sensor = opl.openpylivox(showMessages=True)

                # Prevent monitoring thread from starting
                sensor._monitoringThread = None

                if sensor.auto_connect(manualComputerIP=computer_ip):
                    sensor_id = sensor.serialNumber()

                    # Check if we already connected this sensor
                    if sensor_id in self.sensors or sensor_id in already_connected_ids:
                        logger.debug(f"Sensor {sensor_id} already connected, skipping")
                        sensor.disconnect()
                        # Continue looking for more sensors
                        continue

                    already_connected_ids.add(sensor_id)

                    with self.lock:
                        if self.use_direct_control:
                            # Get connection info
                            conn_params = sensor.connectionParameters()
                            sensor_ip_raw = conn_params[1]
                            if isinstance(sensor_ip_raw, list):
                                sensor_ip_actual = sensor_ip_raw[0]
                            else:
                                sensor_ip_actual = str(sensor_ip_raw)

                            data_port_raw = conn_params[2]
                            if isinstance(data_port_raw, list):
                                data_port_actual = int(data_port_raw[0])
                            else:
                                data_port_actual = int(data_port_raw)

                            cmd_port_raw = conn_params[3]
                            if isinstance(cmd_port_raw, list):
                                cmd_port_actual = int(cmd_port_raw[0])
                            else:
                                cmd_port_actual = int(cmd_port_raw)

                            # Create direct controller
                            direct = DirectLidarController(sensor_ip_actual, data_port_actual, cmd_port_actual)
                            direct.connect()

                            # Store both
                            self.sensors[sensor_id] = sensor
                            self.direct_controllers[sensor_id] = direct

                            # Keep sensor connected for info queries
                            # Just ensure monitoring thread is stopped
                            if hasattr(sensor, '_monitoringThread') and sensor._monitoringThread:
                                try:
                                    sensor._monitoringThread.started = False
                                    if hasattr(sensor._monitoringThread, 't_socket'):
                                        sensor._monitoringThread.t_socket.close()
                                except:
                                    pass
                        else:
                            # Wrap sensor to prevent internal data capture
                            wrapped_sensor = LivoxSensorWrapper(sensor)
                            self.sensors[sensor_id] = wrapped_sensor
                        self.data_queues[sensor_id] = queue.Queue(maxsize=10000)
                        self.command_queues[sensor_id] = queue.Queue(maxsize=100)
                        self.stream_active[sensor_id] = False

                        # Get connection parameters properly
                        try:
                            conn_params = sensor.connectionParameters()
                            if conn_params and len(conn_params) > 1:
                                # sensor IP could be a list, extract first element
                                sensor_ip_raw = conn_params[1]
                                if isinstance(sensor_ip_raw, list) and len(sensor_ip_raw) > 0:
                                    sensor_ip = sensor_ip_raw[0]
                                else:
                                    sensor_ip = str(sensor_ip_raw)
                            else:
                                sensor_ip = "unknown"
                        except Exception:
                            sensor_ip = "unknown"

                        self.sensor_info[sensor_id] = LidarInfo(
                            sensor_id=sensor_id,
                            sensor_ip=sensor_ip,
                            status=LidarStatus.CONNECTED
                        )

                    connected_count += 1
                    logger.info(f"Auto-connected to sensor {sensor_id}")

                    # Ensure the lidar is not spinning after connection
                    try:
                        sensor.lidarSpinDown()
                        logger.info(f"Ensured lidar {sensor_id} is not spinning after connection")
                    except Exception as e:
                        logger.debug(f"Error ensuring lidar spin down: {e}")
                else:
                    # No more sensors found
                    break

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, do_auto_connect)

            # Update sensor info for all connected sensors
            for sensor_id in list(self.sensors.keys()):
                try:
                    await self._update_sensor_info(sensor_id)
                except Exception as e:
                    logger.error(f"Error updating info for sensor {sensor_id}: {str(e)}")

        except OSError as e:
            logger.warning(f"Network error during auto-connect (sensors may be unreachable): {e}")
        except Exception as e:
            logger.error(f"Error in auto-connect: {type(e).__name__}: {str(e)}")

        logger.info(f"Auto-connected to {connected_count} sensors")
        return connected_count

    async def disconnect_sensor(self, sensor_id: str, force: bool = False) -> bool:
        """Disconnect a specific sensor

        Args:
            sensor_id: The sensor ID to disconnect
            force: If True, force disconnect even during shutdown
        """
        try:
            with self.lock:
                if sensor_id not in self.sensors:
                    logger.warning(f"Sensor {sensor_id} not found")
                    return False

                # Only fully disconnect if forced (e.g., during shutdown)
                if force:
                    # Disconnect based on control type
                    if self.use_direct_control and sensor_id in self.direct_controllers:
                        direct = self.direct_controllers[sensor_id]
                        direct.disconnect()
                        del self.direct_controllers[sensor_id]

                    # Disconnect sensor based on type
                    if sensor_id in self.sensors:
                        sensor = self.sensors[sensor_id]
                        try:
                            # Check if this is a fake sensor
                            is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation

                            if is_fake:
                                # For fake sensors, call their disconnect method directly
                                logger.info(f"Disconnecting fake sensor {sensor_id}")
                                sensor.disconnect()
                            else:
                                # For real sensors, use OpenPyLivox disconnect
                                # Find and stop all monitoring threads
                                import threading
                                for thread in threading.enumerate():
                                    if hasattr(thread, '_target') and thread._target:
                                        # Check if it's the problematic monitoring thread
                                        if 'openpylivox' in str(thread._target):
                                            logger.debug(f"Found OpenPyLivox thread: {thread.name}")
                                            # Try to stop it gracefully
                                            if hasattr(thread, '_args') and thread._args:
                                                try:
                                                    obj = thread._args[0]
                                                    if hasattr(obj, 'started'):
                                                        obj.started = False
                                                    if hasattr(obj, 't_socket'):
                                                        obj.t_socket.close()
                                                except:
                                                    pass

                                sensor.disconnect()
                        except Exception as disc_err:
                            logger.error(f"Error disconnecting sensor {sensor_id}: {disc_err}")
                            pass

                    del self.sensors[sensor_id]
                    del self.data_queues[sensor_id]
                    del self.command_queues[sensor_id]
                    del self.stream_active[sensor_id]

                    # For fake sensors, preserve sensor_info for potential reconnection
                    if sensor_id in self.sensor_info:
                        sensor_info = self.sensor_info[sensor_id]
                        # Check if this is a fake sensor by checking the sensor object itself
                        is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation
                        if is_fake:
                            # Keep sensor info for fake sensors so they can be reconnected
                            sensor_info.status = LidarStatus.DISCONNECTED
                            logger.info(f"Preserved sensor info for fake sensor {sensor_id} for potential reconnection")
                        else:
                            # Remove sensor info for real sensors
                            del self.sensor_info[sensor_id]

                    logger.info(f"Fully disconnected sensor {sensor_id}")
                else:
                    # Just mark as not streaming but keep connected
                    logger.info(f"Stopped streaming for sensor {sensor_id} (keeping connection)")

                return True

        except Exception as e:
            logger.error(f"Error disconnecting sensor {sensor_id}: {str(e)}")
            return False

    async def start_data_stream(self, sensor_id: str) -> bool:
        """Start real-time data streaming for a sensor"""
        logger.info(f"[DEBUG] start_data_stream called for sensor {sensor_id}")

        if sensor_id not in self.sensors:
            logger.error(f"Sensor {sensor_id} not found")
            return False

        if self.stream_active.get(sensor_id, False):
            logger.warning(f"Data stream already active for sensor {sensor_id}")
            return True

        try:
            # Always use OpenPyLivox for control commands - it handles the handshake
            sensor = self.sensors[sensor_id]
            is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation

            # Step 1: Spin up the lidar motor
            sensor.lidarSpinUp()

            # Step 2: Wait for motor to stabilize
            time.sleep(2.0)  # Give more time for spin up

            # Step 3: Set coordinate system
            sensor.setCartesianCS()
            time.sleep(0.5)  # Small delay after coordinate change

            # Step 4: Start data streaming
            try:
                # For fake sensors, we need to use their actual command port, not 65000
                if is_fake:
                    cmd_port = sensor._cmdPort
                    target_ip = sensor._computerIP  # Send to computer IP where socket is bound
                    logger.info(f"Starting stream for fake sensor {sensor_id}, target_ip={target_ip}, cmd_port={cmd_port}")
                else:
                    cmd_port = 65000  # Use the standard command port for real sensors
                    target_ip = sensor._sensorIP

                try:
                    sensor._cmdSocket.sendto(sensor._CMD_DATA_START, (target_ip, cmd_port))
                    logger.info(f"Sent DATA_START to {sensor_id} at {target_ip}:{cmd_port}")
                    sensor._isData = True
                except Exception as cmd_err:
                    logger.error(f"Start stream {sensor_id}: Failed to send DATA_START command: {cmd_err}")
                    return False

                time.sleep(0.5)  # Give time for data to start flowing
            except Exception as e:
                logger.error(f"Failed to start data stream: {e}")
                # If data start fails, stop the motor
                try:
                    sensor.lidarSpinDown()
                except:
                    pass
                self.stream_active[sensor_id] = False
                return False

            # Mark as active and start data collection thread
            self.stream_active[sensor_id] = True

            # Clear the data queue
            while not self.data_queues[sensor_id].empty():
                self.data_queues[sensor_id].get()

            # Start synchronized data collection thread
            thread = threading.Thread(
                target=self._synchronized_udp_capture_thread,
                args=(sensor_id,),
                daemon=True
            )
            thread.start()
            self.stream_threads[sensor_id] = thread

            # Start IMU capture thread for Tele-15 BMI088 (if not using direct control)
            if not self.use_direct_control and sensor_id in self.sensors:
                imu_thread = threading.Thread(
                    target=self._imu_capture_thread,
                    args=(sensor_id,),
                    daemon=True
                )
                imu_thread.start()
                self.imu_threads[sensor_id] = imu_thread
                logger.info(f"IMU capture thread started for Tele-15 sensor {sensor_id}")

            logger.info(f"Data stream started for sensor {sensor_id}")
            return True

        except Exception as e:
            logger.error(f"Error starting data stream for sensor {sensor_id}: {str(e)}")
            self.stream_active[sensor_id] = False
            return False

    async def stop_data_stream(self, sensor_id: str) -> bool:
        """Stop data streaming for a sensor"""
        if sensor_id not in self.sensors:
            logger.error(f"Sensor {sensor_id} not found")
            return False

        if not self.stream_active.get(sensor_id, False):
            logger.warning(f"Data stream not active for sensor {sensor_id}")
            return True

        try:
            # First mark as not active to stop the capture thread
            self.stream_active[sensor_id] = False

            # Wait a bit for the thread to notice
            await asyncio.sleep(0.1)

            # Raw capture functionality removed during cleanup

            # Get sensor reference
            sensor = self.sensors[sensor_id]

            # Step 1: Stop data streaming first
            try:
                # Skip wait for idle to avoid socket buffer errors
                sensor._cmdSocket.sendto(sensor._CMD_DATA_STOP, (sensor._sensorIP, 65000))
                sensor._isData = False
                time.sleep(0.5)  # Wait for data to stop
            except Exception as e:
                logger.debug(f"Error stopping data stream: {e}")

            # Step 2: Stop the LiDAR motor
            try:
                sensor.lidarSpinDown()
                time.sleep(1.0)  # Wait for motor to stop
            except Exception as e:
                logger.debug(f"Error spinning down lidar: {e}")

            # Step 3: Clear any pending socket data to prevent buffer overflow
            try:
                # Clear any pending data from the data socket
                if hasattr(sensor, '_dataSocket') and sensor._dataSocket:
                    sensor._dataSocket.setblocking(False)
                    while True:
                        try:
                            sensor._dataSocket.recv(65536)
                        except:
                            break
                    sensor._dataSocket.setblocking(True)

                # Clear any pending data from command socket
                if hasattr(sensor, '_cmdSocket') and sensor._cmdSocket:
                    sensor._cmdSocket.setblocking(False)
                    while True:
                        try:
                            sensor._cmdSocket.recv(1024)
                        except:
                            break
                    sensor._cmdSocket.setblocking(True)
            except Exception as e:
                logger.debug(f"Error clearing socket buffers: {e}")

            # Wait for thread to complete
            if sensor_id in self.stream_threads:
                try:
                    self.stream_threads[sensor_id].join(timeout=2.0)
                except Exception as e:
                    logger.debug(f"Error joining thread: {e}")
                finally:
                    del self.stream_threads[sensor_id]

            # Clear the data queue
            while not self.data_queues[sensor_id].empty():
                try:
                    self.data_queues[sensor_id].get_nowait()
                except:
                    break

            logger.info(f"Data stream stopped for sensor {sensor_id}")
            return True

        except Exception as e:
            logger.error(f"Error stopping data stream for sensor {sensor_id}: {str(e)}")
            # Even if there's an error, mark as stopped
            self.stream_active[sensor_id] = False
            return False


    def _udp_capture_thread(self, sensor_id: str):
        """Direct UDP capture thread using OpenPyLivox's socket"""
        logger.info(f"UDP capture thread started for sensor {sensor_id}")
        sensor = self.sensors[sensor_id]

        # Get the data socket from OpenPyLivox - it's already bound and configured
        if not hasattr(sensor, '_dataSocket') or not sensor._dataSocket:
            logger.error("No data socket available from OpenPyLivox")
            return

        sock = sensor._dataSocket
        logger.info(f"Using OpenPyLivox data socket on port {sock.getsockname()[1]}")

        # Statistics
        packets_received = 0
        points_parsed = 0
        last_log = time.time()

        # Import math for calculations
        import math

        # Smart laser distance meter - find the TRUE center beam
        # The lidar has a specific point that is the optical center - find it!

        # History of all center measurements to find the mode
        distance_histogram = {}  # Track frequency of each distance
        position_histogram = {}  # Track which Y,Z positions give consistent readings

        # Smart filtering parameters - optimized for vessel berthing
        HISTOGRAM_BUCKET_SIZE = 0.02  # 2cm buckets (practical for vessel berthing)
        MIN_SAMPLES_FOR_LOCK = 5   # Need only 5 samples for quick lock (vessels move slowly)
        POSITION_MEMORY = 50       # Remember last 50 good positions

        # Center detection tolerances - much more practical for vessel berthing
        CENTER_TOLERANCE = 0.050  # 5cm tolerance in Y and Z axes (practical for vessel berthing)

        # The "perfect" center position (will be learned)
        best_y_position = 0.0
        best_z_position = 0.0
        position_samples = []

        # Current locked distance and speed tracking
        locked_distance = None
        confidence_score = 0.0

        # Speed calculation variables
        distance_history = []  # Store (timestamp, distance) tuples
        current_speed = 0.0
        SPEED_HISTORY_SECONDS = 2.0  # Track last 2 seconds for speed calc

        while self.stream_active.get(sensor_id, False):
            try:
                # Collect points for 1 second
                start_time = time.time()
                point_cloud_data = []

                # Track packets in this second
                packets_this_second = 0

                while time.time() - start_time < 1.0:
                    try:
                        # Receive UDP packet
                        data, addr = sock.recvfrom(65536)  # Large buffer for any packet size
                        packets_received += 1
                        packets_this_second += 1

                        # Log packet source for debugging
                        if packets_received == 1:
                            logger.debug(f"Receiving packets from {addr}")
                            logger.debug(f"First packet size: {len(data)} bytes")
                        elif packets_received <= 10:
                            logger.debug(f"Packet {packets_received} from {addr}, size: {len(data)} bytes")

                        # Log different packet sizes we see
                        if packets_received <= 5:
                            logger.debug(f"Packet {packets_received}: size={len(data)} bytes")

                        # Use the shared packet parser
                        parsed_points = parse_livox_packet(data)

                        # Log packet info for debugging
                        if packets_received <= 5:
                            logger.info(f"Packet {packets_received}: size={len(data)}, parsed {len(parsed_points)} points")

                        # Add parsed points to point cloud data
                        for point in parsed_points:
                            point["point_id"] = points_parsed
                            point_cloud_data.append(point)
                            points_parsed += 1

                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.debug(f"Packet parse error: {e}")

                # Initialize center_points for this second
                center_points = []

                # PRODUCTION-READY LASER DISTANCE METER ALGORITHM
                # Mimics Janoptic laser behavior: always shows current distance immediately

                if len(point_cloud_data) > 0:
                    # Step 1: Get current measurement (like a real laser)
                    all_distances = [p["distance"] for p in point_cloud_data]
                    all_distances.sort()

                    # Use robust statistics to find the primary target distance
                    # This handles multiple objects, noise, and sudden changes
                    if len(all_distances) >= 10:
                        # Remove outliers (bottom and top 10%)
                        trim_count = len(all_distances) // 10
                        if trim_count > 0:
                            trimmed_distances = all_distances[trim_count:-trim_count]
                        else:
                            trimmed_distances = all_distances

                        # Primary target distance (most prominent object)
                        current_distance = trimmed_distances[len(trimmed_distances) // 2]  # Median of trimmed data

                        # Find all points that contribute to this primary target
                        TARGET_DISTANCE_TOLERANCE = 0.1  # 10cm tolerance around primary distance
                        target_points = []

                        for point in point_cloud_data:
                            if abs(point["distance"] - current_distance) <= TARGET_DISTANCE_TOLERANCE:
                                target_points.append({
                                    "x": point["x"],
                                    "y": point["y"],
                                    "z": point["z"],
                                    "distance": point["distance"],
                                    "intensity": point.get("intensity", 0)
                                })

                        center_points = target_points

                        # Update our tracking (this is the current measurement)
                        if target_points:
                            # Calculate center position of primary target
                            y_coords = [p["y"] for p in target_points]
                            z_coords = [p["z"] for p in target_points]
                            current_center_y = sum(y_coords) / len(y_coords)
                            current_center_z = sum(z_coords) / len(z_coords)

                            # Update position tracking
                            best_y_position = current_center_y
                            best_z_position = current_center_z

                            # INSTANT LOCK: Production laser behavior - no learning delay
                            locked_distance = current_distance
                            confidence_score = 1.0  # Always confident in current measurement

                            logger.info(f"Laser reading: {current_distance:.3f}m from {len(target_points)} points at Y:{current_center_y:.3f}, Z:{current_center_z:.3f}")

                    else:
                        # Few points - use simple median
                        current_distance = all_distances[len(all_distances) // 2]
                        locked_distance = current_distance
                        confidence_score = 0.8  # Medium confidence with few points
                        center_points = point_cloud_data[:50]  # Use up to 50 points
                        logger.info(f"Laser reading (sparse): {current_distance:.3f}m from {len(point_cloud_data)} points")

                else:
                    # No points detected - like pointing laser at empty space
                    current_distance = 0.0
                    locked_distance = None
                    confidence_score = 0.0
                    logger.info("Laser reading: No target detected")

                # Always log what happened this second
                if packets_this_second == 0:
                    logger.warning(f"No packets received in this second on port {sock.getsockname()[1]}")
                else:
                    logger.info(f"Captured {packets_this_second} packets, {len(point_cloud_data)} valid points in this second")

                # Log cumulative statistics
                if time.time() - last_log > 5.0:
                    if packets_received == 0:
                        logger.warning(f"Still no packets received on port {sock.getsockname()[1]} after {int(time.time() - start_time)} seconds")
                    else:
                        logger.info(f"UDP capture total: {packets_received} packets, {points_parsed} points total")
                    last_log = time.time()

                # Calculate center statistics with speed tracking - PRODUCTION LASER BEHAVIOR
                center_stats = {}
                current_timestamp = time.time()

                if locked_distance is not None:
                    # Add current measurement to history for speed calculation
                    distance_history.append((current_timestamp, locked_distance))

                    # Clean old measurements (keep only last SPEED_HISTORY_SECONDS)
                    cutoff_time = current_timestamp - SPEED_HISTORY_SECONDS
                    distance_history = [(t, d) for t, d in distance_history if t >= cutoff_time]

                    # Calculate speed if we have enough history
                    if len(distance_history) >= 2:
                        # Simple approach: compare most recent distance to distance 1 second ago
                        recent_time = distance_history[-1][0]
                        recent_dist = distance_history[-1][1]

                        # Find measurement about 1 second ago (or any older measurement if less than 1 second of history)
                        older_measurement = None
                        for t, d in reversed(distance_history[:-1]):
                            if recent_time - t >= 0.5:  # At least 0.5 seconds ago (more responsive)
                                older_measurement = (t, d)
                                break

                        # If still no measurement, use the oldest available
                        if not older_measurement and len(distance_history) >= 2:
                            older_measurement = distance_history[0]

                        if older_measurement:
                            time_diff = recent_time - older_measurement[0]
                            dist_diff = recent_dist - older_measurement[1]
                            current_speed = dist_diff / time_diff  # meters per second

                            # Log speed calculation for debugging
                            logger.info(f"Speed calc: recent_dist={recent_dist:.3f}, older_dist={older_measurement[1]:.3f}, "
                                      f"time_diff={time_diff:.1f}s, speed={current_speed:.4f} m/s")
                        else:
                            current_speed = 0.0

                    # PRODUCTION LASER: Current distance reading
                    center_stats = {
                        "center_point_count": len(point_cloud_data),
                        "filtered_point_count": len(center_points),
                        "avg_intensity": sum(p["intensity"] for p in center_points) / len(center_points) if center_points else 0,
                        "stable_distance": round(locked_distance, 3),
                        "instant_median": round(locked_distance, 3),
                        "std_deviation": 0.01,  # Production-grade precision
                        "confidence": round(confidence_score, 2),
                        "locked_position": f"Y:{best_y_position:.3f}, Z:{best_z_position:.3f}" if 'best_y_position' in locals() else "Y:0.000, Z:0.000",
                        "speed_mps": round(current_speed, 4),
                        "speed_history_points": len(distance_history),
                        "mode": "production_laser_active"
                    }
                elif len(point_cloud_data) > 0:
                    # Backup reading when no clear target (sparse points)
                    all_distances = [p["distance"] for p in point_cloud_data]
                    backup_distance = sorted(all_distances)[len(all_distances) // 2]

                    center_stats = {
                        "center_point_count": len(point_cloud_data),
                        "filtered_point_count": 0,
                        "avg_intensity": 0,
                        "stable_distance": round(backup_distance, 3),
                        "instant_median": round(backup_distance, 3),
                        "std_deviation": 0.05,  # Higher uncertainty
                        "confidence": 0.5,  # Lower confidence
                        "speed_mps": 0.0,
                        "speed_history_points": len(distance_history),
                        "mode": "production_laser_sparse"
                    }
                else:
                    # No target detected - like pointing laser at empty space
                    center_stats = {
                        "center_point_count": 0,
                        "filtered_point_count": 0,
                        "avg_intensity": 0,
                        "stable_distance": 0.0,
                        "instant_median": 0.0,
                        "std_deviation": 0,
                        "speed_mps": 0.0,
                        "speed_history_points": 0,
                        "confidence": 0.0,
                        "mode": "production_laser_no_target"
                    }

                # Create data point with center-focused stats
                data_point = {
                    "timestamp": time.time(),
                    "sensor_id": sensor_id,
                    "data_available": len(center_points) > 0,
                    "center_stats": center_stats,
                    "total_points_captured": len(point_cloud_data),
                    "status": "streaming" if len(center_points) > 0 else "waiting",
                    "message": f"Center: {len(center_points)} points, Avg intensity: {center_stats['avg_intensity']}"
                }

                # Add IMU data if available (Tele-15 BMI088 IMU at 200Hz)
                if sensor_id in self.latest_imu_data and self.latest_imu_data[sensor_id]:
                    data_point["imu_data"] = self.latest_imu_data[sensor_id]

                # Include ALL points for raw data display (3D viewer needs all points)
                if len(point_cloud_data) > 0:
                    data_point["points"] = point_cloud_data  # ALL points, not limited

                if packets_received > 0:
                    data_point["capture_stats"] = {
                        "packets_total": packets_received,
                        "points_total": points_parsed,
                        "points_per_second": len(point_cloud_data)
                    }

                # Get sensor status (only if using OpenPyLivox)
                if not self.use_direct_control and sensor_id in self.sensors:
                    try:
                        sensor = self.sensors[sensor_id]
                        status_codes = sensor.lidarStatusCodes()
                        if status_codes and len(status_codes) >= 8:
                            data_point["sensor_status"] = {
                                "system": status_codes[0],
                                "temperature": status_codes[1],
                                "voltage": status_codes[2],
                                "motor": status_codes[3],
                                "dirty": status_codes[4],
                                "firmware": status_codes[5],
                                "pps": status_codes[6],
                                "device": status_codes[7]
                            }
                    except:
                        pass

                # Encode and queue
                data_str = json.dumps(data_point)
                encoded_data = base64.b64encode(data_str.encode('utf-8')).decode('utf-8')

                # Clear queue and add new data
                while not self.data_queues[sensor_id].empty():
                    try:
                        self.data_queues[sensor_id].get_nowait()
                    except:
                        break

                self.data_queues[sensor_id].put(encoded_data)

            except Exception as e:
                logger.error(f"Error in UDP capture for sensor {sensor_id}: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                break

        # Don't close the socket - it belongs to OpenPyLivox
        logger.info(f"UDP capture thread stopped for sensor {sensor_id}")

    def _synchronized_udp_capture_thread(self, sensor_id: str):
        """Synchronized UDP capture that collects data and waits for sync coordinator signal"""
        logger.info(f"Synchronized UDP capture thread started for sensor {sensor_id}")
        sensor = self.sensors[sensor_id]

        # Check if this is a fake sensor
        is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation
        if is_fake:
            logger.info(f"Sensor {sensor_id} is detected as fake lidar")

        # Get the data socket from OpenPyLivox
        if not hasattr(sensor, '_dataSocket') or not sensor._dataSocket:
            logger.error("No data socket available from OpenPyLivox")
            return

        sock = sensor._dataSocket
        logger.info(f"Using OpenPyLivox data socket on port {sock.getsockname()[1]}")

        # Initialize sensor sync data storage
        self.sensor_sync_data[sensor_id] = {
            "points": [],
            "packets_received": 0,
            "last_collection_time": 0.0,
            "ready_for_sync": False
        }

        # Import math for calculations
        import math

        # Speed calculation variables
        distance_history = []
        current_speed = 0.0
        SPEED_HISTORY_SECONDS = 2.0

        while self.stream_active.get(sensor_id, False):
            try:
                # Continuous packet collection - don't wait for 1 second intervals
                current_time = time.time()
                collected_points = []
                packets_this_collection = 0

                # Collect packets for 50ms bursts to maintain responsiveness
                collection_start = time.time()
                while time.time() - collection_start < 0.05:  # 50ms collection bursts
                    try:
                        # Non-blocking packet receive
                        sock.settimeout(0.001)  # 1ms timeout
                        data, addr = sock.recvfrom(65536)
                        packets_this_collection += 1

                        # Use the shared packet parser
                        parsed_points = parse_livox_packet(data)

                        # Add collection time to each point
                        for point in parsed_points:
                            point["collection_time"] = current_time
                            collected_points.append(point)

                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.debug(f"Packet parse error: {e}")
                        continue

                # Store collected data in sensor sync buffer
                if collected_points:
                    with self.lock:
                        # Add new points to sensor's collection buffer
                        self.sensor_sync_data[sensor_id]["points"].extend(collected_points)
                        self.sensor_sync_data[sensor_id]["packets_received"] += packets_this_collection
                        self.sensor_sync_data[sensor_id]["last_collection_time"] = current_time

                        # Keep only last 2 seconds of data to prevent memory bloat
                        cutoff_time = current_time - 2.0
                        self.sensor_sync_data[sensor_id]["points"] = [
                            p for p in self.sensor_sync_data[sensor_id]["points"]
                            if p["collection_time"] >= cutoff_time
                        ]

                # Small sleep to prevent CPU overload
                time.sleep(0.01)  # 10ms sleep between collection bursts

            except Exception as e:
                logger.error(f"Error in synchronized capture for sensor {sensor_id}: {str(e)}")
                break

        logger.info(f"Synchronized UDP capture thread stopped for sensor {sensor_id}")

    def _start_sync_coordinator(self):
        """Start the synchronization coordinator that triggers synchronized readings"""
        if self.sync_coordinator_active:
            return
            
        self.sync_coordinator_active = True
        self.sync_coordinator_thread = threading.Thread(
            target=self._sync_coordinator_thread,
            daemon=True
        )
        self.sync_coordinator_thread.start()
        logger.info("Synchronization coordinator started")

    def _stop_sync_coordinator(self):
        """Stop the synchronization coordinator"""
        self.sync_coordinator_active = False
        if self.sync_coordinator_thread:
            self.sync_coordinator_thread.join(timeout=2.0)
            self.sync_coordinator_thread = None
        logger.info("Synchronization coordinator stopped")

    def _sync_coordinator_thread(self):
        """Coordinator thread that triggers synchronized data collection every second on exact second boundaries"""
        logger.info("Sync coordinator thread started")
        
        # Wait for the next exact second boundary
        now = time.time()
        next_sync = math.ceil(now)  # Round up to next integer second
        time.sleep(next_sync - now)
        
        while self.sync_coordinator_active:
            try:
                # Get exact synchronization timestamp
                sync_timestamp = time.time()
                exact_second = math.floor(sync_timestamp)  # Truncate to exact second
                
                logger.info(f"SYNC TRIGGER at exact second: {exact_second}")
                
                # Process data from all sensors at this exact moment
                synchronized_data = {}
                
                with self.lock:
                    for sensor_id in self.berthing_mode_sensors:
                        if sensor_id in self.sensor_sync_data:
                            sensor_data = self.sensor_sync_data[sensor_id]
                            
                            # Get all points collected in the last second (from exact_second-1 to exact_second)
                            recent_points = [
                                p for p in sensor_data["points"]
                                if exact_second - 1.0 <= p["collection_time"] <= exact_second
                            ]
                            
                            if recent_points:
                                # Initialize distance history for sensor if not exists
                                if sensor_id not in self.sensor_distance_history:
                                    self.sensor_distance_history[sensor_id] = []

                                # Calculate center stats for this synchronized reading
                                center_stats = self._calculate_synchronized_center_stats(
                                    sensor_id, recent_points, exact_second, self.sensor_distance_history[sensor_id]
                                )

                                synchronized_data[sensor_id] = {
                                    "timestamp": exact_second,
                                    "sensor_id": sensor_id,
                                    "data_available": True,
                                    "center_stats": center_stats,
                                    "total_points_captured": len(recent_points),
                                    "status": "synchronized_streaming",
                                    "points": recent_points[:50],  # Include sample points
                                    "sync_quality": "high"  # All sensors synchronized
                                }
                            else:
                                # No data for this sensor at sync time
                                synchronized_data[sensor_id] = {
                                    "timestamp": exact_second,
                                    "sensor_id": sensor_id,
                                    "data_available": False,
                                    "status": "sync_no_data",
                                    "sync_quality": "degraded"
                                }
                
                # Update berthing mode center stats and sensor sync data with synchronized data
                self.berthing_mode_center_stats = {}
                for sensor_id, data in synchronized_data.items():
                    if "center_stats" in data:
                        self.berthing_mode_center_stats[sensor_id] = data["center_stats"]
                    # Also update sensor_sync_data with the full synchronized data including center_stats
                    if sensor_id in self.sensor_sync_data:
                        self.sensor_sync_data[sensor_id].update(data)
                
                # Put synchronized data into queues for each sensor
                for sensor_id, data in synchronized_data.items():
                    if sensor_id in self.data_queues:
                        # Encode synchronized data
                        data_str = json.dumps(data)
                        encoded_data = base64.b64encode(data_str.encode('utf-8')).decode('utf-8')
                        
                        # Clear queue and add synchronized data
                        while not self.data_queues[sensor_id].empty():
                            try:
                                self.data_queues[sensor_id].get_nowait()
                            except:
                                break
                        
                        self.data_queues[sensor_id].put(encoded_data)
                
                self.last_sync_timestamp = exact_second
                
                # Sleep until next exact second
                next_sync = exact_second + 1.0
                sleep_time = next_sync - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.warning(f"Sync processing took too long: {-sleep_time:.3f}s behind")
                
            except Exception as e:
                logger.error(f"Error in sync coordinator: {str(e)}")
                time.sleep(0.1)
        
        logger.info("Sync coordinator thread stopped")

    def _calculate_synchronized_center_stats(self, sensor_id: str, points: List[Dict], timestamp: float, distance_history: List) -> Dict:
        """Calculate center statistics using ToF-based measurement system"""
        # Initialize measurement system for sensor if not exists
        if sensor_id not in self.berthing_measurement_systems:
            self.berthing_measurement_systems[sensor_id] = BerthingMeasurementSystem(sensor_id)
            logger.info(f"Initialized ToF measurement system for sensor {sensor_id}")
        
        measurement_system = self.berthing_measurement_systems[sensor_id]
        
        # For now, bypass ToF system and use direct measurement with better filtering
        # The ToF calculations are adding unnecessary complexity when we have direct measurements
        return self._calculate_legacy_center_stats(sensor_id, points, timestamp, distance_history)
        
        # Return ToF-based measurement results
        return {
            "center_point_count": result.get('center_points_used', 0),
            "filtered_point_count": result.get('center_points_used', 0),
            "stable_distance": result.get('calibrated_distance', 0.0),
            "instant_distance": result.get('raw_distance', 0.0),
            "tof_ns": result.get('tof_ns', 0.0),
            "confidence": result.get('measurement_confidence', 0.0),
            "speed_mps": result.get('speed_mps', 0.0),
            "speed_confidence": result.get('speed_confidence', 0.0),
            "precision_mm": result.get('precision_mm', 1.0),
            "measurement_rate_hz": result.get('measurement_rate_hz', 0.0),
            "mode": "tof_precision_measurement",
            "sync_timestamp": timestamp,
            "algorithm": "time_of_flight",
            "environmental_correction": result.get('environmental_correction', {})
        }
    
    def _calculate_legacy_center_stats(self, sensor_id: str, points: List[Dict], timestamp: float, distance_history: List) -> Dict:
        """Legacy REAL-TIME LASER RANGEFINDER: Instant distance updates like LDM302"""
        if not points:
            return {"center_point_count": 0, "filtered_point_count": 0, "stable_distance": 0.0, 
                   "confidence": 0.0, "speed_mps": 0.0, "mode": "ldm302_no_data", "sync_timestamp": timestamp}
        
        import math
        total_points = len(points)
        
        # REAL-TIME LASER: Ultra-fast single-pass processing
        # Get dynamic calibration settings or use defaults
        if hasattr(self, 'calibration_settings') and sensor_id in self.calibration_settings:
            LDM302_CALIBRATION_OFFSET = self.calibration_settings[sensor_id].get('offset', -0.046)
            CENTER_TOLERANCE = self.calibration_settings[sensor_id].get('center_tolerance', 0.05)
        else:
            # Default calibration for oil refinery vessel berthing
            LDM302_CALIBRATION_OFFSET = -0.046  # -46mm offset (LiDAR typically reads higher)
            CENTER_TOLERANCE = 0.05  # 50mm center beam tolerance
        
        # STEP 1: PRECISION CENTER BEAM FINDER - Like real laser rangefinder (INSTANT)
        target_points = []
        
        # Find the TRUE center beam - laser rangefinder physics
        # Real laser has a ~1mm beam diameter at target, so find points closest to optical axis (Y=0, Z=0)
        for point in points:
            x, y, z = point["x"], point["y"], point["z"]
            
            # PRECISION LASER BEAM FILTER - Find true optical center
            if 0.5 < x <= 50.0:  # Reasonable distance range
                # Calculate radial distance from laser optical axis (center beam)
                radial_distance = (y*y + z*z)**0.5

                # PRECISION beam filter - find TRUE center beam
                # Use dynamic tolerance for laser optical axis
                if radial_distance <= CENTER_TOLERANCE:
                    ldm302_distance = x + LDM302_CALIBRATION_OFFSET
                    target_points.append({
                        "distance": ldm302_distance,
                        "x": x,
                        "radial_distance": radial_distance,
                        "intensity": point.get("intensity", 0)
                    })
        
        # FALLBACK: If no center beam points found, expand search slightly
        if len(target_points) == 0:
            logger.debug(f"No center beam points found, expanding search for sensor {sensor_id}")
            EXPANDED_TOLERANCE = CENTER_TOLERANCE * 1.6  # Expanded tolerance (1.6x normal)
            for point in points:
                x, y, z = point["x"], point["y"], point["z"]
                if 0.5 < x <= 50.0:  # Reasonable distance range
                    radial_distance = (y*y + z*z)**0.5
                    if radial_distance <= EXPANDED_TOLERANCE:
                        ldm302_distance = x + LDM302_CALIBRATION_OFFSET
                        target_points.append({
                            "distance": ldm302_distance,
                            "x": x,
                            "radial_distance": radial_distance,
                            "intensity": point.get("intensity", 0)
                        })
            
            if len(target_points) == 0:
                return {"center_point_count": total_points, "filtered_point_count": 0, "stable_distance": 0.0, 
                       "confidence": 0.0, "speed_mps": 0.0, "mode": "ldm302_no_target", "sync_timestamp": timestamp}
        
        # STEP 2: Get most centered target (INSTANT - like real laser)
        # First, filter to get points near the expected range (around 3.5m based on laser)
        if target_points:
            # Calculate median distance to identify the main target
            distances = [p["distance"] for p in target_points]
            distances.sort()
            median_distance = distances[len(distances)//2] if distances else 0
            
            # Filter points within 10cm of median (remove outliers)
            filtered_points = [p for p in target_points 
                             if abs(p["distance"] - median_distance) <= 0.1]
            
            if filtered_points:
                target_points = filtered_points
        
        # Sort by radial distance first (closest to center beam), then by distance
        target_points.sort(key=lambda p: (p["radial_distance"], p["distance"]))
        
        # Get the most centered point (closest to laser optical axis)
        closest_distance = target_points[0]["distance"]
        center_beam_distance = target_points[0]["radial_distance"]
        
        # STEP 3: ENHANCED SMOOTHING for stability
        if not hasattr(self, f'_laser_distance_{sensor_id}'):
            setattr(self, f'_laser_distance_{sensor_id}', closest_distance)
            setattr(self, f'_distance_history_{sensor_id}', [])
        
        prev_distance = getattr(self, f'_laser_distance_{sensor_id}')
        
        # Adaptive smoothing based on distance change
        distance_change = abs(closest_distance - prev_distance)
        
        if distance_change < 0.003:  # Less than 3mm change - very heavy smoothing
            # Nearly stationary, use 90% old, 10% new for production stability
            smooth_distance = 0.90 * prev_distance + 0.10 * closest_distance
        elif distance_change < 0.010:  # 3-10mm change - heavy smoothing
            # Small movements, use 80% old, 20% new
            smooth_distance = 0.80 * prev_distance + 0.20 * closest_distance
        elif distance_change < 0.030:  # 10-30mm change - moderate smoothing
            # Moderate movements, use 60% old, 40% new
            smooth_distance = 0.60 * prev_distance + 0.40 * closest_distance
        else:  # Large change (>30mm) - responsive but smooth
            # Fast movements, use 40% old, 60% new for production
            smooth_distance = 0.40 * prev_distance + 0.60 * closest_distance
        
        setattr(self, f'_laser_distance_{sensor_id}', smooth_distance)
        
        # STEP 4: VESSEL SPEED CALCULATION optimized for berthing
        # Initialize vessel speed calculator if not exists
        if sensor_id not in self.vessel_speed_calculators:
            self.vessel_speed_calculators[sensor_id] = VesselSpeedCalculator(
                sensor_id=sensor_id,
                window_size=20,  # SA parameter (increased for smoother output)
                measurement_frequency=100.0,  # MF parameter (100 Hz)
                distance_std_dev=0.002,  # 2mm standard deviation for production
                history_size=200  # Keep more history for better trend analysis
            )
            logger.info(f"Initialized vessel speed calculator for {sensor_id}")
        
        # Add measurement to vessel speed calculator with precise timestamp
        vessel_calc = self.vessel_speed_calculators[sensor_id]
        # Use actual time for speed calculation, not truncated second
        precise_timestamp = time.time()
        vessel_result = vessel_calc.add_measurement(smooth_distance, precise_timestamp)
        
        # Use vessel calculator results
        speed_mps = vessel_result.get('final_speed', 0.0)
        speed_confidence = vessel_result.get('confidence', 0.0)
        instant_speed = vessel_result.get('instant_speed', 0.0)
        sa_averaged_speed = vessel_result.get('sa_averaged_speed', 0.0)
        trend_speed = vessel_result.get('trend_speed', 0.0)
        speed_precision_mm_s = vessel_result.get('speed_precision_mm_s', 1.0)
        is_moving = vessel_result.get('is_moving', False)
        movement_phase = vessel_result.get('movement_phase', 'stationary')
        
        samples_collected = vessel_result.get('samples_in_window', 0)
        measurement_complete = samples_collected >= 10  # Need at least 10 samples
        
        # Log precision metrics periodically
        if samples_collected % 20 == 0:
            logger.debug(f"Precision calc {sensor_id}: {samples_collected}/100 samples, "
                        f"speed={speed_mps:.4f}m/s +/-{speed_precision_mm_s:.1f}mm/s")
        
        setattr(self, f'_last_distance_{sensor_id}', (smooth_distance, timestamp))
        
        # FINAL RESULT: Instant laser distance (1mm precision)
        final_distance = round(smooth_distance, 3)
        final_speed = round(speed_mps, 4)
        confidence = min(len(target_points) / 100.0, 1.0)  # Simple confidence
        
        # Ensure all values are JSON-safe (no infinity or NaN)
        def safe_float(value, default=0.0):
            return value if math.isfinite(value) else default
        
        return {
            "center_point_count": total_points,
            "filtered_point_count": len(target_points),
            "avg_intensity": safe_float(sum(p["intensity"] for p in target_points) / len(target_points) if target_points else 0),
            "stable_distance": safe_float(final_distance),
            "instant_distance": safe_float(round(closest_distance, 3)),
            "instant_speed": safe_float(instant_speed, 0.0),
            "sa_averaged_speed": safe_float(sa_averaged_speed, 0.0),
            "trend_speed": safe_float(trend_speed, 0.0),
            "center_beam_offset_mm": safe_float(round(center_beam_distance * 1000, 1)),
            "confidence": safe_float(confidence, 0.0),
            "speed_mps": safe_float(final_speed, 0.0),
            "speed_mm_s": safe_float(final_speed * 1000, 0.0),
            "speed_confidence": safe_float(speed_confidence, 0.0),
            "speed_precision_mm_s": safe_float(speed_precision_mm_s, 1000.0),
            "is_vessel_moving": is_moving,
            "movement_phase": movement_phase,
            "samples_collected": samples_collected,
            "samples_required": 10,  # Minimum for vessel speed
            "measurement_complete": measurement_complete,
            "mode": "vessel_berthing_mode",
            "sync_timestamp": timestamp,
            "algorithm": "vessel_speed_optimized",
            "precision_mm": 1,
            "response_time": "adaptive_smoothing",
            "smoothing_type": "heavy" if distance_change < 0.005 else ("moderate" if distance_change < 0.02 else "light")
        }
    
    def set_environmental_conditions(self, sensor_id: str, temperature: float, pressure: float, humidity: float):
        """Set environmental conditions for ToF compensation"""
        if sensor_id in self.berthing_measurement_systems:
            self.berthing_measurement_systems[sensor_id].set_environmental_conditions(
                temperature, pressure, humidity
            )
            logger.info(f"Updated environmental conditions for {sensor_id}: T={temperature}degC, P={pressure}hPa, H={humidity}%")
    
    def add_calibration_reference(self, sensor_id: str, measured_distance: float, actual_distance: float):
        """Add calibration reference point for a sensor"""
        if sensor_id not in self.berthing_measurement_systems:
            self.berthing_measurement_systems[sensor_id] = BerthingMeasurementSystem(sensor_id)
        
        self.berthing_measurement_systems[sensor_id].add_calibration_reference(
            measured_distance, actual_distance
        )
        logger.info(f"Added calibration reference for {sensor_id}: measured={measured_distance}m, actual={actual_distance}m")

    def _imu_capture_thread(self, sensor_id: str):
        """Capture 200Hz IMU data from Tele-15 BMI088 IMU"""
        logger.info(f"Starting IMU capture thread for Tele-15 sensor {sensor_id}")

        if not self.use_direct_control and sensor_id in self.sensors:
            sensor = self.sensors[sensor_id]

            try:
                # Get the IMU socket from OpenPyLivox
                imu_socket = getattr(sensor, '_imuSocket', None)
                if not imu_socket:
                    logger.error(f"No IMU socket available for sensor {sensor_id}")
                    return

                imu_socket.settimeout(1.0)  # 1 second timeout

                while self.stream_active.get(sensor_id, False):
                    try:
                        # Receive IMU data (Tele-15 BMI088 sends at 200Hz)
                        imu_data, addr = imu_socket.recvfrom(50)

                        # Parse IMU packet (based on OpenPyLivox format)
                        if len(imu_data) >= 32:  # Minimum IMU packet size
                            try:
                                # Parse IMU data according to Livox protocol
                                data_type = int.from_bytes(imu_data[9:10], byteorder='little')

                                if data_type == 1:  # IMU data packet
                                    # Extract acceleration and gyroscope data
                                    byte_pos = 18  # Start of IMU payload

                                    # BMI088 IMU data (6DOF: 3 accel + 3 gyro)
                                    accel_x = struct.unpack('<f', imu_data[byte_pos:byte_pos+4])[0]
                                    accel_y = struct.unpack('<f', imu_data[byte_pos+4:byte_pos+8])[0]
                                    accel_z = struct.unpack('<f', imu_data[byte_pos+8:byte_pos+12])[0]

                                    gyro_x = struct.unpack('<f', imu_data[byte_pos+12:byte_pos+16])[0]
                                    gyro_y = struct.unpack('<f', imu_data[byte_pos+16:byte_pos+20])[0]
                                    gyro_z = struct.unpack('<f', imu_data[byte_pos+20:byte_pos+24])[0]

                                    # Convert gyroscope to angles (simple integration for demo)
                                    # In production, use proper IMU fusion algorithms
                                    current_time = time.time()

                                    # Store the latest IMU data for this sensor
                                    self.latest_imu_data[sensor_id] = {
                                        "timestamp": current_time,
                                        "frequency": 200,  # BMI088 runs at 200Hz
                                        "accel_x": round(accel_x, 6),
                                        "accel_y": round(accel_y, 6),
                                        "accel_z": round(accel_z, 6),
                                        "gyro_x": round(gyro_x, 6),
                                        "gyro_y": round(gyro_y, 6),
                                        "gyro_z": round(gyro_z, 6),
                                        # Simple angle estimation (for demo - use proper IMU fusion in production)
                                        "roll": round(gyro_x * 57.2958, 3),  # Convert rad/s to degrees
                                        "pitch": round(gyro_y * 57.2958, 3),
                                        "yaw": round(gyro_z * 57.2958, 3)
                                    }

                                    # Log first few IMU packets for debugging
                                    if hasattr(self, '_imu_packet_count'):
                                        self._imu_packet_count += 1
                                    else:
                                        self._imu_packet_count = 1

                                    if self._imu_packet_count <= 5:
                                        logger.info(f"IMU data received: roll={self.latest_imu_data[sensor_id]['roll']:.3f}deg, "
                                                  f"pitch={self.latest_imu_data[sensor_id]['pitch']:.3f}deg, "
                                                  f"yaw={self.latest_imu_data[sensor_id]['yaw']:.3f}deg")

                            except struct.error as e:
                                logger.debug(f"IMU packet parse error: {e}")
                                continue

                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.debug(f"IMU capture error: {e}")
                        continue

            except Exception as e:
                logger.error(f"IMU capture thread error for sensor {sensor_id}: {e}")

        logger.info(f"IMU capture thread stopped for sensor {sensor_id}")

    async def get_sensor_data_generator(self, sensor_id: str):
        """Async generator for streaming sensor data"""
        while self.stream_active.get(sensor_id, False):
            try:
                if not self.data_queues[sensor_id].empty():
                    data = self.data_queues[sensor_id].get(timeout=0.1)
                    yield data
                else:
                    await asyncio.sleep(0.01)
            except queue.Empty:
                await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"Error in data generator for sensor {sensor_id}: {str(e)}")
                break

    async def _update_sensor_info(self, sensor_id: str):
        """Update sensor information"""
        try:
            if sensor_id not in self.sensors:
                return

            sensor = self.sensors[sensor_id]
            info = self.sensor_info[sensor_id]

            try:
                info.serial_number = sensor.serialNumber()
            except Exception:
                pass

            try:
                info.firmware_version = sensor.firmware()
            except Exception:
                pass

            try:
                conn_params = sensor.connectionParameters()
                if conn_params:
                    # Preserve existing connection parameters (like is_simulation flag)
                    existing_params = info.connection_parameters or {}
                    info.connection_parameters = {
                        "computer_ip": conn_params[0],
                        "sensor_ip": conn_params[1],
                        "data_port": conn_params[2],
                        "cmd_port": conn_params[3]
                    }
                    # Merge with existing parameters to preserve flags like is_simulation
                    info.connection_parameters.update(existing_params)
            except Exception:
                pass

            try:
                ext_params = sensor.extrinsicParameters()
                if ext_params and len(ext_params) == 6:
                    # Handle None values from sensor
                    x = ext_params[0] if ext_params[0] is not None else 0.0
                    y = ext_params[1] if ext_params[1] is not None else 0.0
                    z = ext_params[2] if ext_params[2] is not None else 0.0
                    roll = ext_params[3] if ext_params[3] is not None else 0.0
                    pitch = ext_params[4] if ext_params[4] is not None else 0.0
                    yaw = ext_params[5] if ext_params[5] is not None else 0.0

                    info.extrinsic_parameters = ExtrinsicParameters(
                        x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw
                    )
                else:
                    # If sensor hasn't read extrinsics yet, initialize with zeros
                    info.extrinsic_parameters = ExtrinsicParameters(
                        x=0.0, y=0.0, z=0.0, roll=0.0, pitch=0.0, yaw=0.0
                    )
            except Exception as e:
                logger.warning(f"Failed to read extrinsic parameters: {e}")
                # Ensure we always have extrinsic parameters, even if reading fails
                if not hasattr(info, 'extrinsic_parameters') or info.extrinsic_parameters is None:
                    info.extrinsic_parameters = ExtrinsicParameters(
                        x=0.0, y=0.0, z=0.0, roll=0.0, pitch=0.0, yaw=0.0
                    )

            try:
                status_codes = sensor.lidarStatusCodes()
                if status_codes:
                    info.status_codes = {
                        "system": status_codes[0],
                        "temperature": status_codes[1],
                        "voltage": status_codes[2],
                        "motor": status_codes[3],
                        "dirty": status_codes[4],
                        "firmware": status_codes[5],
                        "pps": status_codes[6],
                        "device": status_codes[7]
                    }
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error updating sensor info for {sensor_id}: {str(e)}")

    async def set_coordinate_system(self, sensor_id: str, system: CoordinateSystem) -> bool:
        """Set coordinate system for a sensor"""
        try:
            if sensor_id not in self.sensors:
                return False

            sensor = self.sensors[sensor_id]

            if system == CoordinateSystem.CARTESIAN:
                sensor.setCartesianCS()
            else:
                sensor.setSphericalCS()

            self.sensor_info[sensor_id].coordinate_system = system
            logger.info(f"Set coordinate system to {system} for sensor {sensor_id}")
            return True

        except Exception as e:
            logger.error(f"Error setting coordinate system for sensor {sensor_id}: {str(e)}")
            return False

    async def set_extrinsic_parameters(self, sensor_id: str, params: ExtrinsicParameters) -> bool:
        """Set extrinsic parameters for a sensor"""
        try:
            if sensor_id not in self.sensors:
                return False

            sensor = self.sensors[sensor_id]
            sensor.setExtrinsicTo(params.x, params.y, params.z,
                                params.roll, params.pitch, params.yaw)

            await self._update_sensor_info(sensor_id)
            logger.info(f"Set extrinsic parameters for sensor {sensor_id}")
            return True

        except Exception as e:
            logger.error(f"Error setting extrinsic parameters for sensor {sensor_id}: {str(e)}")
            return False

    async def set_lidar_mode(self, sensor_id: str, mode: LidarMode) -> bool:
        """Set lidar operating mode"""
        try:
            if sensor_id not in self.sensors:
                return False

            sensor = self.sensors[sensor_id]

            if mode == LidarMode.NORMAL:
                sensor.lidarSpinUp()
            elif mode == LidarMode.POWER_SAVING:
                sensor.lidarSpinDown()
            elif mode == LidarMode.STANDBY:
                sensor.lidarStandBy()

            self.sensor_info[sensor_id].mode = mode
            logger.info(f"Set mode to {mode} for sensor {sensor_id}")
            return True

        except Exception as e:
            logger.error(f"Error setting mode for sensor {sensor_id}: {str(e)}")
            return False

    def get_all_sensors(self) -> Dict[str, LidarInfo]:
        """Get information about all connected sensors"""
        return self.sensor_info.copy()

    def get_sensor_info(self, sensor_id: str) -> Optional[LidarInfo]:
        """Get information about a specific sensor"""
        return self.sensor_info.get(sensor_id)

    async def _enable_single_sensor_berthing(self, sensor_id: str, sensor_network_info: Dict, sensor_index: int, computer_ip: Optional[str]) -> Dict[str, Any]:
        """Process a single sensor for berthing mode enablement concurrently"""
        logger.critical(f"[START] Processing ENABLE for sensor {sensor_id}")

        result = {
            "sensor_id": sensor_id,
            "connected": False,
            "streaming": False,
            "network_issues": False,
            "error": None
        }

        try:
            # Check if already connected
            already_connected = sensor_id in self.sensors

            if already_connected:
                logger.info(f"[OK] Sensor {sensor_id} already connected")
                result["connected"] = True
            else:
                # Find sensor network info
                sensor_info = sensor_network_info.get(sensor_id)
                if not sensor_info:
                    logger.error(f"[ERROR] Sensor {sensor_id} not found in discovery - using fallback config")
                    # Try fallback from config if available
                    try:
                        from ...core.config import settings
                        if hasattr(settings, 'SENSORS'):
                            fallback_sensor = next((s for s in settings.SENSORS if s.get('id') == sensor_id), None)
                            if fallback_sensor:
                                sensor_info = {
                                    'sensor_id': sensor_id,
                                    'ip': fallback_sensor.get('ip'),
                                    'device_type': 'Tele-15',
                                    'serialNumber': sensor_id
                                }
                                logger.info(f"[OK] Using fallback config for {sensor_id}: {sensor_info['ip']}")
                    except Exception as fallback_err:
                        logger.error(f"[ERROR] Fallback config failed for {sensor_id}: {fallback_err}")

                if sensor_info:
                    # Dynamic port assignment based on sensor index
                    base_port = 65000 + (sensor_index * 10)  # 65000, 65010, 65020, etc.

                    data_port = base_port
                    cmd_port = base_port + 1
                    imu_port = base_port + 2 if sensor_info.get('device_type') == 'Tele-15' else None

                    logger.info(f"[START] Assigning ports to {sensor_id}: data={data_port}, cmd={cmd_port}, imu={imu_port}")

                    # CRITICAL: Connection with retry mechanism
                    connection_attempts = 0
                    max_connection_attempts = 3
                    connection_success = False

                    while connection_attempts < max_connection_attempts and not connection_success:
                        try:
                            logger.critical(f"[START] Connection attempt {connection_attempts + 1} for {sensor_id}")
                            connection_success = await self.connect_sensor(
                                sensor_id=sensor_id,
                                computer_ip=computer_ip or self.local_ip,
                                sensor_ip=sensor_info['ip'],
                                data_port=data_port,
                                cmd_port=cmd_port,
                                imu_port=imu_port
                            )

                            if connection_success:
                                result["connected"] = True
                                logger.info(f"[OK] Connected to sensor {sensor_id}")

                                # CRITICAL: Verify hardware connection by sending test command
                                try:
                                    sensor = self.sensors[sensor_id]
                                    if hasattr(sensor, '_cmdSocket') and sensor._cmdSocket:
                                        logger.critical(f"[START] Verifying hardware connection for {sensor_id}")
                                        # Send a ping-like command to verify connection
                                        sensor._cmdSocket.sendto(sensor._CMD_GET_DEVICE_INFO, (sensor._sensorIP, 65000))
                                        logger.info(f"[OK] Hardware connection verified for {sensor_id}")
                                    else:
                                        logger.warning(f"[WARN] No command socket for hardware verification {sensor_id}")
                                except Exception as hw_verify_err:
                                    logger.error(f"[ERROR] Hardware verification failed for {sensor_id}: {hw_verify_err}")
                                    result["network_issues"] = True

                                # Set default extrinsic parameters for berthing if not set
                                try:
                                    current_params = self.sensor_info[sensor_id].extrinsic_parameters
                                    if (current_params and
                                        current_params.x == 0.0 and current_params.y == 0.0 and current_params.z == 0.0):
                                        # Set default berthing position (can be customized per installation)
                                        default_params = ExtrinsicParameters(
                                            x=0.0,    # Sensor at vessel center point
                                            y=0.0,    # No starboard/port offset
                                            z=2.0,    # 2m above deck level
                                            roll=0.0, pitch=0.0, yaw=0.0  # Level mounting
                                        )
                                        await self.set_extrinsic_parameters(sensor_id, default_params)
                                        logger.info(f"Set default extrinsic parameters for berthing sensor {sensor_id}")
                                except Exception as e:
                                    logger.debug(f"Could not set default extrinsics for {sensor_id}: {e}")
                                break
                            else:
                                connection_attempts += 1
                                if connection_attempts < max_connection_attempts:
                                    logger.warning(f"[WARN] Connection failed, retry {connection_attempts}/{max_connection_attempts} for {sensor_id}")
                                    await asyncio.sleep(2.0)

                        except Exception as conn_err:
                            connection_attempts += 1
                            logger.error(f"[ERROR] Connection attempt {connection_attempts} failed for {sensor_id}: {conn_err}")
                            if connection_attempts < max_connection_attempts:
                                await asyncio.sleep(2.0)

                    if not connection_success:
                        logger.error(f"[ERROR] All connection attempts failed for sensor {sensor_id}")
                        result["error"] = "connection_failed"
                else:
                    logger.error(f"[ERROR] No network info available for sensor {sensor_id}")
                    result["error"] = "no_network_info"

            # Step 4: AGGRESSIVELY start data streaming for connected sensor
            if result["connected"]:
                logger.critical(f"[START] Starting data streaming for {sensor_id}")

                # Multiple streaming attempts
                streaming_attempts = 0
                max_streaming_attempts = 3
                streaming_success = False

                while streaming_attempts < max_streaming_attempts and not streaming_success:
                    try:
                        logger.critical(f"[START] Streaming attempt {streaming_attempts + 1} for {sensor_id}")
                        streaming_success = await self.start_data_stream(sensor_id)

                        if streaming_success:
                            result["streaming"] = True
                            logger.info(f"[OK] Started streaming for sensor {sensor_id}")

                            # CRITICAL: Verify data is actually flowing by sending direct command
                            try:
                                sensor = self.sensors[sensor_id]
                                if hasattr(sensor, '_cmdSocket') and sensor._cmdSocket:
                                    logger.critical(f"[START] Sending START commands to hardware {sensor_id}")

                                    # Check if this is a fake sensor
                                    is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation
                                    if is_fake:
                                        # For fake sensors, send to computer IP with correct port
                                        target_ip = sensor._computerIP
                                        cmd_port = sensor._cmdPort
                                    else:
                                        # For real sensors, send to sensor IP with standard port
                                        target_ip = sensor._sensorIP
                                        cmd_port = 65000

                                    # Send start data command directly to ensure hardware is active
                                    sensor._cmdSocket.sendto(sensor._CMD_DATA_START, (target_ip, cmd_port))
                                    logger.info(f"[OK] Sent DATA_START to {sensor_id} at {target_ip}:{cmd_port}")

                                    # Wait brief moment and send spin up if needed
                                    await asyncio.sleep(0.5)
                                    sensor._cmdSocket.sendto(sensor._CMD_LIDAR_SPIN_UP, (target_ip, cmd_port))
                                    logger.info(f"[OK] Sent SPIN_UP to {sensor_id} at {target_ip}:{cmd_port}")
                                else:
                                    logger.warning(f"[WARN] No command socket for hardware start verification {sensor_id}")
                            except Exception as hw_start_err:
                                logger.error(f"[ERROR] Hardware start verification failed for {sensor_id}: {hw_start_err}")
                                result["network_issues"] = True
                            break
                        else:
                            streaming_attempts += 1
                            if streaming_attempts < max_streaming_attempts:
                                logger.warning(f"[WARN] Streaming failed, retry {streaming_attempts}/{max_streaming_attempts} for {sensor_id}")
                                await asyncio.sleep(2.0)

                    except Exception as stream_err:
                        streaming_attempts += 1
                        logger.error(f"[ERROR] Streaming attempt {streaming_attempts} failed for {sensor_id}: {stream_err}")
                        if streaming_attempts < max_streaming_attempts:
                            await asyncio.sleep(2.0)

                if not streaming_success:
                    logger.error(f"[ERROR] All streaming attempts failed for sensor {sensor_id}")
                    result["error"] = "streaming_failed"

        except Exception as sensor_error:
            logger.error(f"[ERROR] Critical error processing sensor {sensor_id}: {sensor_error}")
            result["error"] = str(sensor_error)

        return result

    async def enable_berthing_mode(self, sensor_ids: List[str], computer_ip: Optional[str] = None) -> Dict[str, Any]:
        """CRITICAL: Enable berthing mode - ENSURES hardware actually starts even with network issues"""
        logger.critical(f"[START] CRITICAL: Enabling berthing mode for sensors: {sensor_ids}")

        connected_sensors = []
        streaming_sensors = []
        failed_sensors = []
        network_issues = []

        try:
            # Step 1: FORCE berthing mode state activation FIRST
            logger.critical("[START] FORCE setting berthing mode state")

            # Add new sensors to the existing berthing mode sensors list (don't replace)
            # This allows multiple berths to be active concurrently
            with self.lock:
                for sensor_id in sensor_ids:
                    if sensor_id not in self.berthing_mode_sensors:
                        self.berthing_mode_sensors.append(sensor_id)
                        logger.info(f"Added sensor {sensor_id} to berthing mode")

                # Activate berthing mode if not already active
                if not self.berthing_mode_active:
                    self.berthing_mode_active = True
                    logger.info("Berthing mode activated")

                # Initialize center stats for new sensors only
                for sensor_id in sensor_ids:
                    if sensor_id not in self.berthing_mode_center_stats:
                        self.berthing_mode_center_stats[sensor_id] = {}

            # Step 2: Discover sensors with retry mechanism
            logger.critical("[START] Discovering sensors with network reliability checks")
            discovered_sensors = []
            discovery_attempts = 0
            max_discovery_attempts = 3

            while discovery_attempts < max_discovery_attempts and not discovered_sensors:
                try:
                    discovered_sensors = await self.discover_sensors(computer_ip)
                    if discovered_sensors:
                        logger.info(f"[OK] Discovery successful: found {len(discovered_sensors)} sensors")
                        break
                    else:
                        discovery_attempts += 1
                        if discovery_attempts < max_discovery_attempts:
                            logger.warning(f"[WARN] No sensors discovered, retry {discovery_attempts}/{max_discovery_attempts}")
                            await asyncio.sleep(2.0)
                except Exception as disc_err:
                    discovery_attempts += 1
                    logger.error(f"[ERROR] Discovery attempt {discovery_attempts} failed: {disc_err}")
                    if discovery_attempts < max_discovery_attempts:
                        await asyncio.sleep(2.0)

            sensor_network_info = {s.get('sensor_id', s.get('serialNumber')): s for s in discovered_sensors}

            # Step 3: CONCURRENTLY connect and stream each specified sensor
            logger.critical(f"[START] Processing {len(sensor_ids)} sensors CONCURRENTLY")

            # Create concurrent tasks for all sensors
            sensor_tasks = []
            for i, sensor_id in enumerate(sensor_ids):
                task = self._enable_single_sensor_berthing(sensor_id, sensor_network_info, i, computer_ip)
                sensor_tasks.append(task)

            # Execute all sensor tasks concurrently
            sensor_results = await asyncio.gather(*sensor_tasks, return_exceptions=True)

            # Process results
            for i, result in enumerate(sensor_results):
                sensor_id = sensor_ids[i]

                if isinstance(result, Exception):
                    logger.error(f"[ERROR] Exception in sensor {sensor_id}: {result}")
                    failed_sensors.append(sensor_id)
                    continue

                if result["connected"]:
                    connected_sensors.append(sensor_id)

                if result["streaming"]:
                    streaming_sensors.append(sensor_id)

                if result["network_issues"]:
                    network_issues.append(sensor_id)

                if result["error"]:
                    failed_sensors.append(sensor_id)
                    logger.error(f"[ERROR] Sensor {sensor_id} failed: {result['error']}")
            
            # Step 5: Start synchronization coordinator for all streaming sensors
            if streaming_sensors:
                logger.critical("[START] Starting synchronization coordinator")
                self._start_sync_coordinator()
                await asyncio.sleep(3.0)  # Wait for first synchronized readings
                logger.info(f"[OK] Sync coordinator started for {len(streaming_sensors)} sensors")
            
            # Step 6: Collect initial center stats
            for sensor_id in streaming_sensors:
                try:
                    if sensor_id in self.data_queues and not self.data_queues[sensor_id].empty():
                        # Get latest data
                        latest_data = None
                        while not self.data_queues[sensor_id].empty():
                            try:
                                encoded_data = self.data_queues[sensor_id].get_nowait()
                                data_str = base64.b64decode(encoded_data).decode('utf-8')
                                latest_data = json.loads(data_str)
                            except:
                                break
                        
                        if latest_data and 'center_stats' in latest_data:
                            self.berthing_mode_center_stats[sensor_id] = latest_data['center_stats']
                            logger.debug(f"[OK] Collected initial center stats for {sensor_id}")
                            
                except Exception as e:
                    logger.error(f"Error collecting center stats for {sensor_id}: {str(e)}")
            
            # Step 7: CRITICAL status reporting
            success_count = len(streaming_sensors)
            failed_count = len(failed_sensors)
            network_issue_count = len(set(network_issues))
            
            if success_count > 0:
                message = f"Berthing mode ENABLED: {success_count} streaming successfully"
                if failed_count > 0:
                    message += f", {failed_count} failed"
                if network_issue_count > 0:
                    message += f", {network_issue_count} with network issues"
                    
                logger.critical(f"[OK] {message}")
            else:
                message = f"Berthing mode FAILED: No sensors successfully streaming"
                logger.critical(f"[ERROR] {message}")
                # Don't disable berthing mode state - user can retry
            
            return {
                "active": success_count > 0,  # Only active if at least one sensor is streaming
                "connected_sensors": connected_sensors,
                "streaming_sensors": streaming_sensors,
                "failed_sensors": failed_sensors,
                "network_issues": list(set(network_issues)),  # Remove duplicates
                "message": message,
                "center_stats": self.berthing_mode_center_stats,
                "synchronized": self.sync_coordinator_active,
                "last_sync_timestamp": self.last_sync_timestamp,
                "sync_quality": "high" if self.sync_coordinator_active else "none"
            }
            
        except Exception as e:
            logger.critical(f"[ERROR] CRITICAL ERROR enabling berthing mode: {str(e)}")
            # Reset state on critical error
            self.berthing_mode_active = False
            self.berthing_mode_sensors = []
            self.berthing_mode_center_stats = {}
            self._stop_sync_coordinator()
            raise

    async def disable_berthing_mode(self, sensor_ids: List[str]) -> Dict[str, Any]:
        """CRITICAL: Disable berthing mode - ENSURES hardware actually stops even with network issues"""
        logger.critical(f"[STOP] CRITICAL: Disabling berthing mode for sensors: {sensor_ids}")

        disconnected_sensors = []
        failed_sensors = []
        network_issues = []
        skipped_sensors = []

        try:
            # Step 0: Check which sensors are actually in berthing mode
            sensors_in_berthing = [s for s in sensor_ids if s in self.berthing_mode_sensors]
            sensors_not_in_berthing = [s for s in sensor_ids if s not in self.berthing_mode_sensors]

            # Log and skip sensors not in berthing mode
            if sensors_not_in_berthing:
                logger.info(f"[SKIP] Skipping sensors not in berthing mode: {sensors_not_in_berthing}")
                skipped_sensors.extend(sensors_not_in_berthing)

            # If no sensors are actually in berthing mode, return early
            if not sensors_in_berthing:
                logger.warning(f"[SKIP] No sensors from the requested list are currently in berthing mode")
                return {
                    "active": self.berthing_mode_active,
                    "connected_sensors": [s for s in self.berthing_mode_sensors if s in self.sensors],
                    "streaming_sensors": [s for s in self.berthing_mode_sensors if self.stream_active.get(s, False)],
                    "message": f"No sensors to disable - none of the requested sensors are in berthing mode",
                    "synchronized": self.sync_coordinator_active,
                    "last_sync_timestamp": self.last_sync_timestamp,
                    "sync_quality": "none" if not self.sync_coordinator_active else "high",
                    "disconnected_sensors": [],
                    "failed_sensors": [],
                    "skipped_sensors": skipped_sensors,
                    "network_issues": []
                }

            # Use only sensors that are actually in berthing mode
            actual_sensor_ids = sensors_in_berthing
            logger.critical(f"[STOP] Proceeding with disabling berthing mode for sensors actually in berthing mode: {actual_sensor_ids}")

            # Step 1: Stop synchronization coordinator IMMEDIATELY
            remaining_after_removal = [s for s in self.berthing_mode_sensors if s not in actual_sensor_ids]
            if not remaining_after_removal:
                logger.critical("[STOP] Stopping sync coordinator")
                self._stop_sync_coordinator()
                
            # Step 2: AGGRESSIVELY stop streaming and hardware for each sensor
            for sensor_id in actual_sensor_ids:
                logger.critical(f"[STOP] Processing disable for sensor {sensor_id}")

                try:
                    # Check if sensor exists and is streaming
                    sensor_active = sensor_id in self.sensors
                    is_streaming = self.stream_active.get(sensor_id, False)

                    logger.info(f"Sensor {sensor_id}: active={sensor_active}, streaming={is_streaming}")

                    if sensor_active:
                        sensor = self.sensors[sensor_id]

                        # CRITICAL: Send hardware stop commands directly
                        try:
                            logger.critical(f"[STOP] Sending STOP commands to hardware {sensor_id}")

                            # Send stop data command
                            if hasattr(sensor, '_cmdSocket') and sensor._cmdSocket:
                                try:
                                    # Check if this is a fake sensor
                                    is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation
                                    if is_fake:
                                        # For fake sensors, send to computer IP with correct port
                                        target_ip = sensor._computerIP
                                        cmd_port = sensor._cmdPort
                                    else:
                                        # For real sensors, send to sensor IP with standard port
                                        target_ip = sensor._sensorIP
                                        cmd_port = 65000

                                    sensor._cmdSocket.sendto(sensor._CMD_DATA_STOP, (target_ip, cmd_port))
                                    logger.info(f"[OK] Sent DATA_STOP to {sensor_id} at {target_ip}:{cmd_port}")
                                except Exception as cmd_err:
                                    logger.error(f"[ERROR] Failed to send DATA_STOP to {sensor_id}: {cmd_err}")
                                    network_issues.append(sensor_id)

                                # Wait a moment
                                await asyncio.sleep(0.5)

                                # Send spin down command using high-level API
                                try:
                                    sensor.lidarSpinDown()
                                    logger.info(f"[OK] Sent SPIN_DOWN to {sensor_id}")
                                except Exception as cmd_err:
                                    logger.error(f"[ERROR] Failed to send SPIN_DOWN to {sensor_id}: {cmd_err}")
                                    network_issues.append(sensor_id)
                            else:
                                logger.warning(f"[WARN] No command socket for {sensor_id}")
                                network_issues.append(sensor_id)

                        except Exception as hw_error:
                            logger.error(f"[ERROR] Hardware stop failed for {sensor_id}: {hw_error}")
                            network_issues.append(sensor_id)

                        # Also use high-level stop methods
                        if is_streaming:
                            try:
                                logger.info(f"[STOP] High-level stream stop for {sensor_id}")
                                success = await self.stop_data_stream(sensor_id)
                                if success:
                                    logger.info(f"[OK] High-level stop successful for {sensor_id}")
                                else:
                                    logger.warning(f"[WARN] High-level stop failed for {sensor_id}")
                            except Exception as stop_error:
                                logger.error(f"[ERROR] High-level stop error for {sensor_id}: {stop_error}")

                        # Force disconnect
                        try:
                            logger.info(f"[STOP] Force disconnecting {sensor_id}")
                            success = await self.disconnect_sensor(sensor_id, force=True)
                            if success:
                                disconnected_sensors.append(sensor_id)
                                logger.info(f"[OK] Successfully force disconnected {sensor_id}")
                            else:
                                logger.error(f"[ERROR] Force disconnect failed for {sensor_id}")
                                failed_sensors.append(sensor_id)
                        except Exception as disc_error:
                            logger.error(f"[ERROR] Disconnect error for {sensor_id}: {disc_error}")
                            failed_sensors.append(sensor_id)

                    else:
                        logger.warning(f"[WARN] Sensor {sensor_id} not found in active sensors - assuming already disconnected")
                        disconnected_sensors.append(sensor_id)

                except Exception as sensor_error:
                    logger.error(f"[ERROR] Critical error processing sensor {sensor_id}: {sensor_error}")
                    failed_sensors.append(sensor_id)
            
            # Step 3: FORCE berthing mode state reset regardless of network issues
            logger.critical("[STOP] FORCE resetting berthing mode state")

            # Remove specified sensors from berthing mode (thread-safe)
            with self.lock:
                original_berthing_sensors = self.berthing_mode_sensors.copy()
                self.berthing_mode_sensors = [s for s in self.berthing_mode_sensors if s not in actual_sensor_ids]

                # Clear center stats for specified sensors only
                for sensor_id in actual_sensor_ids:
                    self.berthing_mode_center_stats.pop(sensor_id, None)
                    self.vessel_speed_calculators.pop(sensor_id, None)

                # If no sensors left, disable berthing mode completely
                if not self.berthing_mode_sensors:
                    logger.critical("[STOP] NO SENSORS LEFT - DISABLING BERTHING MODE COMPLETELY")
                    self.berthing_mode_active = False
                    self.berthing_mode_center_stats = {}
                    self.sync_coordinator_active = False
                else:
                    logger.info(f"Berthing mode still active with {len(self.berthing_mode_sensors)} sensors remaining")
            
            # Calculate final state
            remaining_connected = [s for s in self.berthing_mode_sensors if s in self.sensors]
            remaining_streaming = [s for s in remaining_connected if self.stream_active.get(s, False)]
            
            # Create comprehensive status message
            status_parts = []
            if disconnected_sensors:
                status_parts.append(f"{len(disconnected_sensors)} sensors stopped cleanly")
            if network_issues and not failed_sensors:  # Network issues but not complete failures
                status_parts.append(f"{len(network_issues)} sensors had network issues but commands sent")
            if failed_sensors:
                status_parts.append(f"{len(failed_sensors)} sensors failed to disconnect")
                
            message = f"[STOP] BERTHING MODE DISABLED: {', '.join(status_parts) if status_parts else 'All sensors processed'}"
            
            # Add critical warnings for network issues
            if network_issues and not disconnected_sensors:
                message += f" [WARN] CRITICAL: Hardware may still be active for sensors {network_issues} due to network disconnection"
            
            result = {
                "active": self.berthing_mode_active,
                "connected_sensors": remaining_connected,
                "streaming_sensors": remaining_streaming,
                "message": message,
                "synchronized": self.sync_coordinator_active,
                "last_sync_timestamp": self.last_sync_timestamp,
                "sync_quality": "none" if not self.sync_coordinator_active else "high",
                "disconnected_sensors": disconnected_sensors,
                "failed_sensors": failed_sensors,
                "skipped_sensors": skipped_sensors,
                "network_issues": network_issues,
                "warning": "If ethernet was disconnected during disable, LiDAR hardware may still be spinning. Reconnect ethernet and disable again to ensure complete shutdown." if network_issues else None
            }
            
            logger.critical(f"[STOP] BERTHING MODE DISABLE COMPLETE: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error disabling berthing mode: {str(e)}")
            raise

    async def get_berthing_mode_status(self) -> Dict[str, Any]:
        """Get current berthing mode status with latest center stats"""
        try:
            connected_sensors = [s for s in self.berthing_mode_sensors if s in self.sensors]
            streaming_sensors = [s for s in connected_sensors if self.stream_active.get(s, False)]
            
            # Update center stats for streaming sensors
            current_center_stats = {}
            for sensor_id in streaming_sensors:
                try:
                    if sensor_id in self.data_queues and not self.data_queues[sensor_id].empty():
                        # Get latest data without removing from queue
                        queue_items = []
                        latest_data = None
                        
                        # Drain queue and collect all items
                        while not self.data_queues[sensor_id].empty():
                            try:
                                encoded_data = self.data_queues[sensor_id].get_nowait()
                                queue_items.append(encoded_data)
                                # Decode latest
                                data_str = base64.b64decode(encoded_data).decode('utf-8')
                                latest_data = json.loads(data_str)
                            except:
                                break
                        
                        # Put items back (keep most recent)
                        for item in queue_items[-10:]:  # Keep last 10 items
                            try:
                                self.data_queues[sensor_id].put_nowait(item)
                            except:
                                break
                        
                        if latest_data and 'center_stats' in latest_data:
                            current_center_stats[sensor_id] = latest_data['center_stats']
                            
                except Exception as e:
                    logger.error(f"Error collecting center stats for {sensor_id}: {str(e)}")
            
            # Update stored center stats
            self.berthing_mode_center_stats.update(current_center_stats)
            
            message = f"Berthing mode {'active' if self.berthing_mode_active else 'inactive'}: " \
                     f"{len(connected_sensors)} connected, {len(streaming_sensors)} streaming"
            
            return {
                "active": self.berthing_mode_active,
                "sensor_ids": self.berthing_mode_sensors,
                "connected_sensors": connected_sensors,
                "streaming_sensors": streaming_sensors,
                "message": message,
                "center_stats": self.berthing_mode_center_stats,
                "synchronized": self.sync_coordinator_active,
                "last_sync_timestamp": self.last_sync_timestamp,
                "sync_quality": "high" if self.sync_coordinator_active else "none"
            }
            
        except Exception as e:
            logger.error(f"Error getting berthing mode status: {str(e)}")
            return {
                "active": False,
                "sensor_ids": [],
                "connected_sensors": [],
                "streaming_sensors": [],
                "message": f"Error getting status: {str(e)}",
                "synchronized": False,
                "last_sync_timestamp": None,
                "sync_quality": "error"
            }

    async def enable_berthing_by_berth(self, berth_id: int, computer_ip: Optional[str] = None, auto_update: bool = False, berthing_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Enable berthing mode for all lasers/lidars associated with a specific berth.

        This method:
        1. Queries the database via PostgREST to get all lasers for the berth
        2. Filters devices by laser_type_id (1=laser, 2=lidar)
        3. For lasers (type_id=1): starts laser drift compensation
        4. For lidars (type_id=2): enables berthing mode
        5. Starts database consumer if auto_update is True

        Args:
            berth_id: The berth ID to activate berthing for
            computer_ip: Optional computer IP for sensor discovery
            auto_update: Whether to start the database consumer for automatic data streaming
            berthing_id: Optional berthing operation ID

        Returns:
            Dictionary with activation results
        """
        import httpx
        from ..core.config import settings

        logger.info(f"Enabling berthing mode for berth {berth_id}")

        try:
            # Step 1: Query database for lasers associated with this berth via PostgREST
            logger.info(f"Querying database for lasers associated with berth {berth_id}")

            # Make direct PostgREST request (similar to how DatabaseConsumer makes requests)
            postgrest_url = f"{settings.DB_HOST}/rpc/get_lasers_by_berth"
            headers = {'Content-Type': 'application/json'}
            payload = {"p_berth_id": berth_id}

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(postgrest_url, headers=headers, json=payload)
                response.raise_for_status()
                laser_data = response.json()

            if not laser_data:
                logger.warning(f"No lasers found for berth {berth_id}")
                return {
                    "success": False,
                    "berth_id": berth_id,
                    "message": f"No lasers found for berth {berth_id}",
                    "lasers_found": [],
                    "berthing_activated": False
                }

            # Step 2: Filter devices by laser_type_id and categorize them
            laser_devices = []  # laser_type_id = 1
            lidar_devices = []  # laser_type_id = 2

            for laser in laser_data:
                laser_type_id = laser.get('laser_type_id')
                sensor_id = laser.get('serial')

                if sensor_id:
                    # Store laser info including name_for_pager for this sensor
                    self.berthing_mode_laser_info[sensor_id] = laser
                    # Store berth_id and berthing_id for this sensor
                    self.berthing_mode_sensor_berth_info[sensor_id] = {
                        "berth_id": berth_id,
                        "berthing_id": berthing_id
                    }

                    # Categorize by device type
                    if laser_type_id == 1:
                        laser_devices.append(sensor_id)
                        logger.debug(f"Categorized {sensor_id} as laser device")
                    elif laser_type_id == 2:
                        lidar_devices.append(sensor_id)
                        logger.debug(f"Categorized {sensor_id} as lidar device")
                    else:
                        logger.warning(f"Unknown laser_type_id {laser_type_id} for sensor {sensor_id}, defaulting to lidar")
                        lidar_devices.append(sensor_id)

            logger.info(f"Found {len(laser_devices)} laser devices and {len(lidar_devices)} lidar devices for berth {berth_id}")
            logger.info(f"Laser devices: {laser_devices}")
            logger.info(f"Lidar devices: {lidar_devices}")

            # Step 3: Handle laser devices (drift compensation)
            laser_success = True
            if laser_devices:
                try:
                    from .laser_manager import laser_manager
                    logger.info(f"Starting laser drift compensation for devices: {laser_devices}")

                    # Configure laser manager for this berth
                    laser_manager.configure_laser_usage(use_laser=True, berth_id=berth_id)

                    # Start laser drift compensation
                    if not laser_manager.start_laser_drift_compensation():
                        logger.error("Failed to start laser drift compensation")
                        laser_success = False
                    else:
                        logger.info("Laser drift compensation started successfully")
                except Exception as laser_err:
                    logger.error(f"Error starting laser operations: {laser_err}")
                    laser_success = False

            # Step 4: Handle lidar devices (berthing mode)
            lidar_success = True
            berthing_result = None
            if lidar_devices:
                logger.info(f"Enabling berthing mode for lidar devices: {lidar_devices}")

                # Check if berthing mode is already active with other sensors
                already_active = self.berthing_mode_active and len(self.berthing_mode_sensors) > 0
                if already_active:
                    logger.info(f"Berthing mode already active with {len(self.berthing_mode_sensors)} sensors, adding berth {berth_id} lidar sensors")

                berthing_result = await self.enable_berthing_mode(lidar_devices, computer_ip)

                # Update the result to reflect that this berth was added to existing berthing mode
                if already_active and berthing_result.get("active"):
                    berthing_result["message"] = f"Berth {berth_id} lidar sensors added to existing berthing mode"

                lidar_success = berthing_result.get("active", False)

            # Step 5: Start database streamer if auto_update is enabled
            db_streamer_started = False
            if auto_update and ((berthing_result and berthing_result.get("streaming_sensors")) or laser_devices):
                try:
                    logger.info(f"Starting database streamer for berth {berth_id} devices")
                    from .db_streamer_service import db_streamer_service
                    # Start streaming for both laser and lidar devices
                    all_streaming_devices = []
                    if berthing_result and berthing_result.get("streaming_sensors"):
                        all_streaming_devices.extend(berthing_result["streaming_sensors"])
                    all_streaming_devices.extend(laser_devices)

                    if all_streaming_devices:
                        await db_streamer_service.start_db_streaming(all_streaming_devices)
                        db_streamer_started = True
                        logger.info(f"Database streamer started for berth {berth_id}")
                except Exception as db_err:
                    logger.error(f"Failed to start database streamer for berth {berth_id}: {db_err}")

            # Step 6: Return comprehensive result
            overall_success = laser_success and lidar_success
            result = {
                "success": overall_success,
                "berth_id": berth_id,
                "lasers_found": laser_data,
                "laser_devices": laser_devices,
                "lidar_devices": lidar_devices,
                "laser_operations_success": laser_success,
                "lidar_operations_success": lidar_success,
                "berthing_result": berthing_result,
                "db_consumer_started": db_streamer_started,
                "message": f"Berthing operations {'successful' if overall_success else 'partially failed'} for berth {berth_id}: lasers={laser_success}, lidars={lidar_success}"
            }

            logger.info(f"Berth {berth_id} activation result: {result['message']}")
            return result

        except httpx.RequestError as req_err:
            logger.exception(f"Network error querying lasers for berth {berth_id}: {req_err}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Network error querying database for berth {berth_id}: {str(req_err)}",
                "berthing_activated": False
            }
        except httpx.HTTPStatusError as http_err:
            logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {http_err.response.status_code} - {http_err.response.text}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Database error querying berth {berth_id}: {http_err.response.status_code}",
                "berthing_activated": False
            }
        except Exception as e:
            logger.error(f"Error enabling berthing by berth {berth_id}: {str(e)}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Error enabling berthing for berth {berth_id}: {str(e)}",
                "berthing_activated": False
            }

    async def disable_berthing_by_berth(self, berth_id: int) -> Dict[str, Any]:
        """
        Disable berthing mode for all lasers associated with a specific berth.

        This method:
        1. Queries the database via PostgREST to get all lasers for the berth
        2. Disables berthing mode for those lasers
        3. Stops database streaming if it was started for this berth

        Args:
            berth_id: The berth ID to deactivate berthing for

        Returns:
            Dictionary with deactivation results
        """
        import httpx
        from ..core.config import settings

        logger.info(f"Disabling berthing mode for berth {berth_id}")

        try:
            # Step 1: Query database for lasers associated with this berth via PostgREST
            logger.info(f"Querying database for lasers associated with berth {berth_id}")

            # Make direct PostgREST request (similar to how DatabaseConsumer makes requests)
            postgrest_url = f"{settings.DB_HOST}/rpc/get_lasers_by_berth"
            headers = {'Content-Type': 'application/json'}
            payload = {"p_berth_id": berth_id}

            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(postgrest_url, headers=headers, json=payload)
                response.raise_for_status()
                laser_data = response.json()

            if not laser_data:
                logger.warning(f"No lasers found for berth {berth_id}")
                return {
                    "success": False,
                    "berth_id": berth_id,
                    "message": f"No lasers found for berth {berth_id}",
                    "lasers_found": [],
                    "berthing_deactivated": False
                }

            # Extract sensor IDs from the laser data
            sensor_ids = []
            for laser in laser_data:
                sensor_id = laser.get('serial')
                if sensor_id:
                    sensor_ids.append(sensor_id)

            logger.info(f"Found {len(sensor_ids)} sensors for berth {berth_id}: {sensor_ids}")

            # Clear laser info and berth info for sensors being disabled
            for sensor_id in sensor_ids:
                self.berthing_mode_laser_info.pop(sensor_id, None)
                self.berthing_mode_sensor_berth_info.pop(sensor_id, None)

            if not sensor_ids:
                logger.warning(f"No valid sensor IDs found in laser data for berth {berth_id}")
                return {
                    "success": False,
                    "berth_id": berth_id,
                    "message": f"No valid sensor IDs found for berth {berth_id}",
                    "lasers_found": laser_data,
                    "berthing_deactivated": False
                }

            # Step 2: Disable berthing mode for the discovered sensors
            logger.info(f"Disabling berthing mode for sensors: {sensor_ids}")
            berthing_result = await self.disable_berthing_mode(sensor_ids)

            # Step 3: Stop database streaming if it was started for this berth
            db_streaming_stopped = False
            try:
                logger.info(f"Stopping database streaming for berth {berth_id} sensors")
                from .db_streamer_service import db_streamer_service
                await db_streamer_service.stop_db_streaming()
                db_streaming_stopped = True
                logger.info(f"Database streaming stopped for berth {berth_id}")
            except Exception as db_err:
                logger.error(f"Failed to stop database streaming for berth {berth_id}: {db_err}")

            # Step 4: Return comprehensive result
            # Success is based on whether the sensors for this berth were successfully disabled,
            # not on whether berthing mode is globally inactive (which would fail when multiple berths are active)
            sensors_successfully_disabled = len(berthing_result.get("disconnected_sensors", []))
            sensors_failed = len(berthing_result.get("failed_sensors", []))
            berth_success = sensors_successfully_disabled > 0 and sensors_failed == 0

            result = {
                "success": berth_success,  # Success if sensors for this berth were disabled
                "berth_id": berth_id,
                "lasers_found": laser_data,
                "sensor_ids": sensor_ids,
                "berthing_result": berthing_result,
                "db_streaming_stopped": db_streaming_stopped,
                "message": f"Berth {berth_id} {'deactivated successfully' if berth_success else 'deactivation failed'}: {sensors_successfully_disabled} sensors stopped, {sensors_failed} failed"
            }

            logger.info(f"Berth {berth_id} deactivation result: {result['message']}")
            return result

        except httpx.RequestError as req_err:
            logger.exception(f"Network error querying lasers for berth {berth_id}: {req_err}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Network error querying database for berth {berth_id}: {str(req_err)}",
                "berthing_deactivated": False
            }
        except httpx.HTTPStatusError as http_err:
            logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {http_err.response.status_code} - {http_err.response.text}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Database error querying berth {berth_id}: {http_err.response.status_code}",
                "berthing_deactivated": False
            }
        except Exception as e:
            logger.error(f"Error disabling berthing by berth {berth_id}: {str(e)}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Error disabling berthing for berth {berth_id}: {str(e)}",
                "berthing_deactivated": False
            }


lidar_manager = LidarManager()
