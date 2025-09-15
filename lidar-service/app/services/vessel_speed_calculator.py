"""
Vessel Speed Calculator for Berthing Operations
Optimized for detecting slow vessel movements during docking
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
class VesselMovementProfile:
    """Typical vessel movement profiles during berthing"""
    # Speed ranges for different berthing phases (m/s)
    APPROACH_SPEED = 0.5  # 50 cm/s initial approach
    SLOW_APPROACH = 0.1   # 10 cm/s slow approach
    FINAL_APPROACH = 0.03 # 3 cm/s final approach
    CREEP_SPEED = 0.01    # 1 cm/s creeping
    CONTACT_SPEED = 0.005 # 5 mm/s contact speed
    
    # Noise thresholds (adjusted for production reliability)
    STATIONARY_NOISE = 0.002  # 2 mm/s - increased to reduce false movements
    MOVEMENT_THRESHOLD = 0.003  # 3 mm/s - above this is real movement
    
    @classmethod
    def classify_speed(cls, speed_mps: float) -> str:
        """Classify vessel speed into berthing phases"""
        abs_speed = abs(speed_mps)
        
        if abs_speed < cls.STATIONARY_NOISE:
            return "stationary"
        elif abs_speed < cls.CONTACT_SPEED:
            return "contact"
        elif abs_speed < cls.CREEP_SPEED:
            return "creeping"
        elif abs_speed < cls.FINAL_APPROACH:
            return "final_approach"
        elif abs_speed < cls.SLOW_APPROACH:
            return "slow_approach"
        elif abs_speed < cls.APPROACH_SPEED:
            return "approach"
        else:
            return "fast_approach"


class VesselSpeedCalculator:
    """
    Specialized speed calculator for vessel berthing operations
    Handles slow speeds and distinguishes real movement from noise
    Uses precision formula: σᵥ = σd · (f/(N·√N)) · (1/√1300)
    """
    
    def __init__(
        self,
        sensor_id: str,
        window_size: int = 20,  # Increased for smoother output (SA parameter)
        history_size: int = 200,  # More history for better trend analysis
        measurement_frequency: float = 100.0,  # MF parameter in Hz
        distance_std_dev: float = 0.002  # σd in meters (2mm for production)
    ):
        self.sensor_id = sensor_id
        self.window_size = window_size  # N (SA parameter)
        self.history_size = history_size
        self.measurement_frequency = measurement_frequency  # f (MF parameter)
        self.distance_std_dev = distance_std_dev  # σd
        
        # Calculate speed precision using the formula
        # σᵥ = σd · (f/(N·√N)) · (1/√1300)
        self.speed_precision = self._calculate_speed_precision()
        
        # Measurement buffers
        self.distance_history: deque = deque(maxlen=history_size)
        self.speed_history: deque = deque(maxlen=history_size)
        self.filtered_speeds: deque = deque(maxlen=window_size)
        self.distance_measurements: deque = deque(maxlen=window_size)  # For SA averaging
        
        # Vessel movement detection
        self.movement_detected = False
        self.movement_direction = 0  # -1: away, 0: stationary, 1: approaching
        self.consistent_movement_count = 0
        
        # Kalman filter state for smooth speed estimation
        self.kalman_speed = 0.0
        self.kalman_variance = 1.0
        
        logger.info(f"Initialized vessel speed calculator for {sensor_id}")
        logger.info(f"Speed precision (σᵥ): {self.speed_precision:.6f} m/s ({self.speed_precision*1000:.3f} mm/s)")
        logger.info(f"MF={measurement_frequency}Hz, SA={window_size}, σd={distance_std_dev*1000}mm")
    
    def _calculate_speed_precision(self) -> float:
        """
        Calculate speed measurement precision using the formula:
        σᵥ = σd · (f/(N·√N)) · (1/√1300)
        
        Returns:
            Speed precision in m/s
        """
        N = self.window_size  # Number of averaged measurements (SA)
        f = self.measurement_frequency  # Measurement frequency (MF)
        sigma_d = self.distance_std_dev  # Distance measurement std dev
        
        # Apply the formula
        speed_precision = sigma_d * (f / (N * math.sqrt(N))) * (1 / math.sqrt(1300))
        
        return speed_precision
    
    def add_measurement(self, distance: float, timestamp: float) -> Dict:
        """
        Add distance measurement and calculate vessel speed
        
        Args:
            distance: Distance to vessel in meters
            timestamp: Measurement timestamp
            
        Returns:
            Dict with speed calculations and movement analysis
        """
        # Add to history and SA buffer
        self.distance_history.append((timestamp, distance))
        self.distance_measurements.append((timestamp, distance))
        
        if len(self.distance_history) < 2:
            return self._empty_result(timestamp, distance)
        
        # Calculate multiple speed estimates
        instant_speed = self._calculate_instant_speed()
        sa_averaged_speed = self._calculate_sa_averaged_speed()  # Using SA parameter
        windowed_speed = self._calculate_windowed_speed()
        trend_speed = self._calculate_trend_speed()
        kalman_speed = self._update_kalman_filter(sa_averaged_speed)  # Use SA averaged speed
        
        # Analyze movement pattern using SA averaged speed
        movement_analysis = self._analyze_movement(sa_averaged_speed, windowed_speed, trend_speed)
        
        # Determine final speed based on movement analysis
        if movement_analysis['is_moving']:
            # Use Kalman filtered speed for smooth output
            final_speed = kalman_speed
            confidence = movement_analysis['confidence']
        else:
            # Check if SA averaged speed shows movement even if not confirmed
            if abs(sa_averaged_speed) > self.speed_precision:  # Use calculated precision as threshold
                # Report the SA averaged speed but with medium confidence
                final_speed = sa_averaged_speed
                # Calculate confidence based on speed magnitude relative to precision
                confidence = min(abs(sa_averaged_speed) / (self.speed_precision * 3), 0.7)
            else:
                # Stationary - report zero speed
                final_speed = 0.0
                confidence = 1.0  # High confidence in stationary state
        
        # Classify movement phase
        phase = VesselMovementProfile.classify_speed(final_speed)
        
        # Calculate approach ETA if moving toward berth
        eta_seconds = self._calculate_eta(distance, final_speed) if final_speed < 0 else None
        
        return {
            "timestamp": timestamp,
            "distance": round(distance, 4),
            "instant_speed": round(instant_speed, 4),
            "sa_averaged_speed": round(sa_averaged_speed, 4),
            "windowed_speed": round(windowed_speed, 4),
            "trend_speed": round(trend_speed, 4),
            "kalman_speed": round(kalman_speed, 4),
            "final_speed": round(final_speed, 4),
            "speed_mm_s": round(final_speed * 1000, 1),
            "speed_precision_mm_s": round(self.speed_precision * 1000, 3),
            "confidence": round(confidence, 3),
            "is_moving": movement_analysis['is_moving'],
            "movement_direction": movement_analysis['direction'],
            "movement_phase": phase,
            "eta_seconds": eta_seconds,
            "samples_in_window": len(self.distance_measurements),
            "total_samples": len(self.distance_history),
            "algorithm": "precision_formula_sa",
            "mf_hz": self.measurement_frequency,
            "sa_samples": self.window_size
        }
    
    def _calculate_sa_averaged_speed(self) -> float:
        """
        Calculate speed using SA (Sample Averaging) parameter
        This implements the averaging component of the precision formula
        """
        if len(self.distance_measurements) < 2:
            return self._calculate_instant_speed()
        
        # Get SA samples (up to window_size)
        samples = list(self.distance_measurements)
        
        if len(samples) < self.window_size:
            # Not enough samples yet, use what we have
            N = len(samples)
        else:
            # Use exactly SA samples
            N = self.window_size
            samples = samples[-N:]
        
        # Calculate average distance for first and second half of samples
        mid = N // 2
        if mid == 0:
            return self._calculate_instant_speed()
        
        first_half = samples[:mid]
        second_half = samples[mid:]
        
        # Average distances
        first_avg_dist = sum(d for t, d in first_half) / len(first_half)
        second_avg_dist = sum(d for t, d in second_half) / len(second_half)
        
        # Average timestamps
        first_avg_time = sum(t for t, d in first_half) / len(first_half)
        second_avg_time = sum(t for t, d in second_half) / len(second_half)
        
        # Calculate speed from averaged values
        dt = second_avg_time - first_avg_time
        if dt > 0:
            speed = (second_avg_dist - first_avg_dist) / dt
            
            # Debug logging for speed calculation
            if abs(speed) > 0.001:  # Log significant speeds
                logger.debug(f"SA Speed calc: dt={dt:.3f}s, ddist={(second_avg_dist-first_avg_dist)*1000:.1f}mm, speed={speed*1000:.1f}mm/s")
            
            return speed
        
        return 0.0
    
    def _calculate_instant_speed(self) -> float:
        """Calculate instantaneous speed from last two measurements"""
        if len(self.distance_history) < 2:
            return 0.0
        
        t1, d1 = self.distance_history[-2]
        t2, d2 = self.distance_history[-1]
        
        dt = t2 - t1
        if dt > 0:
            speed = (d2 - d1) / dt
            # Log instant speed calculation for debugging
            if abs(speed) > 0.0001:  # Log any non-zero speed
                logger.debug(f"Instant speed: dt={dt:.3f}s, ddist={(d2-d1)*1000:.2f}mm, speed={speed*1000:.2f}mm/s")
            return speed
        return 0.0
    
    def _calculate_windowed_speed(self) -> float:
        """Calculate speed using moving window average"""
        if len(self.distance_history) < self.window_size:
            return self._calculate_instant_speed()
        
        # Get window of measurements
        window = list(self.distance_history)[-self.window_size:]
        
        # Calculate speeds between consecutive points
        speeds = []
        for i in range(1, len(window)):
            t1, d1 = window[i-1]
            t2, d2 = window[i]
            dt = t2 - t1
            if dt > 0:
                speeds.append((d2 - d1) / dt)
        
        if speeds:
            # Use median for robustness against outliers
            return statistics.median(speeds)
        return 0.0
    
    def _calculate_trend_speed(self) -> float:
        """Calculate speed using linear regression over recent history"""
        if len(self.distance_history) < 5:
            return self._calculate_windowed_speed()
        
        # Use last 20 samples or available
        samples = list(self.distance_history)[-20:]
        
        # Extract times and distances
        times = [t - samples[0][0] for t, d in samples]
        distances = [d for t, d in samples]
        
        # Linear regression
        n = len(times)
        if n < 2 or max(times) == 0:
            return 0.0
        
        sum_t = sum(times)
        sum_d = sum(distances)
        sum_td = sum(t * d for t, d in zip(times, distances))
        sum_t2 = sum(t * t for t in times)
        
        denominator = n * sum_t2 - sum_t * sum_t
        if denominator == 0:
            return 0.0
        
        # Slope is the speed
        speed = (n * sum_td - sum_t * sum_d) / denominator
        
        return speed
    
    def _update_kalman_filter(self, measured_speed: float) -> float:
        """
        Update Kalman filter for smooth speed estimation
        
        This provides smooth speed output suitable for display
        """
        # Process noise (how much speed can change)
        process_variance = 0.0005  # Reduced for smoother output
        
        # Measurement noise (sensor accuracy)
        measurement_variance = 0.005  # Reduced for less jumpy output
        
        # Prediction step
        predicted_variance = self.kalman_variance + process_variance
        
        # Update step
        kalman_gain = predicted_variance / (predicted_variance + measurement_variance)
        self.kalman_speed = self.kalman_speed + kalman_gain * (measured_speed - self.kalman_speed)
        self.kalman_variance = (1 - kalman_gain) * predicted_variance
        
        return self.kalman_speed
    
    def _analyze_movement(self, instant: float, windowed: float, trend: float) -> Dict:
        """
        Analyze whether vessel is actually moving or stationary
        
        Returns dict with movement analysis
        """
        # Check consistency of speed measurements
        speeds = [instant, windowed, trend]
        
        # Remove near-zero values for analysis
        significant_speeds = [s for s in speeds if abs(s) > VesselMovementProfile.STATIONARY_NOISE]
        
        if not significant_speeds:
            # All speeds below noise threshold
            self.consistent_movement_count = 0
            return {
                'is_moving': False,
                'direction': 'stationary',
                'confidence': 1.0,
                'reason': 'all_speeds_below_noise'
            }
        
        # Check if speeds agree on direction
        directions = [1 if s > 0 else -1 for s in significant_speeds]
        
        if len(set(directions)) == 1:
            # All significant speeds agree on direction
            avg_speed = statistics.mean(significant_speeds)
            
            if abs(avg_speed) > VesselMovementProfile.MOVEMENT_THRESHOLD:
                self.consistent_movement_count += 1
                
                # Need consistent movement for at least 2 samples (more responsive)
                if self.consistent_movement_count >= 2:
                    return {
                        'is_moving': True,
                        'direction': 'approaching' if avg_speed < 0 else 'departing',
                        'confidence': min(self.consistent_movement_count / 10.0, 1.0),
                        'reason': 'consistent_movement'
                    }
        else:
            # Speeds disagree - likely noise
            self.consistent_movement_count = 0
        
        # Check trend for slow but consistent movement
        if abs(trend) > VesselMovementProfile.MOVEMENT_THRESHOLD:
            # Calculate R-squared for trend confidence
            samples = list(self.distance_history)[-20:]
            if len(samples) >= 10:
                times = [t - samples[0][0] for t, d in samples]
                distances = [d for t, d in samples]
                
                mean_d = sum(distances) / len(distances)
                predicted = [times[i] * trend + distances[0] for i in range(len(times))]
                
                ss_tot = sum((d - mean_d) ** 2 for d in distances)
                ss_res = sum((distances[i] - predicted[i]) ** 2 for i in range(len(distances)))
                
                if ss_tot > 0:
                    r_squared = 1 - (ss_res / ss_tot)
                    
                    if r_squared > 0.7:  # Good linear fit
                        return {
                            'is_moving': True,
                            'direction': 'approaching' if trend < 0 else 'departing',
                            'confidence': r_squared,
                            'reason': 'strong_trend'
                        }
        
        return {
            'is_moving': False,
            'direction': 'stationary',
            'confidence': 0.5,
            'reason': 'inconsistent_movement'
        }
    
    def _calculate_eta(self, current_distance: float, speed: float) -> Optional[float]:
        """Calculate estimated time to arrival at berth"""
        if speed >= 0:  # Not approaching
            return None
        
        # Assume berth is at 0 distance
        time_to_berth = abs(current_distance / speed)
        
        # Cap at 1 hour
        return min(time_to_berth, 3600)
    
    def _empty_result(self, timestamp: float, distance: float) -> Dict:
        """Return empty result when insufficient data"""
        return {
            "timestamp": timestamp,
            "distance": round(distance, 4),
            "instant_speed": 0.0,
            "windowed_speed": 0.0,
            "trend_speed": 0.0,
            "kalman_speed": 0.0,
            "final_speed": 0.0,
            "speed_mm_s": 0.0,
            "confidence": 0.0,
            "is_moving": False,
            "movement_direction": "unknown",
            "movement_phase": "stationary",
            "eta_seconds": None,
            "samples_in_window": len(self.distance_history),
            "algorithm": "vessel_berthing_optimized",
            "status": "collecting_samples"
        }
    
    def reset(self):
        """Reset the calculator"""
        self.distance_history.clear()
        self.speed_history.clear()
        self.filtered_speeds.clear()
        self.movement_detected = False
        self.movement_direction = 0
        self.consistent_movement_count = 0
        self.kalman_speed = 0.0
        self.kalman_variance = 1.0
        logger.info(f"Reset vessel speed calculator for {self.sensor_id}")