import asyncio
import json
from datetime import datetime
from typing import Dict, Set, Any, Optional, List
from fastapi import WebSocket, WebSocketDisconnect
from core.logger import get_logger
from services.pager_service import get_pager_service, PagerData

logger = get_logger(__name__)


class WebSocketManager:
    """
    WebSocket manager for real-time laser data streaming
    """
    
    def __init__(self):
        # Active WebSocket connections per berth
        self.berth_connections: Dict[int, Set[WebSocket]] = {}
        # Active WebSocket connections per laser
        self.laser_connections: Dict[int, Set[WebSocket]] = {}
        # General connections (all data)
        self.general_connections: Set[WebSocket] = set()
        
        # Pager service integration
        self.pager_service = get_pager_service()
        
        # Laser data buffer for pager integration
        self.laser_data_buffer: Dict[int, Dict[str, Any]] = {}
        
    async def connect_to_berth(self, websocket: WebSocket, berth_id: int):
        """Connect a WebSocket client to receive data from a specific berth"""
        await websocket.accept()
        if berth_id not in self.berth_connections:
            self.berth_connections[berth_id] = set()
        self.berth_connections[berth_id].add(websocket)
        logger.info(f"WebSocket client connected to berth {berth_id}")
        
    async def connect_to_laser(self, websocket: WebSocket, laser_id: int):
        """Connect a WebSocket client to receive data from a specific laser"""
        await websocket.accept()
        if laser_id not in self.laser_connections:
            self.laser_connections[laser_id] = set()
        self.laser_connections[laser_id].add(websocket)
        logger.info(f"WebSocket client connected to laser {laser_id}")
        
    async def connect_general(self, websocket: WebSocket):
        """Connect a WebSocket client to receive all laser data"""
        await websocket.accept()
        self.general_connections.add(websocket)
        logger.info("WebSocket client connected to general stream")
        
    def disconnect_from_berth(self, websocket: WebSocket, berth_id: int):
        """Disconnect a WebSocket client from berth stream"""
        if berth_id in self.berth_connections:
            self.berth_connections[berth_id].discard(websocket)
            if not self.berth_connections[berth_id]:
                del self.berth_connections[berth_id]
        logger.info(f"WebSocket client disconnected from berth {berth_id}")
        
    def disconnect_from_laser(self, websocket: WebSocket, laser_id: int):
        """Disconnect a WebSocket client from laser stream"""
        if laser_id in self.laser_connections:
            self.laser_connections[laser_id].discard(websocket)
            if not self.laser_connections[laser_id]:
                del self.laser_connections[laser_id]
        logger.info(f"WebSocket client disconnected from laser {laser_id}")
        
    def disconnect_general(self, websocket: WebSocket):
        """Disconnect a WebSocket client from general stream"""
        self.general_connections.discard(websocket)
        logger.info("WebSocket client disconnected from general stream")
        
    async def broadcast_laser_data(self, laser_id: int, berth_id: Optional[int], data: Dict[str, Any]):
        """
        Broadcast laser data to connected WebSocket clients and send to pager service
        
        Args:
            laser_id: Laser device ID
            berth_id: Berth ID (if applicable)
            data: Measurement data to broadcast
        """
        message = {
            "type": "laser_data",
            "timestamp": datetime.utcnow().isoformat(),
            "laser_id": laser_id,
            "berth_id": berth_id,
            "data": data
        }
        
        message_str = json.dumps(message)
        
        # Send to laser-specific connections
        if laser_id in self.laser_connections:
            await self._send_to_connections(self.laser_connections[laser_id], message_str)
            
        # Send to berth-specific connections
        if berth_id and berth_id in self.berth_connections:
            await self._send_to_connections(self.berth_connections[berth_id], message_str)
            
        # Send to general connections
        await self._send_to_connections(self.general_connections, message_str)
        
        # Update laser data buffer and potentially send to pager service
        await self._update_pager_data(laser_id, berth_id, data)
        
    async def broadcast_mode_change(self, laser_id: int, berth_id: Optional[int], mode: str, details: Dict[str, Any] = None):
        """
        Broadcast mode change notifications to connected WebSocket clients
        
        Args:
            laser_id: Laser device ID
            berth_id: Berth ID (if applicable) 
            mode: New mode (off, berthing, drift)
            details: Additional details about the mode change
        """
        message = {
            "type": "mode_change",
            "timestamp": datetime.utcnow().isoformat(),
            "laser_id": laser_id,
            "berth_id": berth_id,
            "mode": mode,
            "details": details or {}
        }
        
        message_str = json.dumps(message)
        
        # Send to laser-specific connections
        if laser_id in self.laser_connections:
            await self._send_to_connections(self.laser_connections[laser_id], message_str)
            
        # Send to berth-specific connections
        if berth_id and berth_id in self.berth_connections:
            await self._send_to_connections(self.berth_connections[berth_id], message_str)
            
        # Send to general connections
        await self._send_to_connections(self.general_connections, message_str)
        
    async def _send_to_connections(self, connections: Set[WebSocket], message: str):
        """Send message to a set of WebSocket connections"""
        if not connections:
            return
            
        # Create a copy to avoid modification during iteration
        connections_copy = connections.copy()
        
        for connection in connections_copy:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error sending WebSocket message: {str(e)}")
                # Remove dead connections
                connections.discard(connection)
                
    async def _update_pager_data(self, laser_id: int, berth_id: Optional[int], data: Dict[str, Any]):
        """Update laser data buffer and send to pager service when we have multiple lasers"""
        try:
            # Store latest data for this laser
            self.laser_data_buffer[laser_id] = {
                'laser_id': laser_id,
                'berth_id': berth_id,
                'distance': data.get('distance', 0.0),
                'speed': data.get('speed', 0.0),
                'orientation': self._get_laser_orientation(laser_id),
                'timestamp': datetime.utcnow().isoformat(),
                'data': data
            }
            
            # Check if we have data from at least 2 lasers for the same berth
            berth_lasers = []
            if berth_id:
                for lid, ldata in self.laser_data_buffer.items():
                    if ldata.get('berth_id') == berth_id:
                        berth_lasers.append(ldata)
            
            # If we have 2 or more lasers for this berth, send to pager service
            # DISABLED: Pager service not available
            # if len(berth_lasers) >= 2:
            #     await self._send_to_pager_service(berth_lasers, berth_id)
                
        except Exception as e:
            logger.error(f"Error updating pager data: {str(e)}")
            
    def _get_laser_orientation(self, laser_id: int) -> int:
        """Get laser orientation based on laser ID (customize this logic)"""
        # Default orientation mapping - customize based on your setup
        orientation_map = {
            21: 1,  # North
            22: 2,  # East
            23: 3,  # South
            24: 4   # West
        }
        return orientation_map.get(laser_id, 1)  # Default to North
        
    async def _send_to_pager_service(self, laser_data_list: List[Dict[str, Any]], berth_id: Optional[int]):
        """Send laser data to pager service"""
        try:
            if len(laser_data_list) < 2:
                return
                
            # Get the two most recent laser readings
            laser_data_list.sort(key=lambda x: x['timestamp'], reverse=True)
            laser1 = laser_data_list[0]
            laser2 = laser_data_list[1]
            
            # Calculate angle between lasers (placeholder - implement your logic)
            angle = self._calculate_laser_angle(laser1, laser2)
            
            # Send to pager service
            await self.pager_service.send_laser_measurement(
                laser1_data={
                    'orientation': laser1['orientation'],
                    'speed': laser1['speed'],
                    'distance': laser1['distance'] * 1000  # Convert to mm if needed
                },
                laser2_data={
                    'orientation': laser2['orientation'], 
                    'speed': laser2['speed'],
                    'distance': laser2['distance'] * 1000  # Convert to mm if needed
                },
                user_number="0000",  # Default user number - customize as needed
                angle=angle
            )
            
            logger.info(f"Sent laser data to pager service for berth {berth_id}")
            
        except Exception as e:
            logger.error(f"Error sending data to pager service: {str(e)}")
            
    def _calculate_laser_angle(self, laser1: Dict[str, Any], laser2: Dict[str, Any]) -> float:
        """Calculate angle between two laser measurements (placeholder implementation)"""
        # This is a placeholder - implement your specific angle calculation logic
        # For now, return a simple calculation based on orientations
        orientation_diff = abs(laser1['orientation'] - laser2['orientation'])
        return float(orientation_diff * 45.0)  # 45 degrees per orientation step
        
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get WebSocket connection statistics"""
        return {
            "berth_connections": {berth_id: len(connections) for berth_id, connections in self.berth_connections.items()},
            "laser_connections": {laser_id: len(connections) for laser_id, connections in self.laser_connections.items()},
            "general_connections": len(self.general_connections),
            "total_connections": (
                sum(len(connections) for connections in self.berth_connections.values()) +
                sum(len(connections) for connections in self.laser_connections.values()) +
                len(self.general_connections)
            ),
            "pager_service": self.pager_service.get_stats(),
            "laser_data_buffer": len(self.laser_data_buffer)
        }


# Global WebSocket manager instance
websocket_manager = WebSocketManager()