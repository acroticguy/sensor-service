# 🚀 LDM302 Simulator Suite

A comprehensive simulation environment for Jenoptik LDM302 laser distance measurement devices.

## 📋 Overview

This simulator suite provides complete emulation of LDM302 laser devices with realistic measurement data, environmental conditions, and error scenarios for comprehensive testing and development.

## 🔧 Components

### 1. **LDM302 Simulator** (`ldm302_simulator.py`)
- **Complete command set simulation** - All LDM302 commands supported
- **Realistic measurement data** - Distance, speed, temperature, signal strength
- **Environmental simulation** - Temperature drift, signal interference
- **Error condition testing** - Timeout, invalid data, signal loss scenarios
- **TCP server implementation** - Full network compatibility

### 2. **Multi-Simulator Manager** (`multi_simulator_manager.py`)
- **Multiple device support** - Manage dozens of simulators simultaneously
- **Scenario management** - Pre-built testing scenarios:
  - **Static targets** - Fixed distance with minimal noise
  - **Moving targets** - Random movement patterns
  - **Ship berthing** - Realistic approach simulation
  - **Ship drift** - Sinusoidal movement with wind/current
  - **Calibration** - Precision testing at known distances
  - **Error testing** - Various fault conditions
- **Simulator farms** - Quick creation of multiple test devices

### 3. **Data Logger** (`data_logger.py`)
- **Comprehensive logging** - Commands, responses, measurement data
- **Multiple formats** - JSON (compressed), CSV, plain text summaries
- **SQLite database** - Efficient querying and analysis
- **Session management** - Organized data collection periods
- **Replay capabilities** - Recreate test scenarios
- **Statistics generation** - Performance and usage analysis

## 🖥️ Web Interface

Access the complete control interface at: `http://localhost:8000/api/v1/simulator/`

### Features:
- **Real-time control** - Create, start, stop simulators
- **Scenario management** - Launch pre-configured test scenarios  
- **Live monitoring** - Real-time status and data visualization
- **Data logging controls** - Start/stop session recording
- **WebSocket updates** - Live system status and logs

## 📊 Usage Examples

### Creating Simulators
```bash
# Create single simulator
curl -X POST "http://localhost:8000/api/v1/simulator/create" \
  -H "Content-Type: application/json" \
  -d '{"simulator_id": "sim_001", "port": 2030}'

# Create simulator farm (5 devices)
curl -X POST "http://localhost:8000/api/v1/simulator/farm" \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "base_port": 2030}'
```

### Running Scenarios
```bash
# Start berthing scenario
curl -X POST "http://localhost:8000/api/v1/simulator/scenario/start" \
  -H "Content-Type: application/json" \
  -d '{"scenario_name": "ship_berthing"}'

# Start calibration test
curl -X POST "http://localhost:8000/api/v1/simulator/scenario/start" \
  -H "Content-Type: application/json" \
  -d '{"scenario_name": "calibration"}'
```

### Data Logging
```bash
# Start logging session
curl -X POST "http://localhost:8000/api/v1/simulator/logging/start" \
  -H "Content-Type: application/json" \
  -d '{"description": "Performance test session"}'

# Stop logging and save data
curl -X POST "http://localhost:8000/api/v1/simulator/logging/stop"

# Get session statistics
curl "http://localhost:8000/api/v1/simulator/logging/stats"
```

## 🧪 Testing Scenarios

### Available Scenarios

1. **static_10m** - Fixed target at 10 meters
   - Minimal measurement variation
   - Ideal for accuracy testing

2. **moving_random** - Randomly moving target
   - Variable speed and direction
   - Real-world measurement conditions

3. **ship_berthing** - Ship approach simulation
   - 10-minute berthing sequence
   - Logarithmic approach curve
   - Decreasing speed profile

4. **ship_drift** - Drift monitoring
   - 30-minute drift cycle
   - Sinusoidal movement pattern
   - Wind and current simulation

5. **calibration** - Precision testing
   - Known distance measurements
   - Minimal error conditions
   - Multiple test points

6. **error_testing** - Fault condition testing
   - High error rate simulation
   - Various error types
   - Recovery testing

## 📁 Data Storage

Logged data is stored in:
- **Database**: `simulator_logs/simulator_data.db` (SQLite)
- **Session files**: `simulator_logs/{session_id}/`
  - `{session_id}.json.gz` - Complete session data (compressed)
  - `{session_id}.csv` - Measurement data for analysis
  - `{session_id}_summary.txt` - Session statistics

## 🔌 Integration

The simulator integrates seamlessly with the main API:
- **Real laser replacement** - Drop-in replacement for testing
- **Network compatibility** - Standard TCP connections
- **Command compatibility** - Full LDM302 command set
- **Data format matching** - Identical response formats

## 🚀 Quick Start

1. **Start the main API**: `python main.py`
2. **Open web interface**: http://localhost:8000/api/v1/simulator/
3. **Create simulator farm**: Click "Create Farm (5)"
4. **Start a scenario**: Select "ship_berthing" and click "Start Scenario"
5. **Begin logging**: Enter description and click "Start Logging"
6. **Monitor progress**: Watch real-time updates in the interface

The simulator is now ready for comprehensive testing of your laser measurement applications!