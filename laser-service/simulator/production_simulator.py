#!/usr/bin/env python3
"""
Production-ready LDM302 Simulator - Complete protocol implementation
"""

import asyncio
import random
import time
import math
import json
import websockets
from datetime import datetime

class ProductionLDM302Simulator:
    def __init__(self, port=2030, laser_id=21):
        self.port = port
        self.laser_id = laser_id
        self.server = None
        self.device_id = f"LDM302-SIM-{laser_id}"
        self.serial_number = f"SIM0000{laser_id:04d}"
        self.version = "V1.2.3"

        # Simulation state - vary by laser for realism
        self.base_distance = 12.5 + (laser_id % 10) * 0.5  # Slightly different distances
        self.base_speed = 0.0      # Base speed in m/s
        self.temperature = 23.5 + (laser_id % 10) * 0.2    # Slightly different temps
        self.signal_strength = 8500 + (laser_id % 10) * 50 # Different signal strengths
        self.measurement_mode = "OFF"
        self.start_time = time.time()

        # Movement simulation
        self.target_moving = True
        self.movement_pattern = "random"

        # Continuous mode tracking
        self.continuous_mode = False
        self.continuous_task = None
        self.clients = set()

        # WebSocket integration
        self.websocket_connection = None
        self.websocket_task = None
        self.websocket_url = "ws://localhost:8765"
        self.berthing_id = 10

        # Fallback mechanism for continuous mode
        self.last_websocket_data_time = 0
        self.fallback_task = None

        print(f"🎯 Initialized {self.device_id}")
        print(f"📊 Base distance: {self.base_distance}m")
        print(f"🌡️  Temperature: {self.temperature}°C")

    def get_current_distance(self):
        """Generate realistic distance measurements with movement simulation"""
        elapsed = time.time() - self.start_time

        if self.target_moving:
            # Simulate realistic ship movement
            wave_motion = 0.3 * math.sin(elapsed * 0.5)  # Slow wave motion
            random_drift = random.uniform(-0.1, 0.1)     # Small random variations
            wind_effect = 0.15 * math.sin(elapsed * 0.2 + 1.5)  # Wind effect

            distance = self.base_distance + wave_motion + random_drift + wind_effect
        else:
            # Minimal variation for stationary target
            distance = self.base_distance + random.uniform(-0.005, 0.005)

        return max(0.1, distance)  # Ensure positive distance

    def get_current_speed(self):
        """Generate realistic speed measurements"""
        if not self.target_moving:
            return random.uniform(-0.02, 0.02)  # Nearly stationary

        elapsed = time.time() - self.start_time

        # Simulate ship movement with periodic speed changes
        base_speed = 0.5 * math.sin(elapsed * 0.1)  # Slow oscillation
        turbulence = random.uniform(-0.3, 0.3)      # Random speed variations

        return base_speed + turbulence

    def get_current_temperature(self):
        """Generate realistic temperature with drift"""
        elapsed = time.time() - self.start_time
        drift = 0.2 * math.sin(elapsed * 0.01)  # Slow temperature drift
        noise = random.uniform(-0.1, 0.1)       # Small variations

        return self.temperature + drift + noise

    def handle_command(self, command):
        """Process LDM302 commands and return appropriate responses"""
        cmd = command.upper().strip()

        # ESC character - Turn OFF (handle single or multiple ESC characters)
        if '\x1b' in command:
            self.measurement_mode = "OFF"
            self.continuous_mode = False

            # Close WebSocket connection
            if self.websocket_task:
                self.websocket_task.cancel()
                self.websocket_task = None

            # Cancel fallback task
            if self.fallback_task:
                self.fallback_task.cancel()
                self.fallback_task = None

            return ""  # ESC typically returns no response

        # Distance measurement commands
        elif cmd == 'DM':
            distance = self.get_current_distance()
            self.measurement_mode = "DISTANCE"
            return f"{distance:.3f} m"

        # Speed measurement commands
        elif cmd == 'VM':
            speed = self.get_current_speed()
            self.measurement_mode = "SPEED"
            return f"{speed:.2f} m/s"

        # Continuous distance
        elif cmd == 'DT':
            self.measurement_mode = "CONTINUOUS_DISTANCE"
            distance = self.get_current_distance()
            return f"{distance:.3f} m"

        # Continuous speed - VT command
        elif cmd == 'VT':
            self.measurement_mode = "CONTINUOUS_SPEED"
            self.continuous_mode = True
            self.last_websocket_data_time = time.time()

            # Start WebSocket connection for real data
            # VT data will be sent ONLY when WebSocket data is received
            if not self.websocket_task or self.websocket_task.done():
                self.websocket_task = asyncio.create_task(self._websocket_client())

            # Start fallback task to send simulated data if WebSocket stops
            if not self.fallback_task or self.fallback_task.done():
                self.fallback_task = asyncio.create_task(self._fallback_vt_sender())

            print(f"📡 [{self.device_id}] VT mode activated - will transmit WebSocket data with simulated fallback")
            return "VT"  # Acknowledge command

        # Temperature
        elif cmd == 'TP':
            temp = self.get_current_temperature()
            return f"{temp:.1f}"

        # Device identification
        elif cmd == 'ID':
            return self.device_id

        # Version
        elif cmd == 'VN':
            return self.version

        # Serial number
        elif cmd == 'SN':
            return self.serial_number

        # Configuration commands
        elif cmd.startswith('G1'):
            return "OK"  # Configuration accepted
        elif cmd.startswith('G2'):
            return "OK"  # Configuration accepted
        elif cmd.startswith('SF'):
            return "OK"  # Save configuration
        elif cmd.startswith('CF'):
            return "OK"  # Clear configuration

        # Pilot laser control
        elif cmd.startswith('PL'):
            param = cmd[2:].strip() if len(cmd) > 2 else ""
            if param in ['0', '1', '2', '3']:
                return "OK"
            return "ERR"

        # Measurement interval
        elif cmd.startswith('MI'):
            return "OK"

        # Display units
        elif cmd.startswith('DU'):
            return "OK"

        # Trigger configuration
        elif cmd.startswith('TC'):
            return "OK"

        # Default response for unknown commands
        else:
            print(f"⚠️  Unknown command: {repr(command)}")
            return "OK"

    async def handle_client(self, reader, writer):
        client_addr = writer.get_extra_info('peername')
        print(f"🔌 Laser service connected: {client_addr}")
        print(f"📡 Device ready: {self.device_id}")

        # Add client to the set for continuous measurements
        self.clients.add(writer)

        try:
            while True:
                # Read data from client
                data = await reader.read(1024)
                if not data:
                    break

                message = data.decode('utf-8', errors='ignore').strip()

                # Handle each command
                response = self.handle_command(message)

                # Log the interaction
                timestamp = datetime.now().strftime("%H:%M:%S")
                if message == '\x1b':
                    print(f"[{timestamp}] 📨 ESC → 📤 {response}")
                else:
                    print(f"[{timestamp}] 📨 {message} → 📤 {response}")

                # Send response with proper line ending (only if response is not empty)
                if response:
                    response_bytes = (response + "\r\n").encode('utf-8')
                    writer.write(response_bytes)
                    await writer.drain()

                # Small delay for realistic response time
                await asyncio.sleep(0.001)

        except Exception as e:
            print(f"❌ Client error: {e}")
        finally:
            # Remove client from the set
            self.clients.discard(writer)
            print(f"🔌 Laser service disconnected: {client_addr}")
            writer.close()
            await writer.wait_closed()

    async def _websocket_client(self):
        """Connect to WebSocket and stream real data"""
        try:
            print(f"🔌 [{self.device_id}] Connecting to WebSocket at {self.websocket_url}")
            async with websockets.connect(self.websocket_url) as websocket:
                self.websocket_connection = websocket
                print(f"✅ [{self.device_id}] WebSocket connected")

                # Subscribe to berthing_id 10 for this specific laser
                subscribe_msg = {"berthing_id": self.berthing_id, "laser_id": self.laser_id}
                await websocket.send(json.dumps(subscribe_msg))
                print(f"📡 [{self.device_id}] Subscribed to berthing_id: {self.berthing_id} for laser_id: {self.laser_id}")

                while self.continuous_mode:
                    try:
                        # Receive data with timeout
                        message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(message)

                        # Process laser data for this simulator
                        if data.get('type') == 'laser_data':
                            laser_id = data.get('laser_id')
                            berthing_id = data.get('berthing_id')

                            # More flexible matching: accept data for this laser
                            # Accept if laser_id matches or if no laser_id specified (broadcast)
                            laser_matches = (laser_id == self.laser_id or laser_id is None)
                            berthing_matches = (berthing_id == self.berthing_id or berthing_id is None)

                            if laser_matches and berthing_matches:
                                print(f"📊 [{self.device_id}] Received WebSocket data for laser {self.laser_id}: distance={data.get('distance')}, speed={data.get('speed')}, temp={data.get('temperature')}, strength={data.get('strength')}")

                                # IMMEDIATELY send VT data when we receive WebSocket data
                                # This respects the WebSocket's timing intervals
                                if self.continuous_mode:
                                    self.last_websocket_data_time = time.time()
                                    await self._send_vt_data(data)
    
                                    # Cancel any existing fallback task
                                    if self.fallback_task and not self.fallback_task.done():
                                        self.fallback_task.cancel()
                            else:
                                # Only log if it's actually laser data we're ignoring
                                if laser_id is not None:
                                    print(f"🚫 [{self.device_id}] Ignoring data for laser {laser_id} (expected laser {self.laser_id})")

                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        print(f"🔌 [{self.device_id}] WebSocket connection closed")
                        break
                    except Exception as e:
                        print(f"❌ [{self.device_id}] WebSocket error: {e}")
                        break

        except Exception as e:
            print(f"❌ [{self.device_id}] Failed to connect to WebSocket: {e}")
        finally:
            self.websocket_connection = None
            print(f"🔌 [{self.device_id}] WebSocket disconnected")

    async def _send_vt_data(self, real_data):
        """Send VT data to clients ONLY when WebSocket data is received for this laser"""
        if not real_data:
            return

        # More flexible laser ID matching - accept data for this laser
        laser_id = real_data.get('laser_id')
        if laser_id is not None and laser_id != self.laser_id:
            return

        # Use real WebSocket data - fields are directly in the data object
        # Handle None values by providing defaults
        speed = real_data.get('speed')
        if speed is None:
            speed = self.get_current_speed()  # Use simulated fallback

        distance = real_data.get('distance')
        if distance is None:
            distance = self.get_current_distance()  # Use simulated fallback

        signal_strength = real_data.get('strength', self.signal_strength)
        temp = real_data.get('temperature')
        if temp is None:
            temp = self.get_current_temperature()  # Use simulated fallback

        print(f"🌐 [{self.device_id}] Transmitting WebSocket data for laser {self.laser_id}: speed={speed:.2f}, dist={distance:.3f}")

        # VT mode expects: D speed distance signal temperature (4 values)
        measurement = f"D {speed:.2f} {distance:.3f} {signal_strength} {temp:.1f}"

        # Send to all connected clients for THIS laser only
        if self.clients:
            message = f"{measurement}\r\n".encode('utf-8')
            disconnected_clients = set()

            for writer in list(self.clients):
                try:
                    writer.write(message)
                    await writer.drain()
                    print(f"📤 [{self.device_id}] Sent VT data to client: {measurement.strip()}")
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    print(f"⚠️ Client disconnected, will remove: {e}")
                    disconnected_clients.add(writer)
                except Exception as e:
                    print(f"⚠️ Error sending to client: {e}")
                    disconnected_clients.add(writer)

            # Remove disconnected clients after iteration
            for client in disconnected_clients:
                self.clients.discard(client)

    async def _fallback_vt_sender(self):
        """Send simulated VT data when WebSocket data stops coming"""
        print(f"🔄 [{self.device_id}] Starting fallback VT sender")

        while self.continuous_mode:
            try:
                current_time = time.time()
                time_since_last_data = current_time - self.last_websocket_data_time

                # If no WebSocket data for 5 seconds, start sending simulated data
                if time_since_last_data > 5.0:
                    print(f"⚠️ [{self.device_id}] No WebSocket data for {time_since_last_data:.1f}s, using simulated fallback")

                    # Create simulated data
                    simulated_data = {
                        'laser_id': self.laser_id,
                        'distance': self.get_current_distance(),
                        'speed': self.get_current_speed(),
                        'temperature': self.get_current_temperature(),
                        'strength': self.signal_strength
                    }

                    await self._send_vt_data(simulated_data)

                # Send fallback data every 1 second if no WebSocket data
                await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [{self.device_id}] Fallback sender error: {e}")
                break

        print(f"🛑 [{self.device_id}] Fallback VT sender stopped")

    async def start(self):
        print(f"🚀 Starting Production LDM302 Simulator")
        print(f"📡 Port: {self.port}")
        print(f"🏭 Device: {self.device_id}")
        print("=" * 50)

        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                'localhost',
                self.port
            )

            print(f"✅ LDM302 Simulator ready on localhost:{self.port}")
            print(f"🎯 Simulating realistic ship measurements")
            print(f"🌊 Target movement: {'Enabled' if self.target_moving else 'Disabled'}")
            print(f"📊 Base distance: {self.base_distance}m")
            print(f"🌡️  Temperature: {self.temperature}°C")
            print()
            print("💡 Supported commands:")
            print("   ESC - Turn OFF")
            print("   DM  - Distance measurement")
            print("   VM  - Speed measurement")
            print("   DT  - Continuous distance")
            print("   VT  - Continuous speed")
            print("   TP  - Temperature")
            print("   ID  - Device ID")
            print("   VN  - Version")
            print()
            print("🔗 Connect your laser service to this simulator!")
            print("⚡ Press Ctrl+C to stop...")

            async with self.server:
                await self.server.serve_forever()

        except Exception as e:
            print(f"❌ Failed to start simulator: {e}")

    async def stop(self):
        # Stop continuous measurements
        self.continuous_mode = False

        # Cancel WebSocket task
        if self.websocket_task:
            self.websocket_task.cancel()
            try:
                await self.websocket_task
            except asyncio.CancelledError:
                pass
            self.websocket_task = None

        # Cancel fallback task
        if self.fallback_task:
            self.fallback_task.cancel()
            try:
                await self.fallback_task
            except asyncio.CancelledError:
                pass
            self.fallback_task = None

        # Close all client connections
        for writer in self.clients.copy():
            writer.close()
            await writer.wait_closed()
        self.clients.clear()

        # Close server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            print("🛑 LDM302 Simulator stopped")

class DualLaserSimulator:
    """Manager for running two laser simulators simultaneously"""
    def __init__(self):
        self.laser_21 = ProductionLDM302Simulator(port=2030, laser_id=21)
        self.laser_23 = ProductionLDM302Simulator(port=2031, laser_id=23)

    async def start_both(self):
        """Start both laser simulators concurrently"""
        print("🚀 Starting Dual Laser Production Simulator")
        print("📡 Laser 21 on port 2030")
        print("📡 Laser 23 on port 2031")
        print("=" * 60)

        try:
            # Start both simulators concurrently
            await asyncio.gather(
                self.laser_21.start(),
                self.laser_23.start()
            )
        except KeyboardInterrupt:
            print("\n🛑 Stopping both simulators...")
            await asyncio.gather(
                self.laser_21.stop(),
                self.laser_23.stop()
            )
            print("✅ Both simulators stopped cleanly")
        except Exception as e:
            print(f"❌ Dual simulator error: {e}")

async def main():
    """Main function to run the production simulator"""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "dual":
        # Run dual laser mode
        dual_sim = DualLaserSimulator()
        await dual_sim.start_both()
    else:
        # Run single laser mode (backward compatibility)
        port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2030
        laser_id = 21 if port == 2030 else 23
        simulator = ProductionLDM302Simulator(port, laser_id)

        try:
            await simulator.start()
        except KeyboardInterrupt:
            print("\n🛑 Stopping simulator...")
            await simulator.stop()
            print("✅ Simulator stopped cleanly")
        except Exception as e:
            print(f"❌ Simulator error: {e}")

if __name__ == "__main__":
    asyncio.run(main())