import pytest
import asyncio
import socket
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from core.laser_device import LaserDevice
from models.laser_models import MeasurementMode, LaserStatus
from datetime import datetime


@pytest.mark.asyncio
async def test_continuous_state_persists_after_disconnect():
    """Test that continuous mode state persists after disconnection"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket
    mock_socket = MagicMock(spec=socket.socket)
    
    # Create a generator that never runs out
    def recv_generator():
        yield b''  # Initial connection check
        yield socket.timeout  # send_command clearing buffer
        yield b'DT\r\nOK\r\n'  # DT response
        yield b''  # End of data
        # Keep yielding timeout forever
        while True:
            yield socket.timeout
    
    mock_socket.recv.side_effect = recv_generator()
    
    with patch('socket.socket', return_value=mock_socket):
        # Connect and start continuous mode
        await device.connect()
        assert device.is_continuous_mode() is False
        
        # Start continuous measurement
        await device._start_continuous_mode(MeasurementMode.DT)
        
        assert device.is_continuous_mode() is True
        assert device._device_in_continuous is True
        
        # Disconnect
        await device.disconnect()
        
        # Device should remember it was in continuous mode
        assert device._device_in_continuous is True
        assert device._continuous_mode is False  # Not actively reading


@pytest.mark.asyncio
async def test_continuous_state_detected_on_reconnect():
    """Test that continuous mode is detected when reconnecting"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket that returns data (indicating continuous mode)
    mock_socket = MagicMock(spec=socket.socket)
    mock_socket.recv.return_value = b"D 0001.234 03456 +25.3\r\n"
    
    with patch('socket.socket', return_value=mock_socket):
        # Connect - should detect continuous mode
        await device.connect()
        
        # Should have detected continuous mode
        assert device.is_continuous_mode() is True
        assert device._device_in_continuous is True
        assert device._continuous_mode is True
        assert device._continuous_task is not None
        
        # Clean up
        await device.disconnect()


@pytest.mark.asyncio
async def test_continuous_state_not_detected_when_no_data():
    """Test that continuous mode is not detected when no data is received"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket that returns no data
    mock_socket = MagicMock(spec=socket.socket)
    mock_socket.recv.side_effect = socket.timeout
    
    with patch('socket.socket', return_value=mock_socket):
        # Connect - should not detect continuous mode
        await device.connect()
        
        # Should not have detected continuous mode
        assert device.is_continuous_mode() is False
        assert device._device_in_continuous is False
        assert device._continuous_mode is False


@pytest.mark.asyncio
async def test_stop_continuous_sends_escape():
    """Test that stopping continuous mode sends escape character"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket
    mock_socket = MagicMock(spec=socket.socket)
    device.socket = mock_socket
    device._continuous_mode = True
    device._device_in_continuous = True
    
    # Stop continuous mode
    await device.stop_continuous()
    
    # Should have sent escape character 3 times
    escape_calls = [call for call in mock_socket.send.call_args_list 
                   if call[0][0] == b'\x1b']
    assert len(escape_calls) == 3
    
    assert device._continuous_mode is False
    assert device._device_in_continuous is False


@pytest.mark.asyncio
async def test_stream_endpoint_works_after_reconnect():
    """Test that streaming endpoint works after reconnecting to device in continuous mode"""
    from fastapi.testclient import TestClient
    from main import app, laser_manager
    from models.laser_models import MeasurementResult
    import time
    
    # Create mock device
    mock_device = MagicMock()
    mock_device.is_continuous_mode.side_effect = [False, True]  # Not continuous, then continuous after detection
    mock_device.detect_and_resume_continuous_mode = AsyncMock(return_value=True)
    
    # Create a mock measurement result
    mock_measurement = MeasurementResult(
        distance=1234.5,
        speed=None,
        signal_strength=3456,
        temperature=25.3,
        timestamp=time.time()
    )
    mock_device.get_continuous_data = AsyncMock(return_value=mock_measurement)
    
    # Mock laser manager
    laser_manager.get_device = MagicMock(return_value=mock_device)
    laser_manager.get_connected_device = MagicMock(return_value=mock_device)
    
    client = TestClient(app)
    
    # Try to get continuous stream - should auto-detect and resume
    # We'll use a different approach - just check that it doesn't return 400
    response = client.get("/api/v1/measure/continuous?port=2030")
    # Device should be detected as continuous after auto-detection
    assert response.status_code in [200, 204]  # Either data or no data
    
    # Verify auto-detection was attempted
    mock_device.detect_and_resume_continuous_mode.assert_called_once()