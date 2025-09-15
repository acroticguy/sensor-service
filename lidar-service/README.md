# Livox LiDAR Service

A production-ready FastAPI service for controlling and streaming data from Livox LiDAR sensors. This service provides a RESTful API interface for managing multiple Livox sensors simultaneously with real-time data streaming capabilities.

## Features

- **Multi-sensor Support**: Connect and manage multiple Livox LiDAR sensors simultaneously
- **Auto-discovery**: Automatically discover sensors on the network
- **Real-time Streaming**: Stream point cloud data in real-time using Server-Sent Events (SSE)
- **Comprehensive Control**: Full control over sensor parameters including:
  - Operating modes (Normal, Power Saving, Standby)
  - Coordinate systems (Cartesian, Spherical)
  - Extrinsic parameters
  - Rain/fog suppression
  - Fan control
  - IMU data push
- **Professional Logging**: Rotating file logger with configurable levels
- **API Documentation**: Auto-generated Swagger UI and ReDoc documentation
- **Error Handling**: Comprehensive error handling and validation
- **CORS Support**: Built-in CORS middleware for cross-origin requests

## Requirements

- Python 3.8+
- Livox sensor(s) connected to the same network
- Network configuration allowing UDP communication

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd lidar_service
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables (optional):
Create a `.env` file in the root directory:
```env
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
COMPUTER_IP=192.168.1.100  # Optional: specify your computer's IP
AUTO_CONNECT_ON_STARTUP=true
```

## Usage

### Starting the Service

```bash
python run.py
```

The service will start on `http://localhost:8000` by default.

### API Documentation

Once the service is running, you can access:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API Endpoints

### Connection Management

- **POST** `/api/v1/connection/discover` - Discover available sensors
- **POST** `/api/v1/connection/connect/{sensor_id}` - Connect to a specific sensor
- **POST** `/api/v1/connection/auto-connect` - Auto-connect to all available sensors
- **DELETE** `/api/v1/connection/disconnect/{sensor_id}` - Disconnect a sensor
- **DELETE** `/api/v1/connection/disconnect-all` - Disconnect all sensors

### Sensor Control

- **GET** `/api/v1/control/sensors` - Get all connected sensors
- **GET** `/api/v1/control/sensors/{sensor_id}` - Get specific sensor info
- **PUT** `/api/v1/control/sensors/{sensor_id}/mode` - Set operating mode
- **PUT** `/api/v1/control/sensors/{sensor_id}/coordinate-system` - Set coordinate system
- **PUT** `/api/v1/control/sensors/{sensor_id}/extrinsic` - Set extrinsic parameters
- **PUT** `/api/v1/control/sensors/{sensor_id}/extrinsic/reset` - Reset extrinsic parameters
- **POST** `/api/v1/control/sensors/{sensor_id}/rain-fog-suppression` - Toggle rain/fog suppression
- **POST** `/api/v1/control/sensors/{sensor_id}/fan` - Control fan
- **GET** `/api/v1/control/sensors/{sensor_id}/fan` - Get fan state
- **POST** `/api/v1/control/sensors/{sensor_id}/imu` - Control IMU data push
- **GET** `/api/v1/control/sensors/{sensor_id}/imu` - Get IMU data push state

### Data Streaming

- **POST** `/api/v1/streaming/start/{sensor_id}` - Start data stream
- **POST** `/api/v1/streaming/stop/{sensor_id}` - Stop data stream
- **GET** `/api/v1/streaming/data/{sensor_id}` - Stream real-time data (SSE)
- **POST** `/api/v1/streaming/start-all` - Start all streams
- **POST** `/api/v1/streaming/stop-all` - Stop all streams
- **GET** `/api/v1/streaming/status` - Get streaming status

### Health & Info

- **GET** `/` - Service info
- **GET** `/health` - Health check

## Example Usage

### Auto-connect to all sensors
```bash
curl -X POST http://localhost:8000/api/v1/connection/auto-connect
```

### Start streaming from a sensor
```bash
# Start the stream
curl -X POST http://localhost:8000/api/v1/streaming/start/lidar_192_168_1_42

# Connect to the SSE stream
curl -N http://localhost:8000/api/v1/streaming/data/lidar_192_168_1_42
```

### Set coordinate system
```bash
curl -X PUT http://localhost:8000/api/v1/control/sensors/lidar_192_168_1_42/coordinate-system \
  -H "Content-Type: application/json" \
  -d '"cartesian"'
```

## Project Structure

```
lidar_service/
├── app/
│   ├── api/
│   │   ├── endpoints/
│   │   │   ├── connection.py    # Connection management endpoints
│   │   │   ├── control.py       # Sensor control endpoints
│   │   │   └── streaming.py     # Data streaming endpoints
│   │   └── middleware/
│   │       ├── error_handler.py # Error handling middleware
│   │       └── logging_middleware.py # Request logging
│   ├── core/
│   │   ├── config.py           # Configuration settings
│   │   └── logging_config.py   # Logging configuration
│   ├── models/
│   │   └── lidar.py           # Pydantic models
│   ├── services/
│   │   └── lidar_manager.py   # Core LiDAR management service
│   └── main.py                # FastAPI application
├── logs/                      # Log files directory
├── openpylivox-master/        # OpenPyLivox library
├── requirements.txt           # Python dependencies
├── run.py                    # Application entry point
└── README.md                 # This file
```

## Logging

The service uses a rotating file logger that writes to `logs/lidar_service.log`. The log files rotate when they reach 10MB, keeping up to 5 backup files.

Log levels can be configured via the `LOG_LEVEL` environment variable (DEBUG, INFO, WARNING, ERROR, CRITICAL).

## Error Handling

The service includes comprehensive error handling:
- Validation errors return 422 status with detailed error messages
- HTTP exceptions are properly caught and logged
- Unexpected errors return 500 status with error details
- All errors are logged with full stack traces

## Production Deployment

For production deployment:

1. Use a production ASGI server:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

2. Consider using a reverse proxy (nginx, Apache)
3. Set up SSL/TLS certificates
4. Configure firewall rules for LiDAR UDP ports
5. Monitor logs and set up alerting

## Troubleshooting

1. **Cannot discover sensors**: Ensure sensors and computer are on the same network subnet
2. **Connection timeout**: Check firewall settings and UDP port availability
3. **Stream not working**: Verify sensor is in normal mode and data stream is started
4. **High memory usage**: Adjust queue size in `lidar_manager.py` if needed

## License

This project uses the OpenPyLivox library for Livox sensor communication.