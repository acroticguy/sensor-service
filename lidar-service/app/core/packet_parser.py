"""
Common packet parsing utilities for Livox lidar data
"""
import struct
from typing import List, Dict, Any
from ..core.constants import (
    PACKET_VERSION_5, DATA_TYPE_CARTESIAN_SINGLE_RETURN,
    DATA_TYPE_TELE15_SINGLE_RETURN, DATA_TYPE_TELE15_DUAL_RETURN
)


def parse_livox_packet(data: bytes) -> List[Dict[str, Any]]:
    """
    Parse a Livox data packet and return list of points

    Args:
        data: Raw packet data

    Returns:
        List of parsed point dictionaries
    """
    points = []

    if len(data) < 18:  # Minimum header size
        return points

    # Parse packet header
    version = struct.unpack('B', data[0:1])[0]
    data_type = struct.unpack('B', data[9:10])[0] if len(data) > 9 else -1

    # Handle different packet types
    if version == PACKET_VERSION_5:
        if data_type == DATA_TYPE_CARTESIAN_SINGLE_RETURN:
            points.extend(_parse_cartesian_single_return(data))
        elif data_type == DATA_TYPE_TELE15_SINGLE_RETURN:
            points.extend(_parse_tele15_single_return(data))
        elif data_type == DATA_TYPE_TELE15_DUAL_RETURN:
            points.extend(_parse_tele15_dual_return(data))

    return points


def _parse_cartesian_single_return(data: bytes) -> List[Dict[str, Any]]:
    """Parse Mid-40/100 Cartesian single return packets"""
    points = []
    byte_pos = 18

    for i in range(100):
        if byte_pos + 13 > len(data):
            break

        # Parse point data
        x_mm = struct.unpack('<i', data[byte_pos:byte_pos+4])[0]
        y_mm = struct.unpack('<i', data[byte_pos+4:byte_pos+8])[0]
        z_mm = struct.unpack('<i', data[byte_pos+8:byte_pos+12])[0]
        intensity = struct.unpack('B', data[byte_pos+12:byte_pos+13])[0]

        # Filter invalid points
        if y_mm != 0 and _is_valid_coordinate(x_mm, y_mm, z_mm):
            x_m = x_mm / 1000.0
            y_m = y_mm / 1000.0
            z_m = z_mm / 1000.0
            distance = (x_m**2 + y_m**2 + z_m**2)**0.5

            points.append({
                "point_id": len(points),
                "x": round(x_m, 3),
                "y": round(y_m, 3),
                "z": round(z_m, 3),
                "distance": round(distance, 3),
                "intensity": intensity
            })

        byte_pos += 13

    return points


def _parse_tele15_single_return(data: bytes) -> List[Dict[str, Any]]:
    """Parse Tele-15 single return packets"""
    points = []
    byte_pos = 18

    for i in range(96):
        if byte_pos + 14 > len(data):
            break

        # Parse point data (14 bytes)
        x_mm = struct.unpack('<i', data[byte_pos:byte_pos+4])[0]
        y_mm = struct.unpack('<i', data[byte_pos+4:byte_pos+8])[0]
        z_mm = struct.unpack('<i', data[byte_pos+8:byte_pos+12])[0]
        intensity = struct.unpack('B', data[byte_pos+12:byte_pos+13])[0]

        # Filter invalid points
        if y_mm != 0 and _is_valid_coordinate(x_mm, y_mm, z_mm):
            x_m = x_mm / 1000.0
            y_m = y_mm / 1000.0
            z_m = z_mm / 1000.0
            distance = (x_m**2 + y_m**2 + z_m**2)**0.5

            points.append({
                "point_id": len(points),
                "x": round(x_m, 3),
                "y": round(y_m, 3),
                "z": round(z_m, 3),
                "distance": round(distance, 3),
                "intensity": intensity
            })

        byte_pos += 14

    return points


def _parse_tele15_dual_return(data: bytes) -> List[Dict[str, Any]]:
    """Parse Tele-15 dual return packets"""
    points = []
    byte_pos = 18

    for i in range(48):  # 48 points for dual return
        if byte_pos + 28 > len(data):
            break

        # First return (14 bytes)
        x1_mm = struct.unpack('<i', data[byte_pos:byte_pos+4])[0]
        y1_mm = struct.unpack('<i', data[byte_pos+4:byte_pos+8])[0]
        z1_mm = struct.unpack('<i', data[byte_pos+8:byte_pos+12])[0]
        intensity1 = struct.unpack('B', data[byte_pos+12:byte_pos+13])[0]

        # Second return (14 bytes)
        x2_mm = struct.unpack('<i', data[byte_pos+14:byte_pos+18])[0]
        y2_mm = struct.unpack('<i', data[byte_pos+18:byte_pos+22])[0]
        z2_mm = struct.unpack('<i', data[byte_pos+22:byte_pos+26])[0]
        intensity2 = struct.unpack('B', data[byte_pos+26:byte_pos+27])[0]

        # Process first return
        if y1_mm != 0 and _is_valid_coordinate(x1_mm, y1_mm, z1_mm):
            x1_m = x1_mm / 1000.0
            y1_m = y1_mm / 1000.0
            z1_m = z1_mm / 1000.0
            distance1 = (x1_m**2 + y1_m**2 + z1_m**2)**0.5

            points.append({
                "point_id": len(points),
                "x": round(x1_m, 3),
                "y": round(y1_m, 3),
                "z": round(z1_m, 3),
                "distance": round(distance1, 3),
                "intensity": intensity1,
                "return_num": 1
            })

        # Process second return
        if y2_mm != 0 and _is_valid_coordinate(x2_mm, y2_mm, z2_mm):
            x2_m = x2_mm / 1000.0
            y2_m = y2_mm / 1000.0
            z2_m = z2_mm / 1000.0
            distance2 = (x2_m**2 + y2_m**2 + z2_m**2)**0.5

            points.append({
                "point_id": len(points),
                "x": round(x2_m, 3),
                "y": round(y2_m, 3),
                "z": round(z2_m, 3),
                "distance": round(distance2, 3),
                "intensity": intensity2,
                "return_num": 2
            })

        byte_pos += 28

    return points


def _is_valid_coordinate(x: int, y: int, z: int) -> bool:
    """Check if coordinate values are within valid range"""
    return (-500000 <= x <= 500000 and
            -500000 <= y <= 500000 and
            -500000 <= z <= 500000)