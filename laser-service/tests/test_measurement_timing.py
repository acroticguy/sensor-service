import pytest
import asyncio
import socket
import time
from unittest.mock import Mock, patch, MagicMock, call
from core.laser_device import LaserDevice
from models.laser_models import MeasurementMode


@pytest.mark.asyncio
async def test_vm_command_waits_for_measurement_data():
    """Test that VM command waits for actual measurement data, not just command echo"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket that returns command echo first, then measurement data
    mock_socket = MagicMock(spec=socket.socket)
    
    # Create a generator for recv calls
    def recv_generator():
        yield socket.timeout  # Clear buffer timeout
        yield b"VM\r\n"  # Command echo
        yield b"D 0002.500 0001.234 03456 +25.3\r\n"  # Actual measurement data
        yield b''  # End of data
    
    mock_socket.recv.side_effect = recv_generator()
    
    device.socket = mock_socket
    device.status = 'connected'
    
    # Send VM command
    response = await device.send_command("VM")
    
    # Should have received the measurement data, not just the echo
    assert "D 0002.500 0001.234 03456 +25.3" in response
    assert response != "VM"  # Should not return just the command echo
    
    # Verify socket was called multiple times to wait for data
    assert mock_socket.recv.call_count >= 2


@pytest.mark.asyncio
async def test_dm_command_waits_for_measurement_data():
    """Test that DM command waits for actual measurement data"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket
    mock_socket = MagicMock(spec=socket.socket)
    
    # Create a generator for recv calls
    def recv_generator():
        yield socket.timeout  # Clear buffer timeout
        yield b"DM\r\n"  # Command echo
        yield b"D 0001.234 03456 +25.3\r\n"  # Actual measurement
        yield b''  # End of data
    
    mock_socket.recv.side_effect = recv_generator()
    
    device.socket = mock_socket
    device.status = 'connected'
    
    # Send DM command
    response = await device.send_command("DM")
    
    # Should have received the measurement data
    assert "D 0001.234 03456 +25.3" in response
    
    # Verify multiple recv calls
    assert mock_socket.recv.call_count >= 2


@pytest.mark.asyncio
async def test_regular_command_no_extended_wait():
    """Test that regular commands don't wait as long as measurement commands"""
    device = LaserDevice("localhost", 2030)
    
    # Mock socket
    mock_socket = MagicMock(spec=socket.socket)
    
    # Create a generator for recv calls - regular command returns quickly
    def recv_generator():
        yield socket.timeout  # Clear buffer
        yield b"ST\r\nOK\r\n"  # Status response
        while True:  # Keep yielding timeouts if asked for more
            yield socket.timeout
    
    mock_socket.recv.side_effect = recv_generator()
    
    device.socket = mock_socket
    device.status = 'connected'
    
    # Send status command
    start_time = time.time()
    response = await device.send_command("ST")
    elapsed = time.time() - start_time
    
    # Should return quickly
    assert "OK" in response
    assert elapsed < 2.0  # Should not take long
    
    # Should not retry as many times as measurement commands
    assert mock_socket.recv.call_count <= 5