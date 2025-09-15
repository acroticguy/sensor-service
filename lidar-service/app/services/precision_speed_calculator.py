"""
Precision Speed Calculator for Vessel Berthing System
Implements statistical averaging similar to laser interferometry systems
Based on formula: σᵥ = σd · (f/(N·√N)) · (1/√1300)
"""

import time
import math
import statistics
from typing import List, Tuple, Optional, Dict
from collections import deque
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MeasurementSample:
    """Single measurement sample"""
    timestamp: float
    distance: float
    quality: float  # 0-1 quality indicator


class PrecisionSpeedCalculator:
    """
    High-precision speed calculator using statistical averaging
    Similar to laser rangefinder systems (e.g., Jenoptik LDM series)
    """
    
    def __init__(
        self,
        sensor_id: str,
        sample_count: int = 100,  # Number of samples for averaging (SA parameter)
        measurement_frequency: float = 100.0,  # Hz (MF parameter)
        distance_std_dev: float = 0.002,  # 2mm standard deviation for single measurement
        system_constant: float = 1300.0  # System-specific constant
    ):
        self.sensor_id = sensor_id
        self.sample_count = sample_count
        self.measurement_frequency = measurement_frequency
        self.distance_std_dev = distance_std_dev
        self.system_constant = system_constant
        
        # Measurement buffers
        self.distance_buffer: deque = deque(maxlen=sample_count)
        self.speed_buffer: deque = deque(maxlen=sample_count)
        self.measurement_history: deque = deque(maxlen=sample_count * 2)
        
        # State tracking
        self.last_update_time = 0.0
        self.measurement_start_time = 0.0
        self.total_samples = 0
        self.is_measuring = False
        
        # Results
        self.current_distance = 0.0
        self.current_speed = 0.0
        self.distance_precision = 0.0
        self.speed_precision = 0.0
        
        logger.info(f"Initialized precision speed calculator for {sensor_id}")
        logger.info(f"Configuration: N={sample_count}, f={measurement_frequency}Hz, σd={distance_std_dev*1000}mm")
    
    def add_measurement(self, distance: float, timestamp: float) -> Dict:
        """
        Add a new distance measurement and calculate speed with precision
        
        Args:
            distance: Measured distance in meters
            timestamp: Measurement timestamp
            
        Returns:
            Dict with current measurements and precision values
        """
        # Start measurement cycle if needed
        if not self.is_measuring or timestamp - self.measurement_start_time > 2.0:
            self._start_new_measurement_cycle(timestamp)
        
        # Add to buffers
        self.distance_buffer.append(distance)
        self.measurement_history.append(MeasurementSample(timestamp, distance, 1.0))
        self.total_samples += 1
        
        # Calculate current values
        result = self._calculate_with_precision(timestamp)
        
        self.last_update_time = timestamp
        
        return result
    
    def _start_new_measurement_cycle(self, timestamp: float):
        """Start a new measurement cycle"""
        self.measurement_start_time = timestamp
        self.is_measuring = True
        self.distance_buffer.clear()
        self.speed_buffer.clear()
        logger.debug(f"Started new measurement cycle at {timestamp}")
    
    def _calculate_with_precision(self, current_time: float) -> Dict:
        """Calculate distance and speed with precision estimates"""
        
        # Need minimum samples for calculation
        if len(self.distance_buffer) < 2:
            return self._empty_result(current_time)
        
        # Calculate averaged distance
        distances = list(self.distance_buffer)
        self.current_distance = statistics.mean(distances)
        
        # Calculate distance standard deviation
        if len(distances) > 1:
            distance_std = statistics.stdev(distances)
        else:
            distance_std = self.distance_std_dev
        
        # Calculate distance precision (improves with √N)
        n = len(distances)
        self.distance_precision = distance_std / math.sqrt(n)
        
        # Calculate speed using multiple methods
        speed_estimates = []
        
        # Method 1: Linear regression over full buffer
        if len(self.measurement_history) >= 3:
            speed_lr = self._calculate_speed_linear_regression()
            if speed_lr is not None:
                speed_estimates.append(speed_lr)
        
        # Method 2: First derivative with smoothing
        if len(self.measurement_history) >= 10:
            speed_fd = self._calculate_speed_first_derivative()
            if speed_fd is not None:
                speed_estimates.append(speed_fd)
        
        # Method 3: Endpoint comparison
        if len(self.measurement_history) >= self.sample_count // 2:
            speed_ep = self._calculate_speed_endpoints()
            if speed_ep is not None:
                speed_estimates.append(speed_ep)
        
        # Combine speed estimates
        if speed_estimates:
            # Use median for robustness against outliers
            self.current_speed = statistics.median(speed_estimates)
            
            # Calculate speed precision using the formula
            # σᵥ = σd · (f/(N·√N)) · (1/√system_constant)
            self.speed_precision = (
                self.distance_std_dev * 
                (self.measurement_frequency / (n * math.sqrt(n))) * 
                (1.0 / math.sqrt(self.system_constant))
            )
            
            # Apply deadband for stationary objects
            if abs(self.current_speed) < self.speed_precision * 2:
                self.current_speed = 0.0
                speed_confidence = 1.0  # High confidence it's stationary
            else:
                # Calculate confidence based on estimate consistency
                if len(speed_estimates) > 1:
                    speed_std = statistics.stdev(speed_estimates)
                    speed_confidence = max(0.0, 1.0 - (speed_std / (abs(self.current_speed) + 0.001)))
                else:
                    speed_confidence = 0.5
        else:
            self.current_speed = 0.0
            self.speed_precision = 1000.0  # Large but finite value (1 m/s precision when no data)
            speed_confidence = 0.0
        
        # Measurement quality metrics
        measurement_duration = current_time - self.measurement_start_time
        samples_per_second = n / max(measurement_duration, 0.001)
        
        return {
            "distance": round(self.current_distance, 4),
            "distance_precision_mm": round(self.distance_precision * 1000, 2),
            "speed": round(self.current_speed, 4),
            "speed_precision_mm_s": round(self.speed_precision * 1000, 2),
            "speed_confidence": round(speed_confidence, 3),
            "samples_collected": n,
            "samples_required": self.sample_count,
            "measurement_complete": n >= self.sample_count,
            "measurement_duration": round(measurement_duration, 2),
            "effective_frequency_hz": round(samples_per_second, 1),
            "algorithm": "precision_statistical_averaging",
            "deadband_applied": abs(self.current_speed) < self.speed_precision * 2
        }
    
    def _calculate_speed_linear_regression(self) -> Optional[float]:
        """Calculate speed using linear regression"""
        samples = list(self.measurement_history)
        if len(samples) < 3:
            return None
        
        # Extract times and distances
        times = [s.timestamp - samples[0].timestamp for s in samples]
        distances = [s.distance for s in samples]
        
        # Linear regression
        n = len(times)
        if n == 0 or max(times) == 0:
            return None
        
        sum_t = sum(times)
        sum_d = sum(distances)
        sum_td = sum(t * d for t, d in zip(times, distances))
        sum_t2 = sum(t * t for t in times)
        
        denominator = n * sum_t2 - sum_t * sum_t
        if denominator == 0:
            return None
        
        # Slope is the speed
        speed = (n * sum_td - sum_t * sum_d) / denominator
        
        return speed
    
    def _calculate_speed_first_derivative(self) -> Optional[float]:
        """Calculate speed using smoothed first derivative"""
        samples = list(self.measurement_history)
        if len(samples) < 10:
            return None
        
        # Calculate instantaneous speeds
        speeds = []
        for i in range(1, len(samples)):
            dt = samples[i].timestamp - samples[i-1].timestamp
            if dt > 0:
                dd = samples[i].distance - samples[i-1].distance
                speeds.append(dd / dt)
        
        if not speeds:
            return None
        
        # Apply moving average smoothing
        window_size = min(5, len(speeds))
        smoothed_speeds = []
        
        for i in range(len(speeds) - window_size + 1):
            window = speeds[i:i + window_size]
            smoothed_speeds.append(statistics.mean(window))
        
        if smoothed_speeds:
            return statistics.median(smoothed_speeds)
        
        return None
    
    def _calculate_speed_endpoints(self) -> Optional[float]:
        """Calculate speed using endpoint comparison"""
        samples = list(self.measurement_history)
        if len(samples) < 2:
            return None
        
        # Use first and last quarter of samples
        quarter = max(1, len(samples) // 4)
        
        first_samples = samples[:quarter]
        last_samples = samples[-quarter:]
        
        if not first_samples or not last_samples:
            return None
        
        # Average positions and times
        first_time = statistics.mean([s.timestamp for s in first_samples])
        first_dist = statistics.mean([s.distance for s in first_samples])
        
        last_time = statistics.mean([s.timestamp for s in last_samples])
        last_dist = statistics.mean([s.distance for s in last_samples])
        
        dt = last_time - first_time
        if dt > 0:
            return (last_dist - first_dist) / dt
        
        return None
    
    def _empty_result(self, timestamp: float) -> Dict:
        """Return empty result when insufficient data"""
        return {
            "distance": 0.0,
            "distance_precision_mm": 1000.0,  # 1m precision when no data
            "speed": 0.0,
            "speed_precision_mm_s": 1000.0,  # 1m/s precision when no data
            "speed_confidence": 0.0,
            "samples_collected": len(self.distance_buffer),
            "samples_required": self.sample_count,
            "measurement_complete": False,
            "measurement_duration": timestamp - self.measurement_start_time if self.is_measuring else 0.0,
            "effective_frequency_hz": 0.0,
            "algorithm": "precision_statistical_averaging",
            "status": "collecting_samples"
        }
    
    def reset(self):
        """Reset the calculator"""
        self.distance_buffer.clear()
        self.speed_buffer.clear()
        self.measurement_history.clear()
        self.is_measuring = False
        self.current_distance = 0.0
        self.current_speed = 0.0
        logger.info(f"Reset precision calculator for {self.sensor_id}")