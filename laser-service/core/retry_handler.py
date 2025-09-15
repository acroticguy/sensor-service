import asyncio
import random
import time
import functools
from typing import Callable, Any, Optional, Tuple, Type, Union, List
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class RetryStrategy(Enum):
    FIXED_DELAY = "fixed"
    EXPONENTIAL_BACKOFF = "exponential"
    LINEAR_BACKOFF = "linear"
    EXPONENTIAL_JITTER = "exponential_jitter"
    CUSTOM = "custom"


@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 300.0  # 5 minutes
    backoff_multiplier: float = 2.0
    jitter_range: float = 0.1  # 10% jitter
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)
    non_retryable_exceptions: Tuple[Type[Exception], ...] = ()
    custom_delay_func: Optional[Callable[[int, float], float]] = None
    on_retry_callback: Optional[Callable[[int, Exception], None]] = None


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been exhausted"""
    
    def __init__(self, attempts: int, last_exception: Exception):
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(f"Retry exhausted after {attempts} attempts. Last error: {str(last_exception)}")


class AsyncRetryHandler:
    def __init__(self, config: RetryConfig):
        self.config = config
        
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with retry logic"""
        last_exception = None
        
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                logger.debug(f"Attempt {attempt}/{self.config.max_attempts} for {func.__name__}")
                
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                    
                logger.debug(f"Success on attempt {attempt} for {func.__name__}")
                return result
                
            except Exception as e:
                last_exception = e
                
                # Check if exception is retryable
                if not self._is_retryable(e):
                    logger.info(f"Non-retryable exception in {func.__name__}: {type(e).__name__}")
                    raise e
                
                # Don't retry on last attempt
                if attempt == self.config.max_attempts:
                    logger.error(f"Max attempts reached for {func.__name__}: {str(e)}")
                    break
                    
                # Calculate delay
                delay = self._calculate_delay(attempt)
                
                logger.warning(f"Attempt {attempt} failed for {func.__name__}: {str(e)}. Retrying in {delay:.2f}s")
                
                # Call retry callback if provided
                if self.config.on_retry_callback:
                    try:
                        if asyncio.iscoroutinefunction(self.config.on_retry_callback):
                            await self.config.on_retry_callback(attempt, e)
                        else:
                            self.config.on_retry_callback(attempt, e)
                    except Exception as callback_error:
                        logger.warning(f"Retry callback error: {str(callback_error)}")
                
                # Wait before next attempt
                await asyncio.sleep(delay)
                
        # All attempts exhausted
        raise RetryExhaustedError(self.config.max_attempts, last_exception)
        
    def _is_retryable(self, exception: Exception) -> bool:
        """Check if an exception should trigger a retry"""
        # Check non-retryable first
        for non_retryable in self.config.non_retryable_exceptions:
            if isinstance(exception, non_retryable):
                return False
                
        # Check retryable
        for retryable in self.config.retryable_exceptions:
            if isinstance(exception, retryable):
                return True
                
        return False
        
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for next retry attempt"""
        if self.config.strategy == RetryStrategy.FIXED_DELAY:
            delay = self.config.base_delay
            
        elif self.config.strategy == RetryStrategy.LINEAR_BACKOFF:
            delay = self.config.base_delay * attempt
            
        elif self.config.strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
            delay = self.config.base_delay * (self.config.backoff_multiplier ** (attempt - 1))
            
        elif self.config.strategy == RetryStrategy.EXPONENTIAL_JITTER:
            base_delay = self.config.base_delay * (self.config.backoff_multiplier ** (attempt - 1))
            jitter = base_delay * self.config.jitter_range * (2 * random.random() - 1)
            delay = base_delay + jitter
            
        elif self.config.strategy == RetryStrategy.CUSTOM:
            if self.config.custom_delay_func:
                delay = self.config.custom_delay_func(attempt, self.config.base_delay)
            else:
                delay = self.config.base_delay
                
        else:
            delay = self.config.base_delay
            
        # Ensure delay doesn't exceed maximum
        return min(delay, self.config.max_delay)


def retry_async(config: RetryConfig = None, **kwargs):
    """Decorator for async retry functionality"""
    if config is None:
        config = RetryConfig(**kwargs)
        
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **func_kwargs):
            handler = AsyncRetryHandler(config)
            return await handler.execute(func, *args, **func_kwargs)
        return wrapper
    return decorator


def retry_sync(config: RetryConfig = None, **kwargs):
    """Decorator for synchronous retry functionality (runs in event loop)"""
    if config is None:
        config = RetryConfig(**kwargs)
        
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **func_kwargs):
            handler = AsyncRetryHandler(config)
            
            # Try to get current event loop
            try:
                loop = asyncio.get_running_loop()
                # If we're in an async context, run the retry handler
                return asyncio.create_task(handler.execute(func, *args, **func_kwargs))
            except RuntimeError:
                # No event loop running, run synchronously with basic retry
                return _sync_retry(func, config, *args, **func_kwargs)
                
        return wrapper
    return decorator


def _sync_retry(func: Callable, config: RetryConfig, *args, **kwargs) -> Any:
    """Synchronous retry implementation"""
    last_exception = None
    
    for attempt in range(1, config.max_attempts + 1):
        try:
            result = func(*args, **kwargs)
            return result
            
        except Exception as e:
            last_exception = e
            
            # Check if exception is retryable
            if not _is_retryable_sync(e, config):
                raise e
                
            # Don't retry on last attempt
            if attempt == config.max_attempts:
                break
                
            # Calculate delay
            delay = _calculate_delay_sync(attempt, config)
            
            logger.warning(f"Attempt {attempt} failed for {func.__name__}: {str(e)}. Retrying in {delay:.2f}s")
            
            # Call retry callback if provided
            if config.on_retry_callback:
                try:
                    config.on_retry_callback(attempt, e)
                except Exception as callback_error:
                    logger.warning(f"Retry callback error: {str(callback_error)}")
            
            # Wait before next attempt
            time.sleep(delay)
            
    raise RetryExhaustedError(config.max_attempts, last_exception)


def _is_retryable_sync(exception: Exception, config: RetryConfig) -> bool:
    """Synchronous version of retryable check"""
    for non_retryable in config.non_retryable_exceptions:
        if isinstance(exception, non_retryable):
            return False
            
    for retryable in config.retryable_exceptions:
        if isinstance(exception, retryable):
            return True
            
    return False


def _calculate_delay_sync(attempt: int, config: RetryConfig) -> float:
    """Synchronous version of delay calculation"""
    if config.strategy == RetryStrategy.FIXED_DELAY:
        delay = config.base_delay
    elif config.strategy == RetryStrategy.LINEAR_BACKOFF:
        delay = config.base_delay * attempt
    elif config.strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
        delay = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
    elif config.strategy == RetryStrategy.EXPONENTIAL_JITTER:
        base_delay = config.base_delay * (config.backoff_multiplier ** (attempt - 1))
        jitter = base_delay * config.jitter_range * (2 * random.random() - 1)
        delay = base_delay + jitter
    elif config.strategy == RetryStrategy.CUSTOM:
        if config.custom_delay_func:
            delay = config.custom_delay_func(attempt, config.base_delay)
        else:
            delay = config.base_delay
    else:
        delay = config.base_delay
        
    return min(delay, config.max_delay)


# Convenient retry decorators with common configurations
def retry_on_connection_error(max_attempts: int = 5, base_delay: float = 1.0):
    """Retry decorator for connection errors"""
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=base_delay,
        strategy=RetryStrategy.EXPONENTIAL_JITTER,
        retryable_exceptions=(
            ConnectionError,
            TimeoutError,
            OSError,
            asyncio.TimeoutError
        ),
        non_retryable_exceptions=(
            ValueError,
            TypeError,
            KeyError
        )
    )
    return retry_async(config)


def retry_on_timeout(max_attempts: int = 3, base_delay: float = 0.5):
    """Retry decorator for timeout errors"""
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=base_delay,
        strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
        retryable_exceptions=(
            TimeoutError,
            asyncio.TimeoutError
        )
    )
    return retry_async(config)


def retry_with_backoff(max_attempts: int = 5, 
                      base_delay: float = 1.0,
                      max_delay: float = 60.0,
                      exceptions: Tuple[Type[Exception], ...] = (Exception,)):
    """General retry decorator with exponential backoff"""
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        strategy=RetryStrategy.EXPONENTIAL_JITTER,
        retryable_exceptions=exceptions
    )
    return retry_async(config)


# Utility function for manual retry execution
async def execute_with_retry(func: Callable, 
                           config: RetryConfig = None,
                           *args, 
                           **kwargs) -> Any:
    """Execute function with retry logic"""
    if config is None:
        config = RetryConfig()
        
    handler = AsyncRetryHandler(config)
    return await handler.execute(func, *args, **kwargs)


# Context manager for retry operations
class AsyncRetryContext:
    def __init__(self, config: RetryConfig):
        self.config = config
        self.handler = AsyncRetryHandler(config)
        
    async def __aenter__(self):
        return self.handler
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Context manager cleanup if needed
        pass


def retry_context(config: RetryConfig = None, **kwargs):
    """Create retry context manager"""
    if config is None:
        config = RetryConfig(**kwargs)
    return AsyncRetryContext(config)