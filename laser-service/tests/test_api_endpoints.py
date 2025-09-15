import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
import json
import time

from models.laser_models import MeasurementResult, ErrorCode, LaserStatus
from datetime import datetime


@pytest.fixture
def sample_measurement():
    """Create a sample measurement result"""
    return MeasurementResult(
        distance=1234.5,
        speed=None,
        signal_strength=3456,
        temperature=25.3,
        error_code=None,
        raw_data="D 0001.234 03456 +25.3",
        timestamp=time.time()
    )


@pytest.fixture
def sample_speed_measurement():
    """Create a sample speed measurement result"""
    return MeasurementResult(
        distance=1234.5,
        speed=2.5,
        signal_strength=3456,
        temperature=25.3,
        error_code=None,
        raw_data="D 0002.500 0001.234 03456 +25.3",
        timestamp=time.time()
    )


@pytest.fixture
def sample_error_measurement():
    """Create a sample error measurement result"""
    return MeasurementResult(
        distance=None,
        speed=None,
        signal_strength=None,
        temperature=None,
        error_code=ErrorCode.E02,
        raw_data="E02",
        timestamp=time.time()
    )


@pytest.fixture
def mock_laser_device():
    """Create a mock laser device"""
    device = MagicMock()
    device.status = LaserStatus.CONNECTED
    device.last_heartbeat = datetime.now()
    device.error_count = 0
    device.measurements_count = 0
    device.measure_distance = AsyncMock()
    device.measure_speed = AsyncMock()
    device.send_command = AsyncMock()
    device.set_config = AsyncMock()
    device._start_continuous_mode = AsyncMock()
    device.stop_continuous = AsyncMock()
    device.get_continuous_data = AsyncMock()
    device.is_continuous_mode = MagicMock()
    device.detect_and_resume_continuous_mode = AsyncMock()
    return device


@pytest.fixture
def client(mock_laser_device):
    """Create test client with mocked laser manager"""
    from main import app, laser_manager
    
    # Mock the laser manager methods
    laser_manager.get_all_devices = MagicMock()
    laser_manager.get_device = MagicMock()
    laser_manager.get_connected_device = MagicMock()
    
    # Set default return values
    laser_manager.get_connected_device.return_value = mock_laser_device
    laser_manager.get_device.return_value = mock_laser_device
    
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_check(self, client):
        """Test health check endpoint"""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestDeviceEndpoints:
    def test_get_devices(self, client):
        """Test getting all devices"""
        from main import laser_manager
        laser_manager.get_all_devices.return_value = [
            {"port": "2030", "status": "connected", "error_count": 0}
        ]
        
        response = client.get("/api/v1/devices")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["port"] == "2030"
    
    def test_get_device_info(self, client, mock_laser_device):
        """Test getting specific device info"""
        from main import laser_manager
        laser_manager.get_device.return_value = mock_laser_device
        
        response = client.get("/api/v1/devices/2030/info")
        assert response.status_code == 200
        data = response.json()
        assert data["port"] == "2030"
        assert data["status"] == "connected"
    
    def test_get_device_info_not_found(self, client):
        """Test getting non-existent device info"""
        from main import laser_manager
        laser_manager.get_device.return_value = None
        
        response = client.get("/api/v1/devices/9999/info")
        assert response.status_code == 404


class TestMeasurementEndpoints:
    @pytest.mark.asyncio
    async def test_measure_distance(self, client, mock_laser_device, sample_measurement):
        """Test distance measurement endpoint"""
        mock_laser_device.measure_distance.return_value = sample_measurement
        
        response = client.post("/api/v1/measure/distance")
        assert response.status_code == 200
        data = response.json()
        assert data["distance"] == 1234.5
        assert data["signal_strength"] == 3456
        assert data["temperature"] == 25.3
    
    @pytest.mark.asyncio
    async def test_measure_speed(self, client, mock_laser_device, sample_speed_measurement):
        """Test speed measurement endpoint"""
        mock_laser_device.measure_speed.return_value = sample_speed_measurement
        
        response = client.post("/api/v1/measure/speed")
        assert response.status_code == 200
        data = response.json()
        assert data["speed"] == 2.5
        assert data["distance"] == 1234.5
    
    @pytest.mark.asyncio
    async def test_measure_distance_error(self, client, mock_laser_device, sample_error_measurement):
        """Test distance measurement with error"""
        mock_laser_device.measure_distance.return_value = sample_error_measurement
        
        response = client.post("/api/v1/measure/distance")
        assert response.status_code == 200
        data = response.json()
        assert data["distance"] is None
        assert data["error_code"] == "E02"


class TestContinuousEndpoints:
    @pytest.mark.asyncio
    async def test_start_continuous_distance(self, client, mock_laser_device):
        """Test starting continuous distance measurement"""
        response = client.post("/api/v1/measure/distance/continuous/start")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "continuous distance measurement" in data["message"]
    
    @pytest.mark.asyncio
    async def test_start_continuous_speed(self, client, mock_laser_device):
        """Test starting continuous speed measurement"""
        response = client.post("/api/v1/measure/speed/continuous/start")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "continuous speed measurement" in data["message"]
    
    @pytest.mark.asyncio
    async def test_get_continuous_measurement(self, client, mock_laser_device, sample_measurement):
        """Test getting continuous measurement"""
        mock_laser_device.is_continuous_mode.return_value = True
        mock_laser_device.get_continuous_data.return_value = sample_measurement
        
        response = client.get("/api/v1/measure/continuous")
        assert response.status_code == 200
        data = response.json()
        assert data["distance"] == 1234.5
    
    @pytest.mark.asyncio
    async def test_get_continuous_no_data(self, client, mock_laser_device):
        """Test getting continuous measurement with no data"""
        mock_laser_device.is_continuous_mode.return_value = True
        mock_laser_device.get_continuous_data.return_value = None
        
        response = client.get("/api/v1/measure/continuous")
        assert response.status_code == 204  # No Content
    
    @pytest.mark.asyncio
    async def test_get_continuous_auto_detect(self, client, mock_laser_device, sample_measurement):
        """Test auto-detecting continuous mode"""
        mock_laser_device.is_continuous_mode.side_effect = [False, True]
        mock_laser_device.detect_and_resume_continuous_mode.return_value = True
        mock_laser_device.get_continuous_data.return_value = sample_measurement
        
        response = client.get("/api/v1/measure/continuous")
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_stop_measurement(self, client, mock_laser_device):
        """Test stopping continuous measurement"""
        response = client.post("/api/v1/measure/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestConfigurationEndpoints:
    @pytest.mark.asyncio
    async def test_set_config(self, client, mock_laser_device):
        """Test setting device configuration"""
        mock_laser_device.set_config.return_value = True
        
        config = {
            "measurement_frequency": 100,
            "averaging": 10,
            "laser_power": True
        }
        
        response = client.post("/api/v1/devices/2030/config", json=config)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
    
    @pytest.mark.asyncio
    async def test_set_laser_state(self, client, mock_laser_device):
        """Test turning laser on/off"""
        response = client.post("/api/v1/devices/2030/laser/on")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Laser turned on" in data["message"]
    
    @pytest.mark.asyncio
    async def test_get_temperature(self, client, mock_laser_device):
        """Test getting device temperature"""
        mock_laser_device.send_command.return_value = "25.3°C"
        
        response = client.get("/api/v1/devices/2030/temperature")
        assert response.status_code == 200
        data = response.json()
        assert data["temperature"] == 25.3


class TestUtilityEndpoints:
    @pytest.mark.asyncio
    async def test_test_command(self, client, mock_laser_device):
        """Test sending raw command"""
        mock_laser_device.send_command.return_value = "OK\r\n"
        mock_laser_device.timeout = 1.0
        mock_laser_device._parse_measurement = lambda x, command: MeasurementResult(raw_data=x)
        
        response = client.post("/api/v1/test-command", json={"command": "ST", "timeout": 1.0})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["command"] == "ST"
        assert "OK" in data["response"]
    
    @pytest.mark.asyncio
    async def test_test_connection(self, client, mock_laser_device, sample_measurement):
        """Test connection test endpoint"""
        mock_laser_device.measure_distance.return_value = sample_measurement
        
        response = client.get("/api/v1/test-connection")
        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is True
        assert data["test_measurement"]["distance"] == 1234.5


class TestErrorHandling:
    def test_no_device_connected(self, client):
        """Test endpoints when no device is connected"""
        from main import laser_manager
        laser_manager.get_connected_device.return_value = None
        
        response = client.post("/api/v1/measure/distance")
        assert response.status_code == 404
        assert "No connected device found" in response.json()["detail"]
    
    @pytest.mark.asyncio
    async def test_command_timeout(self, client, mock_laser_device):
        """Test command timeout handling"""
        mock_laser_device.send_command.side_effect = TimeoutError("Command timeout")
        
        response = client.post("/api/v1/devices/2030/command", json={"command": "DM"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Command timeout" in data["error"]