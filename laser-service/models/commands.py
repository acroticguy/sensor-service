from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class LaserCommands(str, Enum):
    # Measurement commands
    DM = "DM"  # Single distance measurement
    DT = "DT"  # Continuous distance measurement  
    DF = "DF"  # External trigger distance measurement
    VM = "VM"  # Single speed measurement
    VT = "VT"  # Continuous speed measurement
    S = "S"    # Stop continuous measurement
    
    # Configuration commands
    MF = "MF"  # Measurement frequency (1-2000 Hz)
    AV = "AV"  # Averaging (1-10000)
    MA = "MA"  # Moving average (0-100)
    OF = "OF"  # Distance offset
    SF = "SF"  # Scale factor
    OA = "OA"  # ASCII output format
    OB = "OB"  # Binary output format
    AS = "AS"  # Autosave settings
    MW = "MW"  # Median width
    SO = "SO"  # Standby timeout
    DR = "DR"  # Data rate
    PR = "PR"  # Digital resolution
    SD = "SD"  # Serial data format
    SE = "SE"  # Serial echo
    TE = "TE"  # Test mode
    HE = "HE"  # Help command
    
    # Pilot laser modes
    PL0 = "PL0"  # Pilot laser off
    PL1 = "PL1"  # Pilot laser on
    PL2 = "PL2"  # Pilot laser blink 2Hz (default)
    PL3 = "PL3"  # Pilot laser blink 5Hz
    
    # Trigger commands
    ET0 = "ET0"  # Disable external trigger
    ET1 = "ET1"  # Enable external trigger
    TR = "TR"    # Software trigger
    TRF = "TRF"  # Trigger first
    TRL = "TRL"  # Trigger last
    TRM = "TRM"  # Trigger min
    TRX = "TRX"  # Trigger max
    TF = "TF"    # Trigger frequency
    TD = "TD"    # Trigger duration
    
    # Laser control
    L0 = "L0"  # Laser off
    L1 = "L1"  # Laser on
    LP = "LP"  # Laser power
    
    # Status and info
    ST = "ST"  # Status
    TP = "TP"  # Temperature
    VN = "VN"  # Version number
    ID = "ID"  # Device ID
    
    # Query commands
    PA = "PA"  # Query all parameters
    HW = "HW"  # Hardware version
    Q1 = "Q1"  # Query configuration bank 1
    Q2 = "Q2"  # Query configuration bank 2
    QA = "QA"  # Query all configuration
    BR = "BR"  # Query baud rate
    
    # Special modes
    AM0 = "AM0"  # Disable analog output
    AM1 = "AM1"  # Enable analog output distance
    AM2 = "AM2"  # Enable analog output speed
    AO = "AO"   # Analog output configuration
    
    # Error handling
    ES0 = "ES0"  # Disable error output
    ES1 = "ES1"  # Enable error output
    
    # Display
    DP0 = "DP0"  # Display off
    DP1 = "DP1"  # Display distance
    DP2 = "DP2"  # Display speed
    DP3 = "DP3"  # Display signal
    DP4 = "DP4"  # Display temperature
    
    # Other settings
    BD = "BD"   # Baud rate
    UN0 = "UN0" # Units mm
    UN1 = "UN1" # Units m
    UN2 = "UN2" # Units inch
    UN3 = "UN3" # Units feet
    
    # Factory commands
    FR = "FR"   # Factory reset
    SA = "SA"   # Save settings


class TemperatureResponse(BaseModel):
    temperature: float = Field(..., description="Temperature in °C")
    raw_response: Optional[str] = None


class VersionResponse(BaseModel):
    version: str = Field(..., description="Firmware version")
    raw_response: Optional[str] = None


class DeviceIDResponse(BaseModel):
    device_id: str = Field(..., description="Device ID")
    raw_response: Optional[str] = None


class TriggerConfig(BaseModel):
    frequency: Optional[int] = Field(None, ge=1, le=2000, description="Trigger frequency in Hz")
    duration: Optional[int] = Field(None, ge=1, le=10000, description="Trigger duration in ms")
    mode: Optional[str] = Field(None, description="Trigger mode: first, last, min, max")


class AnalogConfig(BaseModel):
    mode: Optional[int] = Field(None, ge=0, le=2, description="0=off, 1=distance, 2=speed")
    min_value: Optional[float] = Field(None, description="Minimum value for analog output")
    max_value: Optional[float] = Field(None, description="Maximum value for analog output")
    min_voltage: Optional[float] = Field(None, description="Minimum voltage (mA)")
    max_voltage: Optional[float] = Field(None, description="Maximum voltage (mA)")


class DisplayMode(str, Enum):
    OFF = "0"
    DISTANCE = "1"
    SPEED = "2"
    SIGNAL = "3"
    TEMPERATURE = "4"


class Units(str, Enum):
    MM = "0"
    M = "1"
    INCH = "2"
    FEET = "3"


class PilotLaserMode(str, Enum):
    OFF = "0"
    ON = "1"
    BLINK_2HZ = "2"
    BLINK_5HZ = "3"


class AutosaveMode(str, Enum):
    OFF = "0"
    ON = "1"


class SerialEchoMode(str, Enum):
    OFF = "0"
    ON = "1"


class ConfigurationBank(str, Enum):
    BANK1 = "1"
    BANK2 = "2"
    ALL = "A"


class ParameterQueryResponse(BaseModel):
    parameters: dict = Field(..., description="All device parameters")
    raw_response: Optional[str] = None
    formatted_response: Optional[str] = Field(None, description="Formatted multi-line response")


class HardwareVersionResponse(BaseModel):
    hardware_version: str = Field(..., description="Hardware version")
    raw_response: Optional[str] = None


class ConfigurationResponse(BaseModel):
    configuration: dict = Field(..., description="Configuration settings")
    raw_response: Optional[str] = None


class BaudRateResponse(BaseModel):
    baud_rate: int = Field(..., description="Current baud rate")
    raw_response: Optional[str] = None