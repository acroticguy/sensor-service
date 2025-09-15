"""
Berthing Mode Distance and Speed Measurement Module
Implements LiDAR-like Time-of-Flight (ToF) measurements mimicking:
- Livox Tele-15 LiDAR characteristics
- Jenoptik LDM 302 laser distance meter principles
"""

import time
import math
from typing import Dict, List, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field
import logging
import statistics

logger = logging.getLogger(__name__)

# Physical Constants
SPEED_OF_LIGHT = 299792458.0  # m/s in vacuum
SPEED_OF_LIGHT_AIR = 299705000.0  # m/s in standard air (20°C, 1 atm)

# Sensor Characteristics
@dataclass
class SensorSpecs:
    """Specifications matching real sensor characteristics"""
    # Livox Tele-15 specifications
    tele15_range_max: float = 500.0  # meters
    tele15_accuracy: float = 0.02  # ±2cm typical
    tele15_wavelength: float = 905e-9  # 905nm near-infrared
    tele15_pulse_rate: int = 240000  # points/second max
    tele15_beam_divergence: float = 0.28  # degrees
    
    # Jenoptik LDM 302 specifications
    ldm302_range_min: float = 0.2  # meters
    ldm302_range_max: float = 250.0  # meters
    ldm302_accuracy: float = 0.001  # ±1mm
    ldm302_measurement_rate: float = 1000.0  # Hz
    ldm302_beam_diameter: float = 0.006  # 6mm at exit
    
    # Calibration offsets
    calibration_offset: float = 0.040  # 40mm default offset


@dataclass
class EnvironmentalFactors:
    """Environmental compensation factors"""
    temperature: float = 20.0  # Celsius
    pressure: float = 1013.25  # hPa
    humidity: float = 50.0  # percent
    
    def get_refractive_index(self) -> float:
        """Calculate air refractive index for ToF correction"""
        # Edlén equation approximation for air
        n = 1.0 + (0.000273 * self.pressure / 1013.25) * (1.0 - 0.00366 * self.temperature)
        return n
    
    def get_speed_of_light_corrected(self) -> float:
        """Get corrected speed of light in current conditions"""
        n = self.get_refractive_index()
        return SPEED_OF_LIGHT / n


@dataclass
class MeasurementData:
    """Single measurement data point"""
    timestamp: float
    raw_distance: float
    tof_ns: float  # Time of flight in nanoseconds
    intensity: float
    confidence: float
    temperature: float = 20.0
    
    
class ToFDistanceCalculator:
    """Time-of-Flight based distance calculator"""
    
    def __init__(self, sensor_specs: SensorSpecs = None):
        self.specs = sensor_specs or SensorSpecs()
        self.env_factors = EnvironmentalFactors()
        self.calibration_offset = self.specs.calibration_offset
        
    def calculate_distance_from_tof(self, tof_ns: float) -> float:
        """
        Calculate distance from time-of-flight measurement
        
        Args:
            tof_ns: Round-trip time in nanoseconds
            
        Returns:
            Distance in meters
        """
        # Get environmentally corrected speed of light
        c = self.env_factors.get_speed_of_light_corrected()
        
        # Convert nanoseconds to seconds
        tof_s = tof_ns * 1e-9
        
        # Distance = (speed × time) / 2 (round trip)
        distance = (c * tof_s) / 2.0
        
        # Apply calibration offset
        distance += self.calibration_offset
        
        return distance
    
    def calculate_tof_from_distance(self, distance: float) -> float:
        """
        Calculate time-of-flight from distance (for simulation)
        
        Args:
            distance: Distance in meters
            
        Returns:
            Round-trip time in nanoseconds
        """
        # Remove calibration offset
        actual_distance = distance - self.calibration_offset
        
        # Get environmentally corrected speed of light
        c = self.env_factors.get_speed_of_light_corrected()
        
        # ToF = 2 × distance / speed
        tof_s = (2.0 * actual_distance) / c
        
        # Convert to nanoseconds
        tof_ns = tof_s * 1e9
        
        return tof_ns
    
    def set_environmental_conditions(self, temperature: float, pressure: float, humidity: float):
        """Update environmental compensation factors"""
        self.env_factors.temperature = temperature
        self.env_factors.pressure = pressure
        self.env_factors.humidity = humidity
        

class SpeedCalculator:
    """Differential and Doppler-based speed calculator"""
    
    def __init__(self, history_size: int = 10):
        self.distance_history: deque = deque(maxlen=history_size)
        self.doppler_enabled = False
        self.wavelength = 905e-9  # 905nm for Tele-15
        
    def add_measurement(self, timestamp: float, distance: float):
        """Add a distance measurement to history"""
        self.distance_history.append((timestamp, distance))
        
    def calculate_differential_speed(self) -> Tuple[float, float]:
        """
        Calculate speed using differential distance method
        
        Returns:
            Tuple of (speed_mps, confidence)
        """
        if len(self.distance_history) < 2:
            return 0.0, 0.0
            
        # Get first and last measurements
        t1, d1 = self.distance_history[0]
        t2, d2 = self.distance_history[-1]
        
        time_diff = t2 - t1
        if time_diff < 0.01:  # Need at least 10ms between measurements
            return 0.0, 0.0
            
        # Calculate speed
        speed = (d2 - d1) / time_diff
        
        # Calculate confidence based on measurement consistency
        if len(self.distance_history) >= 3:
            speeds = []
            for i in range(1, len(self.distance_history)):
                t_prev, d_prev = self.distance_history[i-1]
                t_curr, d_curr = self.distance_history[i]
                dt = t_curr - t_prev
                if dt > 0:
                    speeds.append((d_curr - d_prev) / dt)
            
            if speeds:
                std_dev = statistics.stdev(speeds) if len(speeds) > 1 else 0.0
                mean_speed = statistics.mean(speeds) if speeds else 0.0
                # Lower std deviation = higher confidence
                confidence = max(0.0, min(1.0, 1.0 - (std_dev / abs(mean_speed + 0.001))))
            else:
                confidence = 0.5
        else:
            confidence = 0.3
            
        return speed, confidence
    
    def calculate_doppler_speed(self, frequency_shift: float) -> float:
        """
        Calculate speed from Doppler frequency shift
        
        Args:
            frequency_shift: Measured frequency shift in Hz
            
        Returns:
            Speed in m/s
        """
        # Speed = (Δf × λ) / 2
        speed = (frequency_shift * self.wavelength) / 2.0
        return speed


class MeasurementFilter:
    """Advanced filtering and noise reduction"""
    
    def __init__(self, window_size: int = 5):
        self.window_size = window_size
        self.measurements: deque = deque(maxlen=window_size)
        
    def add_measurement(self, measurement: MeasurementData):
        """Add measurement to filter window"""
        self.measurements.append(measurement)
        
    def get_filtered_distance(self) -> Tuple[float, float]:
        """
        Get filtered distance using multiple techniques
        
        Returns:
            Tuple of (filtered_distance, confidence)
        """
        if not self.measurements:
            return 0.0, 0.0
            
        distances = [m.raw_distance for m in self.measurements]
        confidences = [m.confidence for m in self.measurements]
        
        # Remove outliers using IQR method
        sorted_distances = sorted(distances)
        n = len(sorted_distances)
        q1 = sorted_distances[n//4] if n >= 4 else min(distances)
        q3 = sorted_distances[3*n//4] if n >= 4 else max(distances)
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr
        
        filtered_distances = [d for d in distances if lower_bound <= d <= upper_bound]
        
        if not filtered_distances:
            filtered_distances = distances
            
        # Weighted average based on confidence
        weights = confidences[:len(filtered_distances)]
        if sum(weights) > 0:
            weighted_avg = sum(d * w for d, w in zip(filtered_distances, weights)) / sum(weights)
        else:
            weighted_avg = statistics.mean(filtered_distances) if filtered_distances else 0.0
            
        # Overall confidence
        confidence = statistics.mean(confidences) if confidences else 0.0
        
        return weighted_avg, confidence


class CalibrationSystem:
    """System for calibrating distance measurements"""
    
    def __init__(self):
        self.calibration_points: List[Tuple[float, float]] = []  # (measured, actual)
        self.calibration_curve: Optional[np.poly1d] = None
        self.systematic_error: float = 0.0
        
    def add_calibration_point(self, measured_distance: float, actual_distance: float):
        """Add a calibration reference point"""
        self.calibration_points.append((measured_distance, actual_distance))
        
        # Recalculate calibration if we have enough points
        if len(self.calibration_points) >= 3:
            self._calculate_calibration_curve()
            
    def _calculate_calibration_curve(self):
        """Calculate calibration curve from reference points"""
        measured = [p[0] for p in self.calibration_points]
        actual = [p[1] for p in self.calibration_points]
        
        # For now, use simple linear calibration without numpy
        # Calculate linear regression manually
        n = len(measured)
        sum_x = sum(measured)
        sum_y = sum(actual)
        sum_xy = sum(m * a for m, a in zip(measured, actual))
        sum_x2 = sum(m * m for m in measured)
        
        # Linear coefficients: y = ax + b
        if n * sum_x2 - sum_x * sum_x != 0:
            a = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x)
            b = (sum_y - a * sum_x) / n
            self.calibration_curve = (a, b)  # Store as tuple (slope, intercept)
        
        # Calculate systematic error
        errors = [actual[i] - measured[i] for i in range(len(measured))]
        self.systematic_error = statistics.mean(errors) if errors else 0.0
        
    def apply_calibration(self, measured_distance: float) -> float:
        """Apply calibration to a measured distance"""
        if self.calibration_curve is not None:
            # Apply linear correction (y = ax + b)
            a, b = self.calibration_curve
            corrected = a * measured_distance + b
        else:
            # Apply simple systematic error correction
            corrected = measured_distance + self.systematic_error
            
        return corrected


class BerthingMeasurementSystem:
    """Complete berthing measurement system"""
    
    def __init__(self, sensor_id: str):
        self.sensor_id = sensor_id
        self.tof_calculator = ToFDistanceCalculator()
        self.speed_calculator = SpeedCalculator()
        self.filter = MeasurementFilter()
        self.calibration = CalibrationSystem()
        
        # Measurement statistics
        self.total_measurements = 0
        self.measurement_rate = 0.0
        self.last_measurement_time = 0.0
        
        # Precision tracking (√N improvement)
        self.precision_measurements: deque = deque(maxlen=1000)
        
    def process_lidar_points(self, points: List[Dict], timestamp: float) -> Dict:
        """
        Process LiDAR point cloud data for berthing measurements
        
        Args:
            points: List of point cloud data
            timestamp: Measurement timestamp
            
        Returns:
            Dictionary with measurement results
        """
        if not points:
            return self._empty_result(timestamp)
            
        # Find center beam points (mimicking laser rangefinder)
        center_points = self._find_center_beam_points(points)
        
        if not center_points:
            return self._empty_result(timestamp)
            
        # Get primary distance measurement
        primary_point = center_points[0]
        
        # Simulate ToF measurement
        tof_ns = self.tof_calculator.calculate_tof_from_distance(primary_point['distance'])
        
        # Create measurement data
        measurement = MeasurementData(
            timestamp=timestamp,
            raw_distance=primary_point['distance'],
            tof_ns=tof_ns,
            intensity=primary_point.get('intensity', 100),
            confidence=self._calculate_confidence(center_points),
            temperature=self.tof_calculator.env_factors.temperature
        )
        
        # Add to filter
        self.filter.add_measurement(measurement)
        
        # Get filtered distance
        filtered_distance, filter_confidence = self.filter.get_filtered_distance()
        
        # Apply calibration
        calibrated_distance = self.calibration.apply_calibration(filtered_distance)
        
        # Calculate speed
        self.speed_calculator.add_measurement(timestamp, calibrated_distance)
        speed, speed_confidence = self.speed_calculator.calculate_differential_speed()
        
        # Update measurement statistics
        self._update_statistics(timestamp)
        
        # Calculate precision (√N improvement)
        precision = self._calculate_precision()
        
        return {
            "timestamp": timestamp,
            "sensor_id": self.sensor_id,
            "raw_distance": primary_point['distance'],
            "tof_ns": tof_ns,
            "filtered_distance": filtered_distance,
            "calibrated_distance": calibrated_distance,
            "speed_mps": speed,
            "speed_confidence": speed_confidence,
            "measurement_confidence": filter_confidence,
            "precision_mm": precision,
            "measurement_rate_hz": self.measurement_rate,
            "total_measurements": self.total_measurements,
            "center_points_used": len(center_points),
            "environmental_correction": {
                "temperature": self.tof_calculator.env_factors.temperature,
                "pressure": self.tof_calculator.env_factors.pressure,
                "humidity": self.tof_calculator.env_factors.humidity,
                "refractive_index": self.tof_calculator.env_factors.get_refractive_index()
            },
            "algorithm": "tof_berthing_measurement",
            "mode": "precision_ranging"
        }
        
    def _find_center_beam_points(self, points: List[Dict]) -> List[Dict]:
        """Find points that represent the center laser beam"""
        center_points = []
        
        for point in points:
            x = point.get('x', 0)
            y = point.get('y', 0)
            z = point.get('z', 0)
            
            # Check if point is in valid range
            if 0.5 < x <= 50.0:
                # Calculate radial distance from optical axis
                radial_distance = math.sqrt(y*y + z*z)
                
                # Tight tolerance for center beam (9cm)
                if radial_distance <= 0.09:
                    center_points.append({
                        'distance': x + self.tof_calculator.calibration_offset,
                        'radial_offset': radial_distance,
                        'intensity': point.get('intensity', 100),
                        'x': x, 'y': y, 'z': z
                    })
                    
        # Sort by radial distance (closest to center first)
        center_points.sort(key=lambda p: (p['radial_offset'], p['distance']))
        
        return center_points
        
    def _calculate_confidence(self, points: List[Dict]) -> float:
        """Calculate measurement confidence"""
        if not points:
            return 0.0
            
        # Factors affecting confidence:
        # 1. Number of points (more = better)
        point_factor = min(len(points) / 100.0, 1.0)
        
        # 2. Intensity consistency
        intensities = [p.get('intensity', 0) for p in points]
        if intensities and len(intensities) > 1:
            intensity_std = statistics.stdev(intensities)
            intensity_mean = statistics.mean(intensities)
            if intensity_mean > 0:
                intensity_factor = max(0, 1.0 - (intensity_std / intensity_mean))
            else:
                intensity_factor = 0.0
        else:
            intensity_factor = 0.0
            
        # 3. Distance consistency
        distances = [p['distance'] for p in points[:10]]  # Use top 10
        if len(distances) > 1:
            distance_std = statistics.stdev(distances)
            distance_factor = max(0, 1.0 - (distance_std / 0.1))  # 10cm variation = 0 confidence
        else:
            distance_factor = 0.5
            
        # Combined confidence
        confidence = (point_factor * 0.3 + intensity_factor * 0.3 + distance_factor * 0.4)
        
        return min(max(confidence, 0.0), 1.0)
        
    def _update_statistics(self, timestamp: float):
        """Update measurement statistics"""
        self.total_measurements += 1
        
        if self.last_measurement_time > 0:
            time_diff = timestamp - self.last_measurement_time
            if time_diff > 0:
                # Exponential moving average for rate
                alpha = 0.1
                instantaneous_rate = 1.0 / time_diff
                self.measurement_rate = alpha * instantaneous_rate + (1 - alpha) * self.measurement_rate
                
        self.last_measurement_time = timestamp
        
    def _calculate_precision(self) -> float:
        """Calculate measurement precision using √N improvement"""
        if self.total_measurements == 0:
            return self.tof_calculator.specs.tele15_accuracy * 1000  # Convert to mm
            
        # Base precision
        base_precision_mm = self.tof_calculator.specs.tele15_accuracy * 1000
        
        # √N improvement factor (capped at 1000 measurements)
        n = min(self.total_measurements, 1000)
        improvement_factor = math.sqrt(n)
        
        # Improved precision
        precision_mm = base_precision_mm / improvement_factor
        
        # Minimum achievable precision (1mm for LDM302)
        precision_mm = max(precision_mm, self.tof_calculator.specs.ldm302_accuracy * 1000)
        
        return precision_mm
        
    def _empty_result(self, timestamp: float) -> Dict:
        """Return empty result when no valid measurements"""
        return {
            "timestamp": timestamp,
            "sensor_id": self.sensor_id,
            "raw_distance": 0.0,
            "calibrated_distance": 0.0,
            "speed_mps": 0.0,
            "measurement_confidence": 0.0,
            "status": "no_valid_measurements"
        }
        
    def set_environmental_conditions(self, temperature: float, pressure: float, humidity: float):
        """Set environmental conditions for compensation"""
        self.tof_calculator.set_environmental_conditions(temperature, pressure, humidity)
        
    def add_calibration_reference(self, measured: float, actual: float):
        """Add a calibration reference point"""
        self.calibration.add_calibration_point(measured, actual)
        logger.info(f"Added calibration point: measured={measured}m, actual={actual}m")