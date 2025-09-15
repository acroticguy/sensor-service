from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Dict, Any, Optional
from core.multi_laser_manager import MultiLaserManager
from services.websocket_service import websocket_manager
from services.pager_service import get_pager_service, PagerData, PagerConfig
from core.logger import get_logger

logger = get_logger(__name__)

class BerthingRequest(BaseModel):
    berth_id: int
    berthing_id: int

class StopBerthingRequest(BaseModel):
    berth_id: int

class ModeRequest(BaseModel):
    berth_id: int

class PagerTestRequest(BaseModel):
    user_number: str = "0000"
    orientation1: int = 1
    speed1: float = 15.5
    dist1: float = 123.45
    orientation2: int = 4
    speed2: float = 22.0
    dist2: float = 98.76
    angle: float = 45.6

def create_berthing_router(laser_manager: MultiLaserManager) -> APIRouter:
    router = APIRouter(prefix="/berthing", tags=["berthing"])
    
    @router.post("/start", 
                 summary="🚢 Start Berthing Mode",
                 description="Initiate berthing operations with database storage and real-time monitoring",
                 responses={
                     200: {
                         "description": "Berthing mode started successfully",
                         "content": {
                             "application/json": {
                                 "example": {
                                     "success": True,
                                     "message": "Berthing mode started for berth 1",
                                     "berth_id": 1,
                                     "berthing_id": 12345,
                                     "timestamp": "2024-01-15T10:30:00.000Z",
                                     "active_lasers": [21, 22]
                                 }
                             }
                         }
                     },
                     500: {"description": "Failed to start berthing mode"}
                 })
    async def start_berthing_mode(request: BerthingRequest):
        """
        **Start Berthing Mode**
        
        Initiates comprehensive berthing operations for a specific berth:
        
        **Operations Performed:**
        1. 🔄 Send escape sequence to put lasers in standby
        2. 🎯 Start VT (speed measurement) continuous mode
        3. 💾 Begin data synchronization to database every second
        4. 📡 Enable WebSocket streaming for real-time monitoring
        5. 📟 Configure pager notifications (if configured)
        
        **Request Parameters:**
        - `berth_id`: Unique berth identifier (1-999)
        - `berthing_id`: Unique berthing operation identifier
        
        **Behavior:**
        - All lasers associated with the berth will be activated
        - Measurement data is stored in database every second
        - WebSocket clients receive live updates
        - Automatic error recovery and reconnection
        
        **Returns:** Success confirmation with berth and laser details
        """
        try:
            logger.info(f"API request to start berthing mode for berth {request.berth_id}")
            
            success = await laser_manager.start_berthing_mode(
                berth_id=request.berth_id,
                berthing_id=request.berthing_id
            )
            
            if success:
                return {
                    "success": True,
                    "message": f"Berthing mode started for berth {request.berth_id}",
                    "berth_id": request.berth_id,
                    "berthing_id": request.berthing_id
                }
            else:
                # Check if it's a connection issue that might be resolved by restart
                laser_status = laser_manager.get_berth_lasers_status(request.berth_id)
                disconnected_lasers = [ls for ls in laser_status if ls.get('status') == 'DISCONNECTED']
                
                if disconnected_lasers:
                    raise HTTPException(
                        status_code=503,  # Service Unavailable instead of 500
                        detail=f"Failed to start berthing mode for berth {request.berth_id} - {len(disconnected_lasers)} laser(s) are disconnected. Try restarting the simulator."
                    )
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to start berthing mode for berth {request.berth_id}"
                    )
                
        except Exception as e:
            logger.error(f"Error starting berthing mode: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/stop", summary="Stop berthing mode for a berth")
    async def stop_berthing_mode(request: StopBerthingRequest):
        """
        Stop berthing mode for all lasers associated with a berth.
        This will:
        1. Send escape sequence to stop continuous mode
        2. Return lasers to standby mode
        3. Stop data synchronization
        """
        try:
            logger.info(f"API request to stop berthing mode for berth {request.berth_id}")
            
            success = await laser_manager.stop_berthing_mode(berth_id=request.berth_id)
            
            if success:
                return {
                    "success": True,
                    "message": f"Berthing mode stopped for berth {request.berth_id}",
                    "berth_id": request.berth_id
                }
            else:
                # Don't raise 500 error for connection issues during simulator restart
                logger.warning(f"Could not stop berthing mode for berth {request.berth_id} - likely due to connection issues")
                return {
                    "success": True,  # Report success since state was cleaned up
                    "message": f"Berthing mode state cleaned up for berth {request.berth_id} (connections may have been reset)",
                    "berth_id": request.berth_id,
                    "warning": "Some connections were broken during stop operation, but state was cleaned up"
                }
                
        except Exception as e:
            logger.error(f"Error stopping berthing mode: {str(e)}")
            # For connection errors, return success with warning instead of 500 error
            if "WinError 10053" in str(e) or "ConnectionError" in str(e) or "ConnectionResetError" in str(e):
                return {
                    "success": True,
                    "message": f"Berthing mode state cleaned up for berth {request.berth_id}",
                    "berth_id": request.berth_id,
                    "warning": f"Connection was broken during stop: {str(e)}"
                }
            else:
                raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/status", summary="Get status of all laser devices")
    async def get_laser_status():
        """
        Get the current status of all connected laser devices,
        including berthing mode status.
        """
        try:
            status = laser_manager.get_connected_lasers()
            return {
                "success": True,
                "lasers": status,
                "total_lasers": len(status),
                "manager_running": laser_manager.is_running()
            }
            
        except Exception as e:
            logger.error(f"Error getting laser status: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/status/{laser_id}", summary="Get status of specific laser device")
    async def get_laser_status_by_id(laser_id: int):
        """
        Get the current status of a specific laser device.
        """
        try:
            all_status = laser_manager.get_connected_lasers()
            
            if laser_id not in all_status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Laser {laser_id} not found"
                )
            
            return {
                "success": True,
                "laser_id": laser_id,
                "status": all_status[laser_id]
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting laser {laser_id} status: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/restart/{laser_id}", summary="Restart berthing mode for specific laser")
    async def restart_laser_berthing(laser_id: int):
        """
        Restart berthing mode for a specific laser device.
        This is useful if the laser stops sending data during berthing.
        """
        try:
            # Find the laser device
            if laser_id not in laser_manager.laser_devices:
                raise HTTPException(
                    status_code=404,
                    detail=f"Laser {laser_id} not found or not connected"
                )
            
            laser = laser_manager.laser_devices[laser_id]
            
            if not laser.is_in_berthing_mode():
                raise HTTPException(
                    status_code=400,
                    detail=f"Laser {laser_id} is not in berthing mode"
                )
            
            berthing_id = laser.get_berthing_id()
            logger.info(f"API request to restart berthing mode for laser {laser_id}")
            
            # Restart berthing mode
            success = await laser.start_berthing_mode(berthing_id, max_retries=5)
            
            if success:
                return {
                    "success": True,
                    "message": f"Berthing mode restarted for laser {laser_id}",
                    "laser_id": laser_id,
                    "berthing_id": berthing_id
                }
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to restart berthing mode for laser {laser_id}"
                )
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error restarting berthing mode for laser {laser_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/drift/start", summary="Start drift mode for a berth")
    async def start_drift_mode(request: ModeRequest):
        """
        Start drift mode for all lasers associated with a berth.
        In drift mode:
        1. Lasers start continuous VT measurement
        2. Data is streamed via websockets only
        3. No database insertion occurs
        """
        try:
            logger.info(f"API request to start drift mode for berth {request.berth_id}")
            
            success = await laser_manager.start_drift_mode(berth_id=request.berth_id)
            
            if success:
                return {
                    "success": True,
                    "message": f"Drift mode started for berth {request.berth_id}",
                    "berth_id": request.berth_id
                }
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to start drift mode for berth {request.berth_id}"
                )
                
        except Exception as e:
            logger.error(f"Error starting drift mode: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/drift/stop", summary="Stop drift mode for a berth") 
    async def stop_drift_mode(request: ModeRequest):
        """
        Stop drift mode for all lasers associated with a berth.
        This will:
        1. Send escape sequence to stop continuous mode
        2. Return lasers to OFF mode
        3. Stop websocket streaming
        """
        try:
            logger.info(f"API request to stop drift mode for berth {request.berth_id}")
            
            success = await laser_manager.stop_drift_mode(berth_id=request.berth_id)
            
            if success:
                return {
                    "success": True,
                    "message": f"Drift mode stopped for berth {request.berth_id}",
                    "berth_id": request.berth_id
                }
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to stop drift mode for berth {request.berth_id}"
                )
                
        except Exception as e:
            logger.error(f"Error stopping drift mode: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/off", summary="Set berth to OFF mode")
    async def set_off_mode(request: ModeRequest):
        """
        Set all lasers in a berth to OFF mode.
        This will:
        1. Send escape sequence to stop any continuous mode
        2. Set lasers to OFF mode
        3. Stop any active data streaming or database sync
        """
        try:
            logger.info(f"API request to set berth {request.berth_id} to OFF mode")
            
            success = await laser_manager.set_berth_mode_off(berth_id=request.berth_id)
            
            if success:
                return {
                    "success": True,
                    "message": f"Berth {request.berth_id} set to OFF mode",
                    "berth_id": request.berth_id
                }
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to set berth {request.berth_id} to OFF mode"
                )
                
        except Exception as e:
            logger.error(f"Error setting berth to OFF mode: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.websocket("/ws/berth/{berth_id}")
    async def websocket_berth(websocket: WebSocket, berth_id: int):
        """
        🔌 **WebSocket: Berth-Specific Data Stream**
        
        Real-time streaming of laser measurement data for a specific berth.
        
        **Connection URL:** `ws://localhost:8080/api/v1/berthing/ws/berth/{berth_id}`
        
        **Message Format:**
        ```json
        {
          "type": "laser_data",
          "timestamp": "2024-01-15T10:30:00.000Z",
          "laser_id": 21,
          "berth_id": 1,
          "data": {
            "distance": 1.234,
            "speed": 0.56,
            "temperature": 23.5,
            "signal_strength": 8500
          }
        }
        ```
        
        **Features:**
        - Live measurement streaming during berthing operations
        - Automatic pager integration when multiple lasers active
        - Connection management with auto-reconnect
        - Error handling and status updates
        """
        try:
            await websocket_manager.connect_to_berth(websocket, berth_id)
            while True:
                # Keep connection alive and wait for messages
                try:
                    data = await websocket.receive_text()
                    # Echo back any received messages for debugging
                    await websocket.send_text(f"Echo: {data}")
                except WebSocketDisconnect:
                    break
        except Exception as e:
            logger.error(f"WebSocket error for berth {berth_id}: {str(e)}")
        finally:
            websocket_manager.disconnect_from_berth(websocket, berth_id)
    
    @router.websocket("/ws/laser/{laser_id}")
    async def websocket_laser(websocket: WebSocket, laser_id: int):
        """WebSocket endpoint for laser-specific data streaming"""
        try:
            await websocket_manager.connect_to_laser(websocket, laser_id)
            while True:
                # Keep connection alive and wait for messages
                try:
                    data = await websocket.receive_text()
                    # Echo back any received messages for debugging
                    await websocket.send_text(f"Echo: {data}")
                except WebSocketDisconnect:
                    break
        except Exception as e:
            logger.error(f"WebSocket error for laser {laser_id}: {str(e)}")
        finally:
            websocket_manager.disconnect_from_laser(websocket, laser_id)
    
    @router.websocket("/ws/all")
    async def websocket_all(websocket: WebSocket):
        """WebSocket endpoint for all laser data streaming"""
        try:
            await websocket_manager.connect_general(websocket)
            while True:
                # Keep connection alive and wait for messages
                try:
                    data = await websocket.receive_text()
                    # Echo back any received messages for debugging
                    await websocket.send_text(f"Echo: {data}")
                except WebSocketDisconnect:
                    break
        except Exception as e:
            logger.error(f"WebSocket error: {str(e)}")
        finally:
            websocket_manager.disconnect_general(websocket)
    
    @router.get("/ws/stats", 
                tags=["websockets"],
                summary="📊 WebSocket Statistics",
                description="Get comprehensive WebSocket connection statistics and performance metrics")
    async def get_websocket_stats():
        """
        **WebSocket Connection Statistics**
        
        Returns detailed statistics about active WebSocket connections:
        
        **Metrics Included:**
        - Active connections by berth and laser
        - Total connection count across all endpoints
        - Pager service integration status
        - Laser data buffer size
        - Connection performance metrics
        
        **Use Cases:**
        - Monitor real-time streaming performance
        - Debug connection issues
        - Capacity planning and scaling
        - Service health monitoring
        """
        try:
            stats = websocket_manager.get_connection_stats()
            return {
                "success": True,
                "stats": stats
            }
        except Exception as e:
            logger.error(f"Error getting WebSocket stats: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # Pager Service Endpoints
    @router.post("/pager/test", 
                 summary="📟 Test Pager Service", 
                 description="Send test data to pager service with custom parameters",
                 responses={
                     200: {
                         "description": "Pager test completed successfully",
                         "content": {
                             "application/json": {
                                 "example": {
                                     "success": True,
                                     "message": "Pager test completed successfully", 
                                     "pager_response": {
                                         "success": True,
                                         "message": "Page request accepted by transmitter."
                                     },
                                     "sent_data": {
                                         "userNumber": "1235",
                                         "orientation1": 2,
                                         "speed1": 7.5,
                                         "dist1": 54.1,
                                         "orientation2": 4,
                                         "speed2": 12.3,
                                         "dist2": 105.78,
                                         "angle": 8.2
                                     }
                                 }
                             }
                         }
                     },
                     500: {"description": "Pager service error"}
                 })
    async def test_pager_service(request: PagerTestRequest):
        """
        **Test Pager Service Integration**
        
        Send test data to the pager service to verify connectivity and functionality.
        
        **Pager Service Details:**
        - **Endpoint**: `https://localhost:43011/send-pager-data`
        - **Method**: POST
        - **Content-Type**: application/json
        
        **Request Parameters:**
        - `user_number`: Pager ID (1-4 digits, default: "0000")
        - `orientation1/2`: Device orientation (1=North, 2=East, 3=South, 4=West)
        - `speed1/2`: Speed measurements (float)
        - `dist1/2`: Distance measurements (float)
        - `angle`: Calculated angle between devices (float)
        
        **Features:**
        - Circuit breaker protection for reliability
        - Automatic retry with exponential backoff
        - SSL verification disabled for localhost testing
        - Comprehensive error handling and logging
        
        **Returns:** Complete test results including pager service response
        """
        try:
            pager_service = get_pager_service()
            
            # Ensure pager service is initialized
            await pager_service.initialize()
            
            # Create pager data from request
            pager_data = PagerData(
                user_number=request.user_number,
                orientation1=request.orientation1,
                speed1=request.speed1,
                dist1=request.dist1,
                orientation2=request.orientation2,
                speed2=request.speed2,
                dist2=request.dist2,
                angle=request.angle
            )
            
            # Send to pager service
            result = await pager_service.send_pager_data(pager_data)
            
            return {
                "success": True,
                "message": "Pager test completed successfully",
                "pager_response": result,
                "sent_data": {
                    "userNumber": request.user_number,
                    "orientation1": request.orientation1,
                    "speed1": request.speed1,
                    "dist1": request.dist1,
                    "orientation2": request.orientation2,
                    "speed2": request.speed2,
                    "dist2": request.dist2,
                    "angle": request.angle
                }
            }
            
        except Exception as e:
            logger.error(f"Error testing pager service: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/pager/health", summary="Check pager service health")
    async def check_pager_health():
        """Check the health of the pager service"""
        try:
            pager_service = get_pager_service()
            await pager_service.initialize()
            
            health_result = await pager_service.health_check()
            
            return {
                "success": True,
                "health": health_result,
                "service_stats": pager_service.get_stats()
            }
            
        except Exception as e:
            logger.error(f"Error checking pager health: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/pager/stats", summary="Get pager service statistics")
    async def get_pager_stats():
        """Get pager service statistics and configuration"""
        try:
            pager_service = get_pager_service()
            
            return {
                "success": True,
                "stats": pager_service.get_stats()
            }
            
        except Exception as e:
            logger.error(f"Error getting pager stats: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return router