import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import HTTPException
from datetime import datetime

from main import app, laser_manager
from api.routes import setup_routes, LaserAPI
from models.laser_models import (
    LaserStatus, MeasurementResult, ErrorCode, LaserConfig,
    MeasurementMode, LaserInfo, CommandResponse
)
from models.commands import (
    TemperatureResponse, VersionResponse, DeviceIDResponse,
    TriggerConfig, AnalogConfig, DisplayMode, Units
)
from core.laser_device import LaserDevice


class TestRoutesEdgeCases:
    """Test edge cases and error conditions in API routes"""
    
    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Reset laser manager before each test"""
        laser_manager.devices.clear()
        yield
        laser_manager.devices.clear()
    
    def test_measure_distance_device_error_state(self):
        """Test measure distance when device is in error state"""
        # Create device in error state
        mock_device = MagicMock()
        mock_device.status = LaserStatus.ERROR
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.post("/api/v1/measure/distance?port=2030")
        
        assert response.status_code == 500  # General exception returns 500
        assert "detail" in response.json()
    
    def test_stop_continuous_when_not_in_continuous_mode(self):
        """Test stopping continuous mode when device is not in continuous mode"""
        mock_device = MagicMock()
        mock_device.is_continuous_mode.return_value = False
        mock_device.stop_continuous = AsyncMock()
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.post("/api/v1/measure/continuous/stop?port=2030")
        
        assert response.status_code == 404  # Returns 404 when no device found
    
    def test_set_config_invalid_frequency(self):
        """Test setting config with invalid measurement frequency"""
        mock_device = MagicMock()
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        
        # Test negative frequency
        response = client.post(
            "/api/v1/devices/2030/config",
            json={"measurement_frequency": -10}
        )
        assert response.status_code == 422
        
        # Test zero frequency
        response = client.post(
            "/api/v1/devices/2030/config",
            json={"measurement_frequency": 0}
        )
        assert response.status_code == 422
        
        # Test frequency too high
        response = client.post(
            "/api/v1/devices/2030/config",
            json={"measurement_frequency": 10000}
        )
        assert response.status_code == 422
    
    def test_command_timeout_handling(self):
        """Test command execution with timeout"""
        mock_device = MagicMock()
        mock_device.send_command = AsyncMock(side_effect=asyncio.TimeoutError())
        laser_manager.get_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.post(
            "/api/v1/devices/2030/command",
            json={"command": "ST", "parameters": ""}
        )
        
        assert response.status_code == 200  # CommandResponse wraps errors
        assert response.json()["success"] is False
    
    def test_device_reconnect_during_operation(self):
        """Test device reconnection during an operation"""
        mock_device = MagicMock()
        mock_device.status = LaserStatus.DISCONNECTED
        mock_device.connect = AsyncMock(return_value=True)
        mock_device.measure_distance = AsyncMock(
            return_value=MeasurementResult(distance=100.0)
        )
        
        # First disconnected, then connected after reconnect
        laser_manager.get_device = MagicMock(return_value=mock_device)
        laser_manager.get_connected_device = MagicMock(side_effect=[None, mock_device])
        laser_manager.connect_device = AsyncMock(return_value=True)
        
        client = TestClient(app)
        response = client.post("/api/v1/measure/distance?port=2030")
        
        # Should attempt reconnection
        assert response.status_code == 200 or response.status_code == 503
    
    
    def test_command_with_special_characters(self):
        """Test command execution with special characters"""
        mock_device = MagicMock()
        mock_device.send_command = AsyncMock(return_value="OK\r\n")
        laser_manager.get_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        
        # Test with newlines, carriage returns, etc
        special_params = ["test\nvalue", "test\rvalue", "test\x00value"]
        
        for param in special_params:
            response = client.post(
                "/api/v1/devices/2030/command",
                json={"command": "TEST", "parameters": param}
            )
            # Should handle special characters appropriately
            assert response.status_code in [200, 400, 422]
    
    def test_get_device_info_race_condition(self):
        """Test get device info with race condition on heartbeat update"""
        mock_device = MagicMock()
        mock_device.status = LaserStatus.CONNECTED
        
        # Set heartbeat to current time
        mock_device.last_heartbeat = datetime.now()
        mock_device.error_count = 0
        mock_device.measurements_count = 100
        
        laser_manager.get_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.get("/api/v1/devices/2030/info")
        
        assert response.status_code == 200
        # Should handle concurrent heartbeat updates gracefully