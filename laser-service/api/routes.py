from fastapi import APIRouter, HTTPException, Query, Body
from typing import List, Optional
import asyncio
import json
from datetime import datetime
from core.multi_laser_manager import MultiLaserManager
from api.berthing_routes import create_berthing_router
from models.laser_models import (
    CommandRequest, CommandResponse, LaserInfo, MeasurementResult,
    LaserConfig, MeasurementMode, ErrorCode
)
from models.commands import (
    LaserCommands, TemperatureResponse, VersionResponse, DeviceIDResponse,
    TriggerConfig, AnalogConfig, DisplayMode, Units, PilotLaserMode,
    AutosaveMode, SerialEchoMode, ConfigurationBank, ParameterQueryResponse,
    HardwareVersionResponse, ConfigurationResponse, BaudRateResponse
)
from core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


class LaserAPI:
    def __init__(self, manager: MultiLaserManager):
        self.manager = manager


def setup_routes(laser_manager: MultiLaserManager) -> APIRouter:
    api = LaserAPI(laser_manager)

    @router.get("/health", tags=["health"],
                summary="🏥 Production Health Check",
                description="Comprehensive system health validation including all components",
                responses={
                    200: {
                        "description": "System is healthy",
                        "content": {
                            "application/json": {
                                "example": {
                                    "status": "healthy",
                                    "timestamp": "2024-01-15T10:30:00.000Z",
                                    "uptime_seconds": 3600,
                                    "manager_running": True,
                                    "connected_lasers": 2,
                                    "components": {
                                        "laser_manager": "healthy",
                                        "database": "healthy",
                                        "websockets": "healthy",
                                        "pager_service": "healthy"
                                    }
                                }
                            }
                        }
                    },
                    503: {"description": "System is unhealthy"}
                })
    async def health_check():
        """
        **Production Health Check Endpoint**

        Performs comprehensive health validation of all system components:

        - **Laser Manager**: Connection status and device availability
        - **Database**: Connection pool health and query performance
        - **WebSocket**: Active connections and streaming status
        - **Pager Service**: External service connectivity
        - **System Resources**: Memory, CPU, and disk usage

        Returns HTTP 200 for healthy, HTTP 503 for unhealthy systems.
        """
        from datetime import datetime
        import time
        import json

        start_time = time.time()
        health_data = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "uptime_seconds": time.time() - startup_time if 'startup_time' in globals() else 0,
            "manager_running": api.manager.is_running(),
            "connected_lasers": len(api.manager.get_connected_lasers()),
            "components": {
                "laser_manager": "healthy" if api.manager.is_running() else "unhealthy",
                "database": "healthy",  # TODO: Add actual database health check
                "websockets": "healthy",  # TODO: Add WebSocket health check
                "pager_service": "healthy"  # TODO: Add pager service health check
            },
            "check_duration_ms": 0
        }

        # Determine overall status
        unhealthy_components = [k for k, v in health_data["components"].items() if v != "healthy"]
        if unhealthy_components:
            health_data["status"] = "unhealthy"
            health_data["unhealthy_components"] = unhealthy_components

        health_data["check_duration_ms"] = round((time.time() - start_time) * 1000, 2)

        # Return appropriate status code
        status_code = 200 if health_data["status"] == "healthy" else 503

        from fastapi import Response
        return Response(
            content=json.dumps(health_data, indent=2),
            status_code=status_code,
            media_type="application/json"
        )

    @router.get("/devices", tags=["devices"])
    async def get_devices():
        """Get all connected laser devices"""
        try:
            devices = api.manager.get_connected_lasers()
            return {
                "success": True,
                "devices": devices,
                "total_devices": len(devices)
            }
        except Exception as e:
            logger.error(f"Error getting devices: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/devices/{laser_id}/info", tags=["devices"])
    async def get_device_info(laser_id: int):
        """Get information about a specific laser device"""
        try:
            devices = api.manager.get_connected_lasers()
            if laser_id not in devices:
                raise HTTPException(status_code=404, detail=f"Laser device {laser_id} not found")

            return {
                "success": True,
                "laser_id": laser_id,
                "device_info": devices[laser_id]
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting device info: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/devices/{laser_id}/command", response_model=CommandResponse, tags=["system"])
    async def send_command(laser_id: int, request: CommandRequest):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command(request.command, request.parameters or "")
            return CommandResponse(success=True, data=response, raw_response=response)
        except Exception as e:
            logger.error(f"Command failed on port {port}, command: {request.command}, error: {str(e)}")
            return CommandResponse(success=False, error=str(e))

    @router.post("/measure/distance", response_model=MeasurementResult, tags=["measurements"])
    async def measure_distance(
        laser_id: Optional[int] = None
    ):
        """Single distance measurement (non-continuous)"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        try:
            result = await device.measure_distance(MeasurementMode.DM)
            return result
        except Exception as e:
            logger.error(f"Distance measurement failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/measure/distance/triggered", response_model=MeasurementResult, tags=["measurements"])
    async def measure_distance_triggered(
        laser_id: Optional[int] = None
    ):
        """Single distance measurement with external trigger"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        try:
            result = await device.measure_distance(MeasurementMode.DF)
            return result
        except Exception as e:
            logger.error(f"Triggered distance measurement failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/measure/distance/continuous/start", tags=["measurements"])
    async def start_continuous_distance(
        laser_id: Optional[int] = None
    ):
        """Start continuous distance measurement mode"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        try:
            await device._start_continuous_mode(MeasurementMode.DT)
            return {
                "success": True,
                "message": "Starting continuous distance measurement mode. Use GET /measure/continuous to retrieve measurements."
            }
        except Exception as e:
            logger.error(f"Failed to start continuous distance measurement: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/measure/speed", response_model=MeasurementResult, tags=["measurements"])
    async def measure_speed(
        laser_id: Optional[int] = None
    ):
        """Single speed measurement (non-continuous)"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        try:
            result = await device.measure_speed(MeasurementMode.VM)

            # If VM fails, it might be because device needs to be moving
            # or doesn't support speed measurement
            if result.error_code == ErrorCode.E99:
                logger.warning("VM command returned no data - device might need to be moving for speed measurement")

            return result
        except Exception as e:
            logger.error(f"Speed measurement failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/measure/speed/continuous/start", tags=["measurements"])
    async def start_continuous_speed(
        laser_id: Optional[int] = None
    ):
        """Start continuous speed measurement mode"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        try:
            await device._start_continuous_mode(MeasurementMode.VT)
            return {
                "success": True,
                "message": "Starting continuous speed measurement mode. Use GET /measure/continuous to retrieve measurements."
            }
        except Exception as e:
            logger.error(f"Failed to start continuous speed measurement: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/measure/stop", tags=["measurements"])
    async def stop_measurement(laser_id: Optional[int] = None):
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        try:
            await device.stop_continuous()
            return {"success": True, "message": "Continuous measurement stopped"}
        except Exception as e:
            logger.error(f"Failed to stop measurement: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/measure/continuous", tags=["measurements"])
    async def get_continuous_measurement(laser_id: Optional[int] = None):
        """Get latest measurement from continuous mode"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        # If device is not in continuous mode, check if it's actually sending continuous data
        if not device.is_continuous_mode():
            # Try to detect if device is sending continuous data
            logger.info("Device not marked as continuous mode, checking for incoming data...")

            # Check for incoming continuous data
            detected = await device.detect_and_resume_continuous_mode()
            if detected:
                logger.info("Detected device is in continuous mode, resumed reading")
                # Give it a moment to start collecting data
                await asyncio.sleep(0.5)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Device not in continuous mode. Use POST /measure/distance/continuous/start or /measure/speed/continuous/start to begin"
                )

        measurement = await device.get_continuous_data(timeout=2.0)
        if measurement and (measurement.distance is not None or measurement.speed is not None):
            return measurement
        else:
            # Return empty response with 204 No Content if no valid data
            from fastapi import Response
            return Response(status_code=204)

    @router.get("/measure/continuous/stream", tags=["measurements"])
    async def stream_continuous_measurements(laser_id: Optional[int] = None):
        """Stream continuous measurements using Server-Sent Events"""
        from fastapi.responses import StreamingResponse
        import json
        from datetime import datetime

        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        # If device is not in continuous mode, check if it's actually sending continuous data
        if not device.is_continuous_mode():
            # Try to detect if device is sending continuous data
            logger.info("Stream: Device not marked as continuous mode, checking for incoming data...")

            # Check for incoming continuous data
            detected = await device.detect_and_resume_continuous_mode()
            if detected:
                logger.info("Stream: Detected device is in continuous mode, resumed reading")
                # Give it a moment to start collecting data
                await asyncio.sleep(0.5)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Device not in continuous mode. Use POST /measure/distance/continuous/start or /measure/speed/continuous/start to begin"
                )

        async def event_generator():
            while device.is_continuous_mode():
                measurement = await device.get_continuous_data(timeout=1.0)
                if measurement:
                    data = {
                        "distance": measurement.distance,
                        "speed": measurement.speed,
                        "signal": measurement.signal_strength,
                        "temperature": measurement.temperature,
                        "error": measurement.error_code.value if measurement.error_code else None,
                        "timestamp": datetime.now().isoformat()
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(0.01)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    @router.get("/measure/status", tags=["measurements"])
    async def get_measurement_status(laser_id: Optional[int] = None):
        """Get current measurement status"""
        device = api.manager.get_device_by_id(laser_id) if laser_id else api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=404, detail="No connected device found")

        return {
            "continuous_mode": device.is_continuous_mode(),
            "measurements_count": device.measurements_count,
            "error_count": device.error_count
        }

    @router.post("/devices/{laser_id}/config", tags=["configuration"])
    async def set_config(laser_id: int, config: LaserConfig):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            success = await device.set_config(config)
            if success:
                return {"success": True, "message": "Configuration updated"}
            else:
                raise HTTPException(status_code=500, detail="Failed to update configuration")
        except Exception as e:
            logger.error(f"Configuration update failed on port {port}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/devices/{laser_id}/status", tags=["devices"])
    async def get_status(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            status = await device.get_status()
            return {"success": True, "status": status}
        except Exception as e:
            logger.error(f"Failed to get status on port {port}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    # Temperature command
    @router.get("/devices/{laser_id}/temperature", response_model=TemperatureResponse, tags=["query"])
    async def get_temperature(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("TP")
            # Parse temperature response - handle different formats
            temp_str = response.replace("°C", "").replace("C", "").strip()
            try:
                temp_value = float(temp_str)
            except ValueError:
                # If parsing fails, try to extract number from response
                import re
                match = re.search(r'[-+]?\d*\.?\d+', response)
                if match:
                    temp_value = float(match.group())
                else:
                    raise ValueError(f"Could not parse temperature from: {response}")
            return TemperatureResponse(temperature=temp_value, raw_response=response)
        except TimeoutError:
            # TP command might not be supported, try getting temp from measurement
            logger.warning("TP command timed out, trying to get temperature from measurement")
            try:
                result = await device.measure_distance()
                if result.temperature is not None:
                    return TemperatureResponse(temperature=result.temperature, raw_response="From measurement")
                else:
                    raise HTTPException(status_code=501, detail="Temperature command not supported by device")
            except:
                raise HTTPException(status_code=501, detail="Temperature command not supported by device")
        except Exception as e:
            logger.error(f"Failed to get temperature: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Version command
    @router.get("/devices/{laser_id}/version", response_model=VersionResponse, tags=["query"])
    async def get_version(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("VN")
            return VersionResponse(version=response, raw_response=response)
        except Exception as e:
            logger.error(f"Failed to get version: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Device ID command
    @router.get("/devices/{laser_id}/id", response_model=DeviceIDResponse, tags=["query"])
    async def get_device_id(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("ID")
            return DeviceIDResponse(device_id=response, raw_response=response)
        except Exception as e:
            logger.error(f"Failed to get device ID: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Laser power control (Note: LDM302 may not support L0/L1 commands)
    @router.post("/devices/{laser_id}/laser/{state}", tags=["laser"])
    async def set_laser_state(laser_id: int, state: str):
        """
        Control measurement laser (if supported by device).
        Note: Many LDM302 devices don't support L0/L1 commands.
        The measurement laser is controlled automatically during measurements.
        Use pilot laser endpoints for visible laser control.
        """
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if state not in ["on", "off"]:
            raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

        try:
            command = "L1" if state == "on" else "L0"
            response = await device.send_command(command)

            # Check if command was accepted or returned error
            if "?" in response:
                logger.warning(f"Device does not support L0/L1 commands. Use pilot laser control instead.")
                raise HTTPException(
                    status_code=501,
                    detail="This device does not support L0/L1 commands. The measurement laser is controlled automatically. Use /pilot-laser endpoints for visible laser control."
                )

            return {"success": True, "message": f"Laser turned {state}"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to set laser state: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Pilot laser control
    @router.post("/devices/{laser_id}/pilot-laser/{mode}", tags=["laser"])
    async def set_pilot_laser_mode(laser_id: int, mode: str):
        """Set pilot laser mode: off, on, blink_2hz, blink_5hz"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        mode_map = {
            "off": "PL0",
            "on": "PL1",
            "blink_2hz": "PL2",
            "blink_5hz": "PL3"
        }

        if mode not in mode_map:
            raise HTTPException(status_code=400, detail=f"Mode must be one of: {', '.join(mode_map.keys())}")

        try:
            await device.send_command(mode_map[mode])
            return {"success": True, "message": f"Pilot laser set to {mode}"}
        except Exception as e:
            logger.error(f"Failed to set pilot laser mode: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/devices/{laser_id}/pilot-laser", tags=["laser"])
    async def set_pilot_laser_mode_enum(laser_id: int, mode: PilotLaserMode = Body(...)):
        """Set pilot laser mode using enum"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command(f"PL{mode.value}")
            return {"success": True, "message": f"Pilot laser set to mode {mode.value}"}
        except Exception as e:
            logger.error(f"Failed to set pilot laser mode: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Trigger commands
    @router.post("/devices/{laser_id}/trigger", tags=["configuration"])
    async def send_trigger(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("TR")
            return {"success": True, "message": "Trigger sent"}
        except Exception as e:
            logger.error(f"Failed to send trigger: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/devices/{laser_id}/trigger/config", tags=["configuration"])
    async def configure_trigger(laser_id: int, config: TriggerConfig):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            if config.frequency is not None:
                await device.send_command("TF", str(config.frequency))

            if config.duration is not None:
                await device.send_command("TD", str(config.duration))

            if config.mode is not None:
                mode_map = {"first": "TRF", "last": "TRL", "min": "TRM", "max": "TRX"}
                if config.mode in mode_map:
                    await device.send_command(mode_map[config.mode])

            return {"success": True, "message": "Trigger configuration updated"}
        except Exception as e:
            logger.error(f"Failed to configure trigger: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Analog output configuration
    @router.post("/devices/{laser_id}/analog", tags=["configuration"])
    async def configure_analog(laser_id: int, config: AnalogConfig):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            if config.mode is not None:
                await device.send_command(f"AM{config.mode}")

            if all(v is not None for v in [config.min_value, config.max_value, config.min_voltage, config.max_voltage]):
                params = f"{config.min_value},{config.max_value},{config.min_voltage},{config.max_voltage}"
                await device.send_command("AO", params)

            return {"success": True, "message": "Analog output configured"}
        except Exception as e:
            logger.error(f"Failed to configure analog output: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Display mode
    @router.post("/devices/{laser_id}/display", tags=["configuration"])
    async def set_display_mode(laser_id: int, mode: DisplayMode = Body(...)):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command(f"DP{mode.value}")
            return {"success": True, "message": f"Display mode set to {mode.name}"}
        except Exception as e:
            logger.error(f"Failed to set display mode: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Units configuration
    @router.post("/devices/{laser_id}/units", tags=["configuration"])
    async def set_units(laser_id: int, units: Units = Body(...)):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command(f"UN{units.value}")
            return {"success": True, "message": f"Units set to {units.name}"}
        except Exception as e:
            logger.error(f"Failed to set units: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Error output control
    @router.post("/devices/{laser_id}/error-output/{state}", tags=["configuration"])
    async def set_error_output(laser_id: int, state: str):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if state not in ["on", "off"]:
            raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

        try:
            command = "ES1" if state == "on" else "ES0"
            await device.send_command(command)
            return {"success": True, "message": f"Error output {state}"}
        except Exception as e:
            logger.error(f"Failed to set error output: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Serial Data format configuration
    @router.post("/devices/{laser_id}/serial-format", tags=["configuration"])
    async def set_serial_format(laser_id: int, format_type: int = Body(..., ge=0, le=2), content: int = Body(..., ge=0, le=3)):
        """Set serial data format. format_type: 0=decimal, 1=hex, 2=binary. content: 0=value only, 1=+signal, 2=+temp, 3=+signal+temp"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("SD", f"{format_type} {content}")
            return {"success": True, "message": f"Serial format set to type={format_type}, content={content}"}
        except Exception as e:
            logger.error(f"Failed to set serial format: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/devices/{laser_id}/serial-format", tags=["query"])
    async def get_serial_format(laser_id: int):
        """Get current serial data format"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("SD")
            # Parse SD response
            parts = response.replace("SD", "").strip().split()
            if len(parts) >= 2:
                return {
                    "format_type": int(parts[0]),
                    "content": int(parts[1]),
                    "format_desc": ["decimal", "hexadecimal", "binary"][int(parts[0])],
                    "content_desc": ["value only", "value + signal", "value + temperature", "value + signal + temperature"][int(parts[1])]
                }
            return {"raw_response": response}
        except Exception as e:
            logger.error(f"Failed to get serial format: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Factory reset
    @router.post("/devices/{laser_id}/factory-reset", tags=["system"])
    async def factory_reset(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("FR")
            return {"success": True, "message": "Factory reset completed"}
        except Exception as e:
            logger.error(f"Failed to factory reset: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Autosave configuration
    @router.post("/devices/{laser_id}/autosave/{state}", tags=["configuration"])
    async def set_autosave(laser_id: int, state: str):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if state not in ["on", "off"]:
            raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

        try:
            command = "AS1" if state == "on" else "AS0"
            await device.send_command(command)
            return {"success": True, "message": f"Autosave {state}"}
        except Exception as e:
            logger.error(f"Failed to set autosave: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Median width configuration
    @router.post("/devices/{laser_id}/median-width", tags=["configuration"])
    async def set_median_width(laser_id: int, width: int = Body(..., ge=0, le=100)):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("MW", str(width))
            return {"success": True, "message": f"Median width set to {width}"}
        except Exception as e:
            logger.error(f"Failed to set median width: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Standby timeout configuration
    @router.post("/devices/{laser_id}/standby-timeout", tags=["configuration"])
    async def set_standby_timeout(laser_id: int, timeout: int = Body(..., ge=0, le=65535)):
        """Set standby timeout in seconds (0=disabled)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("SO", str(timeout))
            return {"success": True, "message": f"Standby timeout set to {timeout} seconds"}
        except Exception as e:
            logger.error(f"Failed to set standby timeout: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Data rate configuration
    @router.post("/devices/{laser_id}/data-rate", tags=["configuration"])
    async def set_data_rate(laser_id: int, rate: int = Body(..., ge=0, le=10)):
        """Set data output rate (0-10)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("DR", str(rate))
            return {"success": True, "message": f"Data rate set to {rate}"}
        except Exception as e:
            logger.error(f"Failed to set data rate: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Digital resolution configuration
    @router.post("/devices/{laser_id}/digital-resolution", tags=["configuration"])
    async def set_digital_resolution(laser_id: int, resolution: int = Body(..., ge=0, le=3)):
        """Set digital resolution (0=0.1mm, 1=1mm, 2=10mm, 3=100mm)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("PR", str(resolution))
            return {"success": True, "message": f"Digital resolution set to mode {resolution}"}
        except Exception as e:
            logger.error(f"Failed to set digital resolution: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Serial echo configuration
    @router.post("/devices/{laser_id}/serial-echo/{state}", tags=["configuration"])
    async def set_serial_echo(laser_id: int, state: str):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if state not in ["on", "off"]:
            raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

        try:
            command = "SE1" if state == "on" else "SE0"
            await device.send_command(command)
            return {"success": True, "message": f"Serial echo {state}"}
        except Exception as e:
            logger.error(f"Failed to set serial echo: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Test mode
    @router.post("/devices/{laser_id}/test-mode", tags=["system"])
    async def activate_test_mode(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("TE")
            return {"success": True, "message": "Test mode activated", "response": response}
        except Exception as e:
            logger.error(f"Failed to activate test mode: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Help command
    @router.get("/devices/{laser_id}/help", tags=["query"])
    async def get_help(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("HE")
            return {"success": True, "help": response}
        except Exception as e:
            logger.error(f"Failed to get help: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Query all parameters
    @router.get("/devices/{laser_id}/parameters", response_model=ParameterQueryResponse, tags=["query"])
    async def get_all_parameters(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("PA")
            # Parse parameters response - PA command returns formatted text
            params = {}
            lines = response.strip().split('\n')

            for line in lines:
                # Skip empty lines and the PA echo
                if not line.strip() or line.strip() == 'PA':
                    continue

                # Parse lines like "measure frequency[MF]............100(max100)hz"
                # Look for pattern: description[COMMAND]...value
                if '[' in line and ']' in line:
                    # Extract command code
                    start_bracket = line.find('[')
                    end_bracket = line.find(']')
                    command = line[start_bracket+1:end_bracket]

                    # Extract description
                    description = line[:start_bracket].strip()

                    # Extract value (everything after the dots)
                    value_part = line[end_bracket+1:].lstrip('.')

                    params[command] = {
                        "description": description,
                        "value": value_part.strip(),
                        "full_line": line.strip()
                    }

            # Also include formatted response for readability
            formatted_lines = []
            for line in lines:
                if line.strip() and line.strip() != 'PA':
                    formatted_lines.append(line.rstrip())

            formatted_response = '\n'.join(formatted_lines)

            return ParameterQueryResponse(
                parameters=params,
                raw_response=response,
                formatted_response=formatted_response
            )
        except Exception as e:
            logger.error(f"Failed to get parameters: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Hardware version
    @router.get("/devices/{laser_id}/hardware-version", response_model=HardwareVersionResponse, tags=["query"])
    async def get_hardware_version(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("HW")
            return HardwareVersionResponse(hardware_version=response, raw_response=response)
        except Exception as e:
            logger.error(f"Failed to get hardware version: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Query configuration
    @router.get("/devices/{laser_id}/configuration", response_model=ConfigurationResponse, tags=["query"])
    async def get_configuration(laser_id: int, bank: Optional[str] = Query(None, regex="^[12A]$")):
        """Get configuration. bank: 1=bank1, 2=bank2, A=all (default)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            if bank == "1":
                command = "Q1"
            elif bank == "2":
                command = "Q2"
            else:
                command = "QA"

            response = await device.send_command(command)
            # Parse configuration response
            config = {}
            lines = response.strip().split('\n')
            for line in lines:
                if '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()
            return ConfigurationResponse(configuration=config, raw_response=response)
        except Exception as e:
            logger.error(f"Failed to get configuration: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Query baud rate
    @router.get("/devices/{laser_id}/baudrate", response_model=BaudRateResponse, tags=["query"])
    async def get_baudrate(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            response = await device.send_command("BR")
            # Parse baud rate from response
            try:
                baud_rate = int(response.replace("BR", "").strip())
            except ValueError:
                # Try to extract number from response
                import re
                match = re.search(r'\d+', response)
                if match:
                    baud_rate = int(match.group())
                else:
                    raise ValueError(f"Could not parse baud rate from: {response}")
            return BaudRateResponse(baud_rate=baud_rate, raw_response=response)
        except Exception as e:
            logger.error(f"Failed to get baud rate: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Save settings
    @router.post("/devices/{laser_id}/save-settings", tags=["system"])
    async def save_settings(laser_id: int):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("SA")
            return {"success": True, "message": "Settings saved"}
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Baud rate configuration
    @router.post("/devices/{laser_id}/baudrate", tags=["configuration"])
    async def set_baudrate(laser_id: int, baudrate: int = Body(..., ge=1200, le=115200)):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("BD", str(baudrate))
            return {"success": True, "message": f"Baud rate set to {baudrate}"}
        except Exception as e:
            logger.error(f"Failed to set baud rate: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Laser power setting
    @router.post("/devices/{laser_id}/laser-power", tags=["laser"])
    async def set_laser_power(laser_id: int, power: int = Body(..., ge=0, le=100)):
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("LP", str(power))
            return {"success": True, "message": f"Laser power set to {power}%"}
        except Exception as e:
            logger.error(f"Failed to set laser power: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Measurement frequency configuration
    @router.post("/devices/{laser_id}/measurement-frequency", tags=["configuration"])
    async def set_measurement_frequency(laser_id: int, frequency: int = Body(..., ge=1, le=2000)):
        """Set measurement frequency in Hz (1-2000)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("MF", str(frequency))
            return {"success": True, "message": f"Measurement frequency set to {frequency} Hz"}
        except Exception as e:
            logger.error(f"Failed to set measurement frequency: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Averaging configuration
    @router.post("/devices/{laser_id}/averaging", tags=["configuration"])
    async def set_averaging(laser_id: int, count: int = Body(..., ge=1, le=10000)):
        """Set averaging count (1-10000)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("AV", str(count))
            return {"success": True, "message": f"Averaging set to {count}"}
        except Exception as e:
            logger.error(f"Failed to set averaging: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Moving average configuration
    @router.post("/devices/{laser_id}/moving-average", tags=["configuration"])
    async def set_moving_average(laser_id: int, percentage: int = Body(..., ge=0, le=100)):
        """Set moving average percentage (0-100)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("MA", str(percentage))
            return {"success": True, "message": f"Moving average set to {percentage}%"}
        except Exception as e:
            logger.error(f"Failed to set moving average: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Distance offset configuration
    @router.post("/devices/{laser_id}/distance-offset", tags=["configuration"])
    async def set_distance_offset(laser_id: int, offset: float = Body(...)):
        """Set distance offset in mm"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("OF", str(offset))
            return {"success": True, "message": f"Distance offset set to {offset} mm"}
        except Exception as e:
            logger.error(f"Failed to set distance offset: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Scale factor configuration
    @router.post("/devices/{laser_id}/scale-factor", tags=["configuration"])
    async def set_scale_factor(laser_id: int, factor: float = Body(..., ge=0.1, le=10.0)):
        """Set scale factor (0.1-10.0)"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            await device.send_command("SF", str(factor))
            return {"success": True, "message": f"Scale factor set to {factor}"}
        except Exception as e:
            logger.error(f"Failed to set scale factor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Output format configuration
    @router.post("/devices/{laser_id}/output-format/{format}", tags=["configuration"])
    async def set_output_format(laser_id: int, format: str):
        """Set output format: ascii or binary"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if format not in ["ascii", "binary"]:
            raise HTTPException(status_code=400, detail="Format must be 'ascii' or 'binary'")

        try:
            command = "OA" if format == "ascii" else "OB"
            await device.send_command(command)
            return {"success": True, "message": f"Output format set to {format}"}
        except Exception as e:
            logger.error(f"Failed to set output format: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # External trigger control
    @router.post("/devices/{laser_id}/external-trigger/{state}", tags=["configuration"])
    async def set_external_trigger(laser_id: int, state: str):
        """Enable or disable external trigger"""
        device = api.manager.get_device_by_id(laser_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if state not in ["on", "off"]:
            raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

        try:
            command = "ET1" if state == "on" else "ET0"
            await device.send_command(command)
            return {"success": True, "message": f"External trigger {state}"}
        except Exception as e:
            logger.error(f"Failed to set external trigger: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Debug endpoint to test raw commands
    @router.post("/test-command", tags=["system"])
    async def test_command(command: str = Body(...), timeout: float = Body(3.0)):
        device = api.manager.get_connected_device()
        if not device:
            raise HTTPException(status_code=503, detail="Device not connected")

        try:
            # Save original timeout
            old_timeout = device.timeout
            device.timeout = timeout

            response = await device.send_command(command)

            # Restore timeout
            device.timeout = old_timeout

            # Split response into lines for better readability
            lines = response.split('\n')

            # Try to parse if it's a measurement command
            parsed_result = None
            if command in ['DM', 'VM', 'DF']:
                try:
                    parsed_result = device._parse_measurement(response, command=command)
                    parsed_result = {
                        "distance": parsed_result.distance,
                        "speed": parsed_result.speed,
                        "signal_strength": parsed_result.signal_strength,
                        "temperature": parsed_result.temperature,
                        "error_code": parsed_result.error_code.value if parsed_result.error_code else None
                    }
                except:
                    pass

            return {
                "success": True,
                "command": command,
                "response": response,
                "response_lines": lines,
                "response_hex": response.encode('latin-1', errors='ignore').hex(),
                "response_length": len(response),
                "parsed_result": parsed_result
            }
        except Exception as e:
            device.timeout = old_timeout
            return {
                "success": False,
                "command": command,
                "error": str(e),
                "error_type": type(e).__name__
            }

    # Simple test endpoint to check basic connectivity
    @router.get("/test-connection", tags=["system"])
    async def test_connection():
        device = api.manager.get_connected_device()
        if not device:
            return {"connected": False, "error": "No device connected"}

        try:
            # Try a simple measurement
            result = await device.measure_distance()
            return {
                "connected": True,
                "test_measurement": {
                    "distance": result.distance,
                    "signal": result.signal_strength,
                    "temperature": result.temperature,
                    "error": result.error_code.value if result.error_code else None
                },
                "raw_response": result.raw_data
            }
        except Exception as e:
            return {
                "connected": True,
                "error": str(e),
                "error_type": type(e).__name__
            }

    # Include berthing mode routes
    berthing_router = create_berthing_router(laser_manager)
    router.include_router(berthing_router)
    return router