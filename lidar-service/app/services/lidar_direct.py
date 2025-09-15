"""
Direct LiDAR control service that bypasses OpenPyLivox's problematic components
Handles all UDP communication directly for reliability
"""
import socket
import struct
import threading
import time
import logging
from typing import Optional, Tuple, Dict
from ..core.constants import (
    _CMD_DATA_START, _CMD_DATA_STOP, _CMD_LIDAR_SPIN_UP,
    _CMD_LIDAR_SPIN_DOWN, _CMD_CARTESIAN_CS
)

logger = logging.getLogger('lidar_service')


class DirectLidarController:
    """Direct control of Livox LiDAR without OpenPyLivox's monitoring issues"""
    
    
    def __init__(self, sensor_ip: str, data_port: int, cmd_port: int):
        self.sensor_ip = sensor_ip
        self.data_port = data_port
        self.cmd_port = cmd_port
        self.cmd_socket = None
        self.data_socket = None
        self.is_connected = False
        self.is_streaming = False
        
    def connect(self):
        """Initialize command socket for sensor control"""
        try:
            # Create command socket
            self.cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.cmd_socket.settimeout(2.0)
            self.is_connected = True
            logger.info(f"Connected to sensor at {self.sensor_ip}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    def disconnect(self):
        """Clean disconnect"""
        try:
            if self.is_streaming:
                self.stop_data_stream()
            
            if self.cmd_socket:
                self.cmd_socket.close()
                self.cmd_socket = None
                
            self.is_connected = False
            logger.info("Disconnected from sensor")
            return True
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")
            return False
    
    def spin_up(self):
        """Start the LiDAR motor"""
        if not self.is_connected:
            return False
            
        try:
            self.cmd_socket.sendto(self._CMD_LIDAR_SPIN_UP, (self.sensor_ip, 65000))
            logger.info("Sent spin up command")
            time.sleep(2.0)  # Wait for motor to spin up
            return True
        except Exception as e:
            logger.error(f"Failed to spin up: {e}")
            return False
    
    def spin_down(self):
        """Stop the LiDAR motor"""
        if not self.is_connected:
            return False
            
        try:
            self.cmd_socket.sendto(self._CMD_LIDAR_SPIN_DOWN, (self.sensor_ip, 65000))
            logger.info("Sent spin down command")
            return True
        except Exception as e:
            logger.error(f"Failed to spin down: {e}")
            return False
    
    def set_cartesian(self):
        """Set coordinate system to Cartesian"""
        if not self.is_connected:
            return False
            
        try:
            self.cmd_socket.sendto(self._CMD_CARTESIAN_CS, (self.sensor_ip, 65000))
            logger.info("Set Cartesian coordinate system")
            return True
        except Exception as e:
            logger.error(f"Failed to set coordinate system: {e}")
            return False
    
    def start_data_stream(self):
        """Send command to start data streaming"""
        if not self.is_connected:
            return False
            
        try:
            self.cmd_socket.sendto(self._CMD_DATA_START, (self.sensor_ip, 65000))
            self.is_streaming = True
            logger.info("Started data stream")
            return True
        except Exception as e:
            logger.error(f"Failed to start data stream: {e}")
            return False
    
    def stop_data_stream(self):
        """Send command to stop data streaming"""
        if not self.is_connected:
            return False
            
        try:
            self.cmd_socket.sendto(self._CMD_DATA_STOP, (self.sensor_ip, 65000))
            self.is_streaming = False
            logger.info("Stopped data stream")
            return True
        except Exception as e:
            logger.error(f"Failed to stop data stream: {e}")
            return False
    
    def get_connection_info(self) -> Dict[str, any]:
        """Get connection information"""
        return {
            'sensor_ip': self.sensor_ip,
            'data_port': self.data_port,
            'cmd_port': self.cmd_port,
            'is_connected': self.is_connected,
            'is_streaming': self.is_streaming
        }