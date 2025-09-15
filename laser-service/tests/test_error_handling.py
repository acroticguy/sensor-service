import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from main import app, laser_manager
from models.laser_models import ErrorCode, MeasurementResult
from core.laser_device import LaserDevice


class TestErrorHandling:
    """Comprehensive error handling tests"""
    
    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Reset laser manager before each test"""
        laser_manager.devices.clear()
        yield
        laser_manager.devices.clear()
    
    def test_malformed_request_data(self):
        """Test handling of malformed request data"""
        client = TestClient(app)
        
        # Test invalid JSON
        response = client.post(
            "/api/v1/devices/2030/command",
            data="not json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        
        # Test missing required fields
        response = client.post(
            "/api/v1/devices/2030/command",
            json={"parameters": "test"}  # Missing 'command' field
        )
        assert response.status_code == 422
    
    def test_invalid_measurement_mode(self):
        """Test handling of invalid measurement modes"""
        mock_device = MagicMock()
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        # Try to get device with invalid port
        laser_manager.get_device = MagicMock(return_value=None)  # Device not found
        
        client = TestClient(app)
        response = client.post("/api/v1/measure/distance/continuous/start?port=INVALID")
        
        # Should return 404 for device not found
        assert response.status_code == 404
    
    def test_device_buffer_overflow(self):
        """Test handling of device buffer overflow"""
        mock_device = MagicMock()
        
        # Simulate buffer overflow response
        mock_device.send_command = AsyncMock(return_value="E99: Buffer overflow\r\n")
        laser_manager.get_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.post(
            "/api/v1/devices/2030/command",
            json={"command": "TEST", "parameters": "x" * 1000}  # Very long parameter
        )
        
        assert response.status_code == 200
        assert "E99" in response.json()["data"]
    
    def test_unicode_handling(self):
        """Test handling of unicode characters"""
        mock_device = MagicMock()
        
        # Return response with unicode
        mock_device.send_command = AsyncMock(return_value="Temperature: 25.3°C ±0.1\r\n")
        laser_manager.get_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.post(
            "/api/v1/devices/2030/command",
            json={"command": "GT", "parameters": ""}
        )
        
        assert response.status_code == 200
        assert "°C" in response.json()["data"]
        assert "±" in response.json()["data"]
    
    def test_empty_response_handling(self):
        """Test handling of empty responses from device"""
        mock_device = MagicMock()
        mock_device.send_command = AsyncMock(return_value="")
        laser_manager.get_device = MagicMock(return_value=mock_device)
        
        client = TestClient(app)
        response = client.post(
            "/api/v1/devices/2030/command",
            json={"command": "TEST", "parameters": ""}
        )
        
        assert response.status_code == 200
        assert response.json()["data"] == ""
    
