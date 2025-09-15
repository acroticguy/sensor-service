import pytest
from models.laser_models import (
    MeasurementResult, LaserConfig, ErrorCode, 
    MeasurementMode, DataFormat, LaserStatus
)


class TestModels:
    """Test data models"""
    
    def test_measurement_result_creation(self):
        """Test creating a measurement result"""
        result = MeasurementResult(
            distance=1234.5,
            speed=2.5,
            signal_strength=3456,
            temperature=25.3,
            error_code=None,
            raw_data="test"
        )
        
        assert result.distance == 1234.5
        assert result.speed == 2.5
        assert result.signal_strength == 3456
        assert result.temperature == 25.3
        assert result.error_code is None
        assert result.raw_data == "test"
    
    def test_measurement_result_with_error(self):
        """Test measurement result with error"""
        result = MeasurementResult(
            error_code=ErrorCode.E02,
            raw_data="E02"
        )
        
        assert result.distance is None
        assert result.error_code == ErrorCode.E02
        assert result.error_code.value == "E02"
    
    def test_laser_config(self):
        """Test laser configuration"""
        config = LaserConfig(
            measurement_frequency=100,
            averaging=10,
            moving_average=5,
            offset=1.5,
            scale_factor=0.999,
            output_format=DataFormat.ASCII,
            external_trigger=False,
            laser_power=True
        )
        
        assert config.measurement_frequency == 100
        assert config.averaging == 10
        assert config.moving_average == 5
        assert config.offset == 1.5
        assert config.scale_factor == 0.999
        assert config.output_format == DataFormat.ASCII
        assert config.external_trigger is False
        assert config.laser_power is True
    
    def test_enums(self):
        """Test enum values"""
        # MeasurementMode
        assert MeasurementMode.DM.value == "DM"
        assert MeasurementMode.DT.value == "DT"
        assert MeasurementMode.VM.value == "VM"
        assert MeasurementMode.VT.value == "VT"
        
        # DataFormat
        assert DataFormat.ASCII.value == "ASCII"
        assert DataFormat.BINARY.value == "BINARY"
        
        # LaserStatus
        assert LaserStatus.CONNECTED.value == "connected"
        assert LaserStatus.DISCONNECTED.value == "disconnected"
        assert LaserStatus.ERROR.value == "error"
        
        # ErrorCode
        assert ErrorCode.E01.value == "E01"
        assert ErrorCode.E02.value == "E02"
        assert ErrorCode.E04.value == "E04"
        assert ErrorCode.E99.value == "E99"