import asyncio
import time
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from enum import Enum
import logging
from contextlib import asynccontextmanager

from .async_connection_pool import AsyncConnectionPool, AsyncConnection
from .circuit_breaker import AsyncCircuitBreaker, CircuitBreakerConfig
from .retry_handler import retry_async, RetryConfig, RetryStrategy
from .health_monitor import get_health_monitor
from .metrics_collector import get_metrics_collector, timed_method
from .graceful_shutdown import get_shutdown_manager
from .async_task_queue import AsyncTaskQueue
from models.laser_models import MeasurementResult, LaserOperatingMode

logger = logging.getLogger(__name__)


@dataclass
class LaserConnectionConfig:
    host: str
    port: int
    timeout: float = 5.0
    buffer_size: int = 4096
    keepalive: bool = True
    retry_attempts: int = 3


class ProductionLaserDevice:
    """Production-ready async laser device with all reliability features"""
    
    def __init__(self, laser_id: int, config: LaserConnectionConfig):
        self.laser_id = laser_id
        self.config = config
        self.is_connected = False
        self.operating_mode = LaserOperatingMode.OFF
        
        # Async infrastructure
        self.connection_pool = AsyncConnectionPool(max_connections=5, min_connections=1)
        self.circuit_breaker = AsyncCircuitBreaker(
            f"laser_{laser_id}",
            CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=30.0,
                timeout=config.timeout
            )
        )
        self.task_queue = AsyncTaskQueue(f"laser_{laser_id}_tasks", max_workers=2)
        
        # Monitoring
        self.health_monitor = get_health_monitor()
        self.metrics = get_metrics_collector()
        self.shutdown_manager = get_shutdown_manager()
        
        # Statistics
        self.stats = {
            'total_commands': 0,
            'successful_commands': 0,
            'failed_commands': 0,
            'avg_response_time': 0.0,
            'last_communication': 0.0,
            'uptime_start': time.time()
        }
        
        # Background tasks
        self.background_tasks = set()
        self.is_running = False
        
    async def initialize(self) -> bool:
        """Initialize laser device with all production features"""
        try:
            logger.info(f"Initializing production laser device {self.laser_id}")
            
            # Add connection to pool
            await self.connection_pool.add_connection(
                f"laser_{self.laser_id}",
                self._create_tcp_connection,
                host=self.config.host,
                port=self.config.port,
                timeout=self.config.timeout
            )
            
            # Start connection pool maintenance
            await self.connection_pool.start_background_maintenance()
            
            # Start task queue
            await self.task_queue.start()
            
            # Register task functions
            self.task_queue.register_function(self._send_command_task, "send_command")
            self.task_queue.register_function(self._measure_distance_task, "measure_distance")
            self.task_queue.register_function(self._measure_speed_task, "measure_speed")
            
            # Register health checks
            self.health_monitor.register_check(
                f"laser_{self.laser_id}_connection",
                self._health_check_connection,
                critical=True,
                tags={f"laser_{self.laser_id}", "connection"}
            )
            
            self.health_monitor.register_check(
                f"laser_{self.laser_id}_response_time",
                self._health_check_response_time,
                critical=False,
                tags={f"laser_{self.laser_id}", "performance"}
            )
            
            # Register shutdown handlers
            self.shutdown_manager.register_handler(
                f"laser_{self.laser_id}_shutdown",
                self._graceful_shutdown,
                timeout=30.0,
                priority=50
            )
            
            # Test initial connection
            await self._test_connection()
            
            self.is_connected = True
            self.is_running = True
            
            # Start background monitoring
            await self._start_background_tasks()
            
            logger.info(f"Laser device {self.laser_id} initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize laser device {self.laser_id}: {str(e)}")
            await self._cleanup()
            return False
            
    async def _create_tcp_connection(self, host: str, port: int, timeout: float):
        """Create TCP connection to laser device"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout
            )
            
            # Wrap in a connection object
            connection = {
                'reader': reader,
                'writer': writer,
                'host': host,
                'port': port,
                'connected_at': time.time()
            }
            
            logger.debug(f"TCP connection established to laser {self.laser_id}")
            return connection
            
        except Exception as e:
            logger.error(f"Failed to create TCP connection to laser {self.laser_id}: {str(e)}")
            raise
            
    async def _test_connection(self):
        """Test laser connection"""
        try:
            # Simple command test
            result = await self.send_command("ID")  # Get device ID
            if result:
                logger.info(f"Laser {self.laser_id} connection test successful")
                await self.metrics.increment_counter("laser_connection_tests", 1.0, {
                    "laser_id": str(self.laser_id),
                    "result": "success"
                })
            else:
                raise Exception("No response from device")
                
        except Exception as e:
            await self.metrics.increment_counter("laser_connection_tests", 1.0, {
                "laser_id": str(self.laser_id),
                "result": "failure"
            })
            raise
            
    @timed_method("laser_send_command_duration")
    @retry_async(RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
        retryable_exceptions=(ConnectionError, TimeoutError, OSError)
    ))
    async def send_command(self, command: str, parameters: str = "") -> str:
        """Send command to laser device with full production features"""
        
        # Use circuit breaker for reliability
        result = await self.circuit_breaker.execute(
            self._execute_command,
            command,
            parameters
        )
        
        # Update metrics
        await self.metrics.increment_counter("laser_commands_total", 1.0, {
            "laser_id": str(self.laser_id),
            "command": command,
            "status": "success"
        })
        
        return result
        
    async def _execute_command(self, command: str, parameters: str = "") -> str:
        """Execute command through connection pool"""
        connection_id = f"laser_{self.laser_id}"
        
        return await self.connection_pool.execute_on_connection(
            connection_id,
            self._send_raw_command,
            command,
            parameters
        )
        
    async def _send_raw_command(self, connection, command: str, parameters: str = "") -> str:
        """Send raw command to laser device"""
        try:
            # Build command string
            if parameters:
                cmd_string = f"{command} {parameters}\r\n"
            else:
                cmd_string = f"{command}\r\n"
                
            # Send command
            writer = connection['writer']
            writer.write(cmd_string.encode('ascii'))
            await writer.drain()
            
            # Read response
            reader = connection['reader']
            response = await asyncio.wait_for(
                reader.readuntil(b'\r\n'),
                timeout=self.config.timeout
            )
            
            result = response.decode('ascii').strip()
            
            # Update statistics
            self.stats['total_commands'] += 1
            self.stats['successful_commands'] += 1
            self.stats['last_communication'] = time.time()
            
            logger.debug(f"Laser {self.laser_id} command '{command}' -> '{result}'")
            return result
            
        except Exception as e:
            self.stats['failed_commands'] += 1
            logger.error(f"Command '{command}' failed for laser {self.laser_id}: {str(e)}")
            raise
            
    async def measure_distance_async(self) -> MeasurementResult:
        """Async distance measurement with task queue"""
        task_id = await self.task_queue.enqueue("measure_distance")
        result = await self.task_queue.get_task_result(task_id, timeout=30.0)
        
        if result and result.status.name == "COMPLETED":
            return result.result
        else:
            error = result.error if result else "Task timeout"
            raise Exception(f"Distance measurement failed: {error}")
            
    async def measure_speed_async(self) -> MeasurementResult:
        """Async speed measurement with task queue"""
        task_id = await self.task_queue.enqueue("measure_speed")
        result = await self.task_queue.get_task_result(task_id, timeout=30.0)
        
        if result and result.status.name == "COMPLETED":
            return result.result
        else:
            error = result.error if result else "Task timeout"
            raise Exception(f"Speed measurement failed: {error}")
            
    # Task functions for queue execution
    async def _send_command_task(self, command: str, parameters: str = "") -> str:
        """Task queue wrapper for send_command"""
        return await self.send_command(command, parameters)
        
    async def _measure_distance_task(self) -> MeasurementResult:
        """Task queue wrapper for distance measurement"""
        response = await self.send_command("DM")
        # Parse response into MeasurementResult (implement based on your protocol)
        return self._parse_measurement_response(response, "distance")
        
    async def _measure_speed_task(self) -> MeasurementResult:
        """Task queue wrapper for speed measurement"""
        response = await self.send_command("VM")
        return self._parse_measurement_response(response, "speed")
        
    def _parse_measurement_response(self, response: str, measurement_type: str) -> MeasurementResult:
        """Parse laser response into MeasurementResult"""
        # Implement based on your laser protocol
        # This is a placeholder implementation
        try:
            value = float(response)
            result = MeasurementResult()
            
            if measurement_type == "distance":
                result.distance = value / 1000.0  # Convert mm to m
            elif measurement_type == "speed":
                result.speed = value
                
            result.timestamp = time.time()
            return result
            
        except ValueError:
            # Handle error responses
            result = MeasurementResult()
            result.error_message = f"Parse error: {response}"
            return result
            
    # Health check functions
    async def _health_check_connection(self) -> bool:
        """Health check for laser connection"""
        try:
            await asyncio.wait_for(self.send_command("ID"), timeout=5.0)
            return True
        except Exception:
            return False
            
    async def _health_check_response_time(self) -> bool:
        """Health check for response time"""
        start_time = time.time()
        try:
            await self.send_command("ID")
            response_time = time.time() - start_time
            return response_time < 2.0  # 2 second threshold
        except Exception:
            return False
            
    async def _start_background_tasks(self):
        """Start background monitoring tasks"""
        if self.background_tasks:
            return
            
        # Heartbeat task
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self.background_tasks.add(heartbeat_task)
        
        # Metrics collection task
        metrics_task = asyncio.create_task(self._metrics_collection_loop())
        self.background_tasks.add(metrics_task)
        
        logger.debug(f"Started background tasks for laser {self.laser_id}")
        
    async def _heartbeat_loop(self):
        """Background heartbeat to keep connection alive"""
        while self.is_running:
            try:
                await self.send_command("ID")  # Simple heartbeat command
                await asyncio.sleep(60)  # Heartbeat every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Heartbeat failed for laser {self.laser_id}: {str(e)}")
                await asyncio.sleep(30)  # Retry in 30 seconds
                
    async def _metrics_collection_loop(self):
        """Background metrics collection"""
        while self.is_running:
            try:
                # Update device metrics
                await self.metrics.set_gauge(f"laser_{self.laser_id}_uptime", 
                                           time.time() - self.stats['uptime_start'])
                
                # Update statistics
                total_commands = self.stats['total_commands']
                if total_commands > 0:
                    success_rate = (self.stats['successful_commands'] / total_commands) * 100
                    await self.metrics.set_gauge(f"laser_{self.laser_id}_success_rate", success_rate)
                
                await asyncio.sleep(30)  # Collect every 30 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Metrics collection error for laser {self.laser_id}: {str(e)}")
                await asyncio.sleep(10)
                
    async def _graceful_shutdown(self):
        """Graceful shutdown handler"""
        logger.info(f"Shutting down laser device {self.laser_id}")
        
        self.is_running = False
        
        # Cancel background tasks
        for task in self.background_tasks:
            task.cancel()
            
        try:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error cancelling background tasks: {str(e)}")
            
        # Stop task queue
        await self.task_queue.stop()
        
        # Close connection pool
        await self.connection_pool.close()
        
        logger.info(f"Laser device {self.laser_id} shutdown complete")
        
    async def _cleanup(self):
        """Cleanup resources"""
        try:
            await self._graceful_shutdown()
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
            
    def get_stats(self) -> Dict[str, Any]:
        """Get device statistics"""
        success_rate = 0.0
        if self.stats['total_commands'] > 0:
            success_rate = (self.stats['successful_commands'] / self.stats['total_commands']) * 100
            
        return {
            'laser_id': self.laser_id,
            'connected': self.is_connected,
            'operating_mode': self.operating_mode.value,
            'uptime': time.time() - self.stats['uptime_start'],
            'total_commands': self.stats['total_commands'],
            'success_rate': round(success_rate, 2),
            'last_communication': self.stats['last_communication'],
            'circuit_breaker': self.circuit_breaker.get_stats(),
            'connection_pool': self.connection_pool.get_pool_stats(),
            'task_queue': self.task_queue.get_stats()
        }