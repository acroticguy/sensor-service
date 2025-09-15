import asyncio
import time
from typing import Dict, Set, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    IDLE = "idle"
    ACTIVE = "active" 
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass
class ConnectionStats:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time: float = 0.0
    last_used: float = 0.0
    created_at: float = 0.0
    reconnect_attempts: int = 0


class AsyncConnection:
    def __init__(self, connection_id: str, create_func: Callable, **kwargs):
        self.connection_id = connection_id
        self.create_func = create_func
        self.kwargs = kwargs
        self.connection = None
        self.state = ConnectionState.IDLE
        self.stats = ConnectionStats(created_at=time.time())
        self.lock = asyncio.Lock()
        self.last_health_check = 0.0
        self.health_check_interval = 30.0  # seconds
        
    async def connect(self):
        """Establish connection"""
        async with self.lock:
            if self.state == ConnectionState.ACTIVE and self.connection:
                return self.connection
                
            try:
                self.state = ConnectionState.RECONNECTING
                self.connection = await self.create_func(**self.kwargs)
                self.state = ConnectionState.ACTIVE
                logger.info(f"Connection {self.connection_id} established")
                return self.connection
                
            except Exception as e:
                self.state = ConnectionState.FAILED
                logger.error(f"Failed to create connection {self.connection_id}: {str(e)}")
                raise
                
    async def disconnect(self):
        """Close connection"""
        async with self.lock:
            if self.connection:
                try:
                    if hasattr(self.connection, 'close'):
                        await self.connection.close()
                    elif hasattr(self.connection, 'disconnect'):
                        await self.connection.disconnect()
                except Exception as e:
                    logger.warning(f"Error closing connection {self.connection_id}: {str(e)}")
                finally:
                    self.connection = None
                    self.state = ConnectionState.CLOSED
                    
    async def is_healthy(self) -> bool:
        """Check if connection is healthy"""
        if self.state != ConnectionState.ACTIVE or not self.connection:
            return False
            
        # Rate limit health checks
        now = time.time()
        if now - self.last_health_check < self.health_check_interval:
            return True
            
        try:
            # Custom health check if available
            if hasattr(self.connection, 'ping'):
                await self.connection.ping()
            elif hasattr(self.connection, 'health_check'):
                await self.connection.health_check()
            
            self.last_health_check = now
            return True
            
        except Exception as e:
            logger.warning(f"Health check failed for {self.connection_id}: {str(e)}")
            return False
            
    async def execute(self, operation: Callable, *args, **kwargs):
        """Execute operation with connection"""
        if not await self.is_healthy():
            await self.connect()
            
        start_time = time.time()
        try:
            self.stats.total_requests += 1
            result = await operation(self.connection, *args, **kwargs)
            
            # Update stats
            response_time = time.time() - start_time
            self.stats.successful_requests += 1
            self.stats.avg_response_time = (
                (self.stats.avg_response_time * (self.stats.successful_requests - 1) + response_time) /
                self.stats.successful_requests
            )
            self.stats.last_used = time.time()
            
            return result
            
        except Exception as e:
            self.stats.failed_requests += 1
            logger.error(f"Operation failed on {self.connection_id}: {str(e)}")
            
            # Mark connection as unhealthy on error
            self.state = ConnectionState.FAILED
            raise


class AsyncConnectionPool:
    def __init__(self, max_connections: int = 10, min_connections: int = 2):
        self.max_connections = max_connections
        self.min_connections = min_connections
        self.connections: Dict[str, AsyncConnection] = {}
        self.available_connections: Set[str] = set()
        self.connection_semaphore = asyncio.Semaphore(max_connections)
        self.pool_lock = asyncio.Lock()
        self.is_closed = False
        self.background_tasks: Set[asyncio.Task] = set()
        
    async def add_connection(self, connection_id: str, create_func: Callable, **kwargs):
        """Add a new connection to the pool"""
        async with self.pool_lock:
            if connection_id in self.connections:
                logger.warning(f"Connection {connection_id} already exists in pool")
                return
                
            connection = AsyncConnection(connection_id, create_func, **kwargs)
            self.connections[connection_id] = connection
            
            # Pre-connect if we're below minimum
            if len(self.available_connections) < self.min_connections:
                try:
                    await connection.connect()
                    self.available_connections.add(connection_id)
                    logger.info(f"Pre-connected {connection_id}")
                except Exception as e:
                    logger.error(f"Failed to pre-connect {connection_id}: {str(e)}")
                    
    async def remove_connection(self, connection_id: str):
        """Remove connection from pool"""
        async with self.pool_lock:
            if connection_id in self.connections:
                connection = self.connections[connection_id]
                await connection.disconnect()
                del self.connections[connection_id]
                self.available_connections.discard(connection_id)
                logger.info(f"Removed connection {connection_id} from pool")
                
    @asynccontextmanager
    async def get_connection(self, connection_id: str):
        """Get connection with automatic resource management"""
        if self.is_closed:
            raise RuntimeError("Connection pool is closed")
            
        async with self.connection_semaphore:
            connection = self.connections.get(connection_id)
            if not connection:
                raise ValueError(f"Connection {connection_id} not found in pool")
                
            try:
                await connection.connect()
                yield connection
            finally:
                # Connection cleanup happens automatically
                pass
                
    async def execute_on_connection(self, connection_id: str, operation: Callable, *args, **kwargs):
        """Execute operation on specific connection"""
        async with self.get_connection(connection_id) as connection:
            return await connection.execute(operation, *args, **kwargs)
            
    async def start_background_maintenance(self):
        """Start background tasks for pool maintenance"""
        if not self.background_tasks:
            # Health check task
            health_task = asyncio.create_task(self._health_check_loop())
            self.background_tasks.add(health_task)
            
            # Connection balancer task
            balance_task = asyncio.create_task(self._connection_balancer_loop())
            self.background_tasks.add(balance_task)
            
            logger.info("Started background maintenance tasks")
            
    async def _health_check_loop(self):
        """Background health check for all connections"""
        while not self.is_closed:
            try:
                async with self.pool_lock:
                    for connection_id, connection in self.connections.items():
                        if not await connection.is_healthy():
                            logger.warning(f"Connection {connection_id} is unhealthy")
                            self.available_connections.discard(connection_id)
                            
                            # Attempt reconnection
                            try:
                                await connection.connect()
                                self.available_connections.add(connection_id)
                                logger.info(f"Reconnected {connection_id}")
                            except Exception as e:
                                logger.error(f"Failed to reconnect {connection_id}: {str(e)}")
                                
            except Exception as e:
                logger.error(f"Health check loop error: {str(e)}")
                
            await asyncio.sleep(30)  # Health check every 30 seconds
            
    async def _connection_balancer_loop(self):
        """Maintain optimal connection count"""
        while not self.is_closed:
            try:
                async with self.pool_lock:
                    available_count = len(self.available_connections)
                    
                    # Add connections if below minimum
                    if available_count < self.min_connections:
                        for connection_id, connection in self.connections.items():
                            if connection_id not in self.available_connections:
                                try:
                                    await connection.connect()
                                    self.available_connections.add(connection_id)
                                    logger.info(f"Auto-connected {connection_id}")
                                    break
                                except Exception as e:
                                    logger.error(f"Auto-connect failed for {connection_id}: {str(e)}")
                                    
            except Exception as e:
                logger.error(f"Connection balancer error: {str(e)}")
                
            await asyncio.sleep(60)  # Balance every minute
            
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get pool statistics"""
        stats = {
            "total_connections": len(self.connections),
            "available_connections": len(self.available_connections),
            "max_connections": self.max_connections,
            "min_connections": self.min_connections,
            "is_closed": self.is_closed,
            "connections": {}
        }
        
        for connection_id, connection in self.connections.items():
            stats["connections"][connection_id] = {
                "state": connection.state.value,
                "total_requests": connection.stats.total_requests,
                "successful_requests": connection.stats.successful_requests,
                "failed_requests": connection.stats.failed_requests,
                "avg_response_time": connection.stats.avg_response_time,
                "uptime": time.time() - connection.stats.created_at,
                "last_used": connection.stats.last_used
            }
            
        return stats
        
    async def close(self):
        """Close all connections and cleanup"""
        self.is_closed = True
        
        # Cancel background tasks
        for task in self.background_tasks:
            task.cancel()
            
        try:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error cancelling background tasks: {str(e)}")
            
        # Close all connections
        async with self.pool_lock:
            for connection in self.connections.values():
                await connection.disconnect()
                
        logger.info("Connection pool closed")