from enum import Enum
from typing import Optional, Union
from pydantic import BaseModel, Field


class MeasurementMode(str, Enum):
    DM = "DM"  # Single distance measurement
    DT = "DT"  # Continuous distance measurement
    DF = "DF"  # External trigger distance measurement
    VM = "VM"  # Single speed measurement
    VT = "VT"  # Continuous speed measurement


class DataFormat(str, Enum):
    ASCII = "ASCII"
    BINARY = "BINARY"


class LaserStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class LaserOperatingMode(str, Enum):
    OFF = "off"
    BERTHING = "berthing"
    DRIFT = "drift"


class ErrorCode(str, Enum):
    E01 = "E01"  # General error
    E02 = "E02"  # No target (target too far or reflectivity too low)
    E04 = "E04"  # Defective laser (hardware error)
    E99 = "E99"  # Unknown error or no data


class MeasurementResult(BaseModel):
    distance: Optional[float] = Field(None, description="Distance in mm")
    speed: Optional[float] = Field(None, description="Speed in m/s")
    signal_strength: Optional[int] = Field(None, description="Signal strength 0-9999")
    temperature: Optional[float] = Field(None, description="Temperature in °C")
    error_code: Optional[ErrorCode] = None
    raw_data: str = ""
    timestamp: Optional[float] = Field(None, description="Unix timestamp when measurement was taken")


class LaserConfig(BaseModel):
    measurement_frequency: Optional[int] = Field(None, ge=1, le=2000, description="Measurement frequency in Hz")
    averaging: Optional[int] = Field(None, ge=1, le=10000, description="Number of measurements to average")
    moving_average: Optional[int] = Field(None, ge=0, le=100, description="Moving average count")
    offset: Optional[float] = Field(None, description="Distance offset in mm")
    scale_factor: Optional[float] = Field(None, description="Scale factor")
    output_format: Optional[DataFormat] = None
    external_trigger: Optional[bool] = None
    laser_power: Optional[bool] = Field(None, description="Laser on/off")


class CommandRequest(BaseModel):
    command: str = Field(..., description="Command to send to laser")
    parameters: Optional[str] = Field(None, description="Command parameters")


class CommandResponse(BaseModel):
    success: bool
    data: Optional[Union[str, MeasurementResult]] = None
    error: Optional[str] = None
    raw_response: Optional[str] = None


class LaserInfo(BaseModel):
    port: str
    status: LaserStatus
    last_heartbeat: Optional[str] = None
    error_count: int = 0
    measurements_count: int = 0