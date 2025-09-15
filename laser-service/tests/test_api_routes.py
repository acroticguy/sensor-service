import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import time

from models.laser_models import LaserInfo, LaserStatus, MeasurementResult, ErrorCode


@pytest.fixture
def mock_laser_device():
    """Create a mock laser device"""
    device = MagicMock()
    device.status = LaserStatus.CONNECTED
    device.last_heartbeat = datetime.now()
    device.error_count = 0
    device.measurements_count = 100
    device.measure_distance = AsyncMock()
    device.measure_speed = AsyncMock()
    device.send_command = AsyncMock()
    device.set_config = AsyncMock()
    device.stop_continuous = AsyncMock()
    device.get_status = AsyncMock()
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


def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_get_devices(client):
    from main import laser_manager
    laser_manager.get_all_devices.return_value = [
        LaserInfo(
            port="2030",
            status=LaserStatus.CONNECTED,
            last_heartbeat="2024-01-01T12:00:00",
            error_count=0,
            measurements_count=100
        )
    ]
    
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["port"] == "2030"
    assert data[0]["status"] == "connected"


def test_get_device_info(client, mock_laser_device):
    response = client.get("/api/v1/devices/2030/info")
    assert response.status_code == 200
    data = response.json()
    assert data["port"] == "2030"
    assert data["status"] == "connected"
    assert data["error_count"] == 0
    assert data["measurements_count"] == 100


def test_get_device_info_not_found(client):
    from main import laser_manager
    laser_manager.get_device.return_value = None
    
    response = client.get("/api/v1/devices/9999/info")
    assert response.status_code == 404


def test_send_command(client, mock_laser_device):
    mock_laser_device.send_command.return_value = "OK"
    
    response = client.post("/api/v1/devices/2030/command", json={
        "command": "ST",
        "parameters": ""
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"] == "OK"


def test_measure_distance(client, mock_laser_device):
    measurement = MeasurementResult(
        distance=1234.5,
        signal_strength=8765,
        temperature=25.3,
        timestamp=time.time()
    )
    mock_laser_device.measure_distance.return_value = measurement
    
    response = client.post("/api/v1/measure/distance")
    assert response.status_code == 200
    data = response.json()
    assert data["distance"] == 1234.5
    assert data["signal_strength"] == 8765
    assert data["temperature"] == 25.3


def test_measure_speed(client, mock_laser_device):
    measurement = MeasurementResult(
        speed=12.5,
        distance=1234.5,
        signal_strength=8765,
        temperature=25.3,
        timestamp=time.time()
    )
    mock_laser_device.measure_speed.return_value = measurement
    
    response = client.post("/api/v1/measure/speed")
    assert response.status_code == 200
    data = response.json()
    assert data["speed"] == 12.5
    assert data["distance"] == 1234.5


def test_stop_measurement(client, mock_laser_device):
    response = client.post("/api/v1/measure/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "stopped" in data["message"]


def test_set_config(client, mock_laser_device):
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


def test_get_status(client, mock_laser_device):
    mock_laser_device.get_status.return_value = {
        "laser": "on",
        "mode": "single",
        "frequency": 100
    }
    
    response = client.get("/api/v1/devices/2030/status")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"]["laser"] == "on"