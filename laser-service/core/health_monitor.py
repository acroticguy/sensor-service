import asyncio
import time
import psutil
import gc
from typing import Dict, Any, List, Callable, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheck:
    name: str
    check_func: Callable
    timeout: float = 5.0
    interval: float = 30.0
    critical: bool = False
    tags: Set[str] = field(default_factory=set)
    last_run: float = 0.0
    last_status: HealthStatus = HealthStatus.UNKNOWN
    last_error: str = ""
    consecutive_failures: int = 0
    max_failures: int = 3


@dataclass
class SystemMetrics:
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_available_mb: float
    disk_usage_percent: float
    active_connections: int
    uptime_seconds: float
    gc_collections: Dict[str, int]
    process_threads: int


class AsyncHealthMonitor:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.health_checks: Dict[str, HealthCheck] = {}
        self.system_metrics: List[SystemMetrics] = []
        self.max_metrics_history = 100
        self.is_running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.start_time = time.time()
        self.lock = asyncio.Lock()
        
        # Built-in system checks
        self._register_system_checks()
        
    def _register_system_checks(self):
        """Register built-in system health checks"""
        self.register_check(
            "system_memory",
            self._check_memory,
            critical=True,
            tags={"system", "memory"}
        )
        
        self.register_check(
            "system_cpu",
            self._check_cpu,
            critical=True,
            tags={"system", "cpu"}
        )
        
        self.register_check(
            "system_disk",
            self._check_disk,
            critical=True,
            tags={"system", "disk"}
        )
        
        self.register_check(
            "gc_health",
            self._check_garbage_collection,
            critical=False,
            tags={"system", "gc"}
        )
        
    def register_check(self, 
                      name: str, 
                      check_func: Callable,
                      timeout: float = 5.0,
                      interval: float = 30.0,
                      critical: bool = False,
                      tags: Set[str] = None):
        """Register a health check"""
        if tags is None:
            tags = set()
            
        health_check = HealthCheck(
            name=name,
            check_func=check_func,
            timeout=timeout,
            interval=interval,
            critical=critical,
            tags=tags
        )
        
        self.health_checks[name] = health_check
        logger.info(f"Registered health check '{name}' (critical: {critical})")
        
    async def start(self):
        """Start the health monitoring service"""
        if self.is_running:
            logger.warning("Health monitor is already running")
            return
            
        self.is_running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Started health monitor for service '{self.service_name}'")
        
    async def stop(self):
        """Stop the health monitoring service"""
        if not self.is_running:
            return
            
        self.is_running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
                
        logger.info("Health monitor stopped")
        
    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.is_running:
            try:
                current_time = time.time()
                
                # Collect system metrics
                await self._collect_system_metrics()
                
                # Run health checks
                checks_to_run = [
                    check for check in self.health_checks.values()
                    if current_time - check.last_run >= check.interval
                ]
                
                if checks_to_run:
                    await self._run_health_checks(checks_to_run)
                    
                await asyncio.sleep(5)  # Check every 5 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor loop: {str(e)}")
                await asyncio.sleep(10)
                
    async def _collect_system_metrics(self):
        """Collect system performance metrics"""
        try:
            # Get memory info
            memory = psutil.virtual_memory()
            
            # Get CPU usage (non-blocking)
            cpu_percent = psutil.cpu_percent(interval=None)
            
            # Get disk usage for root partition
            disk = psutil.disk_usage('/')
            
            # Get network connections
            connections = len(psutil.net_connections())
            
            # Get garbage collection stats
            gc_stats = {}
            for i in range(3):  # GC generations 0, 1, 2
                gc_stats[f"generation_{i}"] = gc.get_count()[i] if i < len(gc.get_count()) else 0
                
            # Get process info
            process = psutil.Process()
            
            metrics = SystemMetrics(
                timestamp=time.time(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_available_mb=memory.available / (1024 * 1024),
                disk_usage_percent=(disk.used / disk.total) * 100,
                active_connections=connections,
                uptime_seconds=time.time() - self.start_time,
                gc_collections=gc_stats,
                process_threads=process.num_threads()
            )
            
            async with self.lock:
                self.system_metrics.append(metrics)
                
                # Keep only recent metrics
                if len(self.system_metrics) > self.max_metrics_history:
                    self.system_metrics = self.system_metrics[-self.max_metrics_history:]
                    
        except Exception as e:
            logger.error(f"Error collecting system metrics: {str(e)}")
            
    async def _run_health_checks(self, checks: List[HealthCheck]):
        """Run a batch of health checks"""
        tasks = []
        for check in checks:
            task = asyncio.create_task(self._run_single_check(check))
            tasks.append(task)
            
        await asyncio.gather(*tasks, return_exceptions=True)
        
    async def _run_single_check(self, check: HealthCheck):
        """Run a single health check"""
        start_time = time.time()
        check.last_run = start_time
        
        try:
            # Run check with timeout
            result = await asyncio.wait_for(
                check.check_func(),
                timeout=check.timeout
            )
            
            # Interpret result
            if isinstance(result, bool):
                status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
            elif isinstance(result, HealthStatus):
                status = result
            elif isinstance(result, dict) and 'status' in result:
                status = result['status']
            else:
                status = HealthStatus.HEALTHY  # Assume success if no exception
                
            check.last_status = status
            check.last_error = ""
            check.consecutive_failures = 0
            
            logger.debug(f"Health check '{check.name}' passed")
            
        except asyncio.TimeoutError:
            check.last_status = HealthStatus.UNHEALTHY
            check.last_error = f"Check timed out after {check.timeout}s"
            check.consecutive_failures += 1
            logger.warning(f"Health check '{check.name}' timed out")
            
        except Exception as e:
            check.last_status = HealthStatus.UNHEALTHY
            check.last_error = str(e)
            check.consecutive_failures += 1
            logger.warning(f"Health check '{check.name}' failed: {str(e)}")
            
    # Built-in health check functions
    async def _check_memory(self) -> HealthStatus:
        """Check system memory usage"""
        memory = psutil.virtual_memory()
        
        if memory.percent > 90:
            return HealthStatus.UNHEALTHY
        elif memory.percent > 80:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
            
    async def _check_cpu(self) -> HealthStatus:
        """Check CPU usage"""
        # Get CPU usage over a short interval
        cpu_percent = psutil.cpu_percent(interval=0.1)
        
        if cpu_percent > 95:
            return HealthStatus.UNHEALTHY
        elif cpu_percent > 85:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
            
    async def _check_disk(self) -> HealthStatus:
        """Check disk usage"""
        disk = psutil.disk_usage('/')
        usage_percent = (disk.used / disk.total) * 100
        
        if usage_percent > 95:
            return HealthStatus.UNHEALTHY
        elif usage_percent > 90:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
            
    async def _check_garbage_collection(self) -> HealthStatus:
        """Check garbage collection health"""
        # Force a collection and measure time
        start_time = time.time()
        collected = gc.collect()
        gc_time = time.time() - start_time
        
        # If GC takes too long, it might indicate memory issues
        if gc_time > 1.0:  # 1 second is quite long for GC
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
            
    async def get_overall_health(self) -> Dict[str, Any]:
        """Get overall health status"""
        async with self.lock:
            critical_checks = [c for c in self.health_checks.values() if c.critical]
            non_critical_checks = [c for c in self.health_checks.values() if not c.critical]
            
            # Determine overall status
            overall_status = HealthStatus.HEALTHY
            
            # Check critical systems
            critical_unhealthy = any(
                c.last_status == HealthStatus.UNHEALTHY for c in critical_checks
            )
            critical_degraded = any(
                c.last_status == HealthStatus.DEGRADED for c in critical_checks
            )
            
            if critical_unhealthy:
                overall_status = HealthStatus.UNHEALTHY
            elif critical_degraded:
                overall_status = HealthStatus.DEGRADED
                
            # Check non-critical systems (only affect status if critical are healthy)
            if overall_status == HealthStatus.HEALTHY:
                non_critical_unhealthy = any(
                    c.last_status == HealthStatus.UNHEALTHY for c in non_critical_checks
                )
                if non_critical_unhealthy:
                    overall_status = HealthStatus.DEGRADED
                    
            # Get latest metrics
            latest_metrics = self.system_metrics[-1] if self.system_metrics else None
            
            return {
                "service": self.service_name,
                "status": overall_status.value,
                "timestamp": time.time(),
                "uptime_seconds": time.time() - self.start_time,
                "checks": {
                    name: {
                        "status": check.last_status.value,
                        "last_run": check.last_run,
                        "consecutive_failures": check.consecutive_failures,
                        "error": check.last_error,
                        "critical": check.critical,
                        "tags": list(check.tags)
                    }
                    for name, check in self.health_checks.items()
                },
                "metrics": {
                    "cpu_percent": latest_metrics.cpu_percent if latest_metrics else 0,
                    "memory_percent": latest_metrics.memory_percent if latest_metrics else 0,
                    "memory_available_mb": latest_metrics.memory_available_mb if latest_metrics else 0,
                    "disk_usage_percent": latest_metrics.disk_usage_percent if latest_metrics else 0,
                    "active_connections": latest_metrics.active_connections if latest_metrics else 0,
                    "process_threads": latest_metrics.process_threads if latest_metrics else 0
                } if latest_metrics else {}
            }
            
    async def get_metrics_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get historical metrics"""
        async with self.lock:
            recent_metrics = self.system_metrics[-limit:] if self.system_metrics else []
            return [
                {
                    "timestamp": m.timestamp,
                    "cpu_percent": m.cpu_percent,
                    "memory_percent": m.memory_percent,
                    "memory_available_mb": m.memory_available_mb,
                    "disk_usage_percent": m.disk_usage_percent,
                    "active_connections": m.active_connections,
                    "uptime_seconds": m.uptime_seconds,
                    "process_threads": m.process_threads
                }
                for m in recent_metrics
            ]
            
    async def run_check_now(self, check_name: str) -> Dict[str, Any]:
        """Run a specific health check immediately"""
        check = self.health_checks.get(check_name)
        if not check:
            raise ValueError(f"Health check '{check_name}' not found")
            
        await self._run_single_check(check)
        
        return {
            "name": check_name,
            "status": check.last_status.value,
            "error": check.last_error,
            "last_run": check.last_run,
            "consecutive_failures": check.consecutive_failures
        }


# Global health monitor instance
health_monitor: Optional[AsyncHealthMonitor] = None


def get_health_monitor(service_name: str = "laser_service") -> AsyncHealthMonitor:
    """Get or create global health monitor instance"""
    global health_monitor
    if health_monitor is None:
        health_monitor = AsyncHealthMonitor(service_name)
    return health_monitor