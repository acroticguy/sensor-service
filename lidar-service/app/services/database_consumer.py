import asyncio
import httpx
import time
from datetime import datetime, timezone
import json
from typing import List, Dict, Any
from ..core.config import settings
from ..core.logging_config import logger
from .berthing_data_core import fetch_berthing_data_for_sensor
from ..core.http_utils import SharedHTTPClient

class DatabaseConsumer:
    def __init__(self, postgrest_url: str):
        """
        Initializes the DatabaseConsumer.

        Args:
            postgrest_url (str): The base URL for the PostgREST API (e.g., "http://localhost:3000").
        """
        self.postgrest_url = postgrest_url
        self.stop_event = asyncio.Event()  # Async event for signaling stop
        self.consumer_tasks = {}  # Stores asyncio.Task objects, mapped by sensor_id
        self.fastapi_server = f"http://{settings.HOST}:{settings.PORT}/api/v1"
        # Shared HTTP client for efficient connection reuse
        self.http_client = SharedHTTPClient(timeout=10)

    async def start_consumer(self, sensor_id: str):
        """Starts an async consumer task for a specific sensor."""
        # Check if a task for this sensor_id is already running and not yet done
        if sensor_id in self.consumer_tasks and not self.consumer_tasks[sensor_id].done():
            logger.warning(f"Database consumer for sensor {sensor_id} is already running.")
            return

        logger.info(f"Starting database consumer task for sensor {sensor_id}...")
        # Create an asyncio.Task from the async _consume_async method
        task = asyncio.create_task(self._consume_async(sensor_id))
        self.consumer_tasks[sensor_id] = task
        logger.info(f"Database consumer started for sensor {sensor_id}")

    async def stop_consumer(self, sensor_id: str):
        """
        Stops a specific consumer task for a given sensor_id.
        This sends a cancellation signal to the task and waits for it to finish.
        """
        if sensor_id in self.consumer_tasks:
            task = self.consumer_tasks[sensor_id]
            if not task.done():
                logger.info(f"Signaling database consumer for {sensor_id} to stop...")
                task.cancel()  # Request cancellation of the task
                try:
                    await task  # Await its completion after cancellation
                    logger.info(f"Database consumer for {sensor_id} gracefully stopped.")
                except asyncio.CancelledError:
                    logger.info(f"Database consumer for {sensor_id} was cancelled.")
                except Exception as e:
                    logger.error(f"Error while stopping consumer for {sensor_id}: {e}")
            del self.consumer_tasks[sensor_id]
        else:
            logger.warning(f"No consumer task found for sensor {sensor_id} to stop.")

    async def stop_all_consumers(self):
        """Stops all running consumer tasks and closes the HTTP client."""
        self.stop_event.set()  # Signal all consumer tasks to stop their loops

        # The SharedHTTPClient handles its own cleanup
        logger.info("Database consumers stopped.")

        if self.consumer_tasks:
            tasks_to_wait_for = [task for task in self.consumer_tasks.values() if not task.done()]
            if tasks_to_wait_for:
                logger.info(f"Waiting for {len(tasks_to_wait_for)} consumer tasks to finish...")
                # Request cancellation for all remaining tasks
                for task in tasks_to_wait_for:
                    task.cancel()
                
                # Wait for all tasks to complete after cancellation, with a timeout
                # Use return_exceptions=True to ensure all tasks are processed even if some fail
                try:
                    await asyncio.gather(*tasks_to_wait_for, return_exceptions=True)
                except asyncio.CancelledError:
                    # This can happen if gather itself is cancelled or if tasks were already cancelled
                    logger.debug("Some consumer tasks were cancelled during asyncio.gather.")
                except Exception as e:
                    logger.error(f"An unexpected error occurred while stopping consumer tasks: {e}")
            self.consumer_tasks.clear()
        logger.info("All database consumers stopped.")


    async def _consume_async(self, sensor_id: str):
        """
        The main loop for the async consumer task. It fetches data from FastAPI
        and sends it to the PostgREST RPC endpoint `nv_insert_laser_data`.
        It attempts to do this once per second.
        """
        logger.info(f"Consumer for {sensor_id} ready to send data to PostgREST at {self.postgrest_url}.")

        rpc_url = f"{self.postgrest_url}/rpc/nv_insert_laser_data_serial"
        headers = {'Content-Type': 'application/json'}
        berthing_url = f"{self.fastapi_server}/berthing/data/sensor/{sensor_id}"

        # Loop until a stop signal is received
        while not self.stop_event.is_set():
            start_time_loop = time.monotonic()  # Record the start time of this loop iteration

            try:
                # Get data from the FastAPI server (assuming this endpoint is fast)
                logger.debug(f"Attempting to fetch data for sensor {sensor_id} from FastAPI at {berthing_url}...")
                
                # Get data from FastAPI
                res = await fetch_berthing_data_for_sensor(sensor_id)
                data = res

                # Extract and prepare payload
                # Use time.time() for current time if timestamp is missing, ensure UTC
                dt_iso_format = datetime.fromtimestamp(data.get('timestamp', time.time()), tz=timezone.utc).isoformat()
                distance = data.get('distance', 0.0)
                speed = data.get('trend_speed', 0.0)
                temperature = data.get('temperature')  # Allows None if not present
                strength = data.get('strength')        # Allows None if not present
                berthing_id = data.get('berthing_id')  # Allows None if not present

                logger.debug(f"Preparing to send data for sensor {sensor_id} at {dt_iso_format} to PostgREST.")

                payload = {
                    "p_serial": sensor_id,
                    "p_dt": dt_iso_format,
                    "p_distance": distance,
                    "p_speed": speed,
                    "p_temperature": temperature,
                    "p_strength": strength,
                    "p_berthing_id": berthing_id,
                }

                # Make POST request to PostgREST RPC endpoint using shared HTTP client
                async with self.http_client:
                    response = await self.http_client.post(rpc_url, json=payload)
                    res_json = response
                # Assuming the RPC function returns a JSON with a 'success' key
                if res_json.get('success'):
                    logger.info(f"Data for sensor {sensor_id} at {dt_iso_format} inserted successfully (ID: {res_json.get('id', 'unknown')}).")
                else:
                    logger.error(f"Failed to insert data for sensor {sensor_id} at {dt_iso_format}: {res_json.get('message', 'Unknown error')}")
                    
            except httpx.RequestError as req_err:
                # Catch network-related errors (connection refused, timeout, etc.) from httpx
                logger.exception(
                    f"Network or PostgREST API error for {sensor_id}: {req_err}. "
                    "Will retry in the next cycle."
                )
            except httpx.HTTPStatusError as http_err:
                # Catch HTTP status errors (4xx or 5xx responses)
                logger.exception(
                    f"PostgREST API returned an error status for {sensor_id}: {http_err.response.status_code} - {http_err.response.text}. "
                    "Will retry in the next cycle."
                )
            except json.JSONDecodeError as json_err:
                # Catch errors during JSON decoding of responses
                logger.exception(
                    f"JSON decoding error for sensor {sensor_id}: {json_err}. "
                    f"Response body: {res.text if 'res' in locals() else 'N/A'}. "
                    "Will retry in the next cycle."
                )
            except Exception as e:
                # Catch all other unexpected errors during data processing
                logger.exception(f"An unexpected error occurred in the consumer for {sensor_id}:")
            
            finally:
                # This block ensures we attempt to maintain the 1-second interval
                elapsed_time = time.monotonic() - start_time_loop
                sleep_duration = max(0, 1.0 - elapsed_time)  # Aim for a 1-second cycle duration
                
                if sleep_duration > 0:
                    logger.debug(f"Sleeping for {sleep_duration:.3f} seconds for sensor {sensor_id} to maintain 1-second interval.")
                    try:
                        await asyncio.sleep(sleep_duration)
                    except asyncio.CancelledError:
                        logger.info(f"Async sleep interrupted for sensor {sensor_id} due to task cancellation.")
                        break  # Exit the loop immediately if the task is cancelled during sleep
        
        logger.info(f"Consumer task for {sensor_id} has stopped.")