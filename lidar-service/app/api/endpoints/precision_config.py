"""
Precision speed calculation configuration for vessel berthing
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any

from ...services.lidar_manager import lidar_manager
from ...core.logging_config import logger

router = APIRouter()


class PrecisionConfig(BaseModel):
    """Configuration for precision speed calculator"""
    sensor_id: str = Field(..., description="Sensor ID")
    sample_count: int = Field(100, description="Number of samples for averaging (SA parameter)", ge=10, le=1000)
    measurement_frequency: float = Field(100.0, description="Measurement frequency in Hz (MF parameter)", gt=0, le=1000)
    distance_std_dev: float = Field(0.002, description="Standard deviation of single distance measurement in meters", gt=0, le=0.1)
    system_constant: float = Field(1300.0, description="System-specific constant from formula", gt=0)


@router.post("/configure", response_model=Dict[str, Any])
async def configure_precision_calculator(config: PrecisionConfig) -> Dict[str, Any]:
    """
    Configure precision speed calculator parameters
    
    Based on formula: σᵥ = σd · (f/(N·√N)) · (1/√system_constant)
    """
    try:
        # Check if sensor exists
        if config.sensor_id not in lidar_manager.sensors:
            raise HTTPException(
                status_code=404,
                detail=f"Sensor {config.sensor_id} not found"
            )
        
        # Create new calculator with specified parameters
        from app.services.precision_speed_calculator import PrecisionSpeedCalculator
        
        calculator = PrecisionSpeedCalculator(
            sensor_id=config.sensor_id,
            sample_count=config.sample_count,
            measurement_frequency=config.measurement_frequency,
            distance_std_dev=config.distance_std_dev,
            system_constant=config.system_constant
        )
        
        # Replace existing calculator
        lidar_manager.precision_calculators[config.sensor_id] = calculator
        
        # Calculate theoretical precision
        import math
        speed_precision = (
            config.distance_std_dev * 
            (config.measurement_frequency / (config.sample_count * math.sqrt(config.sample_count))) * 
            (1.0 / math.sqrt(config.system_constant))
        )
        
        # Calculate measurement duration
        measurement_duration = config.sample_count / config.measurement_frequency
        
        return {
            "success": True,
            "sensor_id": config.sensor_id,
            "configuration": {
                "sample_count": config.sample_count,
                "measurement_frequency_hz": config.measurement_frequency,
                "distance_std_dev_mm": config.distance_std_dev * 1000,
                "system_constant": config.system_constant
            },
            "theoretical_performance": {
                "speed_precision_mm_s": round(speed_precision * 1000, 3),
                "measurement_duration_s": round(measurement_duration, 2),
                "distance_precision_improvement": f"√{config.sample_count} = {math.sqrt(config.sample_count):.1f}x"
            },
            "message": f"Precision calculator configured for {config.sample_count} samples at {config.measurement_frequency}Hz"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error configuring precision calculator: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{sensor_id}", response_model=Dict[str, Any])
async def get_precision_status(sensor_id: str) -> Dict[str, Any]:
    """Get current precision calculation status for a sensor"""
    
    try:
        # Check if calculator exists
        if sensor_id not in lidar_manager.precision_calculators:
            return {
                "sensor_id": sensor_id,
                "configured": False,
                "message": "No precision calculator configured for this sensor"
            }
        
        calculator = lidar_manager.precision_calculators[sensor_id]
        
        # Get current state
        samples = len(calculator.distance_buffer)
        progress = (samples / calculator.sample_count) * 100
        
        return {
            "sensor_id": sensor_id,
            "configured": True,
            "current_state": {
                "samples_collected": samples,
                "samples_required": calculator.sample_count,
                "progress_percent": round(progress, 1),
                "is_measuring": calculator.is_measuring,
                "measurement_complete": samples >= calculator.sample_count
            },
            "current_results": {
                "distance": round(calculator.current_distance, 4),
                "distance_precision_mm": round(calculator.distance_precision * 1000, 2),
                "speed": round(calculator.current_speed, 4),
                "speed_precision_mm_s": round(calculator.speed_precision * 1000, 2)
            },
            "configuration": {
                "sample_count": calculator.sample_count,
                "measurement_frequency_hz": calculator.measurement_frequency,
                "distance_std_dev_mm": calculator.distance_std_dev * 1000,
                "system_constant": calculator.system_constant
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting precision status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset/{sensor_id}", response_model=Dict[str, Any])
async def reset_precision_calculator(sensor_id: str) -> Dict[str, Any]:
    """Reset precision calculator to start new measurement cycle"""
    
    try:
        if sensor_id not in lidar_manager.precision_calculators:
            raise HTTPException(
                status_code=404,
                detail=f"No precision calculator found for sensor {sensor_id}"
            )
        
        calculator = lidar_manager.precision_calculators[sensor_id]
        calculator.reset()
        
        return {
            "success": True,
            "sensor_id": sensor_id,
            "message": "Precision calculator reset - new measurement cycle will begin"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting precision calculator: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/comparison/{sensor_id}", response_model=Dict[str, Any])
async def compare_with_laser(sensor_id: str, laser_distance: float, laser_speed: float) -> Dict[str, Any]:
    """
    Compare LiDAR measurements with reference laser rangefinder
    
    Useful for validating and calibrating the system against ground truth.
    """
    try:
        # Get current berthing mode status
        status = await lidar_manager.get_berthing_mode_status()
        
        if sensor_id not in status.get('center_stats', {}):
            raise HTTPException(
                status_code=400,
                detail=f"No data available for sensor {sensor_id}"
            )
        
        stats = status['center_stats'][sensor_id]
        
        # Calculate errors
        distance_error = stats.get('stable_distance', 0) - laser_distance
        speed_error = stats.get('speed_mps', 0) - laser_speed
        
        # Calculate percentage errors
        distance_error_percent = (abs(distance_error) / laser_distance) * 100 if laser_distance != 0 else 0
        speed_error_percent = (abs(speed_error) / abs(laser_speed)) * 100 if laser_speed != 0 else 0
        
        return {
            "sensor_id": sensor_id,
            "lidar_measurements": {
                "distance": stats.get('stable_distance', 0),
                "speed": stats.get('speed_mps', 0),
                "speed_confidence": stats.get('speed_confidence', 0),
                "samples_collected": stats.get('samples_collected', 0),
                "measurement_complete": stats.get('measurement_complete', False)
            },
            "laser_reference": {
                "distance": laser_distance,
                "speed": laser_speed
            },
            "errors": {
                "distance_error_m": round(distance_error, 4),
                "distance_error_mm": round(distance_error * 1000, 1),
                "distance_error_percent": round(distance_error_percent, 2),
                "speed_error_m_s": round(speed_error, 4),
                "speed_error_mm_s": round(speed_error * 1000, 1),
                "speed_error_percent": round(speed_error_percent, 2)
            },
            "validation": {
                "distance_within_spec": abs(distance_error) < 0.005,  # 5mm tolerance
                "speed_within_spec": abs(speed_error) < 0.002,  # 2mm/s tolerance
                "ready_for_berthing": stats.get('measurement_complete', False) and stats.get('speed_confidence', 0) > 0.8
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error comparing with laser: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))