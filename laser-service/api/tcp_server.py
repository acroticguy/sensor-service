import asyncio
import json
from typing import Dict, Any
from core.logger import get_logger
from core.laser_manager import LaserManager
from models.laser_models import MeasurementMode

logger = get_logger(__name__)


class TCPCommandHandler:
    def __init__(self, manager: LaserManager):
        self.manager = manager
        self.commands = {
            "DM": self._handle_single_distance,
            "DT": self._handle_continuous_distance,
            "DF": self._handle_trigger_distance,
            "VM": self._handle_single_speed,
            "VT": self._handle_continuous_speed,
            "S": self._handle_stop,
            "L0": self._handle_laser_off,
            "L1": self._handle_laser_on,
            "ST": self._handle_status,
            "MF": self._handle_measurement_frequency,
            "AV": self._handle_averaging,
            "MA": self._handle_moving_average,
            "OF": self._handle_offset,
            "SF": self._handle_scale_factor,
            "OA": self._handle_output_ascii,
            "OB": self._handle_output_binary,
            "ET0": self._handle_trigger_off,
            "ET1": self._handle_trigger_on,
            "DEVICES": self._handle_list_devices,
            "HELP": self._handle_help
        }
    
    async def handle_command(self, command_line: str) -> str:
        try:
            parts = command_line.strip().split(maxsplit=1)
            if not parts:
                return "ERROR: Empty command\r"
            
            command = parts[0].upper()
            parameters = parts[1] if len(parts) > 1 else ""
            
            if command in self.commands:
                return await self.commands[command](parameters)
            else:
                # Try to send as raw command
                device = self.manager.get_connected_device()
                if device:
                    response = await device.send_command(command, parameters)
                    return f"{response}\r"
                else:
                    return "ERROR: No connected device\r"
                    
        except Exception as e:
            logger.error(f"Command handling error for command '{command_line}': {str(e)}")
            return f"ERROR: {str(e)}\r"
    
    async def _handle_single_distance(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        result = await device.measure_distance(MeasurementMode.DM)
        if result.error_code:
            return f"{result.error_code}\r"
        return f"{result.distance:.1f}mm {result.signal_strength} {result.temperature:.1f}°C\r"
    
    async def _handle_continuous_distance(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("DT")
        return "OK: Continuous distance measurement started\r"
    
    async def _handle_trigger_distance(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("DF")
        return "OK: External trigger distance mode enabled\r"
    
    async def _handle_single_speed(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        result = await device.measure_speed(MeasurementMode.VM)
        if result.error_code:
            return f"{result.error_code}\r"
        return f"{result.speed:.3f}m/s {result.signal_strength} {result.temperature:.1f}°C\r"
    
    async def _handle_continuous_speed(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("VT")
        return "OK: Continuous speed measurement started\r"
    
    async def _handle_stop(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.stop_continuous()
        return "OK: Measurement stopped\r"
    
    async def _handle_laser_off(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("L0")
        return "OK: Laser off\r"
    
    async def _handle_laser_on(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("L1")
        return "OK: Laser on\r"
    
    async def _handle_status(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        status = await device.get_status()
        return f"STATUS: {json.dumps(status)}\r"
    
    async def _handle_measurement_frequency(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        try:
            freq = int(params)
            if 1 <= freq <= 2000:
                await device.send_command("MF", params)
                return f"OK: Measurement frequency set to {freq} Hz\r"
            else:
                return "ERROR: Frequency must be 1-2000 Hz\r"
        except ValueError:
            return "ERROR: Invalid frequency value\r"
    
    async def _handle_averaging(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        try:
            avg = int(params)
            if 1 <= avg <= 10000:
                await device.send_command("AV", params)
                return f"OK: Averaging set to {avg}\r"
            else:
                return "ERROR: Averaging must be 1-10000\r"
        except ValueError:
            return "ERROR: Invalid averaging value\r"
    
    async def _handle_moving_average(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        try:
            ma = int(params)
            if 0 <= ma <= 100:
                await device.send_command("MA", params)
                return f"OK: Moving average set to {ma}\r"
            else:
                return "ERROR: Moving average must be 0-100\r"
        except ValueError:
            return "ERROR: Invalid moving average value\r"
    
    async def _handle_offset(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        try:
            offset = float(params)
            await device.send_command("OF", params)
            return f"OK: Offset set to {offset} mm\r"
        except ValueError:
            return "ERROR: Invalid offset value\r"
    
    async def _handle_scale_factor(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        try:
            scale = float(params)
            await device.send_command("SF", params)
            return f"OK: Scale factor set to {scale}\r"
        except ValueError:
            return "ERROR: Invalid scale factor value\r"
    
    async def _handle_output_ascii(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("OA")
        return "OK: Output format set to ASCII\r"
    
    async def _handle_output_binary(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("OB")
        return "OK: Output format set to BINARY\r"
    
    async def _handle_trigger_off(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("ET0")
        return "OK: External trigger disabled\r"
    
    async def _handle_trigger_on(self, params: str) -> str:
        device = self.manager.get_connected_device()
        if not device:
            return "ERROR: No connected device\r"
        
        await device.send_command("ET1")
        return "OK: External trigger enabled\r"
    
    async def _handle_list_devices(self, params: str) -> str:
        devices = self.manager.get_all_devices()
        if not devices:
            return "No devices found\r"
        
        result = "Connected devices:\r\n"
        for device in devices:
            result += f"  {device.port}: {device.status} (errors: {device.error_count}, measurements: {device.measurements_count})\r\n"
        return result
    
    async def _handle_help(self, params: str) -> str:
        help_text = """Available commands:
DM - Single distance measurement
DT - Continuous distance measurement
DF - External trigger distance measurement
VM - Single speed measurement
VT - Continuous speed measurement
S - Stop continuous measurement
L0/L1 - Laser off/on
ST - Get device status
MF <freq> - Set measurement frequency (1-2000 Hz)
AV <count> - Set averaging (1-10000)
MA <count> - Set moving average (0-100)
OF <offset> - Set distance offset (mm)
SF <factor> - Set scale factor
OA/OB - Set ASCII/Binary output format
ET0/ET1 - Disable/Enable external trigger
DEVICES - List connected devices
HELP - Show this help
\r"""
        return help_text


class TCPServer:
    def __init__(self, host: str, port: int, laser_manager: LaserManager):
        self.host = host
        self.port = port  # This will be changed to not conflict with bridge
        self.handler = TCPCommandHandler(laser_manager)
        self.server = None
        
    async def start(self):
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        logger.info(f"TCP server listening on {self.host}:{self.port}")
        
    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("TCP server stopped")
    
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_addr = writer.get_extra_info('peername')
        logger.info(f"New TCP connection from {client_addr}")
        
        try:
            writer.write(b"LDM302 Laser Control Server\r\nType 'HELP' for available commands\r\n")
            await writer.drain()
            
            while True:
                try:
                    # Read command (timeout after 60 seconds)
                    data = await asyncio.wait_for(reader.readline(), timeout=60.0)
                    if not data:
                        break
                    
                    command = data.decode().strip()
                    if command.upper() == "QUIT":
                        break
                    
                    logger.debug(f"Received command: {command}")
                    response = await self.handler.handle_command(command)
                    
                    writer.write(response.encode())
                    await writer.drain()
                    
                except asyncio.TimeoutError:
                    writer.write(b"Timeout: Connection idle too long\r\n")
                    await writer.drain()
                    break
                except Exception as e:
                    logger.error(f"Error handling command: {e}")
                    writer.write(f"ERROR: {str(e)}\r\n".encode())
                    await writer.drain()
                    
        except Exception as e:
            logger.error(f"Client connection error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info(f"TCP connection closed from {client_addr}")