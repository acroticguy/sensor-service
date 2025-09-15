import pytest
import asyncio
from unittest.mock import Mock, MagicMock, AsyncMock
from api.tcp_server import TCPCommandHandler
from core.laser_manager import LaserManager
from core.laser_device import LaserDevice
from models.laser_models import MeasurementResult, ErrorCode


@pytest.fixture
def mock_manager():
    manager = Mock(spec=LaserManager)
    manager.get_connected_device = Mock()
    manager.get_all_devices = Mock()
    return manager


@pytest.fixture
def mock_device():
    device = Mock(spec=LaserDevice)
    device.send_command = AsyncMock()
    device.measure_distance = AsyncMock()
    device.measure_speed = AsyncMock()
    device.stop_continuous = AsyncMock()
    device.get_status = AsyncMock()
    return device


@pytest.fixture
def handler(mock_manager):
    return TCPCommandHandler(mock_manager)


@pytest.mark.asyncio
async def test_handle_distance_measurement(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    mock_device.measure_distance.return_value = MeasurementResult(
        distance=1234.5,
        signal_strength=8765,
        temperature=25.3
    )
    
    response = await handler.handle_command("DM")
    
    assert "1234.5mm" in response
    assert "8765" in response
    assert "25.3°C" in response
    mock_device.measure_distance.assert_called_once()


@pytest.mark.asyncio
async def test_handle_distance_measurement_error(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    mock_device.measure_distance.return_value = MeasurementResult(
        error_code=ErrorCode.E04
    )
    
    response = await handler.handle_command("DM")
    
    assert "E04" in response
    mock_device.measure_distance.assert_called_once()


@pytest.mark.asyncio
async def test_handle_no_device(handler, mock_manager):
    mock_manager.get_connected_device.return_value = None
    
    response = await handler.handle_command("DM")
    
    assert "ERROR: No connected device" in response


@pytest.mark.asyncio
async def test_handle_measurement_frequency(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    mock_device.send_command.return_value = "OK"
    
    response = await handler.handle_command("MF 100")
    
    assert "OK: Measurement frequency set to 100 Hz" in response
    mock_device.send_command.assert_called_with("MF", "100")


@pytest.mark.asyncio
async def test_handle_invalid_frequency(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    
    response = await handler.handle_command("MF 3000")
    
    assert "ERROR: Frequency must be 1-2000 Hz" in response
    mock_device.send_command.assert_not_called()


@pytest.mark.asyncio
async def test_handle_help_command(handler):
    response = await handler.handle_command("HELP")
    
    assert "Available commands:" in response
    assert "DM" in response
    assert "MF <freq>" in response


@pytest.mark.asyncio
async def test_handle_unknown_command(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    mock_device.send_command.return_value = "UNKNOWN"
    
    response = await handler.handle_command("XYZ")
    
    assert "UNKNOWN" in response
    mock_device.send_command.assert_called_with("XYZ", "")


@pytest.mark.asyncio
async def test_handle_continuous_mode(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    mock_device.send_command.return_value = "OK"
    
    # Start continuous distance
    response = await handler.handle_command("DT")
    assert "Continuous distance measurement started" in response
    
    # Stop measurement
    response = await handler.handle_command("S")
    assert "Measurement stopped" in response
    mock_device.stop_continuous.assert_called_once()


@pytest.mark.asyncio
async def test_handle_laser_control(handler, mock_manager, mock_device):
    mock_manager.get_connected_device.return_value = mock_device
    mock_device.send_command.return_value = "OK"
    
    # Laser off
    response = await handler.handle_command("L0")
    assert "Laser off" in response
    mock_device.send_command.assert_called_with("L0")
    
    # Laser on
    response = await handler.handle_command("L1")
    assert "Laser on" in response
    mock_device.send_command.assert_called_with("L1")