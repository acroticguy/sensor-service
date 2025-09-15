import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from core.laser_manager import LaserManager
from core.laser_device import LaserDevice
from models.laser_models import LaserStatus, LaserInfo
from core.config import settings


class TestLaserManager:
    """Comprehensive tests for LaserManager"""
    
    @pytest.fixture
    def laser_manager(self):
        """Create a laser manager instance"""
        manager = LaserManager()
        return manager
    
    @pytest.fixture
    def mock_device(self):
        """Create a mock laser device"""
        device = MagicMock(spec=LaserDevice)
        device.status = LaserStatus.CONNECTED
        device.last_heartbeat = datetime.now()
        device.error_count = 0
        device.measurements_count = 10
        device.connect = AsyncMock(return_value=True)
        device.disconnect = AsyncMock()
        device.heartbeat = AsyncMock(return_value=True)
        return device
    
    @pytest.mark.asyncio
    async def test_start_manager(self, laser_manager):
        """Test starting the laser manager"""
        with patch.object(laser_manager, '_auto_discover_devices', AsyncMock()) as mock_discover:
            with patch('asyncio.create_task') as mock_create_task:
                await laser_manager.start()
                
                # Should call auto discover
                mock_discover.assert_called_once()
                
                # Should create background tasks
                assert mock_create_task.call_count == 2
    
    @pytest.mark.asyncio
    async def test_stop_manager(self, laser_manager, mock_device):
        """Test stopping the laser manager"""
        # Set up manager with device and tasks
        laser_manager.devices["laser"] = mock_device
        laser_manager._heartbeat_task = MagicMock()
        laser_manager._reconnect_task = MagicMock()
        
        await laser_manager.stop()
        
        # Should cancel tasks
        laser_manager._heartbeat_task.cancel.assert_called_once()
        laser_manager._reconnect_task.cancel.assert_called_once()
        
        # Should disconnect devices
        mock_device.disconnect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_auto_discover_devices_success(self, laser_manager):
        """Test successful device auto-discovery"""
        mock_device = MagicMock()
        mock_device.connect = AsyncMock(return_value=True)
        
        with patch('core.laser_manager.LaserDevice', return_value=mock_device):
            await laser_manager._auto_discover_devices()
            
            # Should create device with correct settings
            assert "laser" in laser_manager.devices
            assert laser_manager.devices["laser"] == mock_device
            mock_device.connect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_auto_discover_devices_failure(self, laser_manager):
        """Test failed device auto-discovery"""
        mock_device = MagicMock()
        mock_device.connect = AsyncMock(return_value=False)
        
        with patch('core.laser_manager.LaserDevice', return_value=mock_device):
            await laser_manager._auto_discover_devices()
            
            # Should not add device on failure
            assert len(laser_manager.devices) == 0
    
    @pytest.mark.asyncio
    async def test_auto_discover_devices_exception(self, laser_manager):
        """Test device auto-discovery with exception"""
        with patch('core.laser_manager.LaserDevice', side_effect=Exception("Connection error")):
            await laser_manager._auto_discover_devices()
            
            # Should handle exception gracefully
            assert len(laser_manager.devices) == 0
    
    @pytest.mark.asyncio
    async def test_heartbeat_loop_success(self, laser_manager, mock_device):
        """Test heartbeat loop with successful heartbeats"""
        laser_manager.devices["laser"] = mock_device
        
        # Run one iteration of heartbeat loop
        with patch('asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError)):
            try:
                await laser_manager._heartbeat_loop()
            except asyncio.CancelledError:
                pass
        
        # Should not change device status on success
        assert mock_device.status == LaserStatus.CONNECTED
    
    @pytest.mark.asyncio
    async def test_heartbeat_loop_failure(self, laser_manager, mock_device):
        """Test heartbeat loop with failed heartbeat"""
        mock_device.heartbeat = AsyncMock(return_value=False)
        laser_manager.devices["laser"] = mock_device
        
        # Mock sleep to run once then cancel
        sleep_count = 0
        async def mock_sleep(duration):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count > 1:
                raise asyncio.CancelledError
            return
        
        with patch('asyncio.sleep', mock_sleep):
            try:
                await laser_manager._heartbeat_loop()
            except asyncio.CancelledError:
                pass
        
        # Should change device status to ERROR on failure
        assert mock_device.status == LaserStatus.ERROR
    
    @pytest.mark.asyncio
    async def test_heartbeat_loop_exception(self, laser_manager, mock_device):
        """Test heartbeat loop with exception"""
        mock_device.heartbeat = AsyncMock(side_effect=Exception("Heartbeat error"))
        laser_manager.devices["laser"] = mock_device
        
        # Run one iteration
        sleep_count = 0
        async def mock_sleep(duration):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count > 1:
                raise asyncio.CancelledError
            return
        
        with patch('asyncio.sleep', mock_sleep):
            try:
                await laser_manager._heartbeat_loop()
            except asyncio.CancelledError:
                pass
        
        # Should handle exception and continue
    
    @pytest.mark.asyncio
    async def test_reconnect_loop_success(self, laser_manager, mock_device):
        """Test reconnect loop with successful reconnection"""
        mock_device.status = LaserStatus.ERROR
        mock_device.connect = AsyncMock(return_value=True)
        laser_manager.devices["laser"] = mock_device
        
        # Run one iteration
        sleep_count = 0
        async def mock_sleep(duration):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count > 1:
                raise asyncio.CancelledError
            return
        
        with patch('asyncio.sleep', mock_sleep):
            try:
                await laser_manager._reconnect_loop()
            except asyncio.CancelledError:
                pass
        
        # Should attempt reconnection
        mock_device.connect.assert_called_once()
        # Status should be set during reconnection
        assert mock_device.status == LaserStatus.RECONNECTING
    
    @pytest.mark.asyncio
    async def test_reconnect_loop_failure(self, laser_manager, mock_device):
        """Test reconnect loop with failed reconnection"""
        mock_device.status = LaserStatus.DISCONNECTED
        mock_device.connect = AsyncMock(return_value=False)
        laser_manager.devices["laser"] = mock_device
        
        # Run one iteration
        sleep_count = 0
        async def mock_sleep(duration):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count > 1:
                raise asyncio.CancelledError
            return
        
        with patch('asyncio.sleep', mock_sleep):
            try:
                await laser_manager._reconnect_loop()
            except asyncio.CancelledError:
                pass
        
        # Should set status to ERROR on failure
        mock_device.connect.assert_called_once()
        assert mock_device.status == LaserStatus.ERROR
    
    @pytest.mark.asyncio
    async def test_reconnect_loop_skip_connected(self, laser_manager, mock_device):
        """Test reconnect loop skips connected devices"""
        mock_device.status = LaserStatus.CONNECTED
        laser_manager.devices["laser"] = mock_device
        
        # Run one iteration
        sleep_count = 0
        async def mock_sleep(duration):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count > 1:
                raise asyncio.CancelledError
            return
        
        with patch('asyncio.sleep', mock_sleep):
            try:
                await laser_manager._reconnect_loop()
            except asyncio.CancelledError:
                pass
        
        # Should not attempt reconnection for connected device
        mock_device.connect.assert_not_called()
    
    def test_get_device(self, laser_manager, mock_device):
        """Test getting device"""
        laser_manager.devices["laser"] = mock_device
        
        # Should return device regardless of port parameter
        assert laser_manager.get_device() == mock_device
        assert laser_manager.get_device("any_port") == mock_device
        assert laser_manager.get_device("2030") == mock_device
    
    def test_get_device_not_found(self, laser_manager):
        """Test getting device when not found"""
        assert laser_manager.get_device() is None
        assert laser_manager.get_device("2030") is None
    
    def test_get_all_devices(self, laser_manager, mock_device):
        """Test getting all devices info"""
        laser_manager.devices["laser"] = mock_device
        
        devices = laser_manager.get_all_devices()
        
        assert len(devices) == 1
        device_info = devices[0]
        assert device_info.port == "localhost:2030"
        assert device_info.status == LaserStatus.CONNECTED
        assert device_info.error_count == 0
        assert device_info.measurements_count == 10
        assert device_info.last_heartbeat is not None
    
    def test_get_all_devices_empty(self, laser_manager):
        """Test getting all devices when none exist"""
        devices = laser_manager.get_all_devices()
        assert len(devices) == 0
    
    def test_get_all_devices_no_heartbeat(self, laser_manager, mock_device):
        """Test getting all devices with no heartbeat"""
        mock_device.last_heartbeat = None
        laser_manager.devices["laser"] = mock_device
        
        devices = laser_manager.get_all_devices()
        
        assert len(devices) == 1
        assert devices[0].last_heartbeat is None
    
    def test_get_connected_device_success(self, laser_manager, mock_device):
        """Test getting connected device"""
        mock_device.status = LaserStatus.CONNECTED
        laser_manager.devices["laser"] = mock_device
        
        device = laser_manager.get_connected_device()
        assert device == mock_device
    
    def test_get_connected_device_not_connected(self, laser_manager, mock_device):
        """Test getting connected device when not connected"""
        mock_device.status = LaserStatus.DISCONNECTED
        laser_manager.devices["laser"] = mock_device
        
        device = laser_manager.get_connected_device()
        assert device is None
    
    def test_get_connected_device_error_state(self, laser_manager, mock_device):
        """Test getting connected device in error state"""
        mock_device.status = LaserStatus.ERROR
        laser_manager.devices["laser"] = mock_device
        
        device = laser_manager.get_connected_device()
        assert device is None
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(self, laser_manager, mock_device):
        """Test concurrent operations on laser manager"""
        laser_manager.devices["laser"] = mock_device
        
        # Simulate concurrent get operations
        tasks = [
            asyncio.create_task(asyncio.to_thread(laser_manager.get_device)),
            asyncio.create_task(asyncio.to_thread(laser_manager.get_all_devices)),
            asyncio.create_task(asyncio.to_thread(laser_manager.get_connected_device)),
        ]
        
        results = await asyncio.gather(*tasks)
        
        # All operations should complete successfully
        assert results[0] == mock_device
        assert len(results[1]) == 1
        assert results[2] == mock_device
    
    @pytest.mark.asyncio
    async def test_manager_lifecycle(self, laser_manager):
        """Test complete manager lifecycle"""
        with patch.object(laser_manager, '_auto_discover_devices', AsyncMock()):
            # Start manager
            await laser_manager.start()
            
            # Verify tasks are created
            assert laser_manager._heartbeat_task is not None
            assert laser_manager._reconnect_task is not None
            
            # Stop manager
            await laser_manager.stop()
            
            # Wait a bit for cancellation to complete
            await asyncio.sleep(0.1)
            
            # Verify tasks are cancelled
            assert laser_manager._heartbeat_task.cancelled()
            assert laser_manager._reconnect_task.cancelled()
    
    def test_devices_property_initialization(self):
        """Test devices dictionary is properly initialized"""
        manager = LaserManager()
        assert isinstance(manager.devices, dict)
        assert len(manager.devices) == 0
        assert manager._heartbeat_task is None
        assert manager._reconnect_task is None