"""
Dynamic calibration adjustment endpoint
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any

from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger

router = APIRouter()


class CalibrationAdjustment(BaseModel):
    """Calibration adjustment parameters"""
    sensor_id: str = Field(..., description="Sensor ID")
    offset_meters: float = Field(..., description="Calibration offset in meters (can be negative)")
    center_tolerance_meters: float = Field(0.05, description="Center beam tolerance in meters", ge=0.01, le=0.5)


@router.post("/adjust", response_model=Dict[str, Any])
async def adjust_calibration(adjustment: CalibrationAdjustment) -> Dict[str, Any]:
    """
    Dynamically adjust calibration offset for a sensor
    
    Use this to match LiDAR readings with a reference laser rangefinder.
    If LiDAR reads higher than laser, use negative offset.
    """
    try:
        # Store calibration settings for the sensor
        if not hasattr(lidar_manager, 'calibration_settings'):
            lidar_manager.calibration_settings = {}
        
        lidar_manager.calibration_settings[adjustment.sensor_id] = {
            'offset': adjustment.offset_meters,
            'center_tolerance': adjustment.center_tolerance_meters
        }
        
        logger.info(f"Adjusted calibration for {adjustment.sensor_id}: "
                   f"offset={adjustment.offset_meters}m, "
                   f"tolerance={adjustment.center_tolerance_meters}m")
        
        return {
            "success": True,
            "sensor_id": adjustment.sensor_id,
            "calibration": {
                "offset_meters": adjustment.offset_meters,
                "offset_mm": adjustment.offset_meters * 1000,
                "center_tolerance_meters": adjustment.center_tolerance_meters,
                "center_tolerance_mm": adjustment.center_tolerance_meters * 1000
            },
            "message": f"Calibration adjusted. Offset: {adjustment.offset_meters*1000:.1f}mm"
        }
        
    except Exception as e:
        logger.error(f"Error adjusting calibration: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/current/{sensor_id}", response_model=Dict[str, Any])
async def get_calibration(sensor_id: str) -> Dict[str, Any]:
    """Get current calibration settings for a sensor"""
    
    if hasattr(lidar_manager, 'calibration_settings') and sensor_id in lidar_manager.calibration_settings:
        settings = lidar_manager.calibration_settings[sensor_id]
        return {
            "sensor_id": sensor_id,
            "calibration": {
                "offset_meters": settings['offset'],
                "offset_mm": settings['offset'] * 1000,
                "center_tolerance_meters": settings['center_tolerance'],
                "center_tolerance_mm": settings['center_tolerance'] * 1000
            }
        }
    else:
        # Return defaults
        return {
            "sensor_id": sensor_id,
            "calibration": {
                "offset_meters": -0.046,
                "offset_mm": -46.0,
                "center_tolerance_meters": 0.05,
                "center_tolerance_mm": 50.0
            },
            "message": "Using default calibration values"
        }


@router.post("/auto-calibrate", response_model=Dict[str, Any])
async def auto_calibrate(sensor_id: str, reference_distance: float) -> Dict[str, Any]:
    """
    Auto-calibrate by comparing current reading with reference distance
    
    Provide the actual distance from a reference laser rangefinder,
    and this will calculate the required offset.
    """
    try:
        # Get current berthing mode status
        status = await lidar_manager.get_berthing_mode_status()
        
        if sensor_id not in status.get('center_stats', {}):
            raise HTTPException(
                status_code=400,
                detail=f"No data available for sensor {sensor_id}. Ensure it's in berthing mode and streaming."
            )
        
        stats = status['center_stats'][sensor_id]
        current_distance = stats.get('stable_distance', 0)
        
        if current_distance == 0:
            raise HTTPException(
                status_code=400,
                detail="No valid distance reading available"
            )
        
        # Calculate required offset
        required_offset = reference_distance - current_distance
        
        # Apply the calibration
        if not hasattr(lidar_manager, 'calibration_settings'):
            lidar_manager.calibration_settings = {}
        
        lidar_manager.calibration_settings[sensor_id] = {
            'offset': required_offset,
            'center_tolerance': 0.05  # Default tolerance
        }
        
        return {
            "success": True,
            "sensor_id": sensor_id,
            "calibration": {
                "current_reading": current_distance,
                "reference_distance": reference_distance,
                "calculated_offset": required_offset,
                "offset_mm": required_offset * 1000
            },
            "message": f"Auto-calibrated with offset: {required_offset*1000:.1f}mm"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in auto-calibration: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))