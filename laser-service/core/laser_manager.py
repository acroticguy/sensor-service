import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from core.config import settings
from core.logger import get_logger
from core.laser_device import LaserDevice
from models.laser_models import LaserInfo, LaserStatus

logger = get_logger(__name__)


class LaserManager:
    def __init__(self):
        self.devices: Dict[str, LaserDevice] = {}
        self._heartbeat_task = None
        self._reconnect_task = None
        
    async def start(self):
        logger.info("Starting laser manager")
        await self._auto_discover_devices()
        
        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    async def stop(self):
        logger.info("Stopping laser manager")
        
        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._reconnect_task:
            self._reconnect_task.cancel()
        
        # Disconnect all devices
        for device in self.devices.values():
            await device.disconnect()
    
    async def _auto_discover_devices(self):
        logger.info(f"Connecting to laser devices via TCP bridges on ports: {settings.laser_ports}")
        
        # Connect to each configured port
        for port in settings.laser_ports:
            try:
                device = LaserDevice(
                    host=settings.bridge_host,
                    port=port,
                    timeout=settings.laser_timeout
                )
                
                if await device.connect():
                    # Store device with port as key
                    port_key = str(port)
                    self.devices[port_key] = device
                    logger.info(f"Connected to laser device via TCP bridge at {settings.bridge_host}:{port}")
                else:
                    logger.error(f"Failed to connect to TCP bridge at {settings.bridge_host}:{port}")
            except Exception as e:
                logger.error(f"Failed to connect to TCP bridge at {settings.bridge_host}:{port}: {e}")
    
    async def _heartbeat_loop(self):
        while True:
            try:
                await asyncio.sleep(settings.heartbeat_interval)
                
                for name, device in self.devices.items():
                    if device.status == LaserStatus.CONNECTED:
                        if not await device.heartbeat():
                            logger.warning(f"Heartbeat failed for {name}")
                            device.status = LaserStatus.ERROR
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {str(e)}")
    
    async def _reconnect_loop(self):
        while True:
            try:
                await asyncio.sleep(settings.connection_retry_interval)
                
                # Try to reconnect disconnected devices
                for name, device in list(self.devices.items()):
                    if device.status in [LaserStatus.ERROR, LaserStatus.DISCONNECTED]:
                        device.status = LaserStatus.RECONNECTING
                        logger.info(f"Attempting to reconnect {name}")
                        
                        if await device.connect():
                            logger.info(f"Reconnected {name} successfully")
                        else:
                            device.status = LaserStatus.ERROR
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reconnect loop error: {str(e)}")
    
    def get_device(self, port: str = None) -> Optional[LaserDevice]:
        """Get device by port name"""
        if port is None:
            # Return first connected device if no port specified
            return self.get_connected_device()
        
        # Try to find device by port number
        return self.devices.get(port)
    
    def get_all_devices(self) -> List[LaserInfo]:
        devices_info = []
        
        for port_key, device in self.devices.items():
            info = LaserInfo(
                port=port_key,
                status=device.status,
                last_heartbeat=device.last_heartbeat.isoformat() if device.last_heartbeat else None,
                error_count=device.error_count,
                measurements_count=device.measurements_count
            )
            devices_info.append(info)
        
        return devices_info
    
    def get_connected_device(self) -> Optional[LaserDevice]:
        """Get the first connected device"""
        for device in self.devices.values():
            if device.status == LaserStatus.CONNECTED:
                return device
        return None