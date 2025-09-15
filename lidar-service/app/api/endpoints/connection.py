from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from ...models.lidar import (
    LidarConnectionRequest, LidarAutoConnectRequest,
    LidarDiscoveryResponse, OperationResponse, ErrorResponse,
    BerthingModeRequest, BerthingModeResponse
)
from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger

router = APIRouter(prefix="/connection", tags=["Connection Management"])


@router.post("/discover", response_model=LidarDiscoveryResponse)
async def discover_sensors(computer_ip: Optional[str] = Query(None, description="Computer IP address")):
    """Discover available Livox sensors on the network"""
    try:
        sensors = await lidar_manager.discover_sensors(computer_ip)
        return LidarDiscoveryResponse(sensors=sensors, count=len(sensors))
    except Exception as e:
        logger.error(f"Error discovering sensors: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connect/{sensor_id}", response_model=OperationResponse)
async def connect_sensor(sensor_id: str, request: Optional[LidarConnectionRequest] = None):
    """Connect to a specific sensor. For fake sensors, can reconnect using same ID if previously connected (no request body needed)."""
    try:
        # Check if sensor is already connected
        if sensor_id in lidar_manager.sensors:
            # Check if it's a fake sensor
            sensor = lidar_manager.sensors[sensor_id]
            is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation

            if is_fake:
                # For fake sensors, we can reconnect using stored parameters
                logger.info(f"Fake sensor {sensor_id} already connected, reconnecting with stored parameters")

                # Get stored connection parameters
                stored_params = None
                if sensor_id in lidar_manager.sensor_info and lidar_manager.sensor_info[sensor_id].connection_parameters:
                    stored_params = lidar_manager.sensor_info[sensor_id].connection_parameters

                if stored_params:
                    # Disconnect first
                    await lidar_manager.disconnect_sensor(sensor_id, force=True)

                    # Reconnect using stored parameters
                    success, _ = await lidar_manager.connect_fake_lidar(
                        sensor_id=sensor_id,
                        computer_ip=stored_params.get("computer_ip"),
                        sensor_ip=stored_params.get("sensor_ip"),
                        data_port=int(stored_params.get("data_port")) if stored_params.get("data_port") else None,
                        cmd_port=int(stored_params.get("cmd_port")) if stored_params.get("cmd_port") else None,
                        imu_port=request.imu_port
                    )

                    if success:
                        return OperationResponse(
                            success=True,
                            message=f"Successfully reconnected to fake sensor {sensor_id}",
                            data={"sensor_id": sensor_id, "reconnected": True}
                        )
                    else:
                        raise HTTPException(status_code=400, detail=f"Failed to reconnect fake sensor {sensor_id}")
                else:
                    raise HTTPException(status_code=400, detail=f"No stored parameters found for fake sensor {sensor_id}")
            else:
                # For real sensors, return already connected message
                return OperationResponse(
                    success=True,
                    message=f"Sensor {sensor_id} is already connected",
                    data={"sensor_id": sensor_id, "already_connected": True}
                )

        # Check if this is a previously connected fake sensor (not currently connected)
        stored_params = None
        if sensor_id in lidar_manager.sensor_info and lidar_manager.sensor_info[sensor_id].connection_parameters:
            stored_params = lidar_manager.sensor_info[sensor_id].connection_parameters
            is_simulation = stored_params.get("is_simulation", False)

            if is_simulation:
                logger.info(f"Reconnecting previously used fake sensor {sensor_id} with stored parameters")
                logger.info(f"Stored params: {stored_params}")

                # Validate stored parameters
                if not stored_params.get("computer_ip") or not stored_params.get("sensor_ip"):
                    logger.error(f"Invalid stored parameters for {sensor_id}: missing IP addresses")
                    raise HTTPException(status_code=400, detail=f"Invalid stored parameters for fake sensor {sensor_id}")

                # Reconnect using stored parameters with retry mechanism
                success, _ = await lidar_manager.connect_fake_lidar(
                    sensor_id=sensor_id,
                    computer_ip=stored_params.get("computer_ip"),
                    sensor_ip=stored_params.get("sensor_ip"),
                    data_port=int(stored_params.get("data_port")) if stored_params.get("data_port") else None,
                    cmd_port=int(stored_params.get("cmd_port")) if stored_params.get("cmd_port") else None,
                    imu_port=getattr(request, 'imu_port', None) if request else None
                )

                if success:
                    return OperationResponse(
                        success=True,
                        message=f"Successfully reconnected fake sensor {sensor_id} using stored parameters",
                        data={"sensor_id": sensor_id, "reconnected": True, "from_stored": True}
                    )
                else:
                    logger.error(f"Failed to reconnect fake sensor {sensor_id} - connect_fake_lidar returned False")
                    raise HTTPException(status_code=400, detail=f"Failed to reconnect fake sensor {sensor_id}")
        else:
            logger.info(f"No stored parameters found for sensor {sensor_id}")

        # Normal connection process for new sensors
        if not request:
            raise HTTPException(status_code=400, detail="Request body required for new sensor connections")

        success = await lidar_manager.connect_sensor(
            sensor_id=sensor_id,
            computer_ip=request.computer_ip,
            sensor_ip=request.sensor_ip,
            data_port=request.data_port,
            cmd_port=request.cmd_port,
            imu_port=request.imu_port,
            sensor_name=request.sensor_name
        )

        if success:
            return OperationResponse(
                success=True,
                message=f"Successfully connected to sensor {sensor_id}",
                data={"sensor_id": sensor_id}
            )
        else:
            raise HTTPException(status_code=400, detail="Failed to connect to sensor")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting to sensor: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auto-connect", response_model=OperationResponse)
async def auto_connect_all(request: LidarAutoConnectRequest = LidarAutoConnectRequest()):
    """Auto-connect to all available sensors"""
    try:
        count = await lidar_manager.auto_connect_all(request.computer_ip)
        
        if count > 0:
            return OperationResponse(
                success=True,
                message=f"Successfully connected to {count} sensors",
                data={"connected_count": count}
            )
        else:
            return OperationResponse(
                success=False,
                message="No sensors found or connected",
                data={"connected_count": 0}
            )
            
    except Exception as e:
        logger.error(f"Error in auto-connect: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/disconnect/{sensor_id}", response_model=OperationResponse)
async def disconnect_sensor(sensor_id: str):
    """Disconnect a specific sensor"""
    try:
        # Check if sensor exists first
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail=f"Sensor {sensor_id} not found")

        # Check if it's a fake sensor for better messaging
        sensor = lidar_manager.sensors[sensor_id]
        is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation

        success = await lidar_manager.disconnect_sensor(sensor_id, force=True)

        if success:
            sensor_type = "fake sensor" if is_fake else "sensor"
            return OperationResponse(
                success=True,
                message=f"Successfully disconnected {sensor_type} {sensor_id}",
                data={"sensor_id": sensor_id, "sensor_type": "fake" if is_fake else "real"}
            )
        else:
            raise HTTPException(status_code=500, detail=f"Failed to disconnect sensor {sensor_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error disconnecting sensor: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/disconnect-all", response_model=OperationResponse)
async def disconnect_all_sensors():
    """Disconnect all connected sensors"""
    try:
        sensors = list(lidar_manager.sensors.keys())
        disconnected = 0
        fake_disconnected = 0
        real_disconnected = 0

        for sensor_id in sensors:
            # Check sensor type before disconnecting
            sensor = lidar_manager.sensors[sensor_id]
            is_fake = hasattr(sensor, 'is_simulation') and sensor.is_simulation

            if await lidar_manager.disconnect_sensor(sensor_id, force=True):
                disconnected += 1
                if is_fake:
                    fake_disconnected += 1
                else:
                    real_disconnected += 1

        return OperationResponse(
            success=True,
            message=f"Disconnected {disconnected} sensors ({fake_disconnected} fake, {real_disconnected} real)",
            data={
                "disconnected_count": disconnected,
                "fake_disconnected": fake_disconnected,
                "real_disconnected": real_disconnected
            }
        )

    except Exception as e:
        logger.error(f"Error disconnecting all sensors: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/berthing-mode/on", response_model=BerthingModeResponse)
async def berthing_mode_on(request: BerthingModeRequest):
    """Enable berthing mode for specified sensors - discovers, connects, and starts streaming with center stats"""
    try:
        result = await lidar_manager.enable_berthing_mode(
            sensor_ids=request.sensor_ids,
            computer_ip=request.computer_ip
        )
        
        return BerthingModeResponse(
            berthing_mode_active=result["active"],
            sensor_ids=request.sensor_ids,
            connected_sensors=result["connected_sensors"],
            streaming_sensors=result["streaming_sensors"],
            message=result["message"],
            center_stats=result.get("center_stats"),
            synchronized=result.get("synchronized", False),
            last_sync_timestamp=result.get("last_sync_timestamp"),
            sync_quality=result.get("sync_quality")
        )
        
    except Exception as e:
        logger.error(f"Error enabling berthing mode: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/berthing-mode/off", response_model=BerthingModeResponse)
async def berthing_mode_off(request: BerthingModeRequest):
    """Disable berthing mode for specified sensors - stops streaming, spins down, and disconnects"""
    try:
        result = await lidar_manager.disable_berthing_mode(
            sensor_ids=request.sensor_ids
        )
        
        return BerthingModeResponse(
            berthing_mode_active=result["active"],
            sensor_ids=request.sensor_ids,
            connected_sensors=result["connected_sensors"],
            streaming_sensors=result["streaming_sensors"],
            message=result["message"],
            synchronized=result.get("synchronized", False),
            last_sync_timestamp=result.get("last_sync_timestamp"),
            sync_quality=result.get("sync_quality")
        )
        
    except Exception as e:
        logger.error(f"Error disabling berthing mode: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/berthing-mode/status", response_model=BerthingModeResponse)
async def berthing_mode_status():
    """Get current berthing mode status"""
    try:
        result = await lidar_manager.get_berthing_mode_status()
        
        return BerthingModeResponse(
            berthing_mode_active=result["active"],
            sensor_ids=result["sensor_ids"],
            connected_sensors=result["connected_sensors"],
            streaming_sensors=result["streaming_sensors"],
            message=result["message"],
            center_stats=result.get("center_stats"),
            synchronized=result.get("synchronized", False),
            last_sync_timestamp=result.get("last_sync_timestamp"),
            sync_quality=result.get("sync_quality")
        )
        
    except Exception as e:
        logger.error(f"Error getting berthing mode status: {str(e)}")


@router.post("/connect-fake-lidar", response_model=OperationResponse)
async def connect_fake_lidar():
    """Connect to a fake lidar simulator for testing. Each call creates a different fake lidar."""
    try:
        success, sensor_id = await lidar_manager.connect_fake_lidar(
            sensor_id=None,  # Let the manager generate a unique ID
            computer_ip="127.0.0.1",
            sensor_ip="127.0.0.1",
            data_port=None,  # Let the manager find available ports
            cmd_port=None,
            imu_port=None
        )

        if success:
            return OperationResponse(
                success=True,
                message=f"Successfully connected to fake lidar simulator {sensor_id}",
                data={"sensor_id": sensor_id, "is_simulation": True}
            )
        else:
            raise HTTPException(status_code=400, detail=f"Failed to connect to fake lidar {sensor_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting to fake lidar: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))