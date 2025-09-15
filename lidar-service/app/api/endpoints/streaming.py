from fastapi import APIRouter, HTTPException
import json
import base64
import time

from ...models.lidar import OperationResponse
from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger

router = APIRouter(prefix="/streaming", tags=["Data Streaming"])


@router.get("/data/{sensor_id}/latest")
async def get_latest_sensor_data(sensor_id: str):
    """Get the latest data point from a sensor (for polling) - kept for compatibility"""

    if sensor_id not in lidar_manager.sensors:
        raise HTTPException(status_code=404, detail="Sensor not found")

    if not lidar_manager.stream_active.get(sensor_id, False):
        raise HTTPException(status_code=400, detail="Data stream not active for this sensor")

    try:
        # Try to get the latest data from the queue without removing it
        if not lidar_manager.data_queues[sensor_id].empty():
            # Get data and put it back
            encoded_data = lidar_manager.data_queues[sensor_id].get_nowait()
            lidar_manager.data_queues[sensor_id].put(encoded_data)

            # Decode and process
            decoded_data = base64.b64decode(encoded_data).decode('utf-8')
            data = json.loads(decoded_data)

            # Filter points to only include the 50 closest to the center
            if "points" in data and isinstance(data["points"], list) and len(data["points"]) > 0:
                # Calculate distance from center (0,0,0) for each point
                points_with_distance = []
                for point in data["points"]:
                    # Calculate Euclidean distance from origin
                    x = point.get("x", 0)
                    y = point.get("y", 0)
                    z = point.get("z", 0)
                    distance_from_center = (x**2 + y**2 + z**2)**0.5

                    points_with_distance.append({
                        "point": point,
                        "distance_from_center": distance_from_center
                    })

                # Sort by distance from center and take the 50 closest
                points_with_distance.sort(key=lambda p: p["distance_from_center"])
                closest_points = [p["point"] for p in points_with_distance[:50]]

                # Update the data with only the closest points
                data["points"] = closest_points
                data["filtered_points_count"] = len(closest_points)
                data["original_points_count"] = len(points_with_distance)
                data["filter_applied"] = "closest_to_center"

            return data
        else:
            return {
                "timestamp": time.time(),
                "sensor_id": sensor_id,
                "status": "waiting",
                "message": "No data available yet"
            }

    except Exception as e:
        logger.error(f"Error getting latest data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_streaming_status():
    """Get streaming status for all sensors"""
    try:
        status = {}
        for sensor_id in lidar_manager.sensors:
            status[sensor_id] = {
                "streaming": lidar_manager.stream_active.get(sensor_id, False),
                "queue_size": lidar_manager.data_queues[sensor_id].qsize() if sensor_id in lidar_manager.data_queues else 0
            }

        return {
            "sensors": status,
            "total_streaming": sum(1 for s in status.values() if s["streaming"])
        }

    except Exception as e:
        logger.error(f"Error getting streaming status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))