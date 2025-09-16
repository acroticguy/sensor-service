"""
Drift Monitor for vessel sway and yaw detection during berthing operations.

This module monitors distance measurements from sensors during drift operations,
compares current readings to historical averages, and provides warning levels
based on sustained irregularities in distance measurements.
"""

import math
import time
import statistics
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
from ..core.logging_config import logger


class DriftMonitor:
    """
    Monitors vessel drift by detecting irregularities in distance measurements.

    Tracks distance readings over time and compares current measurements
    to historical averages to detect sway and yaw movements.
    """

    def __init__(self, history_seconds: int = 60):
        """
        Initialize the drift monitor.

        Args:
            history_seconds: Number of seconds of history to maintain
        """
        self.history_seconds = history_seconds

        # Store distance history for each sensor
        # sensor_id -> deque of (timestamp, distance)
        self.distance_history: Dict[str, deque] = {}

        # Store current warning states for each sensor
        # sensor_id -> warning_state
        self.warning_states: Dict[str, Dict] = {}

        logger.info(f"DriftMonitor initialized with {history_seconds}s history")

    def add_distance_reading(self, sensor_id: str, distance: float, timestamp: float):
        """
        Add a distance reading for drift monitoring.

        Args:
            sensor_id: Sensor identifier
            distance: Distance measurement
            timestamp: Reading timestamp
        """
        try:
            # Initialize history for new sensors
            if sensor_id not in self.distance_history:
                self.distance_history[sensor_id] = deque(maxlen=self.history_seconds)
                logger.debug(f"Initialized distance history for sensor {sensor_id}")

            # Add reading to history
            self.distance_history[sensor_id].append((timestamp, distance))

            # Clean old readings (keep only last history_seconds)
            cutoff_time = timestamp - self.history_seconds
            while self.distance_history[sensor_id] and self.distance_history[sensor_id][0][0] < cutoff_time:
                self.distance_history[sensor_id].popleft()

        except Exception as e:
            logger.error(f"Error adding distance reading for {sensor_id}: {e}")

    def get_historical_average(self, sensor_id: str, current_time: float) -> Optional[float]:
        """
        Get the historical average distance for a sensor.

        Args:
            sensor_id: Sensor identifier
            current_time: Current timestamp

        Returns:
            Historical average distance or None if insufficient data
        """
        try:
            if sensor_id not in self.distance_history:
                return None

            history = self.distance_history[sensor_id]
            if not history:
                return None

            # Get readings from the last minute (or available time)
            cutoff_time = current_time - 60.0  # Last 60 seconds
            recent_readings = [dist for ts, dist in history if ts >= cutoff_time]

            if len(recent_readings) < 3:  # Need at least 3 readings for meaningful average
                return None

            return statistics.mean(recent_readings)

        except Exception as e:
            logger.error(f"Error calculating historical average for {sensor_id}: {e}")
            return None

    def check_irregularity(self, sensor_id: str, current_distance: float, current_time: float) -> bool:
        """
        Check if current distance reading is irregular compared to historical average.

        Args:
            sensor_id: Sensor identifier
            current_distance: Current distance reading
            current_time: Current timestamp

        Returns:
            True if reading is irregular, False otherwise
        """
        try:
            historical_avg = self.get_historical_average(sensor_id, current_time)
            if historical_avg is None:
                return False  # Not enough history to determine irregularity

            # Calculate deviation from historical average
            deviation = abs(current_distance - historical_avg)

            # Consider irregular if deviation > 10% of average or > 0.5m (whichever is larger)
            irregularity_threshold = max(0.5, historical_avg * 0.1)

            return deviation > irregularity_threshold

        except Exception as e:
            logger.error(f"Error checking irregularity for {sensor_id}: {e}")
            return False

    def monitor_sensor_drift(self, sensor_id: str, current_distance: float, current_time: float) -> int:
        """
        Monitor a sensor for drift irregularities and return warning level.

        Args:
            sensor_id: Sensor identifier
            current_distance: Current distance reading
            current_time: Current timestamp

        Returns:
            Warning level (0-3)
        """
        try:
            # Initialize warning state for this sensor if needed
            if sensor_id not in self.warning_states:
                self.warning_states[sensor_id] = {
                    'level': 0,
                    'irregular_seconds': 0,
                    'last_irregular_time': 0,
                    'extreme_changes': 0
                }

            state = self.warning_states[sensor_id]

            # Add current reading to history
            self.add_distance_reading(sensor_id, current_distance, current_time)

            # Check if current reading is irregular
            is_irregular = self.check_irregularity(sensor_id, current_distance, current_time)

            # Track irregular readings over time
            if is_irregular:
                # If this is a new irregularity period, reset counter
                if current_time - state['last_irregular_time'] > 2.0:  # 2 second gap resets
                    state['irregular_seconds'] = 0

                state['irregular_seconds'] += 1
                state['last_irregular_time'] = current_time

                # Check for extreme changes (spikes)
                historical_avg = self.get_historical_average(sensor_id, current_time)
                if historical_avg and abs(current_distance - historical_avg) > historical_avg * 0.5:  # 50% change
                    state['extreme_changes'] += 1
                    logger.warning(f"EXTREME CHANGE detected for {sensor_id}: {current_distance:.3f}m vs avg {historical_avg:.3f}m")
            else:
                # Gradually reduce irregular seconds when readings become regular
                state['irregular_seconds'] = max(0, state['irregular_seconds'] - 0.5)

            # Determine warning level
            if state['extreme_changes'] >= 3:  # Multiple extreme changes
                state['level'] = 3
                logger.error(f"LEVEL 3 CRITICAL: Persistent extreme changes for {sensor_id}")
                return 3
            elif state['irregular_seconds'] >= 5:  # 5+ seconds of sustained irregularity
                state['level'] = 2
                logger.warning(f"LEVEL 2 ALERT: Sustained irregularity ({state['irregular_seconds']:.1f}s) for {sensor_id}")
                return 2
            elif is_irregular:
                state['level'] = 1
                logger.info(f"LEVEL 1 WARNING: Irregular reading for {sensor_id}: {current_distance:.3f}m")
                return 1
            else:
                # Normal operation
                state['level'] = 0
                return 0

        except Exception as e:
            logger.error(f"Error monitoring sensor drift for {sensor_id}: {e}")
            return 0

    def process_sensor_data(self, sensor_id: str, sensor_data: Dict, baseline_distance: Optional[float] = None) -> Dict[str, Any]:
        """
        Process sensor data for drift monitoring.

        Args:
            sensor_id: Sensor identifier
            sensor_data: Sensor data dictionary
            baseline_distance: Baseline distance from offset_x (optional)

        Returns:
            Dictionary with drift monitoring results
        """
        try:
            current_distance = sensor_data.get('distance', 0.0)
            current_time = sensor_data.get('timestamp', time.time())

            if current_distance <= 0:
                return {
                    'operation_name': f'drift_monitor_{sensor_id}',
                    'danger_level': 0,
                    'sensor_id': sensor_id,
                    'error': 'invalid_distance'
                }

            # Monitor for drift irregularities
            danger_level = self.monitor_sensor_drift(sensor_id, current_distance, current_time)

            # Get historical average for context
            historical_avg = self.get_historical_average(sensor_id, current_time)

            result = {
                'operation_name': f'drift_monitor_{sensor_id}',
                'danger_level': danger_level,
                'sensor_id': sensor_id,
                'current_distance': current_distance,
                'historical_average': historical_avg,
                'timestamp': current_time
            }

            # Include baseline distance if provided
            if baseline_distance is not None:
                result['baseline_distance'] = baseline_distance
                logger.debug(f"Using baseline distance {baseline_distance:.3f}m for sensor {sensor_id}")

            return result

        except Exception as e:
            logger.error(f"Error processing sensor data for {sensor_id}: {e}")
            return {
                'operation_name': f'drift_monitor_{sensor_id}',
                'danger_level': 0,
                'sensor_id': sensor_id,
                'error': str(e)
            }

    def get_sensor_status(self, sensor_id: str) -> Dict[str, Any]:
        """
        Get current drift monitoring status for a sensor.

        Args:
            sensor_id: Sensor identifier

        Returns:
            Current sensor status
        """
        try:
            state = self.warning_states.get(sensor_id, {})
            history = self.distance_history.get(sensor_id, [])
            historical_avg = self.get_historical_average(sensor_id, time.time())

            return {
                'operation_name': f'drift_monitor_{sensor_id}',
                'danger_level': state.get('level', 0),
                'irregular_seconds': state.get('irregular_seconds', 0),
                'extreme_changes': state.get('extreme_changes', 0),
                'historical_average': historical_avg,
                'history_size': len(history),
                'last_irregular_time': state.get('last_irregular_time', 0)
            }

        except Exception as e:
            logger.error(f"Error getting sensor status for {sensor_id}: {e}")
            return {
                'operation_name': f'drift_monitor_{sensor_id}',
                'danger_level': 0,
                'error': str(e)
            }


# Global drift monitor instance
drift_monitor = DriftMonitor()