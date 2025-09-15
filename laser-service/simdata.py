#!/usr/bin/env python3
"""
Fake Laser Simulator
Simulates one or more laser devices that respond to commands and generate fake measurement data.

Usage:
    # Single laser on default port 2032 with ID 27
    python simdata.py

    # Single laser on custom port with custom ID
    python simdata.py 2030 25

    # Dual lasers (27 on 2032, 28 on 2033)
    python simdata.py dual
"""

import asyncio
import random
import time
import math
from datetime import datetime

class FakeLaserSimulator:
    def __init__(self, port=2032, laser_id=27):
        self.port = port
        self.laser_id = laser_id
        self.device_id = f"LDM302-SIM-{laser_id}"
        self.serial_number = f"SIM0000{laser_id:04d}"
        self.version = "V1.2.3"
        self.server = None

        # Simulation state based on provided examples
        self.base_distance = 121.0  # Average of examples: (122.3 + 121.32 + 119.965) / 3
        self.base_speed = -62.0     # Average of examples: (-77.8 + -54.4 + -53.9) / 3
        self.base_temperature = 37.6  # Average of examples
        self.base_signal_strength = 1000  # Average of examples: (592 + 1504 + 1627) / 3

        # Continuous mode tracking
        self.continuous_mode = False
        self.continuous_task = None
        self.clients = set()

        # Start time for time-based variations
        self.start_time = time.time()

        print(f"🎯 Initialized Fake Laser Simulator {self.device_id}")
        print(f"📊 Base distance: {self.base_distance}m")
        print(f"🌡️  Base temperature: {self.base_temperature}°C")

    def generate_distance(self):
        """Generate distance with realistic variations"""
        elapsed = time.time() - self.start_time

        # Simulate gradual movement with small oscillations
        trend = 0.1 * math.sin(elapsed * 0.01)  # Very slow trend
        oscillation = 0.5 * math.sin(elapsed * 0.1)  # Small oscillations
        noise = random.uniform(-0.2, 0.2)  # Random noise

        distance = self.base_distance + trend + oscillation + noise
        return max(0.1, distance)  # Ensure positive

    def generate_speed(self):
        """Generate speed with realistic variations"""
        elapsed = time.time() - self.start_time

        # Simulate speed variations around base speed
        variation = 5.0 * math.sin(elapsed * 0.05)  # Moderate speed changes
        noise = random.uniform(-2.0, 2.0)  # Random noise

        speed = self.base_speed + variation + noise
        return speed

    def generate_temperature(self):
        """Generate temperature with small variations"""
        elapsed = time.time() - self.start_time

        # Slow temperature drift
        drift = 0.2 * math.sin(elapsed * 0.005)
        noise = random.uniform(-0.1, 0.1)

        temperature = self.base_temperature + drift + noise
        return temperature

    def generate_signal_strength(self):
        """Generate signal strength with variations"""
        elapsed = time.time() - self.start_time

        # Signal strength varies with distance and other factors
        distance_factor = 1.0 / (1.0 + abs(self.generate_distance() - self.base_distance) / 10.0)
        variation = 200 * math.sin(elapsed * 0.03)
        noise = random.uniform(-100, 100)

        strength = int(self.base_signal_strength * distance_factor + variation + noise)
        return max(100, min(9999, strength))  # Clamp to valid range

    def handle_command(self, command):
        """Process commands and return appropriate responses"""
        cmd = command.upper().strip()

        # ESC character - Turn OFF
        if '\x1b' in command:
            self.continuous_mode = False
            if self.continuous_task:
                self.continuous_task.cancel()
                self.continuous_task = None
            return ""

        # Distance measurement
        elif cmd == 'DM':
            distance = self.generate_distance()
            return f"{distance:.3f} m"

        # Speed measurement
        elif cmd == 'VM':
            speed = self.generate_speed()
            return f"{speed:.2f} m/s"

        # Continuous distance
        elif cmd == 'DT':
            self.continuous_mode = True
            if not self.continuous_task or self.continuous_task.done():
                self.continuous_task = asyncio.create_task(self._send_continuous_data('DT'))
            distance = self.generate_distance()
            return f"{distance:.3f} m"

        # Continuous speed
        elif cmd == 'VT':
            self.continuous_mode = True
            if not self.continuous_task or self.continuous_task.done():
                self.continuous_task = asyncio.create_task(self._send_continuous_data('VT'))
            return "VT"

        # Temperature
        elif cmd == 'TP':
            temp = self.generate_temperature()
            return f"{temp:.1f}"

        # Device ID
        elif cmd == 'ID':
            return self.device_id

        # Version
        elif cmd == 'VN':
            return self.version

        # Serial number
        elif cmd == 'SN':
            return self.serial_number

        # Status
        elif cmd == 'ST':
            return "OK"

        # Default response for unknown commands
        else:
            print(f"⚠️  Unknown command: {repr(command)}")
            return "OK"

    async def _send_continuous_data(self, mode):
        """Send continuous measurement data"""
        print(f"📡 Starting continuous {mode} mode")

        while self.continuous_mode:
            try:
                # Generate current measurements
                distance = self.generate_distance()
                speed = self.generate_speed()
                temperature = self.generate_temperature()
                signal_strength = self.generate_signal_strength()

                # Format data based on mode
                if mode == 'DT':
                    # Distance continuous: D distance signal temperature
                    measurement = f"D {distance:.3f} {signal_strength} {temperature:.1f}"
                elif mode == 'VT':
                    # Speed continuous: D speed distance signal temperature
                    measurement = f"D {speed:.2f} {distance:.3f} {signal_strength} {temperature:.1f}"
                else:
                    continue

                # Send to all connected clients
                if self.clients:
                    message = f"{measurement}\r\n".encode('utf-8')
                    disconnected_clients = set()

                    for writer in list(self.clients):
                        try:
                            writer.write(message)
                            await writer.drain()
                            timestamp = datetime.now().strftime("%H:%M:%S")
                            print(f"[{timestamp}] 📤 Sent {mode} data: {measurement.strip()}")
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            disconnected_clients.add(writer)
                        except Exception as e:
                            print(f"⚠️ Error sending to client: {e}")
                            disconnected_clients.add(writer)

                    # Remove disconnected clients
                    for client in disconnected_clients:
                        self.clients.discard(client)

                # Wait before sending next measurement (simulate realistic timing)
                await asyncio.sleep(0.5)  # 2 Hz measurement rate

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Continuous data error: {e}")
                break

        print(f"🛑 Stopped continuous {mode} mode")

    async def handle_client(self, reader, writer):
        client_addr = writer.get_extra_info('peername')
        print(f"🔌 Client connected: {client_addr}")
        print(f"📡 Device ready: {self.device_id}")

        # Add client to the set
        self.clients.add(writer)

        try:
            while True:
                # Read data from client
                data = await reader.read(1024)
                if not data:
                    break

                message = data.decode('utf-8', errors='ignore').strip()

                # Handle the command
                response = self.handle_command(message)

                # Log the interaction
                timestamp = datetime.now().strftime("%H:%M:%S")
                if message == '\x1b':
                    print(f"[{timestamp}] 📨 ESC → 📤 (no response)")
                else:
                    print(f"[{timestamp}] 📨 {message} → 📤 {response}")

                # Send response if not empty
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
            print(f"🔌 Client disconnected: {client_addr}")
            writer.close()
            await writer.wait_closed()

    async def start(self):
        print("🚀 Starting Fake Laser Simulator")
        print(f"📡 Port: {self.port}")
        print(f"🏭 Device: {self.device_id}")
        print(f"🆔 Laser ID: {self.laser_id}")
        print("=" * 50)

        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                'localhost',
                self.port
            )

            print("✅ Fake Laser Simulator ready!")
            print("💡 Supported commands:")
            print("   ESC - Turn OFF")
            print("   DM  - Distance measurement")
            print("   VM  - Speed measurement")
            print("   DT  - Continuous distance")
            print("   VT  - Continuous speed")
            print("   TP  - Temperature")
            print("   ID  - Device ID")
            print("   VN  - Version")
            print("   SN  - Serial number")
            print("   ST  - Status")
            print()
            print("🔗 The laser service should now detect this simulator!")
            print("⚡ Press Ctrl+C to stop...")

            async with self.server:
                await self.server.serve_forever()

        except Exception as e:
            print(f"❌ Failed to start simulator: {e}")

    async def stop(self):
        # Stop continuous mode
        self.continuous_mode = False

        # Cancel continuous task
        if self.continuous_task:
            self.continuous_task.cancel()
            try:
                await self.continuous_task
            except asyncio.CancelledError:
                pass

        # Close all client connections
        for writer in self.clients.copy():
            writer.close()
            await writer.wait_closed()
        self.clients.clear()

        # Close server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        print("🛑 Fake Laser Simulator stopped")

async def main():
    """Main function to run the simulator"""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "dual":
        # Run dual laser mode
        await run_dual_simulators()
    else:
        # Run single laser mode
        port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2032
        laser_id = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 27

        simulator = FakeLaserSimulator(port=port, laser_id=laser_id)

        try:
            await simulator.start()
        except KeyboardInterrupt:
            print("\n🛑 Stopping simulator...")
            await simulator.stop()
            print("✅ Simulator stopped cleanly")
        except Exception as e:
            print(f"❌ Simulator error: {e}")

async def run_dual_simulators():
    """Run two simulators concurrently"""
    print("🚀 Starting Dual Fake Laser Simulators")
    print("📡 Laser 27 on port 2032")
    print("📡 Laser 28 on port 2033")
    print("=" * 60)

    # Create two simulators
    sim27 = FakeLaserSimulator(port=2032, laser_id=27)
    sim28 = FakeLaserSimulator(port=2033, laser_id=28)

    try:
        # Start both simulators concurrently
        await asyncio.gather(
            sim27.start(),
            sim28.start()
        )
    except KeyboardInterrupt:
        print("\n🛑 Stopping both simulators...")
        await asyncio.gather(
            sim27.stop(),
            sim28.stop()
        )
        print("✅ Both simulators stopped cleanly")
    except Exception as e:
        print(f"❌ Dual simulator error: {e}")

if __name__ == "__main__":
    asyncio.run(main())