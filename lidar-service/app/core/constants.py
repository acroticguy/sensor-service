"""
Shared constants for the lidar service
"""

# Livox command constants (shared between real and fake sensors)
_CMD_DATA_START = bytes.fromhex('AA011000000000B809000401228D5307')
_CMD_DATA_STOP = bytes.fromhex('AA011000000000B809000400B4BD5470')
_CMD_LIDAR_SPIN_UP = bytes.fromhex('AA011000000000B809000201E09941F1')
_CMD_LIDAR_SPIN_DOWN = bytes.fromhex('AA011000000000B80900020062B94546')
_CMD_CARTESIAN_CS = bytes.fromhex('AA011000000000B809000500F58C4F69')
_CMD_GET_DEVICE_INFO = bytes.fromhex('AA011000000000B80900060066BC4D89')

# Default calibration settings
DEFAULT_CALIBRATION_OFFSET = -0.046  # -46mm offset (LiDAR typically reads higher)
DEFAULT_CENTER_TOLERANCE = 0.05  # 50mm center beam tolerance

# Packet parsing constants
PACKET_VERSION_5 = 5
DATA_TYPE_CARTESIAN_SINGLE_RETURN = 0
DATA_TYPE_TELE15_SINGLE_RETURN = 2
DATA_TYPE_TELE15_DUAL_RETURN = 4

# Default extrinsic parameters
DEFAULT_EXTRINSIC_PARAMS = {
    "x": 0.0,    # Sensor at vessel center point
    "y": 0.0,    # No starboard/port offset
    "z": 2.0,    # 2m above deck level
    "roll": 0.0, # Level mounting
    "pitch": 0.0,
    "yaw": 0.0
}