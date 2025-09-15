import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Set
from core.laser_device import LaserDevice
from models.laser_models import MeasurementMode, LaserOperatingMode, LaserStatus
from services.database_service import DatabaseService
from services.websocket_service import websocket_manager
from core.logger import get_logger

logger = get_logger(__name__)

class MultiLaserManager:
    """
    Manages multiple laser devices with database integration and berthing mode support
    """
    
    def __init__(self, api_base_url: str = "https://api.navitrak.ai"):
        self.db_service = DatabaseService(api_base_url)
        self.laser_devices: Dict[int, LaserDevice] = {}
        self.data_sync_tasks: Dict[int, asyncio.Task] = {}
        self.berth_sync_tasks: Dict[int, asyncio.Task] = {}  # Berth-level sync tasks
        self.active_berths: Set[int] = set()  # Track active berths
        self._shutdown_event = asyncio.Event()
        self._running = False
        
        # Track last known good values for each laser
        self.last_known_values: Dict[int, Dict[str, Any]] = {}
    
    async def initialize(self) -> bool:
        """
        Initialize the laser manager by:
        1. Connecting to database API
        2. Loading laser configurations 
        3. Connecting to all enabled lasers
        4. Setting up data synchronization
        
        Returns:
            Success status
        """
        try:
            logger.info("Initializing Multi-Laser Manager")
            
            # Get laser configurations from database
            async with self.db_service:
                laser_configs = await self.db_service.get_lasers(laser_type_id=1, enabled=True)
            
            if not laser_configs:
                logger.warning("No enabled laser devices found in database")
                return False
            
            logger.info(f"Found {len(laser_configs)} laser devices to manage")
            
            # Initialize laser devices in parallel
            laser_devices = []
            for config in laser_configs:
                laser_id = config['laser_id']
                host = config['ip_host']
                port = config['ip_port']
                berth_id = config.get('berth_id')
                
                logger.info(f"Initializing laser {laser_id} at {host}:{port}")
                
                # Create laser device
                laser_device = LaserDevice(
                    laser_id=laser_id,
                    host=host,
                    port=port,
                    berth_id=berth_id
                )
                laser_devices.append((laser_device, config))
            
            # Connect to all lasers in parallel
            connection_tasks = [laser_device.connect() for laser_device, _ in laser_devices]
            connection_results = await asyncio.gather(*connection_tasks, return_exceptions=True)
            
            # Process connection results and update database status in parallel
            db_updates = []
            for (laser_device, config), connected in zip(laser_devices, connection_results):
                laser_id = config['laser_id']
                host = config['ip_host']
                port = config['ip_port']
                
                # Handle exceptions from connection attempts
                if isinstance(connected, Exception):
                    logger.error(f"Exception connecting to laser {laser_id} at {host}:{port}: {connected}")
                    connected = False
                
                if connected:
                    self.laser_devices[laser_id] = laser_device
                    logger.info(f"Laser {laser_id} connected successfully")
                else:
                    logger.error(f"Failed to connect to laser {laser_id} at {host}:{port}")
                
                # Queue database update
                db_updates.append(self.db_service.update_laser_online_status(laser_id, connected))
            
            # Update all database statuses in parallel
            if db_updates:
                async with self.db_service:
                    await asyncio.gather(*db_updates, return_exceptions=True)
            
            self._running = True
            logger.info(f"Multi-Laser Manager initialized with {len(self.laser_devices)} connected devices")
            return len(self.laser_devices) > 0
            
        except Exception as e:
            logger.error(f"Failed to initialize Multi-Laser Manager: {str(e)}")
            return False
    
    async def start_berthing_mode(self, berth_id: int, berthing_id: int) -> bool:
        """
        Start berthing mode for all lasers associated with a berth
        
        Args:
            berth_id: Berth ID
            berthing_id: Berthing session ID
            
        Returns:
            Success status
        """
        try:
            logger.info(f"Starting berthing mode for berth {berth_id}, berthing {berthing_id}")
            
            # Find lasers for this berth
            berth_lasers = [
                laser for laser in self.laser_devices.values() 
                if laser.berth_id == berth_id
            ]
            
            if not berth_lasers:
                logger.warning(f"No lasers found for berth {berth_id}")
                return False
            
            # Start berthing mode for all lasers in parallel
            berthing_tasks = [
                laser.start_berthing_mode(berthing_id) for laser in berth_lasers
            ]
            berthing_results = await asyncio.gather(*berthing_tasks, return_exceptions=True)
            
            success_count = 0
            started_lasers = []
            websocket_notifications = []
            
            for laser, result in zip(berth_lasers, berthing_results):
                if isinstance(result, Exception):
                    logger.error(f"Exception starting berthing mode for laser {laser.laser_id}: {result}")
                elif result:
                    started_lasers.append(laser)
                    success_count += 1
                    logger.info(f"Started berthing mode for laser {laser.laser_id}")
                    
                    # Queue websocket notification
                    websocket_notifications.append(
                        websocket_manager.broadcast_mode_change(
                            laser_id=laser.laser_id,
                            berth_id=berth_id,
                            mode="berthing",
                            details={"laser_id": laser.laser_id, "berth_id": berth_id, "berthing_id": berthing_id}
                        )
                    )
                else:
                    logger.error(f"Failed to start berthing mode for laser {laser.laser_id}")
            
            # Send all websocket notifications in parallel
            if websocket_notifications:
                await asyncio.gather(*websocket_notifications, return_exceptions=True)
            
            if success_count > 0:
                # Start synchronized data collection for this berth
                self.active_berths.add(berth_id)
                self.berth_sync_tasks[berth_id] = asyncio.create_task(
                    self._synchronized_berth_data_loop(berth_id, started_lasers, berthing_id)
                )
                logger.info(f"Started synchronized data collection for berth {berth_id}")
            
            success = success_count > 0
            logger.info(f"Berthing mode started for {success_count}/{len(berth_lasers)} lasers")
            return success
            
        except Exception as e:
            logger.error(f"Failed to start berthing mode: {str(e)}")
            return False
    
    async def stop_berthing_mode(self, berth_id: int) -> bool:
        """
        Stop berthing mode for all lasers associated with a berth
        
        Args:
            berth_id: Berth ID
            
        Returns:
            Success status
        """
        try:
            logger.info(f"Stopping berthing mode for berth {berth_id}")
            
            # Find lasers for this berth
            berth_lasers = [
                laser for laser in self.laser_devices.values() 
                if laser.berth_id == berth_id and laser.is_in_berthing_mode()
            ]
            
            if not berth_lasers:
                logger.warning(f"No active berthing lasers found for berth {berth_id}")
                return True  # Nothing to stop is success
            
            # Stop synchronized data collection for this berth
            if berth_id in self.berth_sync_tasks:
                self.berth_sync_tasks[berth_id].cancel()
                try:
                    await self.berth_sync_tasks[berth_id]
                except asyncio.CancelledError:
                    pass
                del self.berth_sync_tasks[berth_id]
                logger.info(f"Stopped synchronized data collection for berth {berth_id}")
            
            if berth_id in self.active_berths:
                self.active_berths.remove(berth_id)
            
            # Stop berthing mode for each laser
            success_count = 0
            for laser in berth_lasers:
                try:
                    # Check if laser is disconnected and attempt reconnection
                    if laser.status == LaserStatus.DISCONNECTED:
                        logger.info(f"Laser {laser.laser_id} is disconnected, attempting to reconnect...")
                        if await laser.connect():
                            logger.info(f"Successfully reconnected to laser {laser.laser_id}")
                        else:
                            logger.warning(f"Failed to reconnect to laser {laser.laser_id}, will clean up state anyway")
                    
                    # Stop berthing mode (this will clean up state even if disconnected)
                    if await laser.stop_berthing_mode():
                        success_count += 1
                        logger.info(f"Stopped berthing mode for laser {laser.laser_id}")
                        
                        # Notify websocket clients of mode change
                        await websocket_manager.broadcast_mode_change(
                            laser_id=laser.laser_id,
                            berth_id=berth_id,
                            mode="off",
                            details={"laser_id": laser.laser_id, "berth_id": berth_id}
                        )
                    else:
                        logger.error(f"Failed to stop berthing mode for laser {laser.laser_id}")
                        
                except Exception as e:
                    logger.error(f"Error stopping berthing mode for laser {laser.laser_id}: {str(e)}")
            
            logger.info(f"Berthing mode stopped for {success_count}/{len(berth_lasers)} lasers")
            
            # If we had connection issues but cleaned up state, consider it partial success
            if success_count > 0 or len(berth_lasers) == 0:
                return True
            else:
                return False
            
        except Exception as e:
            logger.error(f"Failed to stop berthing mode: {str(e)}")
            return False
    
    def get_berth_lasers_status(self, berth_id: int) -> List[Dict[str, Any]]:
        """Get status of all lasers for a specific berth"""
        berth_lasers = [
            laser for laser in self.laser_devices.values() 
            if laser.berth_id == berth_id
        ]
        
        return [
            {
                "laser_id": laser.laser_id,
                "status": laser.status.value,
                "berth_id": laser.berth_id,
                "in_berthing_mode": laser.is_in_berthing_mode(),
                "connected": laser.status != LaserStatus.DISCONNECTED
            }
            for laser in berth_lasers
        ]
    
    async def _data_sync_loop(self, laser: LaserDevice) -> None:
        """
        Continuous data synchronization loop for a laser in berthing mode
        Sends data every second using the freshest measurement
        
        Args:
            laser: Laser device instance
        """
        logger.info(f"Starting data sync loop for laser {laser.laser_id}")
        last_measurement = None
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        try:
            while laser.is_in_berthing_mode() and not self._shutdown_event.is_set():
                try:
                    # Get the latest measurement data with a longer timeout
                    measurement = await laser.get_continuous_data(timeout=2.0)
                    
                    if measurement and (measurement.distance is not None or measurement.speed is not None):
                        # We got fresh data
                        last_measurement = measurement
                        consecutive_failures = 0
                        
                        # Insert data into database using current timestamp
                        current_time = datetime.now(timezone.utc)
                        
                        success = await self.db_service.insert_laser_data(
                            laser_id=laser.laser_id,
                            dt=current_time,
                            distance=(measurement.distance / 1000.0) if measurement.distance else 0.0,  # Convert mm to meters
                            speed=measurement.speed or 0.0,
                            temperature=measurement.temperature or 0.0,
                            strength=measurement.signal_strength or 0,
                            berthing_id=laser.get_berthing_id()
                        )
                        
                        if success:
                            logger.debug(f"Inserted fresh data for laser {laser.laser_id}: "
                                       f"dist={measurement.distance}, speed={measurement.speed}")
                        else:
                            logger.warning(f"Failed to insert data for laser {laser.laser_id}")
                            
                    elif last_measurement is not None:
                        # No fresh data, but we have a previous measurement - use it
                        consecutive_failures += 1
                        current_time = datetime.now(timezone.utc)
                        
                        success = await self.db_service.insert_laser_data(
                            laser_id=laser.laser_id,
                            dt=current_time,
                            distance=(last_measurement.distance / 1000.0) if last_measurement.distance else 0.0,  # Convert mm to meters
                            speed=last_measurement.speed or 0.0,
                            temperature=last_measurement.temperature or 0.0,
                            strength=last_measurement.signal_strength or 0,
                            berthing_id=laser.get_berthing_id()
                        )
                        
                        if success:
                            logger.debug(f"Inserted cached data for laser {laser.laser_id} (no fresh data): "
                                       f"dist={last_measurement.distance}, speed={last_measurement.speed}")
                        
                        logger.debug(f"Using cached measurement for laser {laser.laser_id} ({consecutive_failures} consecutive)")
                        
                    else:
                        # No data at all
                        consecutive_failures += 1
                        logger.warning(f"No measurement data available for laser {laser.laser_id} ({consecutive_failures} consecutive failures)")
                    
                    # Check if we need to restart berthing mode
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(f"Too many consecutive failures for laser {laser.laser_id}, attempting to restart VT mode")
                        try:
                            # Try to restart continuous mode
                            await laser._send_escape_sequence()
                            await asyncio.sleep(1.0)
                            
                            success = await laser._start_continuous_mode_with_retry(MeasurementMode.VT)
                            if success:
                                logger.info(f"Successfully restarted VT mode for laser {laser.laser_id}")
                                consecutive_failures = 0
                            else:
                                logger.error(f"Failed to restart VT mode for laser {laser.laser_id}")
                        except Exception as restart_error:
                            logger.error(f"Error restarting VT mode for laser {laser.laser_id}: {str(restart_error)}")
                    
                    # Wait exactly 1 second for next sync
                    await asyncio.sleep(1.0)
                    
                except asyncio.CancelledError:
                    logger.info(f"Data sync loop cancelled for laser {laser.laser_id}")
                    break
                except Exception as e:
                    logger.error(f"Error in data sync loop for laser {laser.laser_id}: {str(e)}")
                    consecutive_failures += 1
                    # Continue the loop but wait a bit before retrying
                    await asyncio.sleep(1.0)
                    
        except Exception as e:
            logger.error(f"Data sync loop failed for laser {laser.laser_id}: {str(e)}")
        finally:
            logger.info(f"Data sync loop ended for laser {laser.laser_id}")
    
    async def _synchronized_berth_data_loop(self, berth_id: int, lasers: List[LaserDevice], berthing_id: int) -> None:
        """
        Simple synchronized data transmission for all lasers in a berth
        Every second, get the freshest data available and send it
        
        Args:
            berth_id: Berth ID
            lasers: List of laser devices for this berth
            berthing_id: Berthing session ID
        """
        logger.info(f"Starting synchronized data transmission for berth {berth_id} with {len(lasers)} lasers")
        
        try:
            while berth_id in self.active_berths and not self._shutdown_event.is_set():
                # Wait 1 second
                await asyncio.sleep(1.0)
                
                # Get current timestamp rounded to integer seconds
                now = datetime.now(timezone.utc)
                sync_timestamp = now.replace(microsecond=0)
                
                # ALWAYS insert data every second for all lasers - use last known good values
                for laser in lasers:
                    if laser.is_in_berthing_mode():
                        try:
                            # Try to get fresh measurement (non-blocking)
                            fresh_measurement = await laser.get_continuous_data(timeout=0.1)
                            
                            # Initialize last known values for this laser if not exists
                            if laser.laser_id not in self.last_known_values:
                                self.last_known_values[laser.laser_id] = {
                                    "distance": None,
                                    "speed": None, 
                                    "temperature": None,
                                    "strength": None
                                }
                            
                            # Stack-like behavior: Always send freshest data available
                            if fresh_measurement:
                                # Fresh data available - push to top of stack (preserve nulls)
                                current_data = {
                                    "distance": fresh_measurement.distance / 1000.0 if fresh_measurement.distance is not None else None,
                                    "speed": fresh_measurement.speed if hasattr(fresh_measurement, 'speed') else None,
                                    "temperature": fresh_measurement.temperature if hasattr(fresh_measurement, 'temperature') else None,
                                    "strength": fresh_measurement.signal_strength if hasattr(fresh_measurement, 'signal_strength') else None
                                }
                                
                                # Update the stack top with fresh data (including nulls)
                                self.last_known_values[laser.laser_id] = current_data.copy()
                                
                                # Special logging for null values
                                if current_data['distance'] is None or current_data['speed'] is None:
                                    logger.info(f"Fresh NULL data for laser {laser.laser_id}: dist={current_data['distance']}, speed={current_data['speed']}")
                                else:
                                    logger.debug(f"Fresh data for laser {laser.laser_id}: dist={current_data['distance']}, speed={current_data['speed']}")
                            else:
                                # No fresh data - use what's on top of stack (most recent data)
                                current_data = self.last_known_values[laser.laser_id].copy()
                                
                                logger.debug(f"Using stack top for laser {laser.laser_id}: dist={current_data['distance']}, speed={current_data['speed']}")
                            
                            # Always send the freshest data available (top of stack)
                            
                            # Fire and forget async insertion - Send freshest data from stack top
                            asyncio.create_task(
                                self.db_service.insert_laser_data(
                                    laser_id=laser.laser_id,
                                    dt=sync_timestamp,
                                    distance=current_data["distance"],
                                    speed=current_data["speed"],
                                    temperature=current_data["temperature"],
                                    strength=current_data["strength"],
                                    berthing_id=berthing_id
                                )
                            )
                            
                            # Stream freshest data via websockets (stack top)
                            websocket_data = {
                                "distance": current_data["distance"],
                                "speed": current_data["speed"],
                                "temperature": current_data["temperature"],
                                "signal_strength": current_data["strength"],
                                "timestamp": sync_timestamp.isoformat(),
                                "berthing_id": berthing_id
                            }
                            
                            asyncio.create_task(
                                websocket_manager.broadcast_laser_data(
                                    laser_id=laser.laser_id,
                                    berth_id=berth_id,
                                    data=websocket_data
                                )
                            )
                            
                            logger.debug(f"Async insert+stream for laser {laser.laser_id}: {current_data['distance']*1000 if current_data['distance'] is not None else None}mm, {current_data['speed'] if current_data['speed'] is not None else None}m/s at {sync_timestamp}")
                        except Exception as e:
                            logger.debug(f"Error getting data for laser {laser.laser_id}: {str(e)}")
                
                logger.debug(f"Berth {berth_id}: Fired async insertions at {sync_timestamp}")
                    
        except asyncio.CancelledError:
            logger.info(f"Synchronized data transmission cancelled for berth {berth_id}")
        except Exception as e:
            logger.error(f"Error in synchronized data transmission for berth {berth_id}: {str(e)}")
        finally:
            logger.info(f"Synchronized data transmission ended for berth {berth_id}")
    
    
    async def shutdown(self) -> None:
        """Shutdown all laser connections and tasks"""
        logger.info("Shutting down Multi-Laser Manager")
        
        # Signal shutdown
        self._shutdown_event.set()
        self._running = False
        
        # Cancel all data sync tasks (legacy individual laser tasks)
        for task in self.data_sync_tasks.values():
            task.cancel()
        
        # Cancel all berth sync tasks
        for task in self.berth_sync_tasks.values():
            task.cancel()
        
        # Wait for tasks to complete
        all_tasks = list(self.data_sync_tasks.values()) + list(self.berth_sync_tasks.values())
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Disconnect all lasers and update status
        for laser_id, laser in self.laser_devices.items():
            try:
                if laser.is_in_berthing_mode():
                    await laser.stop_berthing_mode()
                await laser.disconnect()
                
                # Update offline status in database
                async with self.db_service:
                    await self.db_service.update_laser_online_status(laser_id, False)
                    
            except Exception as e:
                logger.error(f"Error disconnecting laser {laser_id}: {str(e)}")
        
        self.laser_devices.clear()
        self.data_sync_tasks.clear()
        self.berth_sync_tasks.clear()
        self.active_berths.clear()
        
        logger.info("Multi-Laser Manager shutdown complete")
    
    async def start_drift_mode(self, berth_id: int) -> bool:
        """
        Start drift mode for all lasers associated with a berth
        In drift mode, data is streamed via websockets but not stored in database
        
        Args:
            berth_id: Berth ID
            
        Returns:
            Success status
        """
        try:
            logger.info(f"Starting drift mode for berth {berth_id}")
            
            # Find lasers for this berth
            berth_lasers = [
                laser for laser in self.laser_devices.values() 
                if laser.berth_id == berth_id
            ]
            
            if not berth_lasers:
                logger.warning(f"No lasers found for berth {berth_id}")
                return False
            
            # Start drift mode for each laser
            success_count = 0
            started_lasers = []
            
            for laser in berth_lasers:
                try:
                    if await laser.start_drift_mode():
                        started_lasers.append(laser)
                        success_count += 1
                        logger.info(f"Started drift mode for laser {laser.laser_id}")
                        
                        # Notify websocket clients of mode change
                        await websocket_manager.broadcast_mode_change(
                            laser_id=laser.laser_id,
                            berth_id=berth_id,
                            mode="drift",
                            details={"laser_id": laser.laser_id, "berth_id": berth_id}
                        )
                    else:
                        logger.error(f"Failed to start drift mode for laser {laser.laser_id}")
                except Exception as e:
                    logger.error(f"Error starting drift mode for laser {laser.laser_id}: {str(e)}")
            
            if success_count > 0:
                # Start websocket-only streaming for this berth
                self.active_berths.add(berth_id)
                self.berth_sync_tasks[berth_id] = asyncio.create_task(
                    self._drift_stream_loop(berth_id, started_lasers)
                )
                logger.info(f"Started drift streaming for berth {berth_id}")
            
            success = success_count > 0
            logger.info(f"Drift mode started for {success_count}/{len(berth_lasers)} lasers")
            return success
            
        except Exception as e:
            logger.error(f"Failed to start drift mode: {str(e)}")
            return False
    
    async def stop_drift_mode(self, berth_id: int) -> bool:
        """
        Stop drift mode for all lasers associated with a berth
        
        Args:
            berth_id: Berth ID
            
        Returns:
            Success status
        """
        try:
            logger.info(f"Stopping drift mode for berth {berth_id}")
            
            # Find lasers for this berth
            berth_lasers = [
                laser for laser in self.laser_devices.values() 
                if laser.berth_id == berth_id and laser.is_in_drift_mode()
            ]
            
            if not berth_lasers:
                logger.warning(f"No active drift lasers found for berth {berth_id}")
                return True  # Nothing to stop is success
            
            # Stop streaming task for this berth
            if berth_id in self.berth_sync_tasks:
                self.berth_sync_tasks[berth_id].cancel()
                try:
                    await self.berth_sync_tasks[berth_id]
                except asyncio.CancelledError:
                    pass
                del self.berth_sync_tasks[berth_id]
                logger.info(f"Stopped drift streaming for berth {berth_id}")
            
            if berth_id in self.active_berths:
                self.active_berths.remove(berth_id)
            
            # Stop drift mode for each laser
            success_count = 0
            for laser in berth_lasers:
                try:
                    # Stop drift mode
                    if await laser.stop_drift_mode():
                        success_count += 1
                        logger.info(f"Stopped drift mode for laser {laser.laser_id}")
                        
                        # Notify websocket clients of mode change
                        await websocket_manager.broadcast_mode_change(
                            laser_id=laser.laser_id,
                            berth_id=berth_id,
                            mode="off",
                            details={"laser_id": laser.laser_id, "berth_id": berth_id}
                        )
                    else:
                        logger.error(f"Failed to stop drift mode for laser {laser.laser_id}")
                        
                except Exception as e:
                    logger.error(f"Error stopping drift mode for laser {laser.laser_id}: {str(e)}")
            
            logger.info(f"Drift mode stopped for {success_count}/{len(berth_lasers)} lasers")
            return success_count == len(berth_lasers)
            
        except Exception as e:
            logger.error(f"Failed to stop drift mode: {str(e)}")
            return False
    
    async def set_berth_mode_off(self, berth_id: int) -> bool:
        """
        Set all lasers in a berth to OFF mode (sends ESC character)
        
        Args:
            berth_id: Berth ID
            
        Returns:
            Success status
        """
        try:
            logger.info(f"Setting berth {berth_id} to OFF mode")
            
            # Find lasers for this berth
            berth_lasers = [
                laser for laser in self.laser_devices.values() 
                if laser.berth_id == berth_id
            ]
            
            if not berth_lasers:
                logger.warning(f"No lasers found for berth {berth_id}")
                return False
            
            # Stop any active tasks for this berth
            if berth_id in self.berth_sync_tasks:
                self.berth_sync_tasks[berth_id].cancel()
                try:
                    await self.berth_sync_tasks[berth_id]
                except asyncio.CancelledError:
                    pass
                del self.berth_sync_tasks[berth_id]
            
            if berth_id in self.active_berths:
                self.active_berths.remove(berth_id)
            
            # Set OFF mode for each laser
            success_count = 0
            for laser in berth_lasers:
                try:
                    if await laser.set_mode_off():
                        success_count += 1
                        logger.info(f"Set laser {laser.laser_id} to OFF mode")
                        
                        # Notify websocket clients of mode change
                        await websocket_manager.broadcast_mode_change(
                            laser_id=laser.laser_id,
                            berth_id=berth_id,
                            mode="off",
                            details={"laser_id": laser.laser_id, "berth_id": berth_id}
                        )
                    else:
                        logger.error(f"Failed to set laser {laser.laser_id} to OFF mode")
                        
                except Exception as e:
                    logger.error(f"Error setting laser {laser.laser_id} to OFF mode: {str(e)}")
            
            logger.info(f"OFF mode set for {success_count}/{len(berth_lasers)} lasers")
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Failed to set berth {berth_id} to OFF mode: {str(e)}")
            return False
    
    async def _drift_stream_loop(self, berth_id: int, lasers: List[LaserDevice]) -> None:
        """
        Websocket-only streaming loop for drift mode
        Streams data without database insertion
        
        Args:
            berth_id: Berth ID
            lasers: List of laser devices for this berth
        """
        logger.info(f"Starting drift streaming for berth {berth_id} with {len(lasers)} lasers")
        
        try:
            while berth_id in self.active_berths and not self._shutdown_event.is_set():
                # Wait 1 second
                await asyncio.sleep(1.0)
                
                # Get current timestamp
                current_time = datetime.now(timezone.utc)
                
                # Get data from all lasers and stream via websockets
                for laser in lasers:
                    if laser.is_in_drift_mode():
                        try:
                            measurement = await laser.get_continuous_data(timeout=0.1)
                            # Stream data whether we have fresh measurement or not
                            distance = (measurement.distance / 1000.0) if measurement and measurement.distance is not None else 0.0
                            speed = measurement.speed if measurement and measurement.speed is not None else 0.0
                            temperature = measurement.temperature if measurement and measurement.temperature is not None else 0.0
                            strength = measurement.signal_strength if measurement and measurement.signal_strength is not None else 0
                            
                            # Prepare data for websocket streaming
                            data = {
                                "distance": distance,
                                "speed": speed,
                                "temperature": temperature,
                                "signal_strength": strength,
                                "timestamp": current_time.isoformat()
                            }
                            
                            # Broadcast to websocket clients only (no database)
                            await websocket_manager.broadcast_laser_data(
                                laser_id=laser.laser_id,
                                berth_id=berth_id,
                                data=data
                            )
                            
                            logger.debug(f"Streamed drift data for laser {laser.laser_id}: {distance*1000}mm, {speed}m/s")
                            
                        except Exception as e:
                            logger.debug(f"Error getting drift data for laser {laser.laser_id}: {str(e)}")
                
                logger.debug(f"Berth {berth_id}: Streamed drift data at {current_time}")
                    
        except asyncio.CancelledError:
            logger.info(f"Drift streaming cancelled for berth {berth_id}")
        except Exception as e:
            logger.error(f"Error in drift streaming for berth {berth_id}: {str(e)}")
        finally:
            logger.info(f"Drift streaming ended for berth {berth_id}")
    
    def get_connected_lasers(self) -> Dict[int, Dict[str, Any]]:
        """Get status of all connected lasers"""
        status = {}
        for laser_id, laser in self.laser_devices.items():
            status[laser_id] = {
                'host': laser.host,
                'port': laser.port,
                'berth_id': laser.berth_id,
                'connected': laser.status.name,
                'berthing_mode': laser.is_in_berthing_mode(),
                'berthing_id': laser.get_berthing_id(),
                'drift_mode': laser.is_in_drift_mode(),
                'operating_mode': laser.get_operating_mode().value,
                'measurements_count': laser.measurements_count
            }
        return status
    
    def get_device_by_id(self, laser_id: int) -> Optional['LaserDevice']:
        """Get device by laser ID"""
        return self.laser_devices.get(laser_id)
    
    def get_device_by_port(self, port: str) -> Optional['LaserDevice']:
        """Get device by port (for backwards compatibility)"""
        try:
            port_int = int(port)
            for laser in self.laser_devices.values():
                if laser.port == port_int:
                    return laser
        except ValueError:
            pass
        return None
    
    def get_connected_device(self) -> Optional['LaserDevice']:
        """Get any connected device (for simple operations)"""
        for laser in self.laser_devices.values():
            if laser.is_connected():
                return laser
        return None
    
    def get_all_devices(self) -> Dict[int, 'LaserDevice']:
        """Get all devices indexed by laser_id"""
        return self.laser_devices.copy()
    
    def is_running(self) -> bool:
        """Check if manager is running"""
        return self._running