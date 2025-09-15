"""
Simplified berthing data endpoint for point cloud viewer
Provides essential distance and speed measurements
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional

from ...services.berthing_data_core import fetch_berthing_data_for_sensor
from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger
from ...services.db_streamer_service import db_streamer_service
from ...models.lidar import OperationResponse, StartOperationRequest, OperationType
from ...core.http_utils import get_lasers_by_berth

router = APIRouter()


@router.get("/sensor/{sensor_id}", response_model=Dict[str, Any])
async def get_berthing_data(sensor_id: str) -> Dict[str, Any]:
    """
    Get simplified berthing data for point cloud viewer
    
    Returns essential measurements:
    - Synchronized timestamp
    - Distance to vessel
    - Speed (approach/departure)
    - Movement status
    """
    
    try:
        # berthing_data_core handles validation and data extraction
        raw_data = await fetch_berthing_data_for_sensor(sensor_id)
        
        if raw_data.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=raw_data["message"])
        if raw_data.get("status") == "stream_inactive":
            raise HTTPException(status_code=400, detail=raw_data["message"])
        
        return raw_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting berthing data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all", response_model=Dict[str, Any])
async def get_all_berthing_data() -> Dict[str, Any]:
    """
    Get berthing data for all active sensors
    
    Returns a dictionary with sensor IDs as keys
    """
    try:
        result = {}
        
        for sensor_id in lidar_manager.berthing_mode_sensors:
            if lidar_manager.stream_active.get(sensor_id, False):
                try:
                    sensor_data = await get_berthing_data(sensor_id)
                    result[sensor_id] = sensor_data
                except:
                    result[sensor_id] = {
                        "sensor_id": sensor_id,
                        "status": "error"
                    }
        
        return {
            "sensors": result,
            "count": len(result),
            "berthing_mode_active": lidar_manager.berthing_mode_active,
            "synchronized": lidar_manager.sync_coordinator_active
        }
        
    except Exception as e:
        logger.error(f"Error getting all berthing data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{sensor_id}", response_model=Dict[str, Any])
async def get_berthing_history(
    sensor_id: str,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Get historical berthing data for a sensor
    
    Args:
        sensor_id: Sensor identifier
        limit: Maximum number of historical points to return
        
    Returns:
        Historical distance and speed measurements
    """
    try:
        # Check if sensor has vessel speed calculator
        if sensor_id not in lidar_manager.vessel_speed_calculators:
            raise HTTPException(
                status_code=404,
                detail=f"No speed calculator found for sensor {sensor_id}"
            )
        
        calculator = lidar_manager.vessel_speed_calculators[sensor_id]
        
        # Get distance history
        history = list(calculator.distance_history)
        
        # Limit the results
        if len(history) > limit:
            history = history[-limit:]
        
        # Format history data
        history_data = []
        for i, (timestamp, distance) in enumerate(history):
            # Calculate speed between points
            if i > 0:
                prev_t, prev_d = history[i-1]
                dt = timestamp - prev_t
                if dt > 0:
                    speed = (distance - prev_d) / dt
                else:
                    speed = 0.0
            else:
                speed = 0.0
            
            history_data.append({
                "timestamp": timestamp,
                "distance": round(distance, 3),
                "speed": round(speed, 4),
                "speed_mm_s": round(speed * 1000, 1)
            })
        
        return {
            "sensor_id": sensor_id,
            "history": history_data,
            "count": len(history_data),
            "oldest_timestamp": history_data[0]["timestamp"] if history_data else 0,
            "latest_timestamp": history_data[-1]["timestamp"] if history_data else 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting berthing history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary", response_model=Dict[str, Any])
async def get_berthing_summary() -> Dict[str, Any]:
    """
    Get summary of berthing operation status
    
    Provides high-level overview for monitoring displays
    """
    try:
        summary = {
            "berthing_mode_active": lidar_manager.berthing_mode_active,
            "sensors": {},
            "overall_status": "idle"
        }
        
        any_moving = False
        min_distance = float('inf')
        max_speed = 0.0
        
        for sensor_id in lidar_manager.berthing_mode_sensors:
            if sensor_id in lidar_manager.berthing_mode_center_stats:
                stats = lidar_manager.berthing_mode_center_stats[sensor_id]
                
                distance = stats.get("stable_distance", 0.0)
                speed = abs(stats.get("speed_mps", 0.0))
                is_moving = stats.get("is_vessel_moving", False)
                phase = stats.get("movement_phase", "unknown")
                
                summary["sensors"][sensor_id] = {
                    "distance": round(distance, 3),
                    "speed_mm_s": round(speed * 1000, 1),
                    "is_moving": is_moving,
                    "phase": phase
                }
                
                if is_moving:
                    any_moving = True
                if distance < min_distance and distance > 0:
                    min_distance = distance
                if speed > max_speed:
                    max_speed = speed
        
        # Determine overall status
        if not lidar_manager.berthing_mode_active:
            summary["overall_status"] = "inactive"
        elif any_moving:
            if min_distance < 5.0:
                summary["overall_status"] = "final_approach"
            elif min_distance < 20.0:
                summary["overall_status"] = "approaching"
            else:
                summary["overall_status"] = "monitoring"
        else:
            if min_distance < 1.0:
                summary["overall_status"] = "berthed"
            else:
                summary["overall_status"] = "standby"
        
        summary["min_distance"] = round(min_distance, 3) if min_distance != float('inf') else None
        summary["max_speed_mm_s"] = round(max_speed * 1000, 1)
        
        return summary
        
    except Exception as e:
        logger.error(f"Error getting berthing summary: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stream/on", response_model=OperationResponse)
async def start_db_streaming_endpoint():
    """
    Starts the database streaming process.
    """
    try:
        await db_streamer_service.start_db_streaming()
        return OperationResponse(success=True, message="Database streaming initiated.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting DB streaming: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start DB streaming: {e}")


@router.post("/stream/off", response_model=OperationResponse)
async def stop_db_streaming_endpoint():
    """
    Stops the database streaming process.
    """
    try:
        await db_streamer_service.stop_db_streaming()
        return OperationResponse(success=True, message="Database streaming stopped.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping DB streaming: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to stop DB streaming: {e}")


@router.post("/berth/{berth_id}/start_operation/{operation_type}", response_model=Dict[str, Any])
async def start_operation_by_berth(berth_id: int, operation_type: OperationType, request: StartOperationRequest):
    """
    Start berthing operation for all lasers associated with a specific berth.

    This endpoint:
    1. Queries the database to find all lasers for the berth
    2. Enables berthing mode for those lasers
    3. Starts database consumer if operation_type is BERTH or UNMOOR

    Args:
        berth_id: The berth ID to start operation for
        operation_type: The type of operation (BERTH, DRIFT, or UNMOOR)
        request: Request body with berthing_id, use_lidar, and use_laser flags

    Returns:
        Dictionary with operation results including:
        - success: Whether operation was successful
        - berth_id: The berth ID that was processed
        - berthing_id: The berthing operation ID
        - operation_type: The operation type
        - lasers_found: List of lasers associated with the berth
        - sensor_ids: List of sensor IDs that were activated
        - berthing_result: Detailed berthing mode activation results
        - db_consumer_started: Whether database consumer was started
    """
    try:
        logger.info(f"Starting berthing operation {operation_type.value} for berth {berth_id}, berthing_id {request.berthing_id}")
        logger.info(f"Device configuration: use_lidar={request.use_lidar}, use_laser={request.use_laser}")

        # Import device_manager for unified device control
        from ...services.device_manager import device_manager

        # Configure device manager for this berth operation
        device_manager.configure_devices(
            use_lidar=request.use_lidar,
            use_laser=request.use_laser,
            berth_id=berth_id,
        )

        # Determine auto_update based on operation_type
        auto_update = operation_type in [OperationType.BERTH, OperationType.UNMOOR]

        # Start the operation using device_manager
        operation_result = await device_manager.start_operation_by_berth(
            berth_id=berth_id,
            operation_type=operation_type,
            berthing_id=request.berthing_id,
            auto_update=auto_update
        )

        if operation_result.get("success"):
            logger.info(f"Successfully started berthing operation {operation_type.value} for berth {berth_id}")
            # Add berthing_id and operation_type to result
            operation_result["berthing_id"] = request.berthing_id
            operation_result["operation_type"] = operation_type.value
            operation_result["device_config"] = {
                "use_lidar": request.use_lidar,
                "use_laser": request.use_laser
            }
            return operation_result
        else:
            logger.warning(f"Failed to start berthing operation {operation_type.value} for berth {berth_id}: {operation_result.get('message')}")
            raise HTTPException(
                status_code=400,
                detail=operation_result.get("message", f"Failed to start operation for berth {berth_id}")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting berthing operation {operation_type.value} for berth {berth_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error starting operation {operation_type.value} for berth {berth_id}: {str(e)}"
        )


@router.get("/{berth_id}/view_sensors", response_model=Dict[str, Any])
async def get_berth_sensors_status(berth_id: int):
    """
    Get the status of all sensors related to a specific berth.

    This endpoint:
    1. Queries the database to find all lasers for the berth
    2. Retrieves the current status of each sensor
    3. Returns comprehensive sensor status information

    Args:
        berth_id: The berth ID to get sensor status for

    Returns:
        Dictionary with sensor status results including:
        - success: Whether the query was successful
        - berth_id: The berth ID that was processed
        - sensors: List of sensor status information
        - sensor_count: Number of sensors found
        - connected_count: Number of sensors currently connected
        - streaming_count: Number of sensors currently streaming
        - berthing_mode_count: Number of sensors in berthing mode
    """
    from ...core.config import settings

    try:
        logger.info(f"Getting sensor status for berth {berth_id}")

        # Step 1: Query database for lasers associated with this berth via PostgREST
        logger.info(f"Querying database for lasers associated with berth {berth_id}")

        laser_data = await get_lasers_by_berth(berth_id, settings.DB_HOST)

        if not laser_data:
            logger.warning(f"No lasers found for berth {berth_id}")
            return {
                "success": True,
                "berth_id": berth_id,
                "message": f"No lasers found for berth {berth_id}",
                "sensors": [],
                "sensor_count": 0,
                "connected_count": 0,
                "streaming_count": 0,
                "berthing_mode_count": 0
            }

        # Extract sensor IDs from the laser data
        sensor_ids = []
        laser_info = {}
        for laser in laser_data:
            sensor_id = laser.get('serial')
            if sensor_id:
                sensor_ids.append(sensor_id)
                laser_info[sensor_id] = laser

        logger.info(f"Found {len(sensor_ids)} sensors for berth {berth_id}: {sensor_ids}")

        # Step 2: Get status for each sensor
        sensors_status = []
        connected_count = 0
        streaming_count = 0
        berthing_mode_count = 0

        for sensor_id in sensor_ids:
            sensor_status = {
                "sensor_id": sensor_id,
                "laser_info": laser_info.get(sensor_id, {}),
                "connection_status": "disconnected",
                "streaming_status": False,
                "berthing_mode_active": False,
                "sensor_info": None,
                "center_stats": None,
                "last_sync_timestamp": None
            }

            # Check if sensor is connected
            if sensor_id in lidar_manager.sensors:
                connected_count += 1
                sensor_status["connection_status"] = "connected"

                # Get sensor info if available
                sensor_info = lidar_manager.get_sensor_info(sensor_id)
                if sensor_info:
                    sensor_status["sensor_info"] = sensor_info.dict() if hasattr(sensor_info, 'dict') else sensor_info

                # Check if sensor is streaming
                if lidar_manager.stream_active.get(sensor_id, False):
                    streaming_count += 1
                    sensor_status["streaming_status"] = True

                # Check if sensor is in berthing mode
                if sensor_id in lidar_manager.berthing_mode_sensors:
                    berthing_mode_count += 1
                    sensor_status["berthing_mode_active"] = True

                    # Get center stats if available
                    if sensor_id in lidar_manager.berthing_mode_center_stats:
                        sensor_status["center_stats"] = lidar_manager.berthing_mode_center_stats[sensor_id]

                # Get latest sync timestamp
                if sensor_id in lidar_manager.sensor_sync_data:
                    sensor_status["last_sync_timestamp"] = lidar_manager.sensor_sync_data[sensor_id].get("last_collection_time")

            sensors_status.append(sensor_status)

        # Step 3: Return comprehensive status
        result = {
            "success": True,
            "berth_id": berth_id,
            "message": f"Retrieved status for {len(sensor_ids)} sensors in berth {berth_id}",
            "sensors": sensors_status,
            "sensor_count": len(sensor_ids),
            "connected_count": connected_count,
            "streaming_count": streaming_count,
            "berthing_mode_count": berthing_mode_count,
            "berthing_mode_active": lidar_manager.berthing_mode_active,
            "sync_coordinator_active": lidar_manager.sync_coordinator_active,
            "last_global_sync_timestamp": lidar_manager.last_sync_timestamp
        }

        logger.info(f"Successfully retrieved sensor status for berth {berth_id}: {len(sensor_ids)} sensors, {connected_count} connected, {streaming_count} streaming")
        return result

    except Exception as e:
        if isinstance(e, httpx.RequestError):
            logger.exception(f"Network error querying lasers for berth {berth_id}: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Database connection error for berth {berth_id}: {str(e)}"
            )
        elif isinstance(e, httpx.HTTPStatusError):
            logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {e.response.status_code} - {e.response.text}")
            raise HTTPException(
                status_code=502,
                detail=f"Database query error for berth {berth_id}: {e.response.status_code}"
            )
        else:
            logger.exception(f"Error getting sensor status for berth {berth_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal error getting sensor status for berth {berth_id}: {str(e)}"
            )
    except Exception as e:
        logger.error(f"Error getting sensor status for berth {berth_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error getting sensor status for berth {berth_id}: {str(e)}"
        )


@router.post("/berth/{berth_id}/stop_operation", response_model=Dict[str, Any])
async def stop_operation_by_berth(berth_id: int):
    """
    Stop berthing operation for all lasers associated with a specific berth.

    This endpoint:
    1. Queries the database to find all lasers for the berth
    2. Disables berthing mode for those lasers
    3. Stops database streaming if it was started for this berth

    Args:
        berth_id: The berth ID to stop operation for

    Returns:
        Dictionary with stop results including:
        - success: Whether stop was successful
        - berth_id: The berth ID that was processed
        - lasers_found: List of lasers associated with the berth
        - sensor_ids: List of sensor IDs that were deactivated
        - berthing_result: Detailed berthing mode deactivation results
        - db_streaming_stopped: Whether database streaming was stopped
    """
    try:
        logger.info(f"Stopping berthing operation for berth {berth_id}")

        # Import device_manager for unified device control
        from ...services.device_manager import device_manager

        result = await device_manager.stop_operation_by_berth(berth_id)

        if result.get("success"):
            logger.info(f"Successfully stopped berthing operation for berth {berth_id}")
            return result
        else:
            logger.warning(f"Failed to stop berthing operation for berth {berth_id}: {result.get('message')}")
            raise HTTPException(
                status_code=400,
                detail=result.get("message", f"Failed to stop operation for berth {berth_id}")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping berthing operation for berth {berth_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error stopping operation for berth {berth_id}: {str(e)}"
        )