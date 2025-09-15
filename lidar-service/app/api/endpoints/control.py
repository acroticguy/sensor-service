from fastapi import APIRouter, HTTPException
from typing import Dict, List

from ...models.lidar import (
    LidarInfo, LidarMode, CoordinateSystem, ExtrinsicParameters,
    OperationResponse, ErrorResponse
)
from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger

router = APIRouter(prefix="/control", tags=["Sensor Control"])


@router.get("/sensors", response_model=Dict[str, LidarInfo])
async def get_all_sensors():
    """Get information about all connected sensors"""
    return lidar_manager.get_all_sensors()


@router.get("/sensors/{sensor_id}", response_model=LidarInfo)
async def get_sensor_info(sensor_id: str):
    """Get information about a specific sensor"""
    info = lidar_manager.get_sensor_info(sensor_id)
    if not info:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return info


@router.put("/sensors/{sensor_id}/mode", response_model=OperationResponse)
async def set_sensor_mode(sensor_id: str, mode: LidarMode):
    """Set sensor operating mode (normal, power_saving, standby)"""
    try:
        success = await lidar_manager.set_lidar_mode(sensor_id, mode)

        if success:
            return OperationResponse(
                success=True,
                message=f"Successfully set mode to {mode} for sensor {sensor_id}"
            )
        else:
            raise HTTPException(status_code=404, detail="Sensor not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting sensor mode: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sensors/{sensor_id}/coordinate-system", response_model=OperationResponse)
async def set_coordinate_system(sensor_id: str, system: CoordinateSystem):
    """Set coordinate system (cartesian or spherical)"""
    try:
        success = await lidar_manager.set_coordinate_system(sensor_id, system)

        if success:
            return OperationResponse(
                success=True,
                message=f"Successfully set coordinate system to {system} for sensor {sensor_id}"
            )
        else:
            raise HTTPException(status_code=404, detail="Sensor not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting coordinate system: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sensors/{sensor_id}/extrinsic", response_model=OperationResponse)
async def set_extrinsic_parameters(sensor_id: str, params: ExtrinsicParameters):
    """Set extrinsic parameters (position and orientation)"""
    try:
        success = await lidar_manager.set_extrinsic_parameters(sensor_id, params)

        if success:
            return OperationResponse(
                success=True,
                message=f"Successfully set extrinsic parameters for sensor {sensor_id}"
            )
        else:
            raise HTTPException(status_code=404, detail="Sensor not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting extrinsic parameters: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sensors/{sensor_id}/extrinsic/reset", response_model=OperationResponse)
async def reset_extrinsic_parameters(sensor_id: str):
    """Reset extrinsic parameters to zero"""
    try:
        params = ExtrinsicParameters(x=0, y=0, z=0, roll=0, pitch=0, yaw=0)
        success = await lidar_manager.set_extrinsic_parameters(sensor_id, params)

        if success:
            return OperationResponse(
                success=True,
                message=f"Successfully reset extrinsic parameters for sensor {sensor_id}"
            )
        else:
            raise HTTPException(status_code=404, detail="Sensor not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting extrinsic parameters: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/rain-fog-suppression", response_model=OperationResponse)
async def set_rain_fog_suppression(sensor_id: str, enabled: bool):
    """Enable or disable rain/fog suppression"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.setRainFogSuppression(enabled)

        return OperationResponse(
            success=True,
            message=f"Rain/fog suppression {'enabled' if enabled else 'disabled'} for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting rain/fog suppression: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/fan", response_model=OperationResponse)
async def set_fan_state(sensor_id: str, enabled: bool):
    """Enable or disable sensor fan"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.setFan(enabled)

        return OperationResponse(
            success=True,
            message=f"Fan {'enabled' if enabled else 'disabled'} for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting fan state: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sensors/{sensor_id}/fan", response_model=OperationResponse)
async def get_fan_state(sensor_id: str):
    """Get current fan state"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        fan_state = sensor.getFan()

        return OperationResponse(
            success=True,
            message=f"Fan state retrieved for sensor {sensor_id}",
            data={"fan_enabled": fan_state}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting fan state: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/imu", response_model=OperationResponse)
async def set_imu_data_push(sensor_id: str, enabled: bool):
    """Enable or disable IMU data push"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.setIMUdataPush(enabled)

        return OperationResponse(
            success=True,
            message=f"IMU data push {'enabled' if enabled else 'disabled'} for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting IMU data push: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sensors/{sensor_id}/imu", response_model=OperationResponse)
async def get_imu_data_push(sensor_id: str):
    """Get IMU data push state"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        imu_state = sensor.getIMUdataPush()

        return OperationResponse(
            success=True,
            message=f"IMU data push state retrieved for sensor {sensor_id}",
            data={"imu_enabled": imu_state}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting IMU data push state: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/read-extrinsic", response_model=OperationResponse)
async def read_extrinsic_parameters(sensor_id: str):
    """Read current extrinsic parameters from sensor hardware"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        
        # First try to read from sensor hardware
        sensor.readExtrinsic()
        
        # Give sensor time to process the command
        import asyncio
        await asyncio.sleep(0.1)
        
        # Update sensor info to get latest values
        await lidar_manager._update_sensor_info(sensor_id)
        
        extrinsic_params = lidar_manager.sensor_info[sensor_id].extrinsic_parameters
        
        # Check if we got actual values or just defaults
        has_real_values = (
            extrinsic_params.x != 0.0 or extrinsic_params.y != 0.0 or extrinsic_params.z != 0.0 or
            extrinsic_params.roll != 0.0 or extrinsic_params.pitch != 0.0 or extrinsic_params.yaw != 0.0
        )
        
        message = f"Read extrinsic parameters for sensor {sensor_id}"
        if not has_real_values:
            message += " - WARNING: All parameters are zero. Sensor may need calibration or parameters may need to be set manually."

        return OperationResponse(
            success=True,
            message=message,
            data={
                "extrinsic_parameters": extrinsic_params,
                "needs_calibration": not has_real_values,
                "note": "For vessel berthing, accurate extrinsic parameters are crucial for precise distance measurements"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading extrinsic parameters: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/set-extrinsic-zero", response_model=OperationResponse)
async def set_extrinsic_to_zero(sensor_id: str):
    """Set extrinsic parameters to zero using OpenPyLivox method"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.setExtrinsicToZero()

        # Update sensor info to get latest values
        await lidar_manager._update_sensor_info(sensor_id)

        return OperationResponse(
            success=True,
            message=f"Set extrinsic parameters to zero for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting extrinsic to zero: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/calibrate-vessel-berthing", response_model=OperationResponse)
async def calibrate_for_vessel_berthing(sensor_id: str, mounting_height_m: float = 0.0, dock_angle_deg: float = 0.0):
    """Calibrate LiDAR for vessel berthing operations"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        # Set practical extrinsic parameters for vessel berthing
        params = ExtrinsicParameters(
            x=0.0,  # Forward/backward offset from reference point
            y=0.0,  # Left/right offset from reference point  
            z=mounting_height_m,  # Height above water level
            roll=0.0,  # Typically level
            pitch=0.0,  # Typically level
            yaw=dock_angle_deg  # Angle relative to dock face
        )
        
        success = await lidar_manager.set_extrinsic_parameters(sensor_id, params)
        
        if success:
            return OperationResponse(
                success=True,
                message=f"Calibrated sensor {sensor_id} for vessel berthing operations",
                data={
                    "calibration_applied": {
                        "mounting_height_m": mounting_height_m,
                        "dock_angle_deg": dock_angle_deg,
                        "configured_for": "vessel_berthing_operations"
                    },
                    "next_steps": [
                        "Test distance measurements with known targets",
                        "Verify speed calculations during vessel approach",
                        "Adjust mounting angle if needed for optimal coverage"
                    ]
                }
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to set calibration parameters")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calibrating for vessel berthing: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/set-dynamic-ip", response_model=OperationResponse)
async def set_dynamic_ip(sensor_id: str):
    """Set sensor to use dynamic IP (DHCP)"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.setDynamicIP()

        return OperationResponse(
            success=True,
            message=f"Set dynamic IP for sensor {sensor_id} - sensor will need reboot"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting dynamic IP: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/set-static-ip", response_model=OperationResponse)
async def set_static_ip(sensor_id: str, ip_address: str):
    """Set sensor to use static IP address"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.setStaticIP(ip_address)

        return OperationResponse(
            success=True,
            message=f"Set static IP {ip_address} for sensor {sensor_id} - sensor will need reboot"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting static IP: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/spin-up", response_model=OperationResponse)
async def spin_up_lidar(sensor_id: str):
    """Start LiDAR motor spinning"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.lidarSpinUp()

        return OperationResponse(
            success=True,
            message=f"LiDAR motor spin-up initiated for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error spinning up LiDAR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/spin-down", response_model=OperationResponse)
async def spin_down_lidar(sensor_id: str):
    """Stop LiDAR motor spinning"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.lidarSpinDown()

        return OperationResponse(
            success=True,
            message=f"LiDAR motor spin-down initiated for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error spinning down LiDAR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/start-data", response_model=OperationResponse)
async def start_data_capture(sensor_id: str):
    """Start LiDAR data capture"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.dataStart()

        return OperationResponse(
            success=True,
            message=f"Data capture started for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting data capture: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/stop-data", response_model=OperationResponse)
async def stop_data_capture(sensor_id: str):
    """Stop LiDAR data capture"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.dataStop()

        return OperationResponse(
            success=True,
            message=f"Data capture stopped for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping data capture: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sensors/{sensor_id}/debug", response_model=OperationResponse)
async def debug_sensor_state(sensor_id: str):
    """Debug sensor internal state for troubleshooting"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]

        debug_info = {
            "sensor_connected": sensor_id in lidar_manager.sensors,
            "has_capture_stream": hasattr(sensor, '_captureStream'),
            "capture_stream_active": False,
            "is_data_streaming": getattr(sensor, '_isData', False),
            "capture_stream_details": {}
        }

        if hasattr(sensor, '_captureStream') and sensor._captureStream is not None:
            capture_stream = sensor._captureStream
            debug_info["capture_stream_active"] = True
            debug_info["capture_stream_details"] = {
                "is_capturing": getattr(capture_stream, 'isCapturing', False),
                "started": getattr(capture_stream, 'started', False),
                "num_points": getattr(capture_stream, 'numPts', 0),
                "has_coord_data": (
                    hasattr(capture_stream, 'coord1s') and
                    hasattr(capture_stream, 'coord2s') and
                    hasattr(capture_stream, 'coord3s')
                ),
                "coord_lengths": {
                    "coord1s": len(getattr(capture_stream, 'coord1s', [])),
                    "coord2s": len(getattr(capture_stream, 'coord2s', [])),
                    "coord3s": len(getattr(capture_stream, 'coord3s', []))
                }
            }

        return OperationResponse(
            success=True,
            message=f"Debug info for sensor {sensor_id}",
            data=debug_info
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error debugging sensor: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/trigger-capture", response_model=OperationResponse)
async def trigger_data_capture(sensor_id: str):
    """Manually trigger data capture flag in OpenPyLivox"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]

        # Check if capture stream exists
        if hasattr(sensor, '_captureStream') and sensor._captureStream is not None:
            capture_stream = sensor._captureStream
            # Manually set the capturing flag
            capture_stream.isCapturing = True
            logger.info(f"Triggered data capture for sensor {sensor_id}")

            return OperationResponse(
                success=True,
                message=f"Data capture triggered for sensor {sensor_id}"
            )
        else:
            raise HTTPException(status_code=400, detail="No capture stream active")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering capture: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sensors/{sensor_id}/data-start-rt", response_model=OperationResponse)
async def start_realtime_data(sensor_id: str):
    """Start real-time LiDAR data capture"""
    try:
        if sensor_id not in lidar_manager.sensors:
            raise HTTPException(status_code=404, detail="Sensor not found")

        sensor = lidar_manager.sensors[sensor_id]
        sensor.dataStart_RT_B()

        return OperationResponse(
            success=True,
            message=f"Real-time data capture started for sensor {sensor_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting real-time data capture: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))