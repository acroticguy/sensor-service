from .device_manager import device_manager
from .drift_manager import drift_monitor
from ..core.logging_config import logger
from ..core.http_utils import get_lasers_by_berth
from ..core.config import settings
from typing import Dict, Any, List, Optional
import time

async def fetch_berthing_data_for_sensor(sensor_id: str, berth_sensors_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Retrieves raw berthing data for a specific sensor with offset corrections applied.
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
    raw_distance = center_stats.get("stable_distance", 0.0)
    speed = center_stats.get("speed_mps", 0.0)
    speed_mm_s = center_stats.get("speed_mm_s", 0.0)
    instant_speed = center_stats.get("instant_speed", 0.0)
    sa_averaged_speed = center_stats.get("sa_averaged_speed", 0.0)
    trend_speed = center_stats.get("trend_speed", 0.0)
    speed_precision_mm_s = center_stats.get("speed_precision_mm_s", 0.0)
    is_moving = center_stats.get("is_vessel_moving", False)
    movement_phase = center_stats.get("movement_phase", "unknown")
    confidence = center_stats.get("speed_confidence", 0.0)

    # Apply offset corrections if sensor info is available
    corrected_distance = raw_distance
    offset_y = None
    cut_off_distance = None
    baseline_distance = None

    if berth_sensors_info and sensor_id in berth_sensors_info:
        sensor_info = berth_sensors_info[sensor_id]
        laser_info = sensor_info.get('laser_info', {})

        # Apply offset_y correction
        offset_y = laser_info.get('offset_y')
        if offset_y is not None:
            corrected_distance = raw_distance - offset_y
        else:
            corrected_distance = raw_distance

        # Check cut_off_distance
        cut_off_distance = laser_info.get('cut_off_distance')
        if cut_off_distance is not None and corrected_distance > cut_off_distance:
            logger.info(f"Distance {corrected_distance:.3f}m exceeds cut_off_distance {cut_off_distance:.3f}m for {sensor_id}, marking as inactive")
            return {
                "sensor_id": sensor_id,
                "status": "filtered",
                "message": f"Distance exceeds cut_off_distance after offset correction",
                "raw_distance": round(raw_distance, 3),
                "corrected_distance": round(corrected_distance, 3),
                "cut_off_distance": cut_off_distance,
                "timestamp": timestamp
            }

        # Get baseline distance from offset_x
        offset_x = laser_info.get('offset_x')
        if offset_x is not None:
            baseline_distance = float(offset_x)
    else:
        corrected_distance = raw_distance

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
        "distance": round(corrected_distance, 3),
        "distance_mm": round(corrected_distance * 1000, 0),
        "raw_distance": round(raw_distance, 3),
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
        "stable_distance": round(corrected_distance, 3),
        "status": "active"
    }

    # Include correction information
    if offset_y is not None:
        result["offset_y"] = offset_y
    if cut_off_distance is not None:
        result["cut_off_distance"] = cut_off_distance
    if baseline_distance is not None:
        result["baseline_distance"] = baseline_distance

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
        sensor_data: Dictionary of sensor data keyed by sensor_id (with offset corrections already applied)

    Returns:
        List of operation results
    """
    operations = []

    # Process each sensor individually for drift monitoring
    for sensor_id, sensor_data_item in sensor_data.items():
        if sensor_data_item.get('status') == 'active':
            # Get baseline distance from sensor data (already processed with offset corrections)
            baseline_distance = sensor_data_item.get('baseline_distance')

            # Process drift monitoring for this sensor
            operation_result = drift_monitor.process_sensor_data(sensor_id, sensor_data_item, baseline_distance)

            operations.append(operation_result)
    return operations

async def get_all_berthing_data_core() -> Dict[str, Any]:
    """
    Get berthing data for all active sensors and laser devices from the core logic.
    This function will be used by both the HTTP endpoint and the WebSocket emitter.
    Thread-safe implementation to handle concurrent sensor list changes.
    Includes trilateration operations for drift monitoring.
    """
    result = {}

    # Get current operation info first to determine if we need offset corrections
    current_operation = device_manager.get_current_operation()

    # Initialize berth_sensors_info for offset corrections
    berth_sensors_info = None

    # For drift operations, fetch berth sensors information for offset corrections
    if current_operation.get("operation_type") == "drift":
        # Get berth_id from device manager (set during operation start)
        berth_id = device_manager.berth_id if hasattr(device_manager, 'berth_id') and device_manager.berth_id else None

        if berth_id and berth_id != 0:
            try:
                logger.info(f"Fetching berth sensors info for berth {berth_id} to get offset data")
                berth_sensors_data = await get_lasers_by_berth(berth_id, settings.DB_HOST)

                if berth_sensors_data:
                    # Organize by sensor_id for easy lookup
                    berth_sensors_info = {}
                    for laser in berth_sensors_data:
                        sensor_id = laser.get('serial')
                        if sensor_id:
                            berth_sensors_info[sensor_id] = {
                                'laser_info': laser,
                                'sensor_id': sensor_id
                            }
                    logger.info(f"Fetched offset data for {len(berth_sensors_info)} sensors from berth {berth_id}")

                else:
                    logger.warning(f"No berth sensors data returned for berth {berth_id}")

            except Exception as e:
                logger.error(f"Error fetching berth sensors info for berth {berth_id}: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
        else:
            logger.warning("Drift operation detected but no valid berth_id found for offset corrections")

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
                    # Reuse the individual sensor data fetching logic with offset corrections
                    sensor_data = await fetch_berthing_data_for_sensor(sensor_id, berth_sensors_info)
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

    # Process trilateration operations for drift monitoring
    operations = []
    operation_data = {}
    try:
        operations = _process_drift_monitoring_operations(result)

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