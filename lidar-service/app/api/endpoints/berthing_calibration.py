"""
API endpoints for berthing mode calibration and environmental settings
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
import logging

from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger

router = APIRouter()


class EnvironmentalConditions(BaseModel):
    """Environmental conditions for ToF compensation"""
    sensor_id: str = Field(..., description="Sensor ID")
    temperature: float = Field(..., description="Temperature in Celsius", ge=-50, le=100)
    pressure: float = Field(..., description="Atmospheric pressure in hPa", ge=800, le=1200)
    humidity: float = Field(..., description="Relative humidity in percent", ge=0, le=100)


class CalibrationReference(BaseModel):
    """Calibration reference point"""
    sensor_id: str = Field(..., description="Sensor ID")
    measured_distance: float = Field(..., description="Measured distance in meters", gt=0, le=500)
    actual_distance: float = Field(..., description="Actual distance in meters", gt=0, le=500)


class CalibrationConfig(BaseModel):
    """Full calibration configuration"""
    sensor_id: str = Field(..., description="Sensor ID")
    calibration_points: list[tuple[float, float]] = Field(
        ..., 
        description="List of (measured, actual) distance pairs"
    )
    environmental_conditions: Optional[EnvironmentalConditions] = None


@router.post("/environmental", response_model=Dict[str, Any])
async def set_environmental_conditions(
    conditions: EnvironmentalConditions
) -> Dict[str, Any]:
    """
    Set environmental conditions for ToF compensation
    
    Environmental factors affect the speed of light and thus ToF measurements.
    This endpoint allows setting current conditions for accurate compensation.
    """
    try:
        # Check if sensor exists
        if conditions.sensor_id not in lidar_manager.sensors:
            raise HTTPException(
                status_code=404,
                detail=f"Sensor {conditions.sensor_id} not found"
            )
        
        # Set environmental conditions
        lidar_manager.set_environmental_conditions(
            conditions.sensor_id,
            conditions.temperature,
            conditions.pressure,
            conditions.humidity
        )
        
        return {
            "success": True,
            "message": f"Environmental conditions updated for sensor {conditions.sensor_id}",
            "conditions": {
                "temperature": conditions.temperature,
                "pressure": conditions.pressure,
                "humidity": conditions.humidity
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting environmental conditions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calibration/reference", response_model=Dict[str, Any])
async def add_calibration_reference(
    reference: CalibrationReference
) -> Dict[str, Any]:
    """
    Add a single calibration reference point
    
    Calibration references help correct systematic errors in distance measurements.
    Add known actual distances at measured points to build a calibration curve.
    """
    try:
        # Add calibration reference
        lidar_manager.add_calibration_reference(
            reference.sensor_id,
            reference.measured_distance,
            reference.actual_distance
        )
        
        error = reference.actual_distance - reference.measured_distance
        error_percent = (error / reference.actual_distance) * 100
        
        return {
            "success": True,
            "message": f"Calibration reference added for sensor {reference.sensor_id}",
            "reference": {
                "measured": reference.measured_distance,
                "actual": reference.actual_distance,
                "error": error,
                "error_percent": error_percent
            }
        }
        
    except Exception as e:
        logger.error(f"Error adding calibration reference: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calibration/batch", response_model=Dict[str, Any])
async def set_calibration_config(
    config: CalibrationConfig
) -> Dict[str, Any]:
    """
    Set complete calibration configuration with multiple reference points
    
    Provide multiple calibration points to build an accurate calibration curve.
    Minimum 3 points recommended for polynomial fitting.
    """
    try:
        # Add all calibration points
        for measured, actual in config.calibration_points:
            lidar_manager.add_calibration_reference(
                config.sensor_id,
                measured,
                actual
            )
        
        # Set environmental conditions if provided
        if config.environmental_conditions:
            lidar_manager.set_environmental_conditions(
                config.sensor_id,
                config.environmental_conditions.temperature,
                config.environmental_conditions.pressure,
                config.environmental_conditions.humidity
            )
        
        # Calculate overall error statistics
        errors = [(actual - measured) for measured, actual in config.calibration_points]
        avg_error = sum(errors) / len(errors) if errors else 0
        
        return {
            "success": True,
            "message": f"Calibration configuration set for sensor {config.sensor_id}",
            "calibration": {
                "points_added": len(config.calibration_points),
                "average_error": avg_error,
                "environmental_conditions_set": config.environmental_conditions is not None
            }
        }
        
    except Exception as e:
        logger.error(f"Error setting calibration config: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calibration/status/{sensor_id}", response_model=Dict[str, Any])
async def get_calibration_status(
    sensor_id: str
) -> Dict[str, Any]:
    """
    Get current calibration status for a sensor
    
    Returns information about calibration points and environmental settings.
    """
    try:
        # Check if sensor has measurement system
        if sensor_id not in lidar_manager.berthing_measurement_systems:
            return {
                "sensor_id": sensor_id,
                "calibrated": False,
                "message": "No measurement system initialized for this sensor"
            }
        
        measurement_system = lidar_manager.berthing_measurement_systems[sensor_id]
        
        # Get calibration info
        calibration_points = measurement_system.calibration.calibration_points
        has_curve = measurement_system.calibration.calibration_curve is not None
        systematic_error = measurement_system.calibration.systematic_error
        
        # Get environmental conditions
        env = measurement_system.tof_calculator.env_factors
        
        return {
            "sensor_id": sensor_id,
            "calibrated": has_curve or len(calibration_points) > 0,
            "calibration": {
                "points_count": len(calibration_points),
                "has_curve": has_curve,
                "systematic_error": systematic_error,
                "points": calibration_points[:10]  # Return first 10 points
            },
            "environmental": {
                "temperature": env.temperature,
                "pressure": env.pressure,
                "humidity": env.humidity,
                "refractive_index": env.get_refractive_index(),
                "speed_of_light_corrected": env.get_speed_of_light_corrected()
            },
            "measurement_stats": {
                "total_measurements": measurement_system.total_measurements,
                "measurement_rate_hz": measurement_system.measurement_rate,
                "precision_mm": measurement_system._calculate_precision()
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting calibration status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/calibration/{sensor_id}", response_model=Dict[str, Any])
async def clear_calibration(
    sensor_id: str
) -> Dict[str, Any]:
    """
    Clear calibration data for a sensor
    
    Removes all calibration points and resets to default settings.
    """
    try:
        # Check if sensor has measurement system
        if sensor_id in lidar_manager.berthing_measurement_systems:
            # Reset calibration
            measurement_system = lidar_manager.berthing_measurement_systems[sensor_id]
            measurement_system.calibration.calibration_points = []
            measurement_system.calibration.calibration_curve = None
            measurement_system.calibration.systematic_error = 0.0
            
            # Reset environmental to defaults
            measurement_system.tof_calculator.env_factors.temperature = 20.0
            measurement_system.tof_calculator.env_factors.pressure = 1013.25
            measurement_system.tof_calculator.env_factors.humidity = 50.0
            
            return {
                "success": True,
                "message": f"Calibration cleared for sensor {sensor_id}"
            }
        else:
            return {
                "success": False,
                "message": f"No calibration data found for sensor {sensor_id}"
            }
        
    except Exception as e:
        logger.error(f"Error clearing calibration: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))