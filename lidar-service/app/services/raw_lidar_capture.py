"""
Raw LiDAR data capture service for high-speed point cloud streaming
Bypasses OpenPyLivox limitations to capture full 320,000 points/second
"""

import socket
import struct
import time
import threading
import queue
import logging

logger = logging.getLogger('lidar_service')

class RawLidarCapture:
    def __init__(self, data_port: int):
        self.data_port = data_port
        self.is_capturing = False
        self.capture_thread = None
        self.point_queue = queue.Queue(maxsize=1000000)  # Buffer for 1M points
        self.stats = {
            'packets_received': 0,
            'points_parsed': 0,
            'bytes_received': 0,
            'start_time': 0
        }
        
    def start_capture(self):
        """Start high-speed data capture"""
        if self.is_capturing:
            return
            
        self.is_capturing = True
        self.stats['start_time'] = time.time()
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        logger.info(f"Started raw capture on port {self.data_port}")
        
    def stop_capture(self):
        """Stop data capture"""
        self.is_capturing = False
        if self.capture_thread:
            self.capture_thread.join(timeout=2.0)
        logger.info(f"Stopped raw capture. Stats: {self.stats}")
        
    def _capture_loop(self):
        """High-performance capture loop"""
        # Create UDP socket with larger buffer
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Set socket options for performance
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)  # 8MB buffer
        sock.settimeout(0.001)  # 1ms timeout
        
        try:
            sock.bind(('', self.data_port))
            logger.info(f"Listening on UDP port {self.data_port}")
            
            batch_points = []
            last_flush = time.time()
            last_log = time.time()
            
            while self.is_capturing:
                try:
                    # Receive data
                    data, addr = sock.recvfrom(65536)
                    self.stats['packets_received'] += 1
                    self.stats['bytes_received'] += len(data)
                    
                    # Parse Livox Tele-15 packet format
                    points = self._parse_tele15_packet(data)
                    if points:
                        batch_points.extend(points)
                        self.stats['points_parsed'] += len(points)
                    
                    # Flush batch every 100ms or 10000 points
                    if len(batch_points) > 10000 or (time.time() - last_flush) > 0.1:
                        if not self.point_queue.full():
                            self.point_queue.put(batch_points)
                        batch_points = []
                        last_flush = time.time()
                    
                    # Log stats every second
                    if time.time() - last_log > 1.0:
                        logger.info(f"Raw capture: {self.stats['packets_received']} packets, "
                                  f"{self.stats['points_parsed']} points received")
                        last_log = time.time()
                        
                except socket.timeout:
                    # Log timeout periodically
                    if time.time() - last_log > 5.0:
                        logger.debug(f"No packets received on port {self.data_port}")
                        last_log = time.time()
                    continue
                except Exception as e:
                    logger.debug(f"Capture error: {e}")
                    
        finally:
            sock.close()
            
    def _parse_tele15_packet(self, data):
        """Parse Livox Tele-15 packet format"""
        points = []
        
        # Minimum packet size check
        if len(data) < 100:
            return points
            
        # Try multiple offsets to find valid data
        # Tele-15 format: header + 100 points of 13 bytes each
        for offset in [18, 42, 47, 59, 60, 61]:
            if offset + 1300 > len(data):  # 100 points * 13 bytes
                continue
                
            # Quick validation of first point
            try:
                x = struct.unpack('<i', data[offset:offset+4])[0]
                y = struct.unpack('<i', data[offset+4:offset+8])[0]
                z = struct.unpack('<i', data[offset+8:offset+12])[0]
                
                # Check if reasonable values (within ±100m)
                if abs(x) < 100000 and abs(y) < 100000 and abs(z) < 100000:
                    # Parse all 100 points
                    for i in range(100):
                        pt_offset = offset + (i * 13)
                        if pt_offset + 13 > len(data):
                            break
                            
                        x_mm = struct.unpack('<i', data[pt_offset:pt_offset+4])[0]
                        y_mm = struct.unpack('<i', data[pt_offset+4:pt_offset+8])[0]
                        z_mm = struct.unpack('<i', data[pt_offset+8:pt_offset+12])[0]
                        intensity = struct.unpack('B', data[pt_offset+12:pt_offset+13])[0]
                        
                        # Filter valid points
                        if (x_mm != 0 or y_mm != 0 or z_mm != 0) and abs(x_mm) < 100000:
                            points.append({
                                'x': x_mm / 1000.0,
                                'y': y_mm / 1000.0,
                                'z': z_mm / 1000.0,
                                'intensity': intensity
                            })
                    
                    break  # Found valid offset
                    
            except Exception:
                continue
                
        return points
        
    def get_points(self, max_points=None):
        """Get captured points"""
        all_points = []
        
        # Drain queue
        while not self.point_queue.empty() and (max_points is None or len(all_points) < max_points):
            try:
                batch = self.point_queue.get_nowait()
                all_points.extend(batch)
            except queue.Empty:
                break
                
        if max_points and len(all_points) > max_points:
            all_points = all_points[:max_points]
            
        return all_points
        
    def get_stats(self):
        """Get capture statistics"""
        if self.stats['start_time'] > 0:
            elapsed = time.time() - self.stats['start_time']
            self.stats['packets_per_second'] = self.stats['packets_received'] / elapsed if elapsed > 0 else 0
            self.stats['points_per_second'] = self.stats['points_parsed'] / elapsed if elapsed > 0 else 0
            self.stats['mbps'] = (self.stats['bytes_received'] * 8 / 1_000_000) / elapsed if elapsed > 0 else 0
        return self.stats