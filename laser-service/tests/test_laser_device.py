import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch, call
import socket
import time
from datetime import datetime

from core.laser_device import LaserDevice
from models.laser_models import (
    LaserStatus, MeasurementResult, ErrorCode, LaserConfig,
    MeasurementMode, DataFormat
)


# Test data
SAMPLE_RESPONSES = {
    'DM': b'DM\r\nD 0001.234 03456 +25.3\r\n',
    'DM_WITH_D_MINUS': b'DM\r\nD-0001.234 03456 +25.3\r\n',
    'VM': b'VM\r\nD 0002.500 0001.234 03456 +25.3\r\n',
    'VM_WITH_D_MINUS': b'VM\r\nD-0002.500 0001.234 03456 +25.3\r\n',
    'DT': b'DT\r\nOK\r\n',
    'VT': b'VT\r\nOK\r\n',
    'DT_DATA': b'D 0001.234 03456 +25.3\r\n',
    'VT_DATA': b'D 0002.500 0001.234 03456 +25.3\r\n',
    'ERROR': b'E02\r\n',
    'EMPTY': b'\r\n',
    'CORRUPT': b'D 000\xff.234 034\x14 +25.3\r\n',
    'INCOMPLETE': b'D 000',
}


class TestLaserDevice:
    """Test cases for LaserDevice class"""
    
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
        device = LaserDevice(host="localhost", port=2030)
        device.socket = mock_socket
        device.status = LaserStatus.CONNECTED
        device.last_heartbeat = datetime.now()
        return device
    
    @pytest.mark.asyncio
    async def test_connect_success(self, mock_socket):
        """Test successful device connection"""
        device = LaserDevice()
        
        with patch('socket.socket', return_value=mock_socket):
            # Mock successful connection
            mock_socket.recv.side_effect = socket.timeout  # No continuous data
            
            result = await device.connect()
            
            assert result is True
            assert device.status == LaserStatus.CONNECTED
            assert device.last_heartbeat is not None
            mock_socket.connect.assert_called_with(('localhost', 2030))
    
    @pytest.mark.asyncio
    async def test_connect_failure(self, mock_socket):
        """Test failed device connection"""
        device = LaserDevice()
        
        with patch('socket.socket', return_value=mock_socket):
            # Mock connection failure
            mock_socket.connect.side_effect = ConnectionError("Connection refused")
            
            result = await device.connect()
            
            assert result is False
            assert device.status == LaserStatus.ERROR
    
    @pytest.mark.asyncio
    async def test_disconnect(self, laser_device):
        """Test device disconnection"""
        await laser_device.disconnect()
        
        assert laser_device.status == LaserStatus.DISCONNECTED
        laser_device.socket.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_command_dm(self, laser_device):
        """Test sending DM command"""
        # Set up a generator function for recv calls
        def recv_generator():
            yield socket.timeout  # First recv (clearing buffer)
            yield SAMPLE_RESPONSES['DM'][:10]  # Partial response
            yield SAMPLE_RESPONSES['DM'][10:]   # Rest of response
            yield b''  # Additional recv for completion
        
        laser_device.socket.recv.side_effect = recv_generator()
        
        response = await laser_device.send_command("DM")
        
        assert "D 0001.234 03456 +25.3" in response
        laser_device.socket.send.assert_called_with(b'DM\r')
    
    @pytest.mark.asyncio
    async def test_send_command_vm(self, laser_device):
        """Test sending VM command"""
        # Set up responses
        laser_device.socket.recv.side_effect = [
            socket.timeout,  # First recv (clearing buffer)
            SAMPLE_RESPONSES['VM'],
            b''  # End of data
        ]
        
        response = await laser_device.send_command("VM")
        
        assert "D 0002.500 0001.234 03456 +25.3" in response
        laser_device.socket.send.assert_called_with(b'VM\r')
    
    @pytest.mark.asyncio
    async def test_measure_distance(self, laser_device):
        """Test distance measurement"""
        laser_device.socket.recv.side_effect = [
            socket.timeout,  # First recv (clearing buffer)
            SAMPLE_RESPONSES['DM'],
            b''  # End of data
        ]
        
        result = await laser_device.measure_distance()
        
        assert result.distance == 1234.0  # 1.234m * 1000
        assert result.signal_strength == 3456
        assert result.temperature == 25.3
        assert result.error_code is None
    
    @pytest.mark.asyncio
    async def test_measure_distance_with_d_minus(self, laser_device):
        """Test distance measurement with D- format"""
        # Create a generator with enough values
        def recv_generator():
            yield socket.timeout  # First recv (clearing buffer)
            yield SAMPLE_RESPONSES['DM_WITH_D_MINUS']
            yield b''  # End of data
            yield socket.timeout  # Extra for safety
        
        laser_device.socket.recv.side_effect = recv_generator()
        
        result = await laser_device.measure_distance()
        
        assert result.distance == -1234.0  # -1.234m * 1000
        assert result.signal_strength == 3456
        assert result.temperature == 25.3
    
    @pytest.mark.asyncio
    async def test_measure_speed(self, laser_device):
        """Test speed measurement"""
        laser_device.socket.recv.side_effect = [
            socket.timeout,  # First recv (clearing buffer)
            SAMPLE_RESPONSES['VM'],
            b''  # End of data
        ]
        
        result = await laser_device.measure_speed()
        
        assert result.speed == 2.5
        assert result.distance == 1234.0
        assert result.signal_strength == 3456
        assert result.temperature == 25.3
    
    @pytest.mark.asyncio
    async def test_parse_measurement_error(self, laser_device):
        """Test parsing error response"""
        result = laser_device._parse_measurement("E02\n")
        
        assert result.error_code == ErrorCode.E02
        assert result.distance is None
        assert result.speed is None
    
    @pytest.mark.asyncio
    async def test_parse_measurement_corrupt_data(self, laser_device):
        """Test parsing corrupt data"""
        result = laser_device._parse_measurement("D 000r +44.6\n")
        
        # Should skip corrupt line
        assert result.distance is None
        assert result.error_code is None
    
    @pytest.mark.asyncio
    async def test_start_continuous_mode_dt(self, laser_device):
        """Test starting continuous distance mode"""
        # Create a generator that never runs out
        def recv_generator():
            yield socket.timeout  # First recv (clearing buffer)
            yield SAMPLE_RESPONSES['DT']
            yield b''  # End of data
            # Keep yielding timeout forever
            while True:
                yield socket.timeout
        
        laser_device.socket.recv.side_effect = recv_generator()
        
        await laser_device._start_continuous_mode(MeasurementMode.DT)
        
        assert laser_device._continuous_mode is True
        assert laser_device._device_in_continuous is True
        assert laser_device._continuous_task is not None
        
        # Clean up
        await laser_device.stop_continuous()
    
    @pytest.mark.asyncio
    async def test_stop_continuous(self, laser_device):
        """Test stopping continuous mode"""
        # Start continuous mode first
        laser_device._continuous_mode = True
        laser_device._device_in_continuous = True
        
        await laser_device.stop_continuous()
        
        assert laser_device._continuous_mode is False
        assert laser_device._device_in_continuous is False
        # Should send escape character 3 times
        assert laser_device.socket.send.call_count >= 3
    
    @pytest.mark.asyncio
    async def test_get_continuous_data(self, laser_device):
        """Test getting continuous data"""
        # Set up continuous mode
        laser_device._continuous_mode = True
        
        # Create sample measurement
        sample_measurement = MeasurementResult(
            distance=1234.5,
            signal_strength=3456,
            temperature=25.3,
            timestamp=time.time()
        )
        
        # Add measurement to queue
        await laser_device._continuous_data_queue.put(sample_measurement)
        
        result = await laser_device.get_continuous_data(timeout=0.5)
        
        assert result is not None
        assert result.distance == 1234.5
        assert result.signal_strength == 3456
    
    @pytest.mark.asyncio
    async def test_detect_and_resume_continuous_mode(self, laser_device):
        """Test detecting and resuming continuous mode"""
        # Mock receiving continuous data
        laser_device.socket.recv.return_value = SAMPLE_RESPONSES['DT_DATA']
        
        detected = await laser_device.detect_and_resume_continuous_mode()
        
        assert detected is True
        assert laser_device._continuous_mode is True
        assert laser_device._device_in_continuous is True
    
    @pytest.mark.asyncio
    async def test_set_config(self, laser_device):
        """Test setting device configuration"""
        config = LaserConfig(
            measurement_frequency=100,
            averaging=10,
            laser_power=True
        )
        
        # Create a generator for the responses that keeps providing values
        def recv_generator():
            # For MF command
            yield socket.timeout
            yield b'OK\r\n'
            yield b''  # End of data
            # For AV command  
            yield socket.timeout
            yield b'OK\r\n'
            yield b''  # End of data
            # For L1 command
            yield socket.timeout
            yield b'OK\r\n'
            yield b''  # End of data
            # Extra timeouts in case
            while True:
                yield socket.timeout
        
        laser_device.socket.recv.side_effect = recv_generator()
        
        result = await laser_device.set_config(config)
        
        assert result is True
        # Should send MF, AV, and L1 commands
        assert laser_device.socket.send.call_count >= 3
    
    @pytest.mark.asyncio
    async def test_heartbeat_success(self, laser_device):
        """Test successful heartbeat"""
        laser_device.socket.recv.return_value = b'\r\n'
        
        result = await laser_device.heartbeat()
        
        assert result is True
        assert laser_device.last_heartbeat is not None
    
    @pytest.mark.asyncio
    async def test_parse_vt_format(self, laser_device):
        """Test parsing VT continuous speed format"""
        # VT format: D speed distance signal temperature
        result = laser_device._parse_measurement(
            "D 0002.500 0001.234 03456 +25.3",
            command="VT"
        )
        
        assert result.speed == 2.5
        assert result.distance == 1234.0
        assert result.signal_strength == 3456
        assert result.temperature == 25.3
    
    @pytest.mark.asyncio
    async def test_parse_dt_format(self, laser_device):
        """Test parsing DT continuous distance format"""
        # DT format: D distance signal temperature
        result = laser_device._parse_measurement(
            "D 0001.234 03456 +25.3",
            command="DT"
        )
        
        assert result.distance == 1234.0
        assert result.signal_strength == 3456
        assert result.temperature == 25.3
        assert result.speed is None
    
    def test_is_numeric(self, laser_device):
        """Test numeric value detection"""
        assert laser_device._is_numeric("123.45") is True
        assert laser_device._is_numeric("-123.45") is True
        assert laser_device._is_numeric("123,45") is True
        assert laser_device._is_numeric("abc") is False
        assert laser_device._is_numeric("12.34.56") is False