import asyncio
import signal
import sys
import os
from typing import List
from app.core.logging_config import logger
from app.core.config import settings
from app.services.database_consumer import DatabaseConsumer

class DBStreamerService:
    def __init__(self):
        self.db_cons: DatabaseConsumer = None
        self.db_consumer_task: asyncio.Task = None
        self.shutdown_event: asyncio.Event = asyncio.Event()

    async def start_db_streaming(self, sensor_ids: List[str]):
        if self.db_consumer_task and not self.db_consumer_task.done():
            logger.info("Database streaming is already active.")
            return

        if not sensor_ids:
            logger.warning("No sensor IDs provided. Cannot start DB streaming.")
            return

        logger.info(f"Initializing database streamer for sensors: {sensor_ids}...")
        try:
            self.db_cons = DatabaseConsumer(postgrest_url=settings.DB_HOST)
            logger.info("DatabaseConsumer initialized successfully.")

            async def _run_consumers():
                try:
                    for sensor_id in sensor_ids:
                        logger.info(f"Starting consumer for sensor {sensor_id}...")
                        await self.db_cons.start_consumer(sensor_id)
                        logger.info(f"Consumer for sensor {sensor_id} started.")
                    logger.info("Database consumers are running.")
                    await self.shutdown_event.wait() # Wait for shutdown signal
                except Exception as e:
                    logger.exception(f"An error occurred in the database consumer loop: {e}")
                finally:
                    if self.db_cons:
                        logger.info("Stopping all database consumers...")
                        await self.db_cons.stop_all_consumers()
                    logger.info("Database consumer task exited.")

            self.db_consumer_task = asyncio.create_task(_run_consumers())
            logger.info("Database streaming started successfully.")

        except Exception as e:
            logger.exception(f"An unexpected error occurred during DB streamer setup: {e}")
            raise

    async def stop_db_streaming(self):
        if self.db_consumer_task and not self.db_consumer_task.done():
            logger.info("Stopping database streaming...")
            self.shutdown_event.set() # Signal consumers to stop
            self.db_consumer_task.cancel()
            try:
                await self.db_consumer_task
            except asyncio.CancelledError:
                logger.info("Database consumer task cancelled.")
            self.db_consumer_task = None
            self.shutdown_event.clear() # Reset event for future starts
            logger.info("Database streaming stopped.")
        else:
            logger.info("Database streaming is not active.")

db_streamer_service = DBStreamerService()