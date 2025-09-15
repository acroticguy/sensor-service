import threading
import time
import random
from typing import Optional, Dict, Any
from ..core.logging_config import logger
from ..models.laser_models import LaserStatus

class FakeLaserController:
    """Simulates a Modbus laser distance sensor for testing purposes"""
    
    def __init__(self, sensor_id: str, ip_address: str = "127.0.0.1", port: int = 502, unit_id: int = 1):
        self.sensor_id = sensor_id
        self.ip_address = ip_address
        self.port = port
        self.unit_id = unit_id
        self.connected = False
        self._lock = threading.Lock()
        self._fake_distance = 10.0  # Start at 10m
        self._fake_signal = 50000   # High signal strength
        self._running = False
        self._update_thread = None
        self._next_id = 1
        logger.info(f"Created FakeLaserController for {sensor_id}")
    
    def connect(self) -> bool:
        """Simulate connection - always succeeds for fake"""
        try:
            self.connected = True
            self._running = True
            # Start background thread to update fake data
            self._update_thread = threading.Thread(target=self._update_fake_data, daemon=True)
            self._update_thread.start()
            logger.info(f"Fake laser {self.sensor_id} connected successfully")
            return True
        except Exception as e:
            logger.error(f"Error connecting fake laser {self.sensor_id}: {e}")
            return False
    
    def disconnect(self):
        """Simulate disconnect"""
        self._running = False
        if self._update_thread:
            self._update_thread.join(timeout=1.0)
        self.connected = False
        logger.info(f"Fake laser {self.sensor_id} disconnected")
    
    def read_distance(self) -> Optional[float]:
        """Read fake distance measurement"""
        if not self.connected:
            return None
        with self._lock:
            # Return distance in meters, rounded to 3 decimals
            distance = round(self._fake_distance, 3)
            # Validate reasonable range
            if 0.1 <= distance <= 100.0:
                logger.debug(f"Fake laser {self.sensor_id}: distance = {distance}m")
                return distance
            else:
                logger.debug(f"Unreasonable fake distance {distance}m for {self.sensor_id}")
                return None
    
    def read_signal_strength(self) -> Optional[int]:
        """Read fake signal strength"""
        if not self.connected:
            return None
        with self._lock:
            signal = self._fake_signal
            if 0 <= signal <= 65535:
                return signal
            else:
                return None
    
    def read_status(self) -> Optional[Dict[str, int]]:
        """Read fake status registers"""
        if not self.connected:
            return None
        return {
            "status_flag_1": 0,  # Normal operation
            "status_flag_2": 0,  # No errors
            "status_flag_3": 1   # Active
        }
    
    def _update_fake_data(self):
        """Background thread to update fake measurements over time"""
        logger.info(f"Fake laser {self.sensor_id}: starting data update thread")
        start_time = time.time()
        while self._running:
            try:
                with self._lock:
                    # Simulate vessel approaching at ~0.1 m/s (slow berthing)
                    elapsed = time.time() - start_time
                    self._fake_distance = max(0.5, 10.0 - 0.1 * elapsed)
                    
                    # Vary signal slightly
                    self._fake_signal = 50000 + random.randint(-5000, 5000)
                    self._fake_signal = max(0, min(65535, self._fake_signal))
                
                # Update every 0.1s (10Hz)
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error updating fake data for {self.sensor_id}: {e}")
                time.sleep(0.5)
        
        logger.info(f"Fake laser {self.sensor_id}: data update thread stopped")