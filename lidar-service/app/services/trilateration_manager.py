"""
Trilateration Manager for vessel position calculation during drift operations.

This module implements trilateration using pairs of same-type sensors (lasers or lidars)
to calculate vessel position, applies median filtering, and provides warning levels
based on position deviations.
"""

import math
import time
import statistics
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
from ..core.logging_config import logger


class TrilaterationManager:
    """
    Manages trilateration calculations for vessel positioning during drift operations.
    """

    def __init__(self, median_window_size: int = 5):
        """
        Initialize the trilateration manager.

        Args:
            median_window_size: Number of readings to keep for median filtering
        """
        self.median_window_size = median_window_size

        # Store historical position data for each berth
        # berth_id -> sensor_pair_key -> deque of (timestamp, position)
        self.position_history: Dict[int, Dict[str, deque]] = {}

        # Store current warning states for each berth
        # berth_id -> sensor_pair_key -> warning_state
        self.warning_states: Dict[int, Dict[str, Dict]] = {}

        # Store baseline positions for deviation calculation
        # berth_id -> sensor_pair_key -> baseline_position
        self.baseline_positions: Dict[int, Dict[str, Tuple[float, float]]] = {}

        logger.info(f"TrilaterationManager initialized with median window size {median_window_size}")

    def calculate_position_trilateration(self, sensor1_data: Dict, sensor2_data: Dict,
                                       sensor1_pos: Tuple[float, float],
                                       sensor2_pos: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        """
        Calculate vessel position using trilateration with two sensors.

        Args:
            sensor1_data: Data from first sensor (must contain 'distance')
            sensor2_data: Data from second sensor (must contain 'distance')
            sensor1_pos: (x, y) position of first sensor
            sensor2_pos: (x, y) position of second sensor

        Returns:
            (x, y) position of vessel or None if calculation fails
        """
        try:
            # Extract distances
            dist1 = sensor1_data.get('distance', 0.0)
            dist2 = sensor2_data.get('distance', 0.0)

            # Skip if either distance is invalid
            if dist1 <= 0 or dist2 <= 0:
                return None

            # Extract sensor positions
            x1, y1 = sensor1_pos
            x2, y2 = sensor2_pos

            # Calculate distance between sensors
            dx = x2 - x1
            dy = y2 - y1
            dist_sensors = math.sqrt(dx*dx + dy*dy)

            # Check if trilateration is possible (distances must satisfy triangle inequality)
            if dist1 + dist2 <= dist_sensors or abs(dist1 - dist2) >= dist_sensors:
                logger.debug(f"Trilateration not possible: d1={dist1:.3f}, d2={dist2:.3f}, sensor_dist={dist_sensors:.3f}")
                return None

            # Trilateration calculation
            # Using the formula for intersection of two circles
            a = (dist1*dist1 - dist2*dist2 + dist_sensors*dist_sensors) / (2 * dist_sensors)
            h = math.sqrt(dist1*dist1 - a*a)

            # Position of the intersection point
            xm = x1 + a * (x2 - x1) / dist_sensors
            ym = y1 + a * (y2 - y1) / dist_sensors

            # Two possible solutions (vessel could be on either side)
            # For berthing, we assume the vessel is on the "water" side
            # This is a simplification - in practice, you'd need more context
            x_pos1 = xm + h * (y2 - y1) / dist_sensors
            y_pos1 = ym - h * (x2 - x1) / dist_sensors

            x_pos2 = xm - h * (y2 - y1) / dist_sensors
            y_pos2 = ym + h * (x2 - x1) / dist_sensors

            # For berthing operations, we typically want the position closer to the berth
            # As a heuristic, choose the solution that's closer to the midpoint between sensors
            # This is a simplification - real implementation would need berth geometry
            midpoint_x = (x1 + x2) / 2
            midpoint_y = (y1 + y2) / 2

            dist1_to_midpoint = math.sqrt((x_pos1 - midpoint_x)**2 + (y_pos1 - midpoint_y)**2)
            dist2_to_midpoint = math.sqrt((x_pos2 - midpoint_x)**2 + (y_pos2 - midpoint_y)**2)

            if dist1_to_midpoint < dist2_to_midpoint:
                return (x_pos1, y_pos1)
            else:
                return (x_pos2, y_pos2)

        except Exception as e:
            logger.error(f"Error in trilateration calculation: {e}")
            return None

    def apply_median_filter(self, berth_id: int, sensor_pair_key: str,
                           current_position: Tuple[float, float]) -> Tuple[float, float]:
        """
        Apply median filter to position data.

        Args:
            berth_id: Berth identifier
            sensor_pair_key: Key identifying the sensor pair
            current_position: Current calculated position (x, y)

        Returns:
            Filtered position (x, y)
        """
        try:
            # Initialize history for this berth/pair if needed
            if berth_id not in self.position_history:
                self.position_history[berth_id] = {}
            if sensor_pair_key not in self.position_history[berth_id]:
                self.position_history[berth_id][sensor_pair_key] = deque(maxlen=self.median_window_size)

            # Add current position to history
            timestamp = time.time()
            self.position_history[berth_id][sensor_pair_key].append((timestamp, current_position))

            # Apply median filter if we have enough data
            history = list(self.position_history[berth_id][sensor_pair_key])
            if len(history) >= 3:  # Need at least 3 points for median filtering
                # Extract x and y coordinates separately
                x_coords = [pos[1][0] for pos in history]  # pos[1] is the position tuple
                y_coords = [pos[1][1] for pos in history]

                # Calculate median
                median_x = statistics.median(x_coords)
                median_y = statistics.median(y_coords)

                return (median_x, median_y)
            else:
                # Not enough data for filtering, return current position
                return current_position

        except Exception as e:
            logger.error(f"Error applying median filter: {e}")
            return current_position

    def calculate_warning_level(self, berth_id: int, sensor_pair_key: str,
                               filtered_position: Tuple[float, float]) -> int:
        """
        Calculate warning level based on position deviations.

        Args:
            berth_id: Berth identifier
            sensor_pair_key: Key identifying the sensor pair
            filtered_position: Current filtered position

        Returns:
            Warning level (0-3)
        """
        try:
            # Initialize warning state for this berth/pair if needed
            if berth_id not in self.warning_states:
                self.warning_states[berth_id] = {}
            if sensor_pair_key not in self.warning_states[berth_id]:
                self.warning_states[berth_id][sensor_pair_key] = {
                    'level': 0,
                    'consecutive_deviations': 0,
                    'last_position': filtered_position,
                    'velocity_history': deque(maxlen=10),
                    'last_update': time.time()
                }

            state = self.warning_states[berth_id][sensor_pair_key]
            current_time = time.time()

            # Calculate baseline position if not set
            if sensor_pair_key not in self.baseline_positions.get(berth_id, {}):
                if berth_id not in self.baseline_positions:
                    self.baseline_positions[berth_id] = {}
                # Use first valid position as baseline
                self.baseline_positions[berth_id][sensor_pair_key] = filtered_position
                state['baseline_position'] = filtered_position
                return 0

            baseline = self.baseline_positions[berth_id][sensor_pair_key]

            # Calculate deviation from baseline
            dx = filtered_position[0] - baseline[0]
            dy = filtered_position[1] - baseline[1]
            deviation_distance = math.sqrt(dx*dx + dy*dy)

            logger.debug(f"Warning check for {sensor_pair_key}: baseline={baseline}, current={filtered_position}, deviation={deviation_distance:.3f}")

            # Calculate velocity (change in position over time)
            time_diff = current_time - state['last_update']
            if time_diff > 0:
                velocity = deviation_distance / time_diff
                state['velocity_history'].append((current_time, velocity))

                # Calculate acceleration trend
                if len(state['velocity_history']) >= 3:
                    velocities = [v for t, v in state['velocity_history']]
                    # Simple trend: compare recent velocity to average of older velocities
                    recent_vel = velocities[-1]
                    older_vels = velocities[:-1]
                    avg_older_vel = sum(older_vels) / len(older_vels)
                    velocity_trend = recent_vel - avg_older_vel
                else:
                    velocity_trend = 0.0
            else:
                velocity_trend = 0.0

            # Update state
            state['last_position'] = filtered_position
            state['last_update'] = current_time

            # Dynamic warning levels that can increase and decrease
            MINOR_DEVIATION_THRESHOLD = 0.5  # 0.5m deviation
            MAJOR_DEVIATION_THRESHOLD = 5.0  # 5m deviation

            # Update consecutive deviations counter
            if deviation_distance > MINOR_DEVIATION_THRESHOLD:
                state['consecutive_deviations'] += 1
            else:
                # Reset counter if back to normal
                state['consecutive_deviations'] = max(0, state['consecutive_deviations'] - 1)

            # Determine warning level based on current state
            if deviation_distance > MAJOR_DEVIATION_THRESHOLD:
                state['level'] = 3
                logger.warning(f"LEVEL 3 ALERT: Major deviation {deviation_distance:.3f}m for berth {berth_id}, pair {sensor_pair_key}")
                return 3
            elif state['consecutive_deviations'] >= 5:
                state['level'] = 2
                logger.warning(f"LEVEL 2 ALERT: Sustained deviation ({state['consecutive_deviations']} consecutive) for berth {berth_id}, pair {sensor_pair_key}")
                return 2
            elif deviation_distance > MINOR_DEVIATION_THRESHOLD:
                state['level'] = 1
                logger.info(f"LEVEL 1 WARNING: Deviation {deviation_distance:.3f}m for berth {berth_id}, pair {sensor_pair_key}")
                return 1
            else:
                # Normal operation
                state['level'] = 0
                return 0

        except Exception as e:
            logger.error(f"Error calculating warning level: {e}")
            return 0

    def process_sensor_pair(self, berth_id: int, sensor_pair_key: str,
                           sensor1_data: Dict, sensor2_data: Dict,
                           sensor1_pos: Tuple[float, float],
                           sensor2_pos: Tuple[float, float]) -> Dict[str, Any]:
        """
        Process a pair of sensors for trilateration and warning calculation.

        Args:
            berth_id: Berth identifier
            sensor_pair_key: Key identifying the sensor pair
            sensor1_data: Data from first sensor
            sensor2_data: Data from second sensor
            sensor1_pos: Position of first sensor
            sensor2_pos: Position of second sensor

        Returns:
            Dictionary with trilateration results and warning info
        """
        try:
            logger.info(f"Processing trilateration for {sensor_pair_key}: sensor1_pos={sensor1_pos}, sensor2_pos={sensor2_pos}")

            # Calculate raw position using trilateration
            raw_position = self.calculate_position_trilateration(
                sensor1_data, sensor2_data, sensor1_pos, sensor2_pos
            )

            logger.info(f"Trilateration result for {sensor_pair_key}: raw_position={raw_position}")

            if raw_position is None:
                logger.info(f"Trilateration failed for {sensor_pair_key} - returning danger_level=0")
                return {
                    'operation_name': f'trilateration_{sensor_pair_key}',
                    'danger_level': 0,
                    'position_valid': False,
                    'error': 'trilateration_failed'
                }

            # Apply median filter
            filtered_position = self.apply_median_filter(berth_id, sensor_pair_key, raw_position)

            logger.info(f"Filtered position for {sensor_pair_key}: {filtered_position}")

            # Calculate warning level
            danger_level = self.calculate_warning_level(berth_id, sensor_pair_key, filtered_position)

            logger.info(f"Trilateration {sensor_pair_key}: danger_level={danger_level}, position={filtered_position}")

            return {
                'operation_name': f'trilateration_{sensor_pair_key}',
                'danger_level': danger_level,
                'position_valid': True,
                'raw_position': raw_position,
                'filtered_position': filtered_position,
                'berth_id': berth_id,
                'sensor_pair': sensor_pair_key,
                'timestamp': time.time()
            }

        except Exception as e:
            logger.error(f"Error processing sensor pair {sensor_pair_key}: {e}")
            return {
                'operation_name': f'trilateration_{sensor_pair_key}',
                'danger_level': 0,
                'position_valid': False,
                'error': str(e)
            }

    def get_operation_status(self, berth_id: int, sensor_pair_key: str) -> Dict[str, Any]:
        """
        Get current operation status for a sensor pair.

        Args:
            berth_id: Berth identifier
            sensor_pair_key: Key identifying the sensor pair

        Returns:
            Current operation status
        """
        try:
            state = self.warning_states.get(berth_id, {}).get(sensor_pair_key, {})
            baseline = self.baseline_positions.get(berth_id, {}).get(sensor_pair_key)

            return {
                'operation_name': f'trilateration_{sensor_pair_key}',
                'danger_level': state.get('level', 0),
                'baseline_position': baseline,
                'consecutive_deviations': state.get('consecutive_deviations', 0),
                'last_update': state.get('last_update', 0),
                'history_size': len(self.position_history.get(berth_id, {}).get(sensor_pair_key, []))
            }

        except Exception as e:
            logger.error(f"Error getting operation status: {e}")
            return {
                'operation_name': f'trilateration_{sensor_pair_key}',
                'danger_level': 0,
                'error': str(e)
            }


# Global trilateration manager instance
trilateration_manager = TrilaterationManager()