#!/usr/bin/env python3
import asyncio
import websockets
import json
import psycopg2
from datetime import datetime
import sys

def connect_to_db():
    """Connect to PostgreSQL database"""
    try:
        conn = psycopg2.connect(
            host="178.18.252.174",
            database="navitrak",
            user="postadminsuper",
            password="kRnCQxFYRRVBqDAgj9YuBv",
            port="5432"
        )
        return conn
    except psycopg2.Error as e:
        print(f"Error connecting to database: {e}")
        return None

def get_laser_data(berthing_id):
    """Get all laser data for a berthing_id, sorted by timestamp and laser_id"""
    conn = connect_to_db()
    if not conn:
        return []

    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT laser_id, dt, distance, speed, temperature, strength, laser_data_id
            FROM laser_data
            WHERE berthing_id = %s
            ORDER BY dt ASC, laser_id ASC
        """, (berthing_id,))

        results = cursor.fetchall()
        laser_data = []

        for row in results:
            laser_data.append({
                'laser_id': row[0],
                'timestamp': row[1],
                'distance': row[2],
                'speed': row[3],
                'temperature': row[4],
                'strength': row[5],
                'laser_data_id': row[6]
            })

        return laser_data

    except psycopg2.Error as e:
        print(f"Database error: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

async def stream_single_laser(websocket, laser_id, records, global_start_time):
    """Stream data for a single laser with intervals between records"""
    if not records:
        return

    print(f"Starting stream for laser {laser_id} with {len(records)} records")

    for i, record in enumerate(records):
        current_time = record['timestamp']

        # Wait interval between this record and previous record
        if i > 0:
            prev_time = records[i-1]['timestamp']
            interval = (current_time - prev_time).total_seconds()

            # Use actual interval (or scale it down for faster demo)
            if interval > 0:
                await asyncio.sleep(interval)

        # Calculate elapsed time from global start
        elapsed_from_start = (current_time - global_start_time).total_seconds()

        # Send laser data
        message = {
            'type': 'laser_data',
            'laser_id': record['laser_id'],
            'timestamp': record['timestamp'].isoformat(),
            'distance': record['distance'],
            'speed': record['speed'],
            'temperature': record['temperature'],
            'strength': record['strength'],
            'laser_data_id': record['laser_data_id'],
            'elapsed_seconds': elapsed_from_start,
            'sequence': i + 1,
            'total_records': len(records)
        }

        try:
            await websocket.send(json.dumps(message))
            print(f"Laser {laser_id} [{i+1}/{len(records)}]: distance={record['distance']:.3f}, speed={record['speed']:.2f}, temp={record['temperature']:.1f} at {current_time.strftime('%H:%M:%S')}")

            # Add debug info for first few packets
            if i < 5:
                print(f"🔍 Debug packet {i+1}: {json.dumps(message, indent=2)[:200]}...")

        except websockets.exceptions.ConnectionClosed:
            print(f"Client disconnected during laser {laser_id} stream")
            break
        except Exception as e:
            print(f"Error sending laser {laser_id} data: {e}")
            break

    print(f"Completed laser {laser_id} stream")

# The handler function with correct signature for newer websockets version
async def echo(websocket):
    """Handle WebSocket connections"""
    print(f"New client connected")

    try:
        # Send welcome message
        await websocket.send(json.dumps({
            'type': 'welcome',
            'message': 'Connected to laser data streamer'
        }))

        # Wait for client message with berthing_id
        message = await websocket.recv()
        data = json.loads(message)
        berthing_id = data.get('berthing_id')

        if not berthing_id:
            await websocket.send(json.dumps({'error': 'berthing_id required'}))
            return

        print(f"Starting stream for berthing_id: {berthing_id}")

        # Get laser data from database
        laser_data = get_laser_data(berthing_id)
        if not laser_data:
            await websocket.send(json.dumps({'error': f'No data found for berthing_id {berthing_id}'}))
            return

        print(f"Found {len(laser_data)} records")

        # Send stream info
        await websocket.send(json.dumps({
            'type': 'info',
            'total_records': len(laser_data),
            'start_time': laser_data[0]['timestamp'].isoformat() if laser_data else None,
            'berthing_id': berthing_id
        }))

        # Group data by laser_id
        laser_groups = {}
        for record in laser_data:
            laser_id = record['laser_id']
            if laser_id not in laser_groups:
                laser_groups[laser_id] = []
            laser_groups[laser_id].append(record)

        print(f"Streaming {len(laser_groups)} lasers: {list(laser_groups.keys())}")

        # Find the global start time (earliest timestamp across all lasers)
        global_start_time = min(record['timestamp'] for record in laser_data)
        print(f"Global start time: {global_start_time}")

        # Stream each laser independently
        tasks = []
        for laser_id, records in laser_groups.items():
            task = asyncio.create_task(stream_single_laser(websocket, laser_id, records, global_start_time))
            tasks.append(task)

        # Wait for all streams to complete
        await asyncio.gather(*tasks)

        # Send completion message
        await websocket.send(json.dumps({
            'type': 'complete',
            'message': f'Streaming completed for berthing_id {berthing_id}'
        }))

        print(f"Completed streaming for berthing_id {berthing_id}")

    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
    except json.JSONDecodeError:
        await websocket.send(json.dumps({'error': 'Invalid JSON message'}))
    except Exception as e:
        print(f"Error: {e}")
        try:
            await websocket.send(json.dumps({'error': str(e)}))
        except:
            pass

async def main():
    if len(sys.argv) < 2:
        print("Usage: python websocket_final.py <port>")
        print("Example: python websocket_final.py 8765")
        return

    port = int(sys.argv[1])

    print(f"Starting WebSocket server on port {port}")
    print(f"WebSocket server running on ws://localhost:{port}")
    print("Press Ctrl+C to stop")

    # Start server with correct async context
    server = await websockets.serve(echo, "localhost", port)
    await server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")