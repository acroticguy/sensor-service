from typing import List, Optional, Dict
import os


class Settings:
    def __init__(self):
        # TCP bridge connection settings
        # Use host.docker.internal to connect from container to host
        self.bridge_host = os.getenv("LASER_BRIDGE_HOST", "host.docker.internal")
        
        # Support multiple laser ports
        # Format: LASER_PORTS=2030,2031,2032 or just LASER_PORTS=2030 for single port
        ports_env = os.getenv("LASER_PORTS", "2030")
        self.laser_ports = [int(p.strip()) for p in ports_env.split(",")]
        
        # For backward compatibility
        self.bridge_port = self.laser_ports[0] if self.laser_ports else 2030
        
        # API settings
        self.api_host = "0.0.0.0"
        self.api_port = 8080  # Changed from 8000 to 8080
        
        # Connection settings
        self.laser_timeout = float(os.getenv("LASER_API_LASER_TIMEOUT", "1.0"))
        self.heartbeat_interval = float(os.getenv("LASER_API_HEARTBEAT_INTERVAL", "5.0"))
        self.connection_retry_interval = float(os.getenv("LASER_API_CONNECTION_RETRY_INTERVAL", "10.0"))
        self.max_connection_retries = int(os.getenv("LASER_API_MAX_CONNECTION_RETRIES", "3"))
        self.log_level = os.getenv("LASER_API_LOG_LEVEL", "INFO")


settings = Settings()