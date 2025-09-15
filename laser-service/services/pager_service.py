import asyncio
import httpx
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import logging
from contextlib import asynccontextmanager

from core.retry_handler import retry_async, RetryConfig, RetryStrategy
from core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerConfig
from core.metrics_collector import get_metrics_collector, timed_method

logger = logging.getLogger(__name__)


class Orientation(Enum):
    NORTH = 1
    EAST = 2
    SOUTH = 3
    WEST = 4


@dataclass
class PagerData:
    user_number: str
    orientation1: int
    speed1: float
    dist1: float
    orientation2: int
    speed2: float
    dist2: float
    angle: float
    
    def __post_init__(self):
        # Validate orientations
        if self.orientation1 not in [1, 2, 3, 4]:
            raise ValueError(f"orientation1 must be 1-4, got {self.orientation1}")
        if self.orientation2 not in [1, 2, 3, 4]:
            raise ValueError(f"orientation2 must be 1-4, got {self.orientation2}")
        
        # Validate user number (1-4 digits)
        user_str = str(self.user_number)
        if not user_str.isdigit() or len(user_str) > 4:
            raise ValueError(f"user_number must be 1-4 digits, got {self.user_number}")


@dataclass
class PagerConfig:
    base_url: str = "https://localhost:43011"
    endpoint: str = "/send-pager-data"
    timeout: float = 10.0
    max_retries: int = 3
    default_user_number: str = "0000"
    verify_ssl: bool = False  # Set to False for localhost with self-signed cert


class PagerService:
    def __init__(self, config: PagerConfig = None):
        self.config = config or PagerConfig()
        self.metrics = get_metrics_collector()
        
        # Circuit breaker for pager service reliability
        self.circuit_breaker = AsyncCircuitBreaker(
            "pager_service",
            CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=60.0,
                timeout=self.config.timeout,
                expected_exception=(httpx.RequestError, httpx.TimeoutException)
            )
        )
        
        # HTTP client with connection pooling
        self.client: Optional[httpx.AsyncClient] = None
        self.is_initialized = False
        
    async def initialize(self):
        """Initialize HTTP client"""
        if self.is_initialized:
            return
            
        # Configure HTTP client with connection pooling
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            verify=self.config.verify_ssl,
            headers={"Content-Type": "application/json"}
        )
        
        self.is_initialized = True
        logger.info(f"Pager service initialized (endpoint: {self.config.base_url}{self.config.endpoint})")
        
    async def shutdown(self):
        """Shutdown HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
        self.is_initialized = False
        logger.info("Pager service shutdown")
        
    @timed_method("pager_send_duration")
    @retry_async(RetryConfig(
        max_attempts=3,
        strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
        retryable_exceptions=(httpx.RequestError, httpx.TimeoutException)
    ))
    async def send_pager_data(self, data: PagerData) -> Dict[str, Any]:
        """Send data to pager service"""
        if not self.is_initialized:
            await self.initialize()
            
        # Execute through circuit breaker
        result = await self.circuit_breaker.execute(
            self._send_http_request,
            data
        )
        
        # Update metrics
        await self.metrics.increment_counter("pager_requests_total", 1.0, {
            "status": "success",
            "user_number": data.user_number
        })
        
        return result
        
    async def _send_http_request(self, data: PagerData) -> Dict[str, Any]:
        """Send HTTP request to pager service"""
        url = f"{self.config.base_url}{self.config.endpoint}"
        
        # Prepare payload
        payload = {
            "userNumber": data.user_number,
            "orientation1": data.orientation1,
            "speed1": data.speed1,
            "dist1": data.dist1,
            "orientation2": data.orientation2,
            "speed2": data.speed2,
            "dist2": data.dist2,
            "angle": data.angle
        }
        
        try:
            logger.debug(f"Sending pager data to {url}: {payload}")
            
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            
            result = response.json()
            
            logger.info(f"Pager data sent successfully for user {data.user_number}: {result}")
            return result
            
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.error(f"Pager service HTTP error: {error_msg}")
            
            # Update error metrics
            await self.metrics.increment_counter("pager_requests_total", 1.0, {
                "status": "http_error",
                "status_code": str(e.response.status_code),
                "user_number": data.user_number
            })
            raise
            
        except (httpx.RequestError, httpx.TimeoutException) as e:
            error_msg = f"Request error: {str(e)}"
            logger.error(f"Pager service request error: {error_msg}")
            
            # Update error metrics
            await self.metrics.increment_counter("pager_requests_total", 1.0, {
                "status": "request_error",
                "user_number": data.user_number
            })
            raise
            
    async def send_laser_measurement(self,
                                   laser1_data: Dict[str, Any],
                                   laser2_data: Dict[str, Any] = None,
                                   user_number: str = None,
                                   angle: float = 0.0) -> Dict[str, Any]:
        """Convenience method to send laser measurement data"""
        
        # Use default user number if not provided
        if user_number is None:
            user_number = self.config.default_user_number
            
        # Default laser2 data if not provided
        if laser2_data is None:
            laser2_data = {
                'orientation': 1,
                'speed': 0.0,
                'distance': 0.0
            }
            
        # Create pager data
        pager_data = PagerData(
            user_number=user_number,
            orientation1=laser1_data.get('orientation', 1),
            speed1=laser1_data.get('speed', 0.0),
            dist1=laser1_data.get('distance', 0.0),
            orientation2=laser2_data.get('orientation', 1),
            speed2=laser2_data.get('speed', 0.0),
            dist2=laser2_data.get('distance', 0.0),
            angle=angle
        )
        
        return await self.send_pager_data(pager_data)
        
    async def health_check(self) -> Dict[str, Any]:
        """Health check for pager service"""
        if not self.is_initialized:
            return {
                "healthy": False,
                "error": "Service not initialized"
            }
            
        try:
            # Test with minimal payload
            test_data = PagerData(
                user_number="0000",
                orientation1=1,
                speed1=0.0,
                dist1=0.0,
                orientation2=1,
                speed2=0.0,
                dist2=0.0,
                angle=0.0
            )
            
            start_time = time.time()
            await self._send_http_request(test_data)
            response_time = time.time() - start_time
            
            return {
                "healthy": True,
                "response_time": response_time,
                "circuit_breaker": self.circuit_breaker.get_stats()
            }
            
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "circuit_breaker": self.circuit_breaker.get_stats()
            }
            
    def get_orientation_name(self, orientation: int) -> str:
        """Get orientation name from number"""
        orientation_map = {
            1: "North",
            2: "East", 
            3: "South",
            4: "West"
        }
        return orientation_map.get(orientation, "Unknown")
        
    def get_stats(self) -> Dict[str, Any]:
        """Get pager service statistics"""
        return {
            "initialized": self.is_initialized,
            "base_url": self.config.base_url,
            "endpoint": self.config.endpoint,
            "circuit_breaker": self.circuit_breaker.get_stats(),
            "default_user_number": self.config.default_user_number
        }


# Global pager service instance
pager_service: Optional[PagerService] = None


def get_pager_service(config: PagerConfig = None) -> PagerService:
    """Get or create global pager service instance"""
    global pager_service
    if pager_service is None:
        pager_service = PagerService(config)
    return pager_service