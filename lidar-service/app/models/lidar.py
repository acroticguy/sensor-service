from pydantic import BaseModel, Field, IPvAnyAddress
from typing import Optional, Dict, List, Any
from enum import Enum


class OperationType(str, Enum):
    BERTH = "berth"
    DRIFT = "drift"
    UNMOOR = "unmoor"


class LidarStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


class LidarMode(str, Enum):
    NORMAL = "normal"
    POWER_SAVING = "power_saving"
    STANDBY = "standby"


class CoordinateSystem(str, Enum):
    CARTESIAN = "cartesian"
    SPHERICAL = "spherical"


class LidarConnectionRequest(BaseModel):
    computer_ip: Optional[str] = Field(None, description="Computer IP address (optional for reconnection)")
    sensor_ip: Optional[str] = Field(None, description="Sensor IP address (optional for reconnection)")
    data_port: Optional[int] = Field(None, description="Data port number (optional for reconnection)")
    cmd_port: Optional[int] = Field(None, description="Command port number (optional for reconnection)")
    imu_port: Optional[int] = Field(None, description="IMU port number")
    sensor_name: Optional[str] = Field(None, description="Custom sensor name")


class LidarAutoConnectRequest(BaseModel):
    computer_ip: Optional[str] = Field(None, description="Computer IP address (optional)")


class ExtrinsicParameters(BaseModel):
    x: float = Field(..., description="X coordinate in meters")
    y: float = Field(..., description="Y coordinate in meters")
    z: float = Field(..., description="Z coordinate in meters")
    roll: float = Field(..., description="Roll angle in degrees")
    pitch: float = Field(..., description="Pitch angle in degrees")
    yaw: float = Field(..., description="Yaw angle in degrees")


class LidarInfo(BaseModel):
    sensor_id: str
    sensor_ip: str
    serial_number: Optional[str] = None
    firmware_version: Optional[str] = None
    status: LidarStatus
    mode: Optional[LidarMode] = None
    coordinate_system: Optional[CoordinateSystem] = None
    extrinsic_parameters: Optional[ExtrinsicParameters] = None
    connection_parameters: Optional[Dict[str, Any]] = None
    status_codes: Optional[Dict[str, Any]] = None


class DataStreamConfig(BaseModel):
    duration: Optional[float] = Field(None, description="Duration in seconds (0 or None for infinite)")
    buffer_size: Optional[int] = Field(8192, description="Buffer size for streaming")


class LidarDiscoveryResponse(BaseModel):
    sensors: List[Dict[str, str]]
    count: int


class OperationResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class BerthingModeRequest(BaseModel):
    sensor_ids: List[str] = Field(..., description="List of sensor IDs to include in berthing mode")
    computer_ip: Optional[str] = Field(None, description="Computer IP address for discovery and connect")


class BerthingModeResponse(BaseModel):
    berthing_mode_active: bool
    sensor_ids: List[str]
    connected_sensors: List[str]
    streaming_sensors: List[str]
    message: str
    center_stats: Optional[Dict[str, Any]] = None
    synchronized: bool = False
    last_sync_timestamp: Optional[float] = None
    sync_quality: Optional[str] = None


class StartOperationRequest(BaseModel):
    berthing_id: int = Field(..., description="Berthing operation ID")
    use_lidar: bool = Field(True, description="Whether to use LiDAR sensors for this operation")
    use_laser: bool = Field(False, description="Whether to use laser sensors for this operation")


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    status_code: int = 500