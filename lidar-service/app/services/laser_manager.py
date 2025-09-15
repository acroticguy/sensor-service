"""
Laser Manager for handling laser devices via WebSocket connections.

This module manages laser devices that send data via WebSockets from external servers,
providing device-specific operations and data handling.
"""

import asyncio
import json
import time
import math
import threading
import queue
from datetime import datetime
from typing import Dict, List, Optional, Any
import requests
import websockets
from websockets.exceptions import ConnectionClosedError, WebSocketException
from ..core.logging_config import logger
from ..core.config import settings

class LaserManager:
    """
    Manages laser devices connected via WebSockets and handles laser-specific operations.
    """

    def __init__(self):
        # Single WebSocket connection to external laser manager
        self.websocket_url: Optional[str] = None
        self.websocket_connection: Optional[Any] = None
        self.connection_active: bool = False

        # Laser device tracking (received from external manager)
        self.laser_devices: Dict[str, Dict[str, Any]] = {}
        self.device_queues: Dict[str, queue.Queue] = {}

        # Synchronization system
        self.sync_coordinator_active: bool = False
        self.sync_coordinator_thread: Optional[threading.Thread] = None
        self.laser_sync_data: Dict[str, Dict] = {}  # Store synchronized data from all lasers
        self.sync_interval: float = 1.0  # 1 second sync interval
        self.last_sync_timestamp: float = 0.0

        # Laser configuration
        self.use_laser: bool = False
        self.berth_id: Optional[int] = None
        self.drift_compensation_active: bool = False

        logger.info("LaserManager initialized for WebSocket-based laser devices")

    def configure_laser_usage(self, use_laser: bool, berth_id: Optional[int] = None) -> bool:
        """
        Configure whether to use laser devices and set the berth ID.

        Args:
            use_laser: Whether to use laser devices
            berth_id: The berth ID for laser operations

        Returns:
            True if configuration successful
        """
        self.use_laser = use_laser
        self.berth_id = berth_id

        if use_laser and berth_id:
            logger.info(f"Laser usage enabled for berth {berth_id}")
        elif use_laser:
            logger.warning("Laser usage enabled but no berth_id specified")
        else:
            logger.info("Laser usage disabled")

        return True

    def start_laser_drift_compensation(self) -> bool:
        """
        Start laser drift compensation by calling the external laser service.

        Returns:
            True if drift compensation started successfully, False otherwise
        """
        if not self.use_laser:
            logger.info("Laser usage is disabled, skipping drift compensation")
            return True

        if not self.berth_id:
            logger.error("No berth_id configured for laser operations")
            return False

        try:
            # Call the laser drift start endpoint
            drift_url = f"http://{settings.LASER_SERVICE_ADDR}/api/v1/berthing/drift/start"
            payload = {"berth_id": self.berth_id}

            logger.info(f"Starting laser drift compensation for berth {self.berth_id}")

            response = requests.post(drift_url, json=payload)

            if response.status_code == 200:
                self.drift_compensation_active = True
                logger.info(f"Laser drift compensation started successfully for berth {self.berth_id}")
                return True
            else:
                logger.error(f"Failed to start laser drift compensation: HTTP {response.status_code} - {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error starting laser drift compensation: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error starting laser drift compensation: {str(e)}")
            return False

    def stop_laser_drift_compensation(self) -> bool:
        """
        Stop laser drift compensation by calling the external laser service.

        Returns:
            True if drift compensation stopped successfully, False otherwise
        """
        if not self.use_laser or not self.drift_compensation_active:
            logger.info("Laser drift compensation not active")
            return True

        if not self.berth_id:
            logger.error("No berth_id configured for laser operations")
            return False

        try:
            # Call the laser drift stop endpoint
            drift_url = f"http://{settings.LASER_SERVICE_ADDR}/api/v1/berthing/drift/stop"
            payload = {"berth_id": self.berth_id}

            logger.info(f"Stopping laser drift compensation for berth {self.berth_id}")

            response = requests.post(drift_url, json=payload)

            if response.status_code == 200:
                self.drift_compensation_active = False
                logger.info(f"Laser drift compensation stopped successfully for berth {self.berth_id}")
                return True
            else:
                logger.error(f"Failed to stop laser drift compensation: HTTP {response.status_code} - {response.text}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error stopping laser drift compensation: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error stopping laser drift compensation: {str(e)}")
            return False

    def connect_to_laser_manager(self, berth_id: Optional[int] = None) -> bool:
        """
        Connect to the external laser manager via WebSocket.

        Args:
            berth_id: Optional berth ID (uses configured berth_id if not provided)

        Returns:
            True if connection successful, False otherwise
        """
        if not self.use_laser:
            logger.info("Laser usage is disabled, skipping WebSocket connection")
            return True

        berth_to_use = berth_id or self.berth_id
        if not berth_to_use:
            logger.error("No berth_id available for laser manager connection")
            return False

        try:
            if self.connection_active:
                logger.warning("Already connected to laser manager")
                return False

            # Use WebSocket URL format
            websocket_url = f"ws://{settings.LASER_SERVICE_ADDR}/api/v1/berthing/ws/berth/{berth_to_use}"

            self.websocket_url = websocket_url
            self.connection_active = True

            # Start WebSocket data reception thread
            thread = threading.Thread(
                target=self._websocket_reception_thread,
                daemon=True
            )
            thread.start()

            logger.info(f"Connecting to laser manager at: {websocket_url}")
            return True

        except Exception as e:
            logger.error(f"Error connecting to laser manager: {str(e)}")
            self.connection_active = False
            return False

    def disconnect_from_laser_manager(self) -> bool:
        """
        Disconnect from the external laser manager.

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.connection_active:
                logger.warning("Not connected to laser manager")
                return True

            self.connection_active = False

            # Close WebSocket connection
            if self.websocket_connection:
                try:
                    # Add actual WebSocket close logic here
                    pass
                except Exception as e:
                    logger.debug(f"Error closing WebSocket: {e}")
                finally:
                    self.websocket_connection = None

            # Clear all device queues
            for device_id in list(self.device_queues.keys()):
                while not self.device_queues[device_id].empty():
                    try:
                        self.device_queues[device_id].get_nowait()
                    except:
                        break

            # Clear laser devices
            self.laser_devices.clear()
            self.device_queues.clear()

            logger.info("Disconnected from laser manager")
            return True

        except Exception as e:
            logger.error(f"Error disconnecting from laser manager: {str(e)}")
            return False

    def start_sync_coordinator(self) -> bool:
        """
        Start the synchronization coordinator for all laser devices.

        Returns:
            True if successful, False otherwise
        """
        if self.sync_coordinator_active:
            logger.warning("Sync coordinator already active")
            return True

        if not self.connection_active:
            logger.error("Not connected to laser manager")
            return False

        try:
            self.sync_coordinator_active = True
            self.sync_coordinator_thread = threading.Thread(
                target=self._sync_coordinator_thread,
                daemon=True
            )
            self.sync_coordinator_thread.start()
            logger.info("Started sync coordinator for laser devices")
            return True

        except Exception as e:
            logger.error(f"Error starting sync coordinator: {str(e)}")
            self.sync_coordinator_active = False
            return False

    def stop_sync_coordinator(self) -> bool:
        """
        Stop the synchronization coordinator.

        Returns:
            True if successful, False otherwise
        """
        if not self.sync_coordinator_active:
            return True

        try:
            self.sync_coordinator_active = False
            if self.sync_coordinator_thread:
                self.sync_coordinator_thread.join(timeout=2.0)
                self.sync_coordinator_thread = None
            logger.info("Stopped sync coordinator")
            return True

        except Exception as e:
            logger.error(f"Error stopping sync coordinator: {str(e)}")
            return False

    def _parse_laser_packet(self, packet_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse incoming laser packet and convert to standardized format.

        Args:
            packet_data: Raw packet data from WebSocket

        Returns:
            Parsed packet in standardized format or None if invalid
        """
        try:
            # Handle different packet types
            packet_type = packet_data.get("type")

            # Skip non-data packets
            if packet_type not in ["laser_data", "sensor_data"]:
                logger.debug(f"Ignoring packet type: {packet_type}")
                return None

            # Extract laser/sensor ID
            laser_id = packet_data.get("laser_id") or packet_data.get("sensor_id")
            if laser_id is None:
                logger.warning(f"No laser_id or sensor_id in packet: {packet_data}")
                return None

            # Convert timestamp from ISO format to epoch
            timestamp_str = packet_data.get("timestamp")
            if timestamp_str:
                try:
                    # Parse ISO timestamp and convert to epoch
                    dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    epoch_timestamp = dt.timestamp()
                except Exception as e:
                    logger.warning(f"Error parsing timestamp {timestamp_str}: {e}")
                    epoch_timestamp = time.time()
            else:
                epoch_timestamp = time.time()

            # Extract measurements - handle nested data structure
            data = packet_data.get("data", {})

            # If data is nested, use it; otherwise use packet_data directly
            if data:
                distance = data.get("distance", packet_data.get("distance", 0.0))
                speed = data.get("speed", packet_data.get("speed", 0.0))
                temperature = data.get("temperature", packet_data.get("temperature", 0.0))
                strength = data.get("signal_strength", data.get("strength", packet_data.get("strength", 0)))
            else:
                # Fallback to direct fields
                distance = packet_data.get("distance", packet_data.get("range", 0.0))
                speed = packet_data.get("speed", packet_data.get("velocity", 0.0))
                temperature = packet_data.get("temperature", packet_data.get("temp", 0.0))
                strength = packet_data.get("strength", packet_data.get("intensity", 0))


            # Convert to standardized LiDAR-like format
            device_id = f"LASER_{laser_id}"

            standardized_packet = {
                "timestamp": epoch_timestamp,
                "sensor_id": device_id,
                "data_available": True,
                "device_type": "laser",
                "total_points_captured": 1,
                "status": "streaming",
                "points": [{
                    "point_id": 0,
                    "x": distance,  # Distance as x-coordinate
                    "y": 0.0,
                    "z": 0.0,
                    "distance": distance,
                    "intensity": strength,  # Use strength as intensity
                    "speed": speed,
                    "temperature": temperature,
                    "collection_time": epoch_timestamp
                }],
                "capture_stats": {
                    "packets_total": 1,
                    "points_per_second": 1
                },
                "laser_specific": {
                    "laser_id": laser_id,
                    "strength": strength,
                    "temperature": temperature,
                    "speed": speed
                }
            }

            return standardized_packet

        except Exception as e:
            logger.error(f"Error parsing laser packet: {str(e)}")
            return None

    def _websocket_reception_thread(self):
        """
        WebSocket reception thread that connects to the laser manager and receives data packets.
        """
        logger.info("WebSocket reception thread started")

        # Create event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Run the WebSocket client in the event loop
            loop.run_until_complete(self._websocket_client_loop())
        except Exception as e:
            logger.error(f"Error in WebSocket reception thread: {str(e)}")
        finally:
            loop.close()

        logger.info("WebSocket reception thread stopped")

    async def _websocket_client_loop(self):
        """
        Main WebSocket client loop that connects and listens for messages.
        """
        while self.connection_active and self.websocket_url:
            try:
                logger.info(f"Connecting to WebSocket: {self.websocket_url}")
                async with websockets.connect(self.websocket_url) as websocket:
                    logger.info("WebSocket connected to laser manager")

                    # Send initial connection message if needed
                    await websocket.send(json.dumps({
                        "type": "connection_request",
                        "berth_id": self.berth_id,
                        "client_type": "laser_manager"
                    }))

                    while self.connection_active:
                        try:
                            # Receive message from WebSocket
                            message = await asyncio.wait_for(websocket.recv(), timeout=1.0)

                            # Skip non-text messages (ping, pong, etc.)
                            if not isinstance(message, str):
                                logger.debug(f"Skipping non-text WebSocket message: {type(message)}")
                                continue

                            # Try to parse JSON
                            try:
                                packet_data = json.loads(message)
                                logger.debug(f"Received WebSocket message: {packet_data}")
                            except json.JSONDecodeError as json_err:
                                logger.debug(f"Skipping non-JSON WebSocket message: {message[:100]}... Error: {json_err}")
                                continue

                            # Process the received packet
                            await self._process_websocket_message(packet_data)

                        except asyncio.TimeoutError:
                            # Timeout is normal, continue listening
                            continue
                        except (ConnectionClosedError, WebSocketException) as e:
                            logger.warning(f"WebSocket connection error: {str(e)}")
                            break

            except (ConnectionClosedError, WebSocketException, OSError) as e:
                logger.warning(f"WebSocket connection failed: {str(e)}")
                if self.connection_active:
                    logger.info("Retrying WebSocket connection in 5 seconds...")
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket client loop: {str(e)}")
                if self.connection_active:
                    await asyncio.sleep(5)

    async def _process_websocket_message(self, packet_data: Dict[str, Any]):
        """
        Process incoming WebSocket messages from the laser manager.

        Args:
            packet_data: The received packet data
        """
        try:
            current_time = time.time()
            logger.debug(f"Processing WebSocket message: {packet_data}")


            # Parse the laser packet
            parsed_packet = self._parse_laser_packet(packet_data)

            if parsed_packet:
                device_id = parsed_packet["sensor_id"]

                # Register laser device if not already known
                if device_id not in self.laser_devices:
                    laser_id = packet_data.get("laser_id") or packet_data.get("sensor_id")
                    self.laser_devices[device_id] = {
                        'device_id': device_id,
                        'device_type': 'laser',
                        'laser_id': laser_id,
                        'status': 'active',
                        'last_data_time': current_time
                    }
                    self.device_queues[device_id] = queue.Queue(maxsize=10000)
                    logger.info(f"Discovered new laser device: {device_id} (laser_id: {laser_id})")

                # Add collection timestamp
                parsed_packet["collection_time"] = current_time

                # Store data in sync buffer
                with threading.Lock():
                    if device_id not in self.laser_sync_data:
                        self.laser_sync_data[device_id] = {
                            "data_points": [],
                            "packets_received": 0,
                            "last_collection_time": 0.0,
                            "ready_for_sync": False
                        }

                    # Add new data point to sync buffer
                    self.laser_sync_data[device_id]["data_points"].append(parsed_packet)
                    self.laser_sync_data[device_id]["packets_received"] += 1
                    self.laser_sync_data[device_id]["last_collection_time"] = current_time

                    # Keep only last 2 seconds of data to prevent memory bloat
                    cutoff_time = current_time - 2.0
                    self.laser_sync_data[device_id]["data_points"] = [
                        p for p in self.laser_sync_data[device_id]["data_points"]
                        if p.get("collection_time", 0) >= cutoff_time
                    ]

                # Update device status
                self.laser_devices[device_id]['last_data_time'] = current_time

                logger.debug(f"Processed laser packet for device {device_id}")

            else:
                logger.debug(f"Failed to parse packet: {packet_data}")

        except Exception as e:
            logger.error(f"Error processing WebSocket message: {str(e)}")
            logger.error(f"Packet data was: {packet_data}")


    def _sync_coordinator_thread(self):
        """
        Coordinator thread that triggers synchronized data collection every second.
        """
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

                # Process data from all laser devices at this exact moment
                synchronized_data = {}

                with threading.Lock():
                    for device_id in list(self.laser_sync_data.keys()):
                        device_data = self.laser_sync_data[device_id]

                        # Get all data points collected in the last second
                        recent_points = [
                            p for p in device_data["data_points"]
                            if exact_second - 1.0 <= p.get("collection_time", 0) <= exact_second
                        ]

                        if recent_points:
                            # Use the most recent data point for synchronization
                            latest_point = recent_points[-1]
                            logger.debug(f"Sync processing {len(recent_points)} points for {device_id}, latest: {latest_point}")

                            # Extract laser-specific data
                            laser_specific = latest_point.get("laser_specific", {})

                            synchronized_data[device_id] = {
                                "timestamp": exact_second,
                                "sensor_id": device_id,
                                "data_available": True,
                                "device_type": "laser",
                                "total_points_captured": len(recent_points),
                                "status": "synchronized_streaming",
                                "points": [{
                                    "point_id": 0,
                                    "x": latest_point["points"][0]["distance"],
                                    "y": 0.0,
                                    "z": 0.0,
                                    "distance": latest_point["points"][0]["distance"],
                                    "intensity": laser_specific.get("strength", 0),
                                    "speed": laser_specific.get("speed", 0.0),
                                    "temperature": laser_specific.get("temperature", 0.0),
                                    "collection_time": exact_second,
                                }],
                                "sync_quality": "high",  # All lasers synchronized
                                "capture_stats": {
                                    "packets_total": device_data["packets_received"],
                                    "points_per_second": len(recent_points)
                                },
                                "laser_data": {
                                    "laser_id": laser_specific.get("laser_id"),
                                    "strength": laser_specific.get("strength", 0),
                                    "temperature": laser_specific.get("temperature", 0.0),
                                    "speed": laser_specific.get("speed", 0.0)
                                }
                            }

                            # Store synchronized data in laser_sync_data for WS access
                            with threading.Lock():
                                self.laser_sync_data[device_id]["synchronized_data"] = synchronized_data[device_id]
                        else:
                            # No data for this laser at sync time
                            synchronized_data[device_id] = {
                                "timestamp": exact_second,
                                "sensor_id": device_id,
                                "data_available": False,
                                "device_type": "laser",
                                "status": "sync_no_data",
                                "sync_quality": "degraded"
                            }

                # Put synchronized data into device queues
                for device_id, data in synchronized_data.items():
                    if device_id in self.device_queues:
                        try:
                            # Encode synchronized data
                            data_str = json.dumps(data)
                            self.device_queues[device_id].put(data_str)
                        except Exception as e:
                            logger.error(f"Error queuing sync data for {device_id}: {str(e)}")

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

    def get_laser_data_generator(self, device_id: str):
        """
        Generator for streaming laser device data.

        Args:
            device_id: Laser device to get data for

        Yields:
            Device data as JSON string
        """
        while self.connection_active and device_id in self.device_queues:
            try:
                if not self.device_queues[device_id].empty():
                    data = self.device_queues[device_id].get(timeout=0.1)
                    yield data
                else:
                    yield None
            except:
                yield None

    def get_all_laser_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get information about all discovered laser devices."""
        return self.laser_devices.copy()

    def get_laser_device_info(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific laser device."""
        return self.laser_devices.get(device_id)

    def is_connected(self) -> bool:
        """Check if connected to the laser manager."""
        return self.connection_active

    def get_synchronized_laser_data(self, exact_second: float) -> Dict[str, Dict[str, Any]]:
        """
        Get synchronized laser data for a specific timestamp without queuing.

        This method processes raw laser data points and returns synchronized data
        for the device_manager's unified sync coordinator.

        Args:
            exact_second: The exact second timestamp to synchronize to

        Returns:
            Dictionary of synchronized laser data by device_id
        """
        synchronized_data = {}

        try:
            with threading.Lock():
                for device_id, device_data in self.laser_sync_data.items():
                    # Get all data points collected in the last second
                    recent_points = [
                        p for p in device_data["data_points"]
                        if exact_second - 1.0 <= p.get("collection_time", 0) <= exact_second
                    ]

                    if recent_points:
                        # Use the most recent data point for synchronization
                        latest_point = recent_points[-1]
                        logger.debug(f"Sync processing {len(recent_points)} points for {device_id}")

                        # Extract laser-specific data
                        laser_specific = latest_point.get("laser_specific", {})

                        synchronized_data[device_id] = {
                            "timestamp": exact_second,
                            "sensor_id": device_id,
                            "data_available": True,
                            "device_type": "laser",
                            "total_points_captured": len(recent_points),
                            "status": "synchronized_streaming",
                            "points": [{
                                "point_id": 0,
                                "x": latest_point["points"][0]["distance"],
                                "y": 0.0,
                                "z": 0.0,
                                "distance": latest_point["points"][0]["distance"],
                                "intensity": laser_specific.get("strength", 0),
                                "speed": laser_specific.get("speed", 0.0),
                                "temperature": laser_specific.get("temperature", 0.0),
                                "collection_time": exact_second
                            }],
                            "sync_quality": "high",  # All lasers synchronized
                            "capture_stats": {
                                "packets_total": device_data["packets_received"],
                                "points_per_second": len(recent_points)
                            },
                            "laser_data": {
                                "laser_id": laser_specific.get("laser_id"),
                                "strength": laser_specific.get("strength", 0),
                                "temperature": laser_specific.get("temperature", 0.0),
                                "speed": laser_specific.get("speed", 0.0)
                            }
                        }
                    else:
                        # No data for this laser at sync time
                        synchronized_data[device_id] = {
                            "timestamp": exact_second,
                            "sensor_id": device_id,
                            "data_available": False,
                            "device_type": "laser",
                            "status": "sync_no_data",
                            "sync_quality": "degraded"
                        }

            return synchronized_data

        except Exception as e:
            logger.error(f"Error synchronizing laser data: {e}")
            return {}

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status of the laser manager."""
        return {
            "use_laser": self.use_laser,
            "berth_id": self.berth_id,
            "connection_active": self.connection_active,
            "drift_compensation_active": self.drift_compensation_active,
            "sync_coordinator_active": self.sync_coordinator_active,
            "websocket_url": self.websocket_url,
            "laser_devices_count": len(self.laser_devices),
            "last_sync_timestamp": self.last_sync_timestamp
        }


# Global laser manager instance
laser_manager = LaserManager()