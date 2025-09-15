"""
Modbus relay control endpoints
Provides API endpoints for controlling Waveshare Modbus relays
"""

import socket
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Literal

from ...core.logging_config import logger

# Default configuration for the Waveshare relay
RELAY_HOST = "192.168.1.200"  # Replace with your relay's IP address
RELAY_PORT = 8000
DEVICE_ADDRESS = 0x01

router = APIRouter(prefix="/modbus", tags=["Modbus Control"])


def calculate_crc16(data: bytes) -> bytes:
    """
    Calculates the CRC-16 Modbus checksum for the given data.
    """
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for _ in range(8):
            if (crc & 1) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, byteorder='little')


def send_command(command: bytes) -> bytes:
    """
    Establishes a one-time socket connection to send a command and receive a response.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)  # Set a timeout for the connection
        try:
            s.connect((RELAY_HOST, RELAY_PORT))
            s.sendall(command)
            response = s.recv(1024)
            return response
        except socket.error as e:
            logger.error(f"Socket error: {e}")
            raise HTTPException(status_code=500, detail=f"Socket error: {e}")


@router.get("/relays/{relay_num}/status", response_model=Dict[str, Any])
async def get_relay_status(relay_num: int) -> Dict[str, Any]:
    """
    Get the status of a specific relay.

    Args:
        relay_num: Relay number (1-8)

    Returns:
        Dictionary containing relay status information
    """
    try:
        if not 1 <= relay_num <= 8:
            raise HTTPException(status_code=400, detail="Relay number must be between 1 and 8.")

        # Modbus function code 0x01: Read Coil Status
        function_code = 0x01
        # Relay address (0-indexed)
        start_address = (relay_num - 1).to_bytes(2, byteorder='big')
        # Number of relays to read
        quantity = (1).to_bytes(2, byteorder='big')

        # Construct the command without CRC
        command = bytes([DEVICE_ADDRESS, function_code]) + start_address + quantity
        # Calculate and append CRC
        crc = calculate_crc16(command)
        full_command = command + crc

        # Send the command and get a response
        response = send_command(full_command)

        logger.info(f"Relay {relay_num} status query successful")

        return {
            "relay_number": relay_num,
            "command_sent_hex": full_command.hex(),
            "response_hex": response.hex(),
            "status": "success"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting relay status for relay {relay_num}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error getting relay status: {str(e)}")


@router.post("/relays/{relay_num}/toggle", response_model=Dict[str, Any])
async def toggle_relay(relay_num: int) -> Dict[str, Any]:
    """
    Toggle a specific relay (set to ON).

    Args:
        relay_num: Relay number (1-8)

    Returns:
        Dictionary containing toggle operation result
    """
    try:
        if not 1 <= relay_num <= 8:
            raise HTTPException(status_code=400, detail="Relay number must be between 1 and 8.")

        # Modbus function code 0x05: Write Single Coil
        function_code = 0x05
        # Relay address (0-indexed)
        relay_address = (relay_num - 1).to_bytes(2, byteorder='big')
        # Data to write: 0xFF00 for ON, 0x0000 for OFF
        data = b'\x55\x00'  # Turn ON

        # Construct the command without CRC
        command = bytes([DEVICE_ADDRESS, function_code]) + relay_address + data
        # Calculate and append CRC
        crc = calculate_crc16(command)
        full_command = command + crc

        # Send the command and get a response
        response = send_command(full_command)

        logger.info(f"Relay {relay_num} toggle successful")

        return {
            "relay_number": relay_num,
            "action": "toggle",
            "command_sent_hex": full_command.hex(),
            "response_hex": response.hex(),
            "status": "success"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling relay {relay_num}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error toggling relay: {str(e)}")
    
@router.get("/relays/all/toggle")
async def toggle_all_relays(state: Literal['on', 'off'] = 'on'):
    """
    Constructs and sends a command to turn all relays ON or OFF.
    """
    # Modbus function code 0x0F: Write Multiple Coils
    function_code = 0x0F
    start_address = (0x0000).to_bytes(2, byteorder='big')
    quantity = (8).to_bytes(2, byteorder='big')
    byte_count = (1).to_bytes(1, byteorder='big')

    # Set coil data based on the desired state
    if state == 'on':
        coil_data = (0xFF).to_bytes(1, byteorder='big')  # All relays ON
    else:
        coil_data = (0x00).to_bytes(1, byteorder='big')  # All relays OFF

    # Construct the command without CRC
    command = bytes([DEVICE_ADDRESS, function_code]) + start_address + quantity + byte_count + coil_data
    # Calculate and append CRC
    crc = calculate_crc16(command)
    full_command = command + crc

    # Send the command and get a response
    response = send_command(full_command)

    return {
        "action": f"toggle_all_relays (set to {state.upper()})",
        "command_sent_hex": full_command.hex(),
        "response_hex": response.hex()
    }


@router.get("/health", response_model=Dict[str, Any])
async def modbus_health_check() -> Dict[str, Any]:
    """
    Health check for Modbus connectivity.

    Returns:
        Dictionary containing health status
    """
    try:
        # Simple connectivity test - try to connect to the relay
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            result = s.connect_ex((RELAY_HOST, RELAY_PORT))

            if result == 0:
                return {
                    "status": "healthy",
                    "relay_host": RELAY_HOST,
                    "relay_port": RELAY_PORT,
                    "device_address": DEVICE_ADDRESS,
                    "message": "Modbus relay connection successful"
                }
            else:
                return {
                    "status": "unhealthy",
                    "relay_host": RELAY_HOST,
                    "relay_port": RELAY_PORT,
                    "message": "Cannot connect to Modbus relay"
                }

    except Exception as e:
        logger.error(f"Error in Modbus health check: {str(e)}")
        return {
            "status": "error",
            "relay_host": RELAY_HOST,
            "relay_port": RELAY_PORT,
            "message": f"Health check failed: {str(e)}"
        }