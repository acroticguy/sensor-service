import asyncio
import asyncpg
import json
import time
from typing import Dict, Any, List, Optional, Union, Callable
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "postgres"
    username: str = "postgres"
    password: str = ""
    min_connections: int = 5
    max_connections: int = 20
    connection_timeout: float = 10.0
    command_timeout: float = 30.0
    ssl_required: bool = False
    connection_kwargs: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.connection_kwargs is None:
            self.connection_kwargs = {}


class AsyncDatabasePool:
    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.pool: Optional[asyncpg.Pool] = None
        self.is_connected = False
        self.connection_stats = {
            'total_connections': 0,
            'active_connections': 0,
            'total_queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'avg_query_time': 0.0,
            'pool_created_at': None
        }
        self.lock = asyncio.Lock()
        
    async def connect(self):
        """Initialize database connection pool"""
        if self.is_connected and self.pool:
            return
            
        try:
            logger.info("Creating database connection pool...")
            
            # Build connection parameters
            connection_params = {
                'host': self.config.host,
                'port': self.config.port,
                'database': self.config.database,
                'user': self.config.username,
                'password': self.config.password,
                'min_size': self.config.min_connections,
                'max_size': self.config.max_connections,
                'timeout': self.config.connection_timeout,
                'command_timeout': self.config.command_timeout,
                **self.config.connection_kwargs
            }
            
            # Create connection pool
            self.pool = await asyncpg.create_pool(**connection_params)
            
            # Test connection
            async with self.pool.acquire() as conn:
                await conn.fetchval('SELECT 1')
                
            self.is_connected = True
            self.connection_stats['pool_created_at'] = time.time()
            self.connection_stats['total_connections'] = self.config.max_connections
            
            logger.info(f"Database pool created successfully ({self.config.min_connections}-{self.config.max_connections} connections)")
            
        except Exception as e:
            logger.error(f"Failed to create database pool: {str(e)}")
            self.is_connected = False
            raise
            
    async def disconnect(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            self.is_connected = False
            logger.info("Database pool closed")
            
    @asynccontextmanager
    async def acquire_connection(self):
        """Acquire a connection from the pool"""
        if not self.is_connected or not self.pool:
            await self.connect()
            
        async with self.pool.acquire() as connection:
            async with self.lock:
                self.connection_stats['active_connections'] += 1
                
            try:
                yield connection
            finally:
                async with self.lock:
                    self.connection_stats['active_connections'] -= 1
                    
    async def execute_query(self, query: str, *args, fetch_mode: str = 'none') -> Any:
        """Execute a database query
        
        Args:
            query: SQL query to execute
            args: Query parameters
            fetch_mode: 'none', 'one', 'many', or 'val'
        """
        start_time = time.time()
        
        try:
            async with self.acquire_connection() as conn:
                if fetch_mode == 'none':
                    result = await conn.execute(query, *args)
                elif fetch_mode == 'one':
                    result = await conn.fetchrow(query, *args)
                elif fetch_mode == 'many':
                    result = await conn.fetch(query, *args)
                elif fetch_mode == 'val':
                    result = await conn.fetchval(query, *args)
                else:
                    raise ValueError(f"Invalid fetch_mode: {fetch_mode}")
                    
                # Update statistics
                query_time = time.time() - start_time
                await self._update_query_stats(True, query_time)
                
                return result
                
        except Exception as e:
            query_time = time.time() - start_time
            await self._update_query_stats(False, query_time)
            logger.error(f"Database query failed: {str(e)}")
            raise
            
    async def execute_transaction(self, queries: List[Dict[str, Any]]) -> List[Any]:
        """Execute multiple queries in a transaction
        
        Args:
            queries: List of query dictionaries with 'query', 'args', and 'fetch_mode'
        """
        start_time = time.time()
        results = []
        
        try:
            async with self.acquire_connection() as conn:
                async with conn.transaction():
                    for query_info in queries:
                        query = query_info['query']
                        args = query_info.get('args', ())
                        fetch_mode = query_info.get('fetch_mode', 'none')
                        
                        if fetch_mode == 'none':
                            result = await conn.execute(query, *args)
                        elif fetch_mode == 'one':
                            result = await conn.fetchrow(query, *args)
                        elif fetch_mode == 'many':
                            result = await conn.fetch(query, *args)
                        elif fetch_mode == 'val':
                            result = await conn.fetchval(query, *args)
                        else:
                            raise ValueError(f"Invalid fetch_mode: {fetch_mode}")
                            
                        results.append(result)
                        
                # Update statistics
                query_time = time.time() - start_time
                await self._update_query_stats(True, query_time, len(queries))
                
                return results
                
        except Exception as e:
            query_time = time.time() - start_time
            await self._update_query_stats(False, query_time, len(queries))
            logger.error(f"Database transaction failed: {str(e)}")
            raise
            
    async def _update_query_stats(self, success: bool, query_time: float, query_count: int = 1):
        """Update query statistics"""
        async with self.lock:
            self.connection_stats['total_queries'] += query_count
            
            if success:
                self.connection_stats['successful_queries'] += query_count
            else:
                self.connection_stats['failed_queries'] += query_count
                
            # Update average query time
            total_successful = self.connection_stats['successful_queries']
            if total_successful > 0:
                current_avg = self.connection_stats['avg_query_time']
                self.connection_stats['avg_query_time'] = (
                    (current_avg * (total_successful - query_count) + query_time) / total_successful
                )
                
    async def health_check(self) -> Dict[str, Any]:
        """Check database health"""
        if not self.is_connected or not self.pool:
            return {
                'healthy': False,
                'error': 'Database pool not initialized'
            }
            
        try:
            start_time = time.time()
            
            async with self.acquire_connection() as conn:
                # Simple health check query
                await conn.fetchval('SELECT 1')
                
            response_time = time.time() - start_time
            
            return {
                'healthy': True,
                'response_time': response_time,
                'pool_stats': self.get_pool_stats()
            }
            
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e)
            }
            
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        pool_stats = {}
        
        if self.pool:
            pool_stats.update({
                'size': self.pool.get_size(),
                'min_size': self.pool.get_min_size(),
                'max_size': self.pool.get_max_size(),
                'idle_connections': self.pool.get_idle_size()
            })
            
        pool_stats.update(self.connection_stats)
        
        # Calculate success rate
        total_queries = self.connection_stats['total_queries']
        if total_queries > 0:
            success_rate = (self.connection_stats['successful_queries'] / total_queries) * 100
            pool_stats['success_rate'] = round(success_rate, 2)
        else:
            pool_stats['success_rate'] = 0.0
            
        return pool_stats


class AsyncDatabaseService:
    """High-level database service with common operations"""
    
    def __init__(self, config: DatabaseConfig):
        self.pool = AsyncDatabasePool(config)
        
    async def start(self):
        """Start database service"""
        await self.pool.connect()
        
    async def stop(self):
        """Stop database service"""
        await self.pool.disconnect()
        
    async def insert(self, table: str, data: Dict[str, Any], returning: str = None) -> Any:
        """Insert data into table"""
        columns = list(data.keys())
        values = list(data.values())
        placeholders = [f'${i+1}' for i in range(len(values))]
        
        query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
        
        if returning:
            query += f" RETURNING {returning}"
            fetch_mode = 'val' if returning.count(',') == 0 and returning != '*' else 'one'
        else:
            fetch_mode = 'none'
            
        return await self.pool.execute_query(query, *values, fetch_mode=fetch_mode)
        
    async def update(self, table: str, data: Dict[str, Any], where: Dict[str, Any], returning: str = None) -> Any:
        """Update data in table"""
        set_clauses = [f"{col} = ${i+1}" for i, col in enumerate(data.keys())]
        where_clauses = [f"{col} = ${i+len(data)+1}" for i, col in enumerate(where.keys())]
        
        query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)}"
        
        values = list(data.values()) + list(where.values())
        
        if returning:
            query += f" RETURNING {returning}"
            fetch_mode = 'val' if returning.count(',') == 0 and returning != '*' else 'one'
        else:
            fetch_mode = 'none'
            
        return await self.pool.execute_query(query, *values, fetch_mode=fetch_mode)
        
    async def delete(self, table: str, where: Dict[str, Any], returning: str = None) -> Any:
        """Delete data from table"""
        where_clauses = [f"{col} = ${i+1}" for i, col in enumerate(where.keys())]
        query = f"DELETE FROM {table} WHERE {' AND '.join(where_clauses)}"
        
        values = list(where.values())
        
        if returning:
            query += f" RETURNING {returning}"
            fetch_mode = 'val' if returning.count(',') == 0 and returning != '*' else 'one'
        else:
            fetch_mode = 'none'
            
        return await self.pool.execute_query(query, *values, fetch_mode=fetch_mode)
        
    async def select(self, table: str, 
                    columns: str = "*", 
                    where: Dict[str, Any] = None,
                    order_by: str = None,
                    limit: int = None,
                    offset: int = None,
                    fetch_one: bool = False) -> Union[List[Any], Any]:
        """Select data from table"""
        query = f"SELECT {columns} FROM {table}"
        values = []
        
        if where:
            where_clauses = [f"{col} = ${i+1}" for i, col in enumerate(where.keys())]
            query += f" WHERE {' AND '.join(where_clauses)}"
            values = list(where.values())
            
        if order_by:
            query += f" ORDER BY {order_by}"
            
        if limit:
            query += f" LIMIT {limit}"
            
        if offset:
            query += f" OFFSET {offset}"
            
        fetch_mode = 'one' if fetch_one else 'many'
        return await self.pool.execute_query(query, *values, fetch_mode=fetch_mode)
        
    async def exists(self, table: str, where: Dict[str, Any]) -> bool:
        """Check if record exists"""
        where_clauses = [f"{col} = ${i+1}" for i, col in enumerate(where.keys())]
        query = f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {' AND '.join(where_clauses)})"
        values = list(where.values())
        
        return await self.pool.execute_query(query, *values, fetch_mode='val')
        
    async def count(self, table: str, where: Dict[str, Any] = None) -> int:
        """Count records in table"""
        query = f"SELECT COUNT(*) FROM {table}"
        values = []
        
        if where:
            where_clauses = [f"{col} = ${i+1}" for i, col in enumerate(where.keys())]
            query += f" WHERE {' AND '.join(where_clauses)}"
            values = list(where.values())
            
        return await self.pool.execute_query(query, *values, fetch_mode='val')
        
    async def bulk_insert(self, table: str, data_list: List[Dict[str, Any]]) -> str:
        """Bulk insert multiple records"""
        if not data_list:
            return "INSERT 0 0"
            
        # Assume all records have the same structure
        first_record = data_list[0]
        columns = list(first_record.keys())
        
        # Build bulk insert query
        value_rows = []
        all_values = []
        
        for i, record in enumerate(data_list):
            row_placeholders = []
            for j, col in enumerate(columns):
                placeholder_idx = i * len(columns) + j + 1
                row_placeholders.append(f'${placeholder_idx}')
                all_values.append(record[col])
                
            value_rows.append(f"({', '.join(row_placeholders)})")
            
        query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES {', '.join(value_rows)}"
        
        return await self.pool.execute_query(query, *all_values, fetch_mode='none')
        
    async def execute_raw(self, query: str, *args, fetch_mode: str = 'none') -> Any:
        """Execute raw SQL query"""
        return await self.pool.execute_query(query, *args, fetch_mode=fetch_mode)
        
    async def health_check(self) -> Dict[str, Any]:
        """Database health check"""
        return await self.pool.health_check()


# Global database service instance
database_service: Optional[AsyncDatabaseService] = None


def get_database_service(config: DatabaseConfig = None) -> AsyncDatabaseService:
    """Get or create global database service"""
    global database_service
    if database_service is None:
        if config is None:
            # Default configuration
            config = DatabaseConfig()
        database_service = AsyncDatabaseService(config)
    return database_service


# Decorator for database operations
def with_database(func: Callable) -> Callable:
    """Decorator that provides database service to function"""
    async def wrapper(*args, **kwargs):
        db = get_database_service()
        return await func(db, *args, **kwargs)
    return wrapper


# Context manager for database transactions
@asynccontextmanager
async def database_transaction():
    """Context manager for database transactions"""
    db = get_database_service()
    async with db.pool.acquire_connection() as conn:
        async with conn.transaction():
            yield conn