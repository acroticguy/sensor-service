"""
Device Manager - Abstraction layer for managing multiple device types.

This module provides a unified interface for managing different types of devices
(LiDAR, Laser, etc.) by coordinating with their respective manager services.
Handles data pooling, synchronization, and provides a consistent API.
"""

import asyncio
import json
import time
import math
import threading
import queue
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
import requests
import httpx
from ..core.logging_config import logger
from ..core.config import settings
from ..core.http_utils import get_lasers_by_berth
from ..models.lidar import OperationType


class DeviceManager:
    """
    Unified device manager that coordinates multiple device types through their
    respective manager services, providing synchronized data pooling.
    """

    def __init__(self):
        # Device type managers
        self.lidar_manager = None
        self.laser_manager = None

        # Unified device tracking
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.device_queues: Dict[str, queue.Queue] = {}

        # Synchronization system
        self.sync_coordinator_active: bool = False
        self.sync_coordinator_thread: Optional[threading.Thread] = None
        self.device_sync_data: Dict[str, Dict] = {}  # Store synchronized data from all devices
        self.sync_interval: float = 1.0  # 1 second sync interval
        self.last_sync_timestamp: float = 0.0

        # Configuration
        self.use_lidar: bool = True  # Default to using LiDAR
        self.use_laser: bool = False
        self.berth_id: Optional[int] = None

        # Current operation tracking
        self.current_operation: Optional[OperationType] = None
        self.current_berthing_id: Optional[int] = None

        logger.info("DeviceManager initialized as unified device abstraction layer")

    def configure_devices(self, use_lidar: bool = True, use_laser: bool = False, berth_id: Optional[int] = None) -> bool:
        """
        Configure which device types to use and their settings.

        Args:
            use_lidar: Whether to use LiDAR devices
            use_laser: Whether to use laser devices
            berth_id: Berth ID for laser operations

        Returns:
            True if configuration successful
        """
        self.use_lidar = use_lidar
        self.use_laser = use_laser
        self.berth_id = berth_id

        logger.info(f"Device configuration: LiDAR={use_lidar}, Laser={use_laser}, Berth={berth_id}")

        # Import managers dynamically to avoid circular imports
        if use_lidar:
            try:
                from .lidar_manager import lidar_manager
                self.lidar_manager = lidar_manager
                logger.info("LiDAR manager connected")
            except ImportError as e:
                logger.error(f"Failed to import LiDAR manager: {e}")
                return False

        if use_laser:
            try:
                from .laser_manager import laser_manager
                self.laser_manager = laser_manager
                # Configure laser manager with berth_id
                if berth_id:
                    self.laser_manager.configure_laser_usage(use_laser=True, berth_id=berth_id)
                logger.info("Laser manager connected")
            except ImportError as e:
                logger.error(f"Failed to import laser manager: {e}")
                return False

        return True

    def start_operation(self, operation_name: str = "general") -> bool:
        """
        Start an operation across all configured device types.

        Args:
            operation_name: Name of the operation being started

        Returns:
            True if operation started successfully
        """
        logger.info(f"Starting operation '{operation_name}' across all device types")

        success = True

        # Start laser drift compensation if laser is enabled
        if self.use_laser and self.laser_manager:
            logger.info(f"Starting laser drift compensation for operation '{operation_name}'")
            if not self.laser_manager.start_laser_drift_compensation():
                logger.error(f"Failed to start laser drift compensation for '{operation_name}'")
                success = False

        # Start LiDAR operations if enabled
        if self.use_lidar and self.lidar_manager:
            logger.info(f"Starting LiDAR operations for '{operation_name}'")
            # LiDAR manager operations would be started here
            pass

        if success:
            logger.info(f"Operation '{operation_name}' started successfully")
        else:
            logger.error(f"Operation '{operation_name}' failed to start completely")

        return success

    def stop_operation(self, operation_name: str = "general") -> bool:
        """
        Stop an operation across all configured device types.

        Args:
            operation_name: Name of the operation being stopped

        Returns:
            True if operation stopped successfully
        """
        logger.info(f"Stopping operation '{operation_name}' across all device types")

        success = True

        # Stop laser operations if enabled
        if self.use_laser and self.laser_manager:
            logger.info(f"Stopping laser operations for '{operation_name}'")
            if not self.laser_manager.stop_laser_drift_compensation():
                logger.warning(f"Failed to stop laser drift compensation for '{operation_name}'")
                success = False

        # Stop LiDAR operations if enabled
        if self.use_lidar and self.lidar_manager:
            logger.info(f"Stopping LiDAR operations for '{operation_name}'")
            # LiDAR manager cleanup would happen here
            pass

        logger.info(f"Operation '{operation_name}' stopped")
        return success

    async def start_operation_by_berth(self, berth_id: int, operation_type: OperationType, berthing_id: int, auto_update: bool = False) -> Dict[str, Any]:
        """
        Start berthing operation for all lasers associated with a specific berth.

        This method:
        1. Queries the database via PostgREST to get all lasers for the berth
        2. Filters lasers by laser_type_id (1=laser, 2=lidar)
        3. Starts drift compensation for lasers
        4. Enables berthing mode for lidars
        5. Starts database consumer if auto_update is True

        Args:
            berth_id: The berth ID to activate operation for
            operation_type: The type of operation (BERTH, DRIFT, or UNMOOR)
            berthing_id: The berthing operation ID
            auto_update: Whether to start the database consumer for automatic data streaming

        Returns:
            Dictionary with operation results
        """
        logger.info(f"Starting {operation_type.value} operation for berth {berth_id}, berthing_id {berthing_id}")

        # Set current operation
        self.current_operation = operation_type
        self.current_berthing_id = berthing_id

        try:
            # Step 1: Query database for lasers associated with this berth via PostgREST
            logger.info(f"Querying database for lasers associated with berth {berth_id}")

            laser_data = await get_lasers_by_berth(berth_id, settings.DB_HOST)

            if not laser_data:
                logger.warning(f"No lasers found for berth {berth_id}")
                return {
                    "success": False,
                    "berth_id": berth_id,
                    "berthing_id": berthing_id,
                    "operation_type": operation_type.value,
                    "message": f"No lasers found for berth {berth_id}",
                    "lasers_found": [],
                    "lasers_count": 0,
                    "lidars_count": 0,
                    "operation_started": False
                }

            # Step 2: Filter lasers by laser_type_id
            lasers = []
            lidars = []

            for laser in laser_data:
                laser_type_id = laser.get('laser_type_id')
                if laser_type_id == 1:  # Laser
                    lasers.append(laser)
                elif laser_type_id == 2:  # Lidar
                    lidars.append(laser)

            logger.info(f"Found {len(lasers)} lasers and {len(lidars)} lidars for berth {berth_id}")

            # Step 3: Start operations for lasers and lidars concurrently
            laser_success = True
            lidar_success = True
            berthing_result = None

            # Prepare concurrent tasks
            tasks = []

            # Task for laser operations (connect and start drift compensation)
            if lasers and self.use_laser and self.laser_manager:
                async def start_laser_operations():
                    logger.info(f"Starting laser operations for {len(lasers)} lasers")

                    # First connect to laser manager
                    connect_success = await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.connect_to_laser_manager, berth_id)
                    if not connect_success:
                        logger.error("Failed to connect to laser manager")
                        return False

                    # Then start drift compensation
                    drift_success = await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.start_laser_drift_compensation)
                    if not drift_success:
                        logger.error("Failed to start laser drift compensation")
                        return False

                    # Start sync coordinator for laser data
                    sync_success = await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.start_sync_coordinator)
                    if not sync_success:
                        logger.error("Failed to start laser sync coordinator")
                        return False

                    logger.info("Laser operations started successfully")
                    return True

                tasks.append(start_laser_operations())

            # Task for lidar berthing mode enable
            lidar_sensor_ids = [lidar.get('serial') for lidar in lidars if lidar.get('serial')]
            if lidars and self.use_lidar and self.lidar_manager and lidar_sensor_ids:
                async def enable_lidar_berthing():
                    logger.info(f"Enabling berthing mode for {len(lidar_sensor_ids)} lidars: {lidar_sensor_ids}")
                    result = await self.lidar_manager.enable_berthing_mode(lidar_sensor_ids)
                    if not result.get("active"):
                        logger.error("Failed to enable berthing mode for lidars")
                    return result

                tasks.append(enable_lidar_berthing())

            # Execute tasks concurrently
            if tasks:
                logger.info(f"Starting {len(tasks)} operations concurrently: laser drift compensation and lidar berthing mode")
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                result_idx = 0
                if lasers and self.use_laser and self.laser_manager:
                    laser_result = results[result_idx]
                    if isinstance(laser_result, Exception):
                        logger.error(f"Exception in laser drift compensation: {laser_result}")
                        laser_success = False
                    else:
                        laser_success = laser_result
                    result_idx += 1

                if lidars and self.use_lidar and self.lidar_manager and lidar_sensor_ids:
                    lidar_result = results[result_idx]
                    if isinstance(lidar_result, Exception):
                        logger.error(f"Exception in lidar berthing mode: {lidar_result}")
                        lidar_success = False
                        berthing_result = None
                    else:
                        berthing_result = lidar_result
                        lidar_success = berthing_result.get("active", False)
            else:
                logger.info("No operations to start")

            # Step 5: Start database streamer if auto_update is enabled
            db_streamer_started = False
            if auto_update and berthing_result and berthing_result.get("streaming_sensors"):
                try:
                    logger.info(f"Starting database streamer for berth {berth_id} lidars")
                    from .db_streamer_service import db_streamer_service
                    await db_streamer_service.start_db_streaming(berthing_result["streaming_sensors"])
                    db_streamer_started = True
                    logger.info(f"Database streamer started for berth {berth_id}")
                except Exception as db_err:
                    logger.error(f"Failed to start database streamer for berth {berth_id}: {db_err}")

            # Step 6: Return comprehensive result
            operation_success = laser_success and lidar_success

            result = {
                "success": operation_success,
                "berth_id": berth_id,
                "berthing_id": berthing_id,
                "operation_type": operation_type.value,
                "lasers_found": laser_data,
                "lasers_count": len(lasers),
                "lidars_count": len(lidars),
                "laser_operation_success": laser_success,
                "lidar_operation_success": lidar_success,
                "berthing_result": berthing_result,
                "db_consumer_started": db_streamer_started,
                "message": f"{operation_type.value.capitalize()} operation {'started' if operation_success else 'failed'} for berth {berth_id}: {len(lasers)} lasers, {len(lidars)} lidars"
            }

            logger.info(f"Operation {operation_type.value} for berth {berth_id} result: {result['message']}")
            return result

        except httpx.RequestError as req_err:
            logger.exception(f"Network error querying lasers for berth {berth_id}: {req_err}")
            return {
                "success": False,
                "berth_id": berth_id,
                "berthing_id": berthing_id,
                "operation_type": operation_type.value,
                "message": f"Network error querying database for berth {berth_id}: {str(req_err)}",
                "operation_started": False
            }
        except httpx.HTTPStatusError as http_err:
            logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {http_err.response.status_code} - {http_err.response.text}")
            return {
                "success": False,
                "berth_id": berth_id,
                "berthing_id": berthing_id,
                "operation_type": operation_type.value,
                "message": f"Database error querying berth {berth_id}: {http_err.response.status_code}",
                "operation_started": False
            }
        except Exception as e:
            logger.error(f"Error starting {operation_type.value} operation for berth {berth_id}: {str(e)}")
            return {
                "success": False,
                "berth_id": berth_id,
                "berthing_id": berthing_id,
                "operation_type": operation_type.value,
                "message": f"Error starting operation {operation_type.value} for berth {berth_id}: {str(e)}",
                "operation_started": False
            }

    async def stop_operation_by_berth(self, berth_id: int) -> Dict[str, Any]:
        """
        Stop berthing operation for all lasers associated with a specific berth.

        This method:
        1. Queries the database via PostgREST to get all lasers for the berth
        2. Filters lasers by laser_type_id (1=laser, 2=lidar)
        3. Stops drift compensation for lasers
        4. Disables berthing mode for lidars
        5. Stops database streaming if it was started for this berth

        Args:
            berth_id: The berth ID to deactivate operation for

        Returns:
            Dictionary with stop results
        """
        logger.info(f"Stopping operation for berth {berth_id}")

        # Clear current operation if it was for this berth
        if self.current_berthing_id == berth_id:
            self.current_operation = None
            self.current_berthing_id = None

        try:
            # Step 1: Query database for lasers associated with this berth via PostgREST
            logger.info(f"Querying database for lasers associated with berth {berth_id}")

            laser_data = await get_lasers_by_berth(berth_id, settings.DB_HOST)

            if not laser_data:
                logger.warning(f"No lasers found for berth {berth_id}")
                return {
                    "success": False,
                    "berth_id": berth_id,
                    "message": f"No lasers found for berth {berth_id}",
                    "lasers_found": [],
                    "lasers_count": 0,
                    "lidars_count": 0,
                    "operation_stopped": False
                }

            # Step 2: Filter lasers by laser_type_id
            lasers = []
            lidars = []

            for laser in laser_data:
                laser_type_id = laser.get('laser_type_id')
                if laser_type_id == 1:  # Laser
                    lasers.append(laser)
                elif laser_type_id == 2:  # Lidar
                    lidars.append(laser)

            logger.info(f"Found {len(lasers)} lasers and {len(lidars)} lidars for berth {berth_id}")

            # Step 3: Stop operations for lasers and lidars concurrently
            laser_success = True
            lidar_success = True
            berthing_result = None

            # Prepare concurrent tasks
            tasks = []

            # Task for stopping laser operations (stop sync, drift compensation, and disconnect)
            if lasers and self.use_laser and self.laser_manager:
                async def stop_laser_operations():
                    logger.info(f"Stopping laser operations for {len(lasers)} lasers")

                    # Stop sync coordinator first
                    sync_success = await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.stop_sync_coordinator)
                    if not sync_success:
                        logger.warning("Failed to stop laser sync coordinator")

                    # Stop drift compensation
                    drift_success = await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.stop_laser_drift_compensation)
                    if not drift_success:
                        logger.error("Failed to stop laser drift compensation")
                        return False

                    # Disconnect from laser manager
                    disconnect_success = await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.disconnect_from_laser_manager)
                    if not disconnect_success:
                        logger.warning("Failed to disconnect from laser manager")

                    logger.info("Laser operations stopped successfully")
                    return True

                tasks.append(stop_laser_operations())

            # Task for disabling lidar berthing mode
            lidar_sensor_ids = [lidar.get('serial') for lidar in lidars if lidar.get('serial')]
            if lidars and self.use_lidar and self.lidar_manager and lidar_sensor_ids:
                async def disable_lidar_berthing():
                    logger.info(f"Disabling berthing mode for {len(lidar_sensor_ids)} lidars: {lidar_sensor_ids}")
                    result = await self.lidar_manager.disable_berthing_mode(lidar_sensor_ids)
                    if not result.get("active") is False:  # Should be inactive after disable
                        logger.error("Failed to disable berthing mode for lidars")
                    return result

                tasks.append(disable_lidar_berthing())

            # Execute tasks concurrently
            if tasks:
                logger.info(f"Stopping {len(tasks)} operations concurrently: laser drift compensation and lidar berthing mode")
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                result_idx = 0
                if lasers and self.use_laser and self.laser_manager:
                    laser_result = results[result_idx]
                    if isinstance(laser_result, Exception):
                        logger.error(f"Exception in stopping laser drift compensation: {laser_result}")
                        laser_success = False
                    else:
                        laser_success = laser_result
                    result_idx += 1

                if lidars and self.use_lidar and self.lidar_manager and lidar_sensor_ids:
                    lidar_result = results[result_idx]
                    if isinstance(lidar_result, Exception):
                        logger.error(f"Exception in disabling lidar berthing mode: {lidar_result}")
                        lidar_success = False
                        berthing_result = None
                    else:
                        berthing_result = lidar_result
                        lidar_success = berthing_result.get("active") is False  # Should be inactive after disable
            else:
                logger.info("No operations to stop")

            # Step 5: Stop database streaming if it was started for this berth
            db_streaming_stopped = False
            try:
                logger.info(f"Stopping database streaming for berth {berth_id}")
                from .db_streamer_service import db_streamer_service
                await db_streamer_service.stop_db_streaming()
                db_streaming_stopped = True
                logger.info(f"Database streaming stopped for berth {berth_id}")
            except Exception as db_err:
                logger.error(f"Failed to stop database streaming for berth {berth_id}: {db_err}")

            # Step 6: Return comprehensive result
            operation_success = laser_success and lidar_success

            result = {
                "success": operation_success,
                "berth_id": berth_id,
                "lasers_found": laser_data,
                "lasers_count": len(lasers),
                "lidars_count": len(lidars),
                "laser_operation_success": laser_success,
                "lidar_operation_success": lidar_success,
                "berthing_result": berthing_result,
                "db_streaming_stopped": db_streaming_stopped,
                "message": f"Operation {'stopped' if operation_success else 'failed to stop completely'} for berth {berth_id}: {len(lasers)} lasers, {len(lidars)} lidars"
            }

            logger.info(f"Stop operation for berth {berth_id} result: {result['message']}")
            return result

        except httpx.RequestError as req_err:
            logger.exception(f"Network error querying lasers for berth {berth_id}: {req_err}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Network error querying database for berth {berth_id}: {str(req_err)}",
                "operation_stopped": False
            }
        except httpx.HTTPStatusError as http_err:
            logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {http_err.response.status_code} - {http_err.response.text}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Database error querying berth {berth_id}: {http_err.response.status_code}",
                "operation_stopped": False
            }
        except Exception as e:
            logger.error(f"Error stopping operation for berth {berth_id}: {str(e)}")
            return {
                "success": False,
                "berth_id": berth_id,
                "message": f"Error stopping operation for berth {berth_id}: {str(e)}",
                "operation_stopped": False
            }

    def discover_devices(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Discover available devices from all configured device types.

        Returns:
            Dictionary with device types as keys and device lists as values
        """
        discovered_devices = {
            "lidar": [],
            "laser": []
        }

        # Discover LiDAR devices
        if self.use_lidar and self.lidar_manager:
            try:
                lidar_devices = asyncio.run(self.lidar_manager.discover_sensors())
                discovered_devices["lidar"] = lidar_devices or []
                logger.info(f"Discovered {len(discovered_devices['lidar'])} LiDAR devices")
            except Exception as e:
                logger.error(f"Error discovering LiDAR devices: {e}")

        # Discover laser devices
        if self.use_laser and self.laser_manager:
            try:
                laser_devices = self.laser_manager.get_all_laser_devices()
                discovered_devices["laser"] = [
                    {
                        "sensor_id": device_id,
                        "serialNumber": device_info.get("laser_id", device_id),
                        "device_type": "laser",
                        "status": device_info.get("status", "unknown")
                    }
                    for device_id, device_info in laser_devices.items()
                ]
                logger.info(f"Discovered {len(discovered_devices['laser'])} laser devices")
            except Exception as e:
                logger.error(f"Error discovering laser devices: {e}")

        return discovered_devices

    def connect_device(self, device_id: str, device_type: str, **kwargs) -> bool:
        """
        Connect to a specific device through its appropriate manager.

        Args:
            device_id: Device identifier
            device_type: Type of device ("lidar" or "laser")
            **kwargs: Additional connection parameters

        Returns:
            True if connection successful
        """
        try:
            if device_type == "lidar" and self.use_lidar and self.lidar_manager:
                # Handle LiDAR connection through LiDAR manager
                logger.info(f"Connecting LiDAR device: {device_id}")
                # LiDAR connection logic would go here
                return True

            elif device_type == "laser" and self.use_laser and self.laser_manager:
                # Handle laser connection through laser manager
                logger.info(f"Connecting laser device: {device_id}")
                return self.laser_manager.connect_to_laser_manager(self.berth_id)

            else:
                logger.error(f"Unsupported device type '{device_type}' or manager not available")
                return False

        except Exception as e:
            logger.error(f"Error connecting device {device_id}: {e}")
            return False

    def start_device_stream(self, device_id: str, device_type: str) -> bool:
        """
        Start data streaming for a specific device.

        Args:
            device_id: Device identifier
            device_type: Type of device

        Returns:
            True if streaming started successfully
        """
        try:
            if device_type == "lidar" and self.use_lidar and self.lidar_manager:
                logger.info(f"Starting LiDAR stream: {device_id}")
                # LiDAR streaming logic would go here
                return True

            elif device_type == "laser" and self.use_laser and self.laser_manager:
                logger.info(f"Starting laser stream: {device_id}")
                return self.laser_manager.start_sync_coordinator()

            else:
                logger.error(f"Unsupported device type '{device_type}' or manager not available")
                return False

        except Exception as e:
            logger.error(f"Error starting stream for device {device_id}: {e}")
            return False

    def get_device_data(self, device_id: str, device_type: str) -> Optional[str]:
        """
        Get the latest synchronized data for a specific device.

        Args:
            device_id: Device identifier
            device_type: Type of device

        Returns:
            JSON string of device data or None if no data available
        """
        try:
            if device_type == "lidar" and self.use_lidar and self.lidar_manager:
                # Get LiDAR data from LiDAR manager
                # This would need to be implemented based on LiDAR manager's API
                return None

            elif device_type == "laser" and self.use_laser and self.laser_manager:
                # Get laser data from laser manager
                if device_id in self.laser_manager.device_queues:
                    try:
                        if not self.laser_manager.device_queues[device_id].empty():
                            return self.laser_manager.device_queues[device_id].get_nowait()
                    except:
                        pass
                return None

            else:
                logger.warning(f"Unsupported device type '{device_type}' or manager not available")
                return None

        except Exception as e:
            logger.error(f"Error getting data for device {device_id}: {e}")
            return None

    def start_sync_coordinator(self) -> bool:
        """
        Start the unified synchronization coordinator for all device types.

        Returns:
            True if successful, False otherwise
        """
        if self.sync_coordinator_active:
            logger.warning("Sync coordinator already active")
            return True

        try:
            self.sync_coordinator_active = True
            self.sync_coordinator_thread = threading.Thread(
                target=self._unified_sync_coordinator_thread,
                daemon=True
            )
            self.sync_coordinator_thread.start()
            logger.info("Started unified sync coordinator")
            return True

        except Exception as e:
            logger.error(f"Error starting unified sync coordinator: {str(e)}")
            self.sync_coordinator_active = False
            return False

    def stop_sync_coordinator(self) -> bool:
        """
        Stop the unified synchronization coordinator.

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
            logger.info("Stopped unified sync coordinator")
            return True

        except Exception as e:
            logger.error(f"Error stopping unified sync coordinator: {str(e)}")
            return False

    def _unified_sync_coordinator_thread(self):
        """
        Unified synchronization coordinator that pools data from all device types.
        """
        logger.info("Unified sync coordinator thread started")

        # Wait for the next exact second boundary
        now = time.time()
        next_sync = math.ceil(now)  # Round up to next integer second
        time.sleep(next_sync - now)

        while self.sync_coordinator_active:
            try:
                # Get exact synchronization timestamp
                sync_timestamp = time.time()
                exact_second = math.floor(sync_timestamp)  # Truncate to exact second

                logger.info(f"UNIFIED SYNC at exact second: {exact_second}")

                # Collect data from all device types
                all_device_data = {}

                # Collect LiDAR data
                if self.use_lidar and self.lidar_manager:
                    try:
                        # This would collect data from LiDAR manager
                        # For now, placeholder
                        lidar_data = {}
                        all_device_data.update(lidar_data)
                    except Exception as e:
                        logger.error(f"Error collecting LiDAR data: {e}")

                # Collect laser data
                if self.use_laser and self.laser_manager:
                    try:
                        laser_data = {}
                        for device_id in self.laser_manager.laser_devices.keys():
                            if device_id in self.laser_manager.device_queues:
                                try:
                                    if not self.laser_manager.device_queues[device_id].empty():
                                        data_str = self.laser_manager.device_queues[device_id].get_nowait()
                                        data = json.loads(data_str)
                                        laser_data[device_id] = data
                                except:
                                    pass
                        all_device_data.update(laser_data)
                    except Exception as e:
                        logger.error(f"Error collecting laser data: {e}")

                # Create unified synchronized packet
                if all_device_data:
                    unified_packet = {
                        "timestamp": exact_second,
                        "sync_type": "unified_devices",
                        "total_devices": len(all_device_data),
                        "device_types": {
                            "lidar": self.use_lidar,
                            "laser": self.use_laser
                        },
                        "devices": all_device_data,
                        "sync_quality": "high" if len(all_device_data) > 0 else "none",
                        "pooled_data_points": sum(
                            len(device_data.get("points", []))
                            for device_data in all_device_data.values()
                            if isinstance(device_data, dict)
                        )
                    }

                    # Put unified data into device queues
                    for device_id in all_device_data.keys():
                        if device_id in self.device_queues:
                            try:
                                data_str = json.dumps(unified_packet)
                                self.device_queues[device_id].put(data_str)
                            except Exception as e:
                                logger.error(f"Error queuing unified data for {device_id}: {str(e)}")

                self.last_sync_timestamp = exact_second

                # Sleep until next exact second
                next_sync = exact_second + 1.0
                sleep_time = next_sync - time.time()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.warning(f"Unified sync processing took too long: {-sleep_time:.3f}s behind")

            except Exception as e:
                logger.error(f"Error in unified sync coordinator: {str(e)}")
                time.sleep(0.1)

        logger.info("Unified sync coordinator thread stopped")

    def get_unified_data_generator(self, device_id: str):
        """
        Generator for streaming unified device data.

        Args:
            device_id: Device identifier

        Yields:
            Unified device data as JSON string
        """
        while device_id in self.device_queues:
            try:
                if not self.device_queues[device_id].empty():
                    data = self.device_queues[device_id].get(timeout=0.1)
                    yield data
                else:
                    yield None
            except:
                yield None

    async def auto_connect_lidars(self, computer_ip: str) -> int:
        """
        Auto-connect to LiDAR sensors, following the same logic as main.py.

        Args:
            computer_ip: Computer IP address for sensor discovery

        Returns:
            Number of sensors connected
        """
        if not self.lidar_manager:
            logger.warning("LiDAR manager not available for auto-connect")
            return 0

        try:
            count = await self.lidar_manager.auto_connect_all(computer_ip)
            logger.info(f"Auto-connected to {count} sensors")
            return count
        except Exception as e:
            logger.error(f"Error during auto-connect: {str(e)}")
            return 0

    async def shutdown_devices(self) -> None:
        """
        Shutdown all devices (LiDAR and Laser), following the same logic as main.py shutdown.

        This method handles stopping all data streams, drift compensation, and disconnecting all devices.
        """
        try:
            # Shutdown LiDAR devices
            if self.lidar_manager:
                sensors = list(self.lidar_manager.sensors.keys())

                # First stop all data streams
                for sensor_id in sensors:
                    if self.lidar_manager.stream_active.get(sensor_id, False):
                        logger.info(f"Stopping stream for {sensor_id}...")
                        await self.lidar_manager.stop_data_stream(sensor_id)

                # Then disconnect all sensors
                for sensor_id in sensors:
                    # Force disconnect during shutdown
                    await self.lidar_manager.disconnect_sensor(sensor_id, force=True)

                logger.info("All LiDAR sensors stopped and disconnected")
            else:
                logger.warning("LiDAR manager not available for shutdown")

            # Shutdown laser devices
            if self.use_laser and self.laser_manager:
                logger.info("Shutting down laser devices...")

                # Stop sync coordinator if active
                if self.laser_manager.sync_coordinator_active:
                    logger.info("Stopping laser sync coordinator...")
                    await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.stop_sync_coordinator)

                # Stop drift compensation if active
                if self.laser_manager.drift_compensation_active:
                    logger.info("Stopping laser drift compensation...")
                    await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.stop_laser_drift_compensation)

                # Disconnect from laser manager
                if self.laser_manager.connection_active:
                    logger.info("Disconnecting from laser manager...")
                    await asyncio.get_event_loop().run_in_executor(None, self.laser_manager.disconnect_from_laser_manager)

                logger.info("Laser devices shutdown completed")
            else:
                logger.info("Laser manager not configured or not in use")

        except Exception as e:
            logger.error(f"Error during device shutdown: {str(e)}")

    # Keep the old method for backward compatibility
    async def shutdown_lidars(self) -> None:
        """
        Legacy method for LiDAR-only shutdown. Use shutdown_devices() instead.
        """
        await self.shutdown_devices()

    def get_current_operation(self) -> Dict[str, Any]:
        """
        Get information about the currently running operation.

        Returns:
            Dictionary with current operation info
        """
        if self.current_operation:
            return {
                "operation_type": self.current_operation.value,
                "berthing_id": self.current_berthing_id,
                "active": True
            }
        else:
            return {
                "operation_type": None,
                "berthing_id": None,
                "active": False
            }

    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status of all device managers.

        Returns:
            Status dictionary
        """
        status = {
            "use_lidar": self.use_lidar,
            "use_laser": self.use_laser,
            "berth_id": self.berth_id,
            "sync_coordinator_active": self.sync_coordinator_active,
            "last_sync_timestamp": self.last_sync_timestamp,
            "current_operation": self.get_current_operation(),
            "managers": {
                "lidar": self.lidar_manager is not None,
                "laser": self.laser_manager is not None
            }
        }

        # Add laser manager status if available
        if self.laser_manager:
            try:
                status["laser_manager"] = self.laser_manager.get_status()
            except:
                status["laser_manager"] = {"error": "status_unavailable"}

        # Add LiDAR manager status if available
        if self.lidar_manager:
            try:
                # LiDAR manager status would be added here
                status["lidar_manager"] = {"connected": True}
            except:
                status["lidar_manager"] = {"error": "status_unavailable"}

        return status


# Global device manager instance
device_manager = DeviceManager()