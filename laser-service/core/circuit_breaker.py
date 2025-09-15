import asyncio
import time
from typing import Callable, Any, Optional, Dict
from dataclasses import dataclass
from enum import Enum
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit breaker is open, calls fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Number of failures before opening
    recovery_timeout: float = 60.0      # Seconds to wait before trying again
    success_threshold: int = 3          # Successes needed to close from half-open
    timeout: float = 5.0                # Operation timeout
    expected_exception: tuple = (Exception,)  # Exceptions that count as failures


@dataclass
class CircuitBreakerStats:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    timeouts: int = 0
    circuit_opens: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0


class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open"""
    pass


class CircuitBreakerTimeoutException(Exception):
    """Exception raised when operation times out"""
    pass


class AsyncCircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.stats = CircuitBreakerStats()
        self.lock = asyncio.Lock()
        
    async def __call__(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        return await self.execute(func, *args, **kwargs)
        
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker logic"""
        async with self.lock:
            await self._update_state()
            
            if self.state == CircuitState.OPEN:
                self.stats.total_calls += 1
                raise CircuitBreakerOpenException(
                    f"Circuit breaker '{self.name}' is OPEN"
                )
        
        # Execute outside of lock for better concurrency
        start_time = time.time()
        self.stats.total_calls += 1
        
        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=self.config.timeout
            )
            
            # Record success
            await self._record_success()
            return result
            
        except asyncio.TimeoutError:
            self.stats.timeouts += 1
            await self._record_failure()
            raise CircuitBreakerTimeoutException(
                f"Operation timed out after {self.config.timeout}s"
            )
            
        except self.config.expected_exception as e:
            await self._record_failure()
            raise
            
    async def _update_state(self):
        """Update circuit breaker state based on current conditions"""
        current_time = time.time()
        
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if (current_time - self.last_failure_time) >= self.config.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info(f"Circuit breaker '{self.name}' moved to HALF_OPEN")
                
        elif self.state == CircuitState.HALF_OPEN:
            # In half-open, we let calls through to test recovery
            pass
            
    async def _record_success(self):
        """Record successful execution"""
        async with self.lock:
            self.stats.successful_calls += 1
            self.stats.last_success_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    logger.info(f"Circuit breaker '{self.name}' moved to CLOSED")
                    
    async def _record_failure(self):
        """Record failed execution"""
        async with self.lock:
            self.stats.failed_calls += 1
            self.stats.last_failure_time = time.time()
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.CLOSED:
                self.failure_count += 1
                if self.failure_count >= self.config.failure_threshold:
                    self.state = CircuitState.OPEN
                    self.stats.circuit_opens += 1
                    logger.warning(f"Circuit breaker '{self.name}' moved to OPEN")
                    
            elif self.state == CircuitState.HALF_OPEN:
                # Any failure in half-open state moves back to open
                self.state = CircuitState.OPEN
                self.failure_count = self.config.failure_threshold
                self.stats.circuit_opens += 1
                logger.warning(f"Circuit breaker '{self.name}' moved back to OPEN")
                
    @asynccontextmanager
    async def context(self):
        """Context manager for circuit breaker"""
        if self.state == CircuitState.OPEN:
            async with self.lock:
                await self._update_state()
                if self.state == CircuitState.OPEN:
                    raise CircuitBreakerOpenException(
                        f"Circuit breaker '{self.name}' is OPEN"
                    )
        
        start_time = time.time()
        self.stats.total_calls += 1
        
        try:
            yield self
            await self._record_success()
            
        except self.config.expected_exception as e:
            await self._record_failure()
            raise
            
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        success_rate = 0.0
        if self.stats.total_calls > 0:
            success_rate = (self.stats.successful_calls / self.stats.total_calls) * 100
            
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "stats": {
                "total_calls": self.stats.total_calls,
                "successful_calls": self.stats.successful_calls,
                "failed_calls": self.stats.failed_calls,
                "timeouts": self.stats.timeouts,
                "circuit_opens": self.stats.circuit_opens,
                "success_rate": round(success_rate, 2),
                "last_failure_time": self.stats.last_failure_time,
                "last_success_time": self.stats.last_success_time
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout
            }
        }
        
    async def reset(self):
        """Reset circuit breaker to closed state"""
        async with self.lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            logger.info(f"Circuit breaker '{self.name}' manually reset to CLOSED")
            
    async def force_open(self):
        """Force circuit breaker to open state"""
        async with self.lock:
            self.state = CircuitState.OPEN
            self.stats.circuit_opens += 1
            logger.warning(f"Circuit breaker '{self.name}' manually forced to OPEN")


class CircuitBreakerRegistry:
    """Global registry for circuit breakers"""
    
    def __init__(self):
        self.circuit_breakers: Dict[str, AsyncCircuitBreaker] = {}
        self.lock = asyncio.Lock()
        
    async def get_or_create(self, name: str, config: CircuitBreakerConfig = None) -> AsyncCircuitBreaker:
        """Get existing circuit breaker or create new one"""
        async with self.lock:
            if name not in self.circuit_breakers:
                self.circuit_breakers[name] = AsyncCircuitBreaker(name, config)
                logger.info(f"Created circuit breaker '{name}'")
                
            return self.circuit_breakers[name]
            
    async def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all circuit breakers"""
        stats = {}
        async with self.lock:
            for name, cb in self.circuit_breakers.items():
                stats[name] = cb.get_stats()
        return stats
        
    async def reset_all(self):
        """Reset all circuit breakers"""
        async with self.lock:
            for cb in self.circuit_breakers.values():
                await cb.reset()
        logger.info("Reset all circuit breakers")
        
    def get_circuit_breaker(self, name: str) -> Optional[AsyncCircuitBreaker]:
        """Get circuit breaker by name (synchronous)"""
        return self.circuit_breakers.get(name)


# Global registry instance
circuit_breaker_registry = CircuitBreakerRegistry()


def circuit_breaker(name: str, config: CircuitBreakerConfig = None):
    """Decorator for circuit breaker protection"""
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs):
            cb = await circuit_breaker_registry.get_or_create(name, config)
            return await cb.execute(func, *args, **kwargs)
        return wrapper
    return decorator


async def with_circuit_breaker(name: str, func: Callable, *args, config: CircuitBreakerConfig = None, **kwargs):
    """Execute function with circuit breaker protection"""
    cb = await circuit_breaker_registry.get_or_create(name, config)
    return await cb.execute(func, *args, **kwargs)