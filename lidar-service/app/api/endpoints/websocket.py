from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict, List, Any
import asyncio
import json
import time
import base64
import queue
import math
from ...services.device_manager import device_manager
from ...core.logging_config import logger
from ...services.berthing_data_core import get_all_berthing_data_core # Import the core data function

router = APIRouter(prefix="/ws", tags=["WebSocket"])

class ConnectionManager:
    def __init__(self, all_berthing_emit_interval: float = 1.0):
        # Connections for sensor-specific streams (e.g., point cloud, metrics)
        self.sensor_connections: Dict[str, List[WebSocket]] = {}
        # Connections for the "all berthing data" global stream
        self.global_berthing_connections: List[WebSocket] = []

        self.all_berthing_emit_interval = all_berthing_emit_interval
        self.all_berthing_emitter_task: asyncio.Task | None = None
        logger.info(f"WebSocket ConnectionManager initialized. Global berthing emit interval: {self.all_berthing_emit_interval}s")
    
    async def connect_sensor_stream(self, websocket: WebSocket, sensor_id: str):
        await websocket.accept()
        if sensor_id not in self.sensor_connections:
            self.sensor_connections[sensor_id] = []
        self.sensor_connections[sensor_id].append(websocket)
        logger.info(f"Sensor WebSocket connected for {sensor_id}. Total for sensor: {len(self.sensor_connections[sensor_id])}")
    
    def disconnect_sensor_stream(self, websocket: WebSocket, sensor_id: str):
        if sensor_id in self.sensor_connections:
            if websocket in self.sensor_connections[sensor_id]:
                self.sensor_connections[sensor_id].remove(websocket)
                logger.info(f"Sensor WebSocket disconnected for {sensor_id}. Remaining: {len(self.sensor_connections[sensor_id])}")
            
            # Clean up empty sensor connections list
            if not self.sensor_connections[sensor_id]:
                del self.sensor_connections[sensor_id]
        else:
            logger.warning(f"Attempted to disconnect sensor stream for {sensor_id} but no connections found for it.")

    async def connect_global_berthing_stream(self, websocket: WebSocket):
        logger.info(f"Attempting to accept global berthing WebSocket connection from client: {websocket.client.host}:{websocket.client.port}")
        await websocket.accept()
        try:
            self.global_berthing_connections.append(websocket)
            logger.info(f"Global Berthing WebSocket connected from client: {websocket.client.host}:{websocket.client.port}. Total global: {len(self.global_berthing_connections)}")
        except Exception as e:
            logger.error(f"CRITICAL ERROR: Failed to accept global berthing WebSocket connection from client: {websocket.client.host}:{websocket.client.port} - Exception: {e}", exc_info=True)

    def disconnect_global_berthing_stream(self, websocket: WebSocket):
        try:
            self.global_berthing_connections.remove(websocket)
            logger.info(f"Global Berthing WebSocket disconnected. Remaining global: {len(self.global_berthing_connections)}")
        except ValueError:
            logger.warning(f"Attempted to disconnect a global WebSocket not in active connections.")

    async def send_to_sensor_connections(self, sensor_id: str, data: dict):
        if sensor_id in self.sensor_connections:
            message = json.dumps(data)
            dead_connections = []
            
            for connection in self.sensor_connections[sensor_id]:
                try:
                    await connection.send_text(message)
                except WebSocketDisconnect:
                    dead_connections.append(connection)
                    logger.warning(f"Detected disconnected sensor client {sensor_id} during send_to_sensor_connections.")
                except Exception as e:
                    logger.warning(f"Failed to send to sensor WebSocket connection {sensor_id}: {e}")
                    dead_connections.append(connection)
            
            for dead_conn in dead_connections:
                self.disconnect_sensor_stream(dead_conn, sensor_id)
    
    async def broadcast_all_berthing_data(self, data: Dict[str, Any]):
        """Broadcasts the aggregated berthing data to all global subscribers."""
        if not self.global_berthing_connections:
            return # No clients to send to

        message = json.dumps(data)
        dead_connections = []
        
        for connection in self.global_berthing_connections:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                dead_connections.append(connection)
                logger.warning("Detected disconnected global berthing client during broadcast_all_berthing_data.")
            except Exception as e:
                logger.warning(f"Failed to broadcast to global berthing WebSocket connection: {e}")
                dead_connections.append(connection)
        
        for dead_conn in dead_connections:
            self.disconnect_global_berthing_stream(dead_conn)

    async def _all_berthing_data_emitter_task(self):
        """
        Background task to periodically fetch and broadcast aggregated berthing data.
        Handles dynamic sensor changes gracefully.
        """
        logger.info("Starting global berthing data emitter task...")
        last_sensor_count = 0
        last_sync_timestamp = 0

        while True:
            try:
                start_time = asyncio.get_event_loop().time() # Use monotonic time for accurate intervals

                # Only fetch if there are active global connections
                if self.global_berthing_connections:
                    try:
                        all_data = await get_all_berthing_data_core()

                        # Check if sensor configuration has changed
                        current_sensor_count = len(all_data.get("sensors", {}))
                        current_sync_timestamp = all_data.get("_server_timestamp_utc", 0)

                        # Log changes in sensor configuration
                        if current_sensor_count != last_sensor_count:
                            logger.info(f"Sensor configuration changed: {last_sensor_count} -> {current_sensor_count} sensors")
                            last_sensor_count = current_sensor_count

                        # Only broadcast if we have new data or sensor changes
                        if current_sync_timestamp > last_sync_timestamp or current_sensor_count != last_sensor_count:
                            await self.broadcast_all_berthing_data(all_data)
                            last_sync_timestamp = current_sync_timestamp
                        else:
                            logger.debug("No new berthing data to broadcast")

                    except Exception as data_error:
                        logger.error(f"Error fetching berthing data: {data_error}")
                        # Send error message to clients
                        error_data = {
                            "sensors": {},
                            "count": 0,
                            "berthing_mode_active": False,
                            "synchronized": False,
                            "_server_timestamp_utc": time.time(),
                            "error": f"Data fetch error: {str(data_error)}"
                        }
                        await self.broadcast_all_berthing_data(error_data)
                else:
                    logger.debug("No global berthing clients, skipping data fetch and broadcast.")

                elapsed = asyncio.get_event_loop().time() - start_time
                sleep_for = max(0, self.all_berthing_emit_interval - elapsed)
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                logger.info("Global berthing data emitter task cancelled.")
                break
            except Exception as e:
                logger.exception(f"Error in global berthing data emitter task: {e}")
                # Don't break on error, attempt to continue after a short pause
                await asyncio.sleep(self.all_berthing_emit_interval) # Wait before retrying

    def start_all_berthing_data_stream(self):
        if self.all_berthing_emitter_task and not self.all_berthing_emitter_task.done():
            logger.info("Global berthing data stream emitter is already running.")
            return
        self.all_berthing_emitter_task = asyncio.create_task(self._all_berthing_data_emitter_task())
        logger.info("Global berthing data stream emitter task initiated.")

    async def stop_all_berthing_data_stream(self):
        if self.all_berthing_emitter_task:
            logger.info("Stopping global berthing data stream emitter task...")
            self.all_berthing_emitter_task.cancel()
            try:
                await self.all_berthing_emitter_task # Wait for the task to finish cancelling
            except asyncio.CancelledError:
                pass # Expected when cancelling
            except Exception as e:
                logger.error(f"Error while stopping global berthing data emitter task: {e}")
            self.all_berthing_emitter_task = None
            logger.info("Global berthing data stream emitter task stopped.")


manager = ConnectionManager(all_berthing_emit_interval=1.0) # Adjust interval for global data as needed

# --- WebSocket Endpoints ---

@router.websocket("/sensors/{sensor_id}/stream")
async def websocket_point_cloud_stream(websocket: WebSocket, sensor_id: str):
    """Real-time point cloud streaming via WebSocket for a specific sensor."""
    await manager.connect_sensor_stream(websocket, sensor_id) # Use new method
    
    if not device_manager.lidar_manager or sensor_id not in device_manager.lidar_manager.sensors:
        await websocket.send_text(json.dumps({
            "type": "error", "message": f"Sensor {sensor_id} not found" }))
        await websocket.close()
        manager.disconnect_sensor_stream(websocket, sensor_id) # Ensure cleanup
        return

    streaming_started = False
    if not device_manager.lidar_manager.stream_active.get(sensor_id, False):
        logger.info(f"Starting data stream for WebSocket client on sensor {sensor_id}")
        success = await device_manager.lidar_manager.start_data_stream(sensor_id)
        if not success:
            await websocket.send_text(json.dumps({
                "type": "error", "message": f"Failed to start data stream for sensor {sensor_id}" }))
            await websocket.close()
            manager.disconnect_sensor_stream(websocket, sensor_id) # Ensure cleanup
            return
        streaming_started = True
    
    try:
        await websocket.send_text(json.dumps({
            "type": "connection_established", "sensor_id": sensor_id, "streaming": True,
            "message": "Real-time point cloud streaming started" }))
        
        last_data_time = time.time()
        while True:
            # Check for disconnect signals or client messages
            try:
                # This will raise WebSocketDisconnect if client closes
                # We can also handle actual messages if clients are meant to send commands
                # await websocket.receive_text()
                pass
            except asyncio.CancelledError:
                break # Task was cancelled
            except WebSocketDisconnect:
                break # Client disconnected

            if device_manager.lidar_manager and sensor_id in device_manager.lidar_manager.data_queues and not device_manager.lidar_manager.data_queues[sensor_id].empty():
                try:
                    # Get data from queue without removing it permanently
                    encoded_data = lidar_manager.data_queues[sensor_id].get_nowait()
                    decoded_data = base64.b64decode(encoded_data).decode('utf-8')
                    latest_data = json.loads(decoded_data)
                    lidar_manager.data_queues[sensor_id].put(encoded_data) # Put it back

                    current_time = time.time()
                    if current_time - last_data_time >= 0.0083:
                        websocket_message = {
                            "type": "point_cloud_data", "timestamp": latest_data["timestamp"],
                            "sensor_id": sensor_id, "points": latest_data.get("points", []),
                            "center_stats": latest_data.get("center_stats", {}),
                            "total_points_captured": latest_data.get("total_points_captured", 0),
                            "capture_stats": latest_data.get("capture_stats", {}),
                            "status": "streaming"
                        }
                        await websocket.send_text(json.dumps(websocket_message))
                        last_data_time = current_time
                        
                except queue.Empty:
                    pass
                except Exception as e:
                    logger.error(f"Error processing point cloud data for {sensor_id}: {e}")
                    await websocket.send_text(json.dumps({"type": "error", "message": f"Data processing error: {str(e)}"}))
                    # Consider breaking if error is severe
            
            await asyncio.sleep(0.0083) # Max 120 FPS
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected from sensor {sensor_id} (point cloud stream).")
    except Exception as e:
        logger.error(f"WebSocket error in point cloud stream for {sensor_id}: {e}")
    finally:
        manager.disconnect_sensor_stream(websocket, sensor_id) # Use new method
        
        if streaming_started and not manager.sensor_connections.get(sensor_id): # If no more clients for THIS sensor
            logger.info(f"Last WebSocket client for sensor {sensor_id} point cloud disconnected, considering stopping its data stream for optimal resource use if no other consumers (HTTP, DB) need it.")
            # Important: Here you might want to call lidar_manager.stop_data_stream(sensor_id)
            # However, be cautious. If other parts of your system (like HTTP endpoints or DatabaseConsumer)
            # still rely on that stream being active, stopping it here would break them.
            # You need a more sophisticated reference counting or explicit control mechanism for stream lifecycle.
            # For now, we'll leave it running as per your original note: "keeping stream active for HTTP clients"
            pass


@router.websocket("/sensors/{sensor_id}/metrics")
async def websocket_metrics_stream(websocket: WebSocket, sensor_id: str):
    """Real-time metrics streaming via WebSocket for a specific sensor."""
    await manager.connect_sensor_stream(websocket, sensor_id) # Use new method
    
    if not device_manager.lidar_manager or sensor_id not in device_manager.lidar_manager.sensors:
        await websocket.send_text(json.dumps({
            "type": "error", "message": f"Sensor {sensor_id} not found" }))
        await websocket.close()
        manager.disconnect_sensor_stream(websocket, sensor_id) # Ensure cleanup
        return

    try:
        await websocket.send_text(json.dumps({
            "type": "metrics_stream_started", "sensor_id": sensor_id,
            "message": "Real-time metrics streaming started" }))
        
        while True:
            try:
                # Check for disconnect signals or client messages
                # await websocket.receive_text()
                pass
            except asyncio.CancelledError:
                break
            except WebSocketDisconnect:
                break

            if device_manager.lidar_manager and sensor_id in device_manager.lidar_manager.data_queues and not device_manager.lidar_manager.data_queues[sensor_id].empty():
                try:
                    encoded_data = device_manager.lidar_manager.data_queues[sensor_id].get_nowait()
                    decoded_data = base64.b64decode(encoded_data).decode('utf-8')
                    latest_data = json.loads(decoded_data)
                    device_manager.lidar_manager.data_queues[sensor_id].put(encoded_data) # Put it back
                    
                    metrics_message = {
                        "type": "metrics_data", "timestamp": latest_data["timestamp"],
                        "sensor_id": sensor_id, "center_stats": latest_data.get("center_stats", {}),
                        "total_points_captured": latest_data.get("total_points_captured", 0),
                        "capture_stats": latest_data.get("capture_stats", {}),
                        "point_count": len(latest_data.get("points", [])),
                        "status": "streaming"
                    }
                    await websocket.send_text(json.dumps(metrics_message))
                        
                except queue.Empty:
                    pass
                except Exception as e:
                    logger.error(f"Error processing metrics data for {sensor_id}: {e}")
                    await websocket.send_text(json.dumps({"type": "error", "message": f"Data processing error: {str(e)}"}))
            
            await asyncio.sleep(0.2) # 5 FPS for metrics
                
    except WebSocketDisconnect:
        logger.info(f"Metrics WebSocket client disconnected from sensor {sensor_id}.")
    except Exception as e:
        logger.error(f"Metrics WebSocket error for {sensor_id}: {e}")
    finally:
        manager.disconnect_sensor_stream(websocket, sensor_id) # Use new method


@router.websocket("/berthing/data/all") # GLOBAL ENDPOINT FOR ALL SENSORS
async def websocket_all_berthing_data_stream(websocket: WebSocket):
    """
    WebSocket endpoint for streaming aggregated berthing data for all active sensors.
    Clients subscribe here to receive a consolidated view.
    """
    await manager.connect_global_berthing_stream(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "connection_established",
            "message": "Global berthing data stream started"
        }))

        # This loop simply keeps the connection alive. The background task in manager
        # will handle sending data. If clients send messages, you'd process them here.
        while True:
            # You can listen for messages from the client if needed.
            # For a broadcast-only stream, we just need to detect disconnect.
            await websocket.receive_text() # This will raise WebSocketDisconnect if client closes
    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/berthing/data/all (global stream).")
    except Exception as e:
        logger.error(f"Error in global berthing data WebSocket endpoint: {e}")
    finally:
        manager.disconnect_global_berthing_stream(websocket)


@router.websocket("/berthing/data/{berth_id}") # BERTH-SPECIFIC ENDPOINT
async def websocket_berth_berthing_data_stream(websocket: WebSocket, berth_id: int):
    """
    WebSocket endpoint for streaming berthing data for sensors associated with a specific berth.
    Clients subscribe here to receive filtered data for only the sensors in their berth.
    This endpoint subscribes to the global berthing data stream and filters it.
    """
    import httpx
    from ...core.config import settings

    logger.info(f"Client connecting to berth-specific stream for berth {berth_id}")

    # Query database to get sensors for this berth
    berth_sensors = []
    sensor_laser_info = {}
    try:
        postgrest_url = f"{settings.DB_HOST}/rpc/get_lasers_by_berth"
        headers = {'Content-Type': 'application/json'}
        payload = {"p_berth_id": berth_id}

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(postgrest_url, headers=headers, json=payload)
            response.raise_for_status()
            laser_data = response.json()

        # Extract sensor IDs and laser info including name_for_pager
        for laser in laser_data:
            sensor_id = laser.get('serial')
            if sensor_id:
                berth_sensors.append(sensor_id)
                sensor_laser_info[sensor_id] = laser

        logger.info(f"Found {len(berth_sensors)} sensors for berth {berth_id}: {berth_sensors}")

    except httpx.RequestError as req_err:
        logger.exception(f"Network error querying lasers for berth {berth_id}: {req_err}")
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Failed to query database for berth {berth_id}: {str(req_err)}"
        }))
        await websocket.close()
        return
    except httpx.HTTPStatusError as http_err:
        logger.exception(f"PostgREST error querying lasers for berth {berth_id}: {http_err.response.status_code}")
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Database error for berth {berth_id}: {http_err.response.status_code}"
        }))
        await websocket.close()
        return
    except Exception as e:
        logger.exception(f"Unexpected error querying lasers for berth {berth_id}: {e}")
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Unexpected error for berth {berth_id}: {str(e)}"
        }))
        await websocket.close()
        return

    if not berth_sensors:
        logger.warning(f"No sensors found for berth {berth_id}")
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"No sensors found for berth {berth_id}"
        }))
        await websocket.close()
        return

    # Accept the WebSocket connection
    await websocket.accept()

    try:
        await websocket.send_text(json.dumps({
            "type": "connection_established",
            "berth_id": berth_id,
            "sensor_ids": berth_sensors,
            "message": f"Berth-specific berthing data stream started for berth {berth_id}"
        }))

        # Subscribe to global berthing data stream instead of creating our own loop
        # This prevents conflicts with the global stream
        last_data_timestamp = 0
        consecutive_errors = 0
        max_consecutive_errors = 5

        while True:
            try:
                # Check if there's new data available by monitoring the global data
                current_data = await get_all_berthing_data_core()
                current_timestamp = current_data.get("_server_timestamp_utc", 0)

                # Only process if we have new data
                if current_timestamp > last_data_timestamp:
                    last_data_timestamp = current_timestamp
                    consecutive_errors = 0  # Reset error counter on successful data fetch

                    # Filter to only include sensors for this berth
                    filtered_data = {
                        "sensors": {},
                        "count": 0,
                        "berthing_mode_active": current_data.get("berthing_mode_active", False),
                        "synchronized": current_data.get("synchronized", False),
                        "_server_timestamp_utc": current_timestamp,
                        "berth_id": berth_id,
                        "berth_sensors": berth_sensors
                    }

                    # Collect distances for deg calculation
                    distances = []

                    # Only include sensors that are in this berth and still active
                    active_berth_sensors = 0
                    for sensor_id, sensor_data in current_data.get("sensors", {}).items():
                        if sensor_id in berth_sensors:
                            # Check if sensor is active - either LiDAR in berthing mode or laser device
                            is_active = False
                            if sensor_data.get("data_type") == "lidar":
                                is_active = (device_manager.lidar_manager and
                                           sensor_id in device_manager.lidar_manager.berthing_mode_sensors)
                            elif sensor_data.get("data_type") == "laser":
                                # For laser devices, check if they exist in laser_manager and have data
                                is_active = (device_manager.laser_manager and
                                           sensor_id in device_manager.laser_manager.laser_devices and
                                           sensor_data.get("status") == "active")

                            if is_active:
                                # Include name_for_pager from laser info if available
                                laser_info = sensor_laser_info.get(sensor_id, {})
                                if laser_info.get('name_for_pager'):
                                    sensor_data["name_for_pager"] = laser_info['name_for_pager']

                                filtered_data["sensors"][sensor_id] = sensor_data
                                filtered_data["count"] += 1
                                active_berth_sensors += 1

                                # Collect distance for deg calculation
                                if sensor_data.get("status") == "active" and "distance" in sensor_data:
                                    distances.append(sensor_data["distance"])
                            else:
                                logger.debug(f"Sensor {sensor_id} no longer active, excluding from berth {berth_id}")

                    # Log if berth sensors have changed
                    if active_berth_sensors != len(berth_sensors):
                        logger.info(f"Berth {berth_id}: {active_berth_sensors}/{len(berth_sensors)} sensors active")

                    # Calculate deg based on two distance measurements for this berth
                    deg = 0.0
                    baseline = 75.0  # Distance between the two sensors in meters
                    if len(distances) == 2:
                        dist1, dist2 = distances
                        if dist1 > 0 and dist2 > 0:  # Ensure valid distances
                            diff = abs(dist1 - dist2)
                            if diff == 0:
                                deg = 0.0
                            else:
                                # Calculate angle using arcsin of (difference / baseline)
                                # This gives 0-90 degrees based on the geometric relationship
                                deg = math.degrees(math.asin(min(diff / baseline, 1.0)))

                    filtered_data["deg"] = deg

                    # Send filtered data
                    await websocket.send_text(json.dumps(filtered_data))

                # Wait before checking for new data (same interval as global stream)
                await asyncio.sleep(1.0)

            except WebSocketDisconnect:
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in berth-specific stream for berth {berth_id} (attempt {consecutive_errors}): {e}")

                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors for berth {berth_id}, closing connection")
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"Stream failed after {max_consecutive_errors} consecutive errors"
                    }))
                    break
                else:
                    # Send error but continue
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"Stream error: {str(e)}"
                    }))
                    await asyncio.sleep(1.0)  # Wait before retrying

    except WebSocketDisconnect:
        logger.info(f"Client disconnected from berth-specific stream for berth {berth_id}")
    except Exception as e:
        logger.error(f"Error in berth-specific WebSocket endpoint for berth {berth_id}: {e}")
    finally:
        logger.info(f"Berth-specific stream connection closed for berth {berth_id}")


# Functions to broadcast updates (can be called from other modules if needed)
async def broadcast_sensor_update(sensor_id: str, data: dict):
    """Broadcast sensor-specific updates to relevant WebSocket clients."""
    await manager.send_to_sensor_connections(sensor_id, data)

async def broadcast_system_update(data: dict):
    """
    Broadcast system-wide updates to ALL currently connected WebSocket clients
    (including sensor-specific and global ones).
    Note: This will iterate all `sensor_connections` and `global_berthing_connections`.
    If this is only meant for `global_berthing_connections`, use `manager.broadcast_all_berthing_data`.
    """
    # This function's original intent was perhaps to hit all.
    # We will iterate through sensor-specific connections first
    for sensor_id, connections in manager.sensor_connections.items():
        message = json.dumps(data)
        dead_connections = []
        for connection in connections:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                dead_connections.append(connection)
            except Exception as e:
                logger.warning(f"Failed to broadcast system update to sensor client {sensor_id}: {e}")
                dead_connections.append(connection)
        for dead_conn in dead_connections:
            manager.disconnect_sensor_stream(dead_conn, sensor_id)

    # Then iterate through global berthing connections
    await manager.broadcast_all_berthing_data(data) # Re-use for global connections