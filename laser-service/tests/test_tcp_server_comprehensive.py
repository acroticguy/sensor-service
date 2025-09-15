import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from api.tcp_server import TCPCommandHandler, TCPServer
from core.laser_manager import LaserManager
from core.laser_device import LaserDevice
from models.laser_models import (
    LaserStatus, MeasurementResult, ErrorCode, MeasurementMode
)


class TestTCPCommandHandler:
    """Comprehensive tests for TCP command handler"""
    
    @pytest.fixture
    def laser_manager(self):
        """Create a mock laser manager"""
        manager = LaserManager()
        return manager
    
    @pytest.fixture
    def tcp_handler(self, laser_manager):
        """Create TCP command handler"""
        return TCPCommandHandler(laser_manager)
    
    @pytest.fixture
    def mock_device(self):
        """Create a mock laser device"""
        device = MagicMock()
        device.status = LaserStatus.CONNECTED
        device.send_command = AsyncMock(return_value="OK\r\n")
        device.stop_continuous = AsyncMock(return_value=None)
        device.get_status = AsyncMock(return_value={"status": "connected"})
        device.measure_distance = AsyncMock(
            return_value=MeasurementResult(
                distance=1234.5,
                signal_strength=3456,
                temperature=25.3
            )
        )
        device.measure_speed = AsyncMock(
            return_value=MeasurementResult(
                speed=2.345,
                distance=1234.5,
                signal_strength=3456,
                temperature=25.3
            )
        )
        return device
    
    @pytest.mark.asyncio
    async def test_handle_dm_command(self, tcp_handler, laser_manager, mock_device):
        """Test DM (single distance) command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        response = await tcp_handler.handle_command("DM")
        
        assert "1234.5mm" in response
        assert "3456" in response
        assert "25.3°C" in response
        mock_device.measure_distance.assert_called_once_with(MeasurementMode.DM)
    
    @pytest.mark.asyncio
    async def test_handle_vm_command(self, tcp_handler, laser_manager, mock_device):
        """Test VM (single speed) command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        response = await tcp_handler.handle_command("VM")
        
        assert "2.345m/s" in response
        assert "3456" in response
        assert "25.3°C" in response
        mock_device.measure_speed.assert_called_once_with(MeasurementMode.VM)
    
    @pytest.mark.asyncio
    async def test_handle_dt_command(self, tcp_handler, laser_manager, mock_device):
        """Test DT (continuous distance) command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        response = await tcp_handler.handle_command("DT")
        
        assert "OK" in response
        assert "Continuous distance measurement started" in response
        mock_device.send_command.assert_called_once_with("DT")
    
    @pytest.mark.asyncio
    async def test_handle_vt_command(self, tcp_handler, laser_manager, mock_device):
        """Test VT (continuous speed) command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        response = await tcp_handler.handle_command("VT")
        
        assert "OK" in response
        assert "Continuous speed measurement started" in response
        mock_device.send_command.assert_called_once_with("VT")
    
    @pytest.mark.asyncio
    async def test_handle_stop_command(self, tcp_handler, laser_manager, mock_device):
        """Test S (stop) command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        response = await tcp_handler.handle_command("S")
        
        assert "OK" in response
        assert "stopped" in response.lower()
        mock_device.stop_continuous.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_laser_commands(self, tcp_handler, laser_manager, mock_device):
        """Test L0/L1 (laser off/on) commands"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        # Test laser off
        response = await tcp_handler.handle_command("L0")
        assert "OK" in response
        assert "Laser off" in response
        
        # Test laser on
        response = await tcp_handler.handle_command("L1")
        assert "OK" in response
        assert "Laser on" in response
    
    @pytest.mark.asyncio
    async def test_handle_status_command(self, tcp_handler, laser_manager, mock_device):
        """Test ST (status) command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        mock_device.send_command = AsyncMock(return_value="Device Status: OK\r\n")
        
        mock_device.get_status = AsyncMock(return_value={"status": "connected", "error_count": 0})
        
        response = await tcp_handler.handle_command("ST")
        
        assert "STATUS:" in response
        mock_device.get_status.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_configuration_commands(self, tcp_handler, laser_manager, mock_device):
        """Test configuration commands (MF, AV, MA, OF, SF)"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        # Test measurement frequency
        response = await tcp_handler.handle_command("MF 100")
        assert "OK" in response
        assert "Measurement frequency set to 100" in response
        
        # Test averaging
        response = await tcp_handler.handle_command("AV 10")
        assert "OK" in response
        assert "Averaging set to 10" in response
        
        # Test moving average
        response = await tcp_handler.handle_command("MA 5")
        assert "OK" in response
        assert "Moving average set to 5" in response
        
        # Test offset
        response = await tcp_handler.handle_command("OF 1.5")
        assert "OK" in response
        assert "Offset set to 1.5" in response
        
        # Test scale factor
        response = await tcp_handler.handle_command("SF 2.0")
        assert "OK" in response
        assert "Scale factor set to 2.0" in response
    
    @pytest.mark.asyncio
    async def test_handle_output_format_commands(self, tcp_handler, laser_manager, mock_device):
        """Test OA/OB (output ASCII/binary) commands"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        # Test ASCII output
        response = await tcp_handler.handle_command("OA")
        assert "OK" in response
        assert "ASCII" in response
        
        # Test binary output
        response = await tcp_handler.handle_command("OB")
        assert "OK" in response
        assert "BINARY" in response
    
    @pytest.mark.asyncio
    async def test_handle_trigger_commands(self, tcp_handler, laser_manager, mock_device):
        """Test ET0/ET1 (external trigger off/on) commands"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        # Test trigger off
        response = await tcp_handler.handle_command("ET0")
        assert "OK" in response
        assert "External trigger disabled" in response
        
        # Test trigger on
        response = await tcp_handler.handle_command("ET1")
        assert "OK" in response
        assert "External trigger enabled" in response
    
    @pytest.mark.asyncio
    async def test_handle_devices_command(self, tcp_handler, laser_manager):
        """Test DEVICES command"""
        # Add some devices
        device1 = MagicMock()
        device1.status = LaserStatus.CONNECTED
        device2 = MagicMock()
        device2.status = LaserStatus.DISCONNECTED
        
        device1.port = "2030"
        device1.error_count = 0
        device1.measurements_count = 100
        device2.port = "2031"
        device2.error_count = 5
        device2.measurements_count = 200
        
        laser_manager.get_all_devices = MagicMock(return_value=[device1, device2])
        
        response = await tcp_handler.handle_command("DEVICES")
        
        assert "Connected devices:" in response
        assert "2030:" in response
        assert "2031:" in response
    
    @pytest.mark.asyncio
    async def test_handle_help_command(self, tcp_handler):
        """Test HELP command"""
        response = await tcp_handler.handle_command("HELP")
        
        assert "Available commands:" in response
        assert "DM" in response
        assert "VM" in response
        assert "DT" in response
        assert "VT" in response
    
    @pytest.mark.asyncio
    async def test_handle_unknown_command(self, tcp_handler, laser_manager, mock_device):
        """Test handling unknown command"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        mock_device.send_command = AsyncMock(return_value="Unknown command\r\n")
        
        response = await tcp_handler.handle_command("UNKNOWN")
        
        assert "Unknown command" in response
        mock_device.send_command.assert_called_once_with("UNKNOWN", "")
    
    @pytest.mark.asyncio
    async def test_handle_command_no_device(self, tcp_handler, laser_manager):
        """Test commands when no device is connected"""
        laser_manager.get_connected_device = MagicMock(return_value=None)
        
        response = await tcp_handler.handle_command("DM")
        assert "ERROR: No connected device" in response
        
        response = await tcp_handler.handle_command("VM")
        assert "ERROR: No connected device" in response
    
    @pytest.mark.asyncio
    async def test_handle_empty_command(self, tcp_handler):
        """Test handling empty command"""
        response = await tcp_handler.handle_command("")
        assert "ERROR: Empty command" in response
        
        response = await tcp_handler.handle_command("   ")
        assert "ERROR: Empty command" in response
    
    @pytest.mark.asyncio
    async def test_handle_command_with_error(self, tcp_handler, laser_manager, mock_device):
        """Test command handling when device returns error"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        
        # DM with error
        mock_device.measure_distance = AsyncMock(
            return_value=MeasurementResult(error_code=ErrorCode.E02)
        )
        response = await tcp_handler.handle_command("DM")
        assert "E02" in response
        
        # VM with error
        mock_device.measure_speed = AsyncMock(
            return_value=MeasurementResult(error_code=ErrorCode.E01)
        )
        response = await tcp_handler.handle_command("VM")
        assert "E01" in response
    
    @pytest.mark.asyncio
    async def test_handle_command_exception(self, tcp_handler, laser_manager, mock_device):
        """Test command handling with exceptions"""
        laser_manager.get_connected_device = MagicMock(return_value=mock_device)
        mock_device.get_status = AsyncMock(side_effect=Exception("Test error"))
        
        response = await tcp_handler.handle_command("ST")
        assert "ERROR: Test error" in response




class TestTCPServer:
    """Test TCP server functionality"""
    
    @pytest.mark.asyncio
    async def test_tcp_server_start(self):
        """Test starting TCP server"""
        mock_manager = MagicMock()
        tcp_server = TCPServer('127.0.0.1', 5555, mock_manager)
        
        with patch('asyncio.start_server') as mock_start_server:
            mock_server = MagicMock()
            mock_start_server.return_value = mock_server
            
            await tcp_server.start()
            
            assert tcp_server.server == mock_server
            mock_start_server.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_tcp_server_stop(self):
        """Test stopping TCP server"""
        mock_manager = MagicMock()
        tcp_server = TCPServer('127.0.0.1', 5555, mock_manager)
        
        mock_server = MagicMock()
        mock_server.close = MagicMock()
        mock_server.wait_closed = AsyncMock()
        tcp_server.server = mock_server
        
        await tcp_server.stop()
        
        mock_server.close.assert_called_once()
        mock_server.wait_closed.assert_called_once()