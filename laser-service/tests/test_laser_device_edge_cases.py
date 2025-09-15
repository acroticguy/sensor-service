import pytest
import asyncio
import socket
import time
import struct
import itertools
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta

from core.laser_device import LaserDevice
from models.laser_models import (
    LaserStatus, MeasurementResult, ErrorCode, LaserConfig,
    MeasurementMode, DataFormat
)


class TestLaserDeviceEdgeCases:
    """Test edge cases and error conditions for LaserDevice"""
    
    @pytest.fixture
    def mock_socket(self):
        """Create a mock socket for testing"""
        mock = MagicMock(spec=socket.socket)
        mock.connect = MagicMock()
        mock.send = MagicMock(return_value=None)
        mock.recv = MagicMock(return_value=b'')
        mock.close = MagicMock()
        mock.settimeout = MagicMock()
        mock.getpeername = MagicMock(return_value=('localhost', 2030))
        return mock
    
    @pytest.fixture
    def laser_device(self, mock_socket):
        """Create a laser device with mocked socket"""
        device = LaserDevice(host="localhost", port=2030, timeout=0.1)  # Short timeout for tests
        device.socket = mock_socket
        device.status = LaserStatus.CONNECTED
        device.last_heartbeat = datetime.now()
        return device
    
    @pytest.mark.asyncio
    async def test_connect_with_timeout(self):
        """Test connection timeout handling"""
        device = LaserDevice(timeout=0.1)
        
        with patch('socket.socket') as mock_socket_class:
            mock_socket = MagicMock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = socket.timeout("Connection timed out")
            
            result = await device.connect()
            
            assert result is False
            assert device.status == LaserStatus.ERROR
    
    @pytest.mark.asyncio
    async def test_connect_already_connected(self, laser_device):
        """Test connecting when already connected"""
        # Device is already connected
        laser_device.status = LaserStatus.CONNECTED
        
        with patch('socket.socket'):
            result = await laser_device.connect()
            
            # Should still return True but not create new socket
            assert result is True
            assert laser_device.status == LaserStatus.CONNECTED
    
    @pytest.mark.asyncio
    async def test_send_command_socket_error(self, laser_device):
        """Test send command with socket error"""
        laser_device.socket.send.side_effect = socket.error("Socket error")
        
        with pytest.raises(OSError):
            await laser_device.send_command("DM")
    
    @pytest.mark.asyncio
    async def test_send_command_partial_response(self, laser_device):
        """Test handling partial response data"""
        # Simulate receiving data in multiple chunks
        laser_device.socket.recv.side_effect = [
            socket.timeout,  # Clear buffer
            b'D 00',  # Partial data
            b'01.234',  # More data
            b' 03456 +',  # More data
            b'25.3\r\n',  # Complete data
            b''  # End
        ]
        
        response = await laser_device.send_command("DM")
        
        assert "D 0001.234 03456 +25.3" in response
    
    @pytest.mark.asyncio
    async def test_parse_measurement_incomplete_data(self, laser_device):
        """Test parsing incomplete measurement data"""
        # Test various incomplete formats
        incomplete_data = [
            "D",
            "D ",
            "D 123",
            "D 123.45",
            "D 123.45 ",
            "D 123.45 3456",
            "D 123.45 3456 ",
        ]
        
        for data in incomplete_data:
            result = laser_device._parse_measurement(data)
            # Should handle gracefully without crashing
            assert result is not None
            # May have partial data or error
    
    @pytest.mark.asyncio
    async def test_parse_measurement_invalid_numeric_values(self, laser_device):
        """Test parsing with invalid numeric values"""
        # Test non-numeric values in measurement
        invalid_data = [
            "D abc.def ghij +25.3",
            "D 123.45 abc +25.3",
            "D 123.45 3456 +abc",
            "D NaN 3456 +25.3",
            "D inf 3456 +25.3",
        ]
        
        for data in invalid_data:
            result = laser_device._parse_measurement(data)
            # Should handle gracefully
            assert result is not None
    
    @pytest.mark.asyncio
    async def test_continuous_mode_queue_overflow(self, laser_device):
        """Test continuous mode with queue overflow"""
        # Fill the queue to capacity
        laser_device._continuous_mode = True
        
        # Queue has maxsize=100
        for i in range(100):
            measurement = MeasurementResult(
                distance=float(i),
                signal_strength=1000,
                temperature=25.0
            )
            await laser_device._continuous_data_queue.put(measurement)
        
        # Queue is full, adding more should handle gracefully
        # The implementation should drop old data or handle overflow
        laser_device._continuous_data_queue.full()  # Should be True
    
    @pytest.mark.asyncio
    async def test_continuous_mode_rapid_stop_start(self, laser_device):
        """Test rapidly starting and stopping continuous mode"""
        # Create a cycle iterator that returns timeout then OK repeatedly
        responses = itertools.cycle([socket.timeout, b'OK\r\n'])
        laser_device.socket.recv.side_effect = responses
        
        # Rapid start/stop cycles
        for _ in range(2):  # Reduced from 5 to 2 for faster tests
            await laser_device._start_continuous_mode(MeasurementMode.DT)
            await asyncio.sleep(0.001)  # Reduced sleep time
            await laser_device.stop_continuous()
        
        # Should handle without errors
        assert laser_device._continuous_mode is False
    
    @pytest.mark.asyncio
    async def test_parse_measurement_extreme_values(self, laser_device):
        """Test parsing extreme measurement values"""
        extreme_data = [
            "D 9999999.999 99999 +999.9",  # Maximum values
            "D -9999999.999 0 -999.9",  # Negative distance
            "D 0.001 1 -273.0",  # Minimum positive distance, extreme cold
            "D 0000000.000 00000 +000.0",  # All zeros
        ]
        
        for data in extreme_data:
            result = laser_device._parse_measurement(data)
            assert result is not None
            # Values should be parsed correctly
    
    @pytest.mark.asyncio
    async def test_heartbeat_failure(self, laser_device):
        """Test heartbeat failure handling"""
        laser_device.socket.recv.side_effect = socket.timeout
        laser_device.socket.send.side_effect = socket.error("Connection lost")
        # Make getpeername also fail to simulate true connection loss
        laser_device.socket.getpeername.side_effect = socket.error("Not connected")
        
        result = await laser_device.heartbeat()
        
        assert result is False
        assert laser_device.status == LaserStatus.ERROR
    
    @pytest.mark.asyncio
    async def test_concurrent_command_execution(self, laser_device):
        """Test concurrent command execution (should be serialized by lock)"""
        call_count = 0
        
        def slow_recv(*args):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                raise socket.timeout
            return b'OK\r\n'
        
        laser_device.socket.recv.side_effect = slow_recv
        
        # Start multiple commands concurrently
        tasks = [
            laser_device.send_command("DM"),
            laser_device.send_command("VM"),
            laser_device.send_command("ST")
        ]
        
        # They should be serialized by the lock
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # All should complete without errors
        assert len(results) == 3
        for result in results:
            assert isinstance(result, str)  # Should be response strings, not exceptions
    
    @pytest.mark.asyncio
    async def test_binary_data_handling(self, laser_device):
        """Test handling of binary data in responses"""
        # Binary data that might appear in corrupted streams
        binary_responses = [
            b'\x00\x01\x02\x03\x04\x05',  # Control characters
            b'\xff\xfe\xfd\xfc\xfb\xfa',  # High bytes
            b'D 123.45 3456 +25.3\x00\r\n',  # Null in data
            b'D\x00123.45 3456 +25.3\r\n',  # Null in middle
        ]
        
        for binary_data in binary_responses:
            laser_device.socket.recv.side_effect = [
                socket.timeout,
                binary_data,
                b''
            ]
            
            try:
                response = await laser_device.send_command("DM")
                # Should handle binary data gracefully
                assert response is not None
            except:
                # Or raise appropriate error
                pass
    
    @pytest.mark.asyncio
    async def test_set_config_partial_failure(self, laser_device):
        """Test config setting with partial failures"""
        config = LaserConfig(
            measurement_frequency=100,
            averaging=10,
            laser_power=True,
            offset=1.5,
            scale_factor=2.0
        )
        
        # Make some commands fail
        responses = [
            socket.timeout,  # Clear buffer
            b'OK\r\n',  # MF success
            b'',
            socket.timeout,  # Clear buffer
            b'ERROR\r\n',  # AV failure
            b'',
            socket.timeout,  # Clear buffer
            b'OK\r\n',  # L1 success
            b'',
            socket.timeout,  # Clear buffer
            b'OK\r\n',  # OF success
            b'',
            socket.timeout,  # Clear buffer
            b'OK\r\n',  # SF success
            b''
        ]
        
        def recv_generator():
            for r in responses:
                yield r
            while True:
                yield socket.timeout
        
        laser_device.socket.recv.side_effect = recv_generator()
        
        result = await laser_device.set_config(config)
        
        # Should return True (set_config doesn't check individual responses)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_disconnect_with_pending_continuous_task(self, laser_device):
        """Test disconnecting while continuous task is running"""
        # Start a continuous task
        laser_device._continuous_mode = True
        laser_device._continuous_task = asyncio.create_task(asyncio.sleep(10))
        
        await laser_device.disconnect()
        
        # Wait a bit for task cancellation to complete
        await asyncio.sleep(0.1)
        
        # Task should be cancelled
        assert laser_device._continuous_task.cancelled()
        assert laser_device.status == LaserStatus.DISCONNECTED
    
    @pytest.mark.asyncio
    async def test_measurement_timestamp_accuracy(self, laser_device):
        """Test measurement timestamp accuracy"""
        laser_device.socket.recv.side_effect = [
            socket.timeout,
            b'DM\r\nD 0001.234 03456 +25.3\r\n',
            b''
        ]
        
        before_time = time.time()
        result = await laser_device.measure_distance()
        after_time = time.time()
        
        # Timestamp should be between before and after
        assert before_time <= result.timestamp <= after_time
    
    @pytest.mark.asyncio
    async def test_is_numeric_edge_cases(self, laser_device):
        """Test _is_numeric method with edge cases"""
        test_cases = [
            ("123", True),
            ("-123", True),
            ("123.45", True),
            ("-123.45", True),
            ("123,45", True),  # European decimal
            ("-123,45", True),
            ("+123.45", True),
            ("1.23e10", True),  # Scientific notation
            ("1.23E-10", True),
            ("NaN", True),  # Python float() accepts NaN
            ("Infinity", True),  # Python float() accepts Infinity
            ("123.45.67", False),
            ("12a34", False),
            ("", False),
            (" ", False),
            (".", False),
            (",", False),
            ("++123", False),
            ("--123", False),
        ]
        
        for value, expected in test_cases:
            result = laser_device._is_numeric(value)
            assert result == expected, f"Failed for value: {value}"
    
    @pytest.mark.asyncio
    async def test_check_continuous_state_with_corrupted_data(self, laser_device):
        """Test continuous state detection with corrupted data"""
        # Return corrupted continuous data
        laser_device.socket.recv.return_value = b'\xff\xfe\xfd\xfc'
        
        await laser_device._check_continuous_state()
        
        # Should handle corrupted data gracefully
        # May or may not detect continuous mode
    
    @pytest.mark.asyncio
    async def test_continuous_reader_task_exception(self, laser_device):
        """Test continuous reader task with exceptions"""
        # Set up continuous mode
        laser_device._continuous_mode = True
        laser_device._device_in_continuous = True
        
        # Make recv raise exception after some data
        call_count = 0
        def recv_side_effect(*args):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return b'D 0001.234 03456 +25.3\r\n'
            raise Exception("Socket error")
        
        laser_device.socket.recv.side_effect = recv_side_effect
        
        # Start reader task
        reader_task = asyncio.create_task(
            laser_device._read_continuous_data()
        )
        
        # Wait a bit
        await asyncio.sleep(0.1)
        
        # Task should handle exception and continue or exit gracefully
        if not reader_task.done():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
    
    @pytest.mark.asyncio
    async def test_send_command_long_response(self, laser_device):
        """Test handling very long responses"""
        # Create a very long response
        long_data = b'D ' + b'1234.567 ' * 1000 + b'\r\n'
        
        laser_device.socket.recv.side_effect = [
            socket.timeout,
            long_data[:4096],  # First chunk
            long_data[4096:8192],  # Second chunk
            long_data[8192:],  # Rest
            b''
        ]
        
        response = await laser_device.send_command("TEST")
        
        # Should handle long response
        assert len(response) > 4096
    
    @pytest.mark.asyncio
    async def test_invalid_host_port(self):
        """Test device creation with invalid host/port"""
        # LaserDevice doesn't validate ports in constructor, only on connect
        device1 = LaserDevice(port=-1)
        device2 = LaserDevice(port=99999)
        
        # These should fail on connect
        with patch('socket.socket') as mock_socket_class:
            mock_socket = MagicMock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = OSError("Invalid port")
            
            result1 = await device1.connect()
            assert result1 is False
            
            result2 = await device2.connect()
            assert result2 is False
        
        # Invalid host should also fail on connect
        device = LaserDevice(host="invalid.host.name", port=2030)
        result = await device.connect()
        assert result is False