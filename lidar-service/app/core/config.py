from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    PROJECT_NAME: str = "Livox LiDAR Service"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "FastAPI service for controlling Livox LiDAR sensors"

    HOST: str = "0.0.0.0"
    PORT: int = 8001

    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/lidar_service.log"
    LOG_MAX_SIZE: int = 10485760  # 10MB
    LOG_BACKUP_COUNT: int = 5

    COMPUTER_IP: Optional[str] = None
    AUTO_CONNECT_ON_STARTUP: bool = True
    AUTO_UPDATE_DB: bool = True

    HEARTBEAT_INTERVAL: int = 10
    CONNECTION_TIMEOUT: int = 30

    DB_HOST: str = 'your_ip_address'
    DB_PORT: int = 5432
    
    LASER_SERVICE_ADDR: str = 'localhost:8080'

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()