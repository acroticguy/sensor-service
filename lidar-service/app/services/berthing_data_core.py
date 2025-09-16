from .device_manager import device_manager
from .drift_manager import drift_monitor
from ..core.logging_config import logger
from typing import Dict, Any, List
import time

async def fetch_berthing_data_for_sensor(sensor_id: str) -> Dict[str, Any]:
    """
    Retrieves raw berthing data for a specific sensor.
    """
    if not device_manager.lidar_manager or sensor_id not in device_manager.lidar_manager.sensors:
        logger.warning(f"Sensor {sensor_id} not found when fetching core data.")
        return {"sensor_id": sensor_id, "status": "not_found", "message": f"Sensor {sensor_id} not found."}

    if not device_manager.lidar_manager.stream_active.get(sensor_id, False):
        logger.warning(f"Data stream not active for sensor {sensor_id}.")
        return {"sensor_id": sensor_id, "status": "stream_inactive", "message": f"Data stream not active for sensor {sensor_id}."}

    sync_data = device_manager.lidar_manager.sensor_sync_data.get(sensor_id, {})
    center_stats = sync_data.get("center_stats", {})

    # Extract essential data
    timestamp = sync_data.get("timestamp", 0)
    distance = center_stats.get("stable_distance", 0.0)
    speed = center_stats.get("speed_mps", 0.0)
    speed_mm_s = center_stats.get("speed_mm_s", 0.0)
    instant_speed = center_stats.get("instant_speed", 0.0)
    sa_averaged_speed = center_stats.get("sa_averaged_speed", 0.0)
    trend_speed = center_stats.get("trend_speed", 0.0)
    speed_precision_mm_s = center_stats.get("speed_precision_mm_s", 0.0)
    is_moving = center_stats.get("is_vessel_moving", False)
    movement_phase = center_stats.get("movement_phase", "unknown")
    confidence = center_stats.get("speed_confidence", 0.0)

    # Determine movement direction
    if is_moving:
        if speed < 0:
            direction = "approaching"
        elif speed > 0:
            direction = "departing"
        else:
            direction = "lateral"
    else:
        direction = "stationary"

    # Get laser info including name_for_pager if available
    laser_info = device_manager.lidar_manager.berthing_mode_laser_info.get(sensor_id, {})
    name_for_pager = laser_info.get('name_for_pager')

    # Get berth and berthing info if available
    berth_info = device_manager.lidar_manager.berthing_mode_sensor_berth_info.get(sensor_id, {})
    berth_id = berth_info.get('berth_id')
    berthing_id = berth_info.get('berthing_id')

    result = {
        "sensor_id": sensor_id,
        "timestamp": timestamp,
        "distance": round(distance, 3),
        "distance_mm": round(distance * 1000, 0),
        "speed": round(speed, 4),
        "speed_mm_s": round(speed_mm_s, 1),
        "instant_speed": round(instant_speed, 4),
        "instant_speed_mm_s": round(instant_speed * 1000, 1),
        "sa_averaged_speed": round(sa_averaged_speed, 4),
        "sa_averaged_speed_mm_s": round(sa_averaged_speed * 1000, 1),
        "trend_speed": round(trend_speed, 4),
        "speed_precision_mm_s": round(speed_precision_mm_s, 3),
        "is_moving": is_moving,
        "direction": direction,
        "movement_phase": movement_phase,
        "confidence": round(confidence, 2),
        "stable_distance": round(distance, 3),
        "status": "active"
    }

    # Include name_for_pager if available
    if name_for_pager:
        result["name_for_pager"] = name_for_pager

    # Always include berth_id and berthing_id (set to None if not available)
    result["berth_id"] = berth_id
    result["berthing_id"] = berthing_id

    return result

async def fetch_laser_data_for_device(device_id: str) -> Dict[str, Any]:
    """
    Retrieves raw laser data for a specific device.
    """
    if not device_manager.laser_manager or device_id not in device_manager.laser_manager.laser_devices:
        logger.warning(f"Laser device {device_id} not found when fetching core data.")
        return {"device_id": device_id, "status": "not_found", "message": f"Laser device {device_id} not found."}

    if not device_manager.laser_manager.connection_active:
        logger.warning(f"Laser manager not connected for device {device_id}.")
        return {"device_id": device_id, "status": "disconnected", "message": f"Laser manager not connected for device {device_id}."}

    # Get latest synchronized data for this laser device
    sync_data = device_manager.laser_manager.laser_sync_data.get(device_id, {})

    # Prefer synchronized data over raw data points
    if "synchronized_data" in sync_data:
        latest_data = sync_data["synchronized_data"]
    else:
        data_points = sync_data.get("data_points", [])
        if not data_points:
            logger.warning(f"No data points available for laser device {device_id}.")
            return {"device_id": device_id, "status": "no_data", "message": f"No data available for laser device {device_id}."}
        # Use the most recent data point
        latest_data = data_points[-1]

    # Extract laser-specific data
    laser_data = latest_data.get("laser_data", {})
    points = latest_data.get("points", [{}])[0] if latest_data.get("points") else {}

    result = {
        "device_id": device_id,
        "device_type": "laser",
        "timestamp": latest_data.get("timestamp", 0),
        "distance": round(points.get("distance", 0.0), 3),
        "distance_mm": round(points.get("distance", 0.0) * 1000, 0),
        "speed": round(laser_data.get("speed", 0.0), 4),
        "speed_mm_s": round(laser_data.get("speed", 0.0) * 1000, 1),
        "temperature": round(laser_data.get("temperature", 0.0), 1),
        "stable_distance": round(points.get("distance", 0.0), 3),
        "trend_speed": round(laser_data.get("speed", 0.0), 4),
        "strength": laser_data.get("strength", 0),
        "laser_id": laser_data.get("laser_id"),
        "status": "active",
        "data_available": latest_data.get("data_available", False),
        "total_points_captured": latest_data.get("total_points_captured", 0),
        "sync_quality": latest_data.get("sync_quality", "unknown")
    }

    return result

def _group_sensors_by_berth_and_type(sensor_data: Dict[str, Any]) -> Dict[int, Dict[str, List[Dict]]]:
    """
    Group sensors by berth and sensor type for trilateration processing.

    Args:
        sensor_data: Dictionary of sensor data keyed by sensor_id

    Returns:
        Dictionary: berth_id -> sensor_type -> list of sensor data
    """
    grouped = {}

    for sensor_id, data in sensor_data.items():
        berth_id = data.get('berth_id')
        data_type = data.get('data_type', 'unknown')
        status = data.get('status', 'unknown')

        # Only include active sensors with valid berth_id
        if berth_id is not None and status == 'active':
            if berth_id not in grouped:
                grouped[berth_id] = {}
            if data_type not in grouped[berth_id]:
                grouped[berth_id][data_type] = []

            # Assign test positions for sensors (in real implementation, get from sensor calibration)
            # For testing: place sensors at positions that work with ~5m distances
            if len(grouped[berth_id][data_type]) == 0:
                # First sensor at position (0, 0)
                position = (0.0, 0.0)
            else:
                # Second sensor at position (8, 0) - 8 meters apart for valid trilateration
                position = (8.0, 0.0)

            grouped[berth_id][data_type].append({
                'sensor_id': sensor_id,
                'data': data,
                'position': position
            })

    return grouped

def _process_drift_monitoring_operations(sensor_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Process drift monitoring operations for individual sensors during drift operations.

    Args:
        sensor_data: Dictionary of sensor data keyed by sensor_id

    Returns:
        List of operation results
    """
    operations = []

    logger.info(f"Processing {len(sensor_data)} sensors for drift monitoring")

    # Process each sensor individually for drift monitoring
    for sensor_id, sensor_data in sensor_data.items():
        if sensor_data.get('status') == 'active':
            logger.debug(f"Processing drift monitoring for sensor {sensor_id}")

            # Process drift monitoring for this sensor
            operation_result = drift_monitor.process_sensor_data(sensor_id, sensor_data)

            operations.append(operation_result)
            logger.debug(f"Processed drift monitoring for {sensor_id}: danger_level={operation_result.get('danger_level', 'N/A')}")

    logger.info(f"Total drift monitoring operations processed: {len(operations)}")
    return operations

async def get_all_berthing_data_core() -> Dict[str, Any]:
    """
    Get berthing data for all active sensors and laser devices from the core logic.
    This function will be used by both the HTTP endpoint and the WebSocket emitter.
    Thread-safe implementation to handle concurrent sensor list changes.
    Includes trilateration operations for drift monitoring.
    """
    print("DEBUG: get_all_berthing_data_core called")
    logger.info("get_all_berthing_data_core called")
    result = {}

    # Get data from LiDAR manager if available
    if device_manager.lidar_manager:
        # Create a snapshot of the sensor list to avoid concurrent modification issues
        with device_manager.lidar_manager.lock:
            active_sensors = list(device_manager.lidar_manager.berthing_mode_sensors)
            berthing_mode_active = device_manager.lidar_manager.berthing_mode_active
            sync_coordinator_active = device_manager.lidar_manager.sync_coordinator_active

        # Fetch data for each LiDAR sensor outside the lock to avoid blocking
        for sensor_id in active_sensors:
            # Double-check sensor is still active (it might have been removed)
            if sensor_id in device_manager.lidar_manager.berthing_mode_sensors and device_manager.lidar_manager.stream_active.get(sensor_id, False):
                try:
                    # Reuse the individual sensor data fetching logic
                    sensor_data = await fetch_berthing_data_for_sensor(sensor_id)
                    sensor_data["data_type"] = "lidar"  # Distinguish LiDAR from laser
                    result[sensor_id] = sensor_data
                except Exception as e:
                    logger.error(f"Error fetching core berthing data for sensor {sensor_id}: {e}")
                    result[sensor_id] = {
                        "sensor_id": sensor_id,
                        "data_type": "lidar",
                        "status": "error",
                        "message": str(e)
                    }
    else:
        berthing_mode_active = False
        sync_coordinator_active = False

    # Fetch data for each laser device if laser manager is available
    if device_manager.laser_manager:
        active_laser_devices = list(device_manager.laser_manager.laser_devices.keys())
        for device_id in active_laser_devices:
            try:
                # Fetch laser data
                laser_data = await fetch_laser_data_for_device(device_id)
                laser_data["data_type"] = "laser"  # Distinguish laser from LiDAR
                result[device_id] = laser_data
            except Exception as e:
                logger.error(f"Error fetching core laser data for device {device_id}: {e}")
                result[device_id] = {
                    "device_id": device_id,
                    "data_type": "laser",
                    "status": "error",
                    "message": str(e)
                }

    # Get current operation info
    current_operation = device_manager.get_current_operation()

    # Process trilateration operations for drift monitoring
    operations = []
    operation_data = {}
    try:
        logger.info(f"Current operation: {current_operation}")
        logger.info(f"Processing {len(result)} sensors for trilateration")

        # For drift operations, ensure sensors have berth_id for trilateration
        if current_operation.get("operation_type") == "drift":
            berth_id = current_operation.get("berthing_id")
            logger.info(f"Drift operation detected, berthing_id: {berth_id}")
            if berth_id is not None:
                # Assign berth_id to sensors that don't have it (for testing with fake sensors)
                for sensor_id, sensor_data in result.items():
                    if sensor_data.get("berth_id") is None:
                        sensor_data["berth_id"] = berth_id
                        logger.info(f"Assigned berth_id {berth_id} to sensor {sensor_id} for trilateration")
            else:
                logger.info("Drift operation detected but berthing_id is None")

        operations = _process_drift_monitoring_operations(result)
        logger.info(f"Processed {len(operations)} drift monitoring operations")

        # For drift operations, include operation-specific data
        if current_operation.get("operation_type") == "drift":
            # Find the highest danger level from all operations
            max_danger_level = 0
            for op in operations:
                if op.get("danger_level", 0) > max_danger_level:
                    max_danger_level = op["danger_level"]

            operation_data = {
                "danger_level": max_danger_level,
                "drift_operations": operations
            }
            logger.info(f"Drift operation data: danger_level={max_danger_level}, operations={len(operations)}")
    except Exception as e:
        logger.error(f"Error processing trilateration operations: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")

    return {
        "sensors": result,
        "count": len(result),
        "berthing_mode_active": berthing_mode_active,
        "synchronized": sync_coordinator_active,
        "current_operation": current_operation,
        "operation_data": operation_data,
        "_server_timestamp_utc": time.time() # Add server timestamp here for consistency
    }