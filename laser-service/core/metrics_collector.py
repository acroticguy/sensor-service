import asyncio
import time
import json
from typing import Dict, Any, List, Optional, Callable, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import logging
from contextlib import asynccontextmanager

try:
    from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.metrics import MeterProvider
    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

logger = logging.getLogger(__name__)


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"  
    HISTOGRAM = "histogram"
    TIMER = "timer"


@dataclass
class MetricPoint:
    timestamp: float
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class Metric:
    name: str
    metric_type: MetricType
    description: str = ""
    unit: str = ""
    labels: Dict[str, str] = field(default_factory=dict)
    points: deque = field(default_factory=lambda: deque(maxlen=1000))


class MetricsCollector:
    def __init__(self, service_name: str = "laser_service"):
        self.service_name = service_name
        self.metrics: Dict[str, Metric] = {}
        self.prometheus_registry = None
        self.prometheus_metrics = {}
        self.opentelemetry_meter = None
        self.opentelemetry_instruments = {}
        self.lock = asyncio.Lock()
        self.background_tasks: Set[asyncio.Task] = set()
        self.is_running = False
        
        # Initialize monitoring backends
        self._setup_prometheus()
        self._setup_opentelemetry()
        
    def _setup_prometheus(self):
        """Setup Prometheus metrics collection"""
        if not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus client not available")
            return
            
        try:
            self.prometheus_registry = CollectorRegistry()
            
            # Create default metrics
            self.prometheus_metrics = {
                'requests_total': Counter(
                    'http_requests_total',
                    'Total HTTP requests',
                    ['method', 'endpoint', 'status'],
                    registry=self.prometheus_registry
                ),
                'request_duration': Histogram(
                    'http_request_duration_seconds',
                    'HTTP request duration',
                    ['method', 'endpoint'],
                    registry=self.prometheus_registry
                ),
                'active_connections': Gauge(
                    'active_connections',
                    'Active connections',
                    registry=self.prometheus_registry
                ),
                'laser_operations': Counter(
                    'laser_operations_total',
                    'Total laser operations',
                    ['laser_id', 'operation', 'status'],
                    registry=self.prometheus_registry
                ),
                'laser_response_time': Histogram(
                    'laser_response_time_seconds',
                    'Laser response time',
                    ['laser_id', 'operation'],
                    registry=self.prometheus_registry
                )
            }
            
            logger.info("Prometheus metrics initialized")
            
        except Exception as e:
            logger.error(f"Failed to setup Prometheus: {str(e)}")
            
    def _setup_opentelemetry(self):
        """Setup OpenTelemetry metrics collection"""
        if not OPENTELEMETRY_AVAILABLE:
            logger.warning("OpenTelemetry not available")
            return
            
        try:
            # Set up meter
            meter_provider = MeterProvider()
            metrics.set_meter_provider(meter_provider)
            self.opentelemetry_meter = metrics.get_meter(self.service_name)
            
            # Create instruments
            self.opentelemetry_instruments = {
                'requests_counter': self.opentelemetry_meter.create_counter(
                    "http_requests_total",
                    description="Total HTTP requests"
                ),
                'active_connections_gauge': self.opentelemetry_meter.create_up_down_counter(
                    "active_connections",
                    description="Active connections"
                ),
                'laser_operations_counter': self.opentelemetry_meter.create_counter(
                    "laser_operations_total",
                    description="Total laser operations"
                )
            }
            
            logger.info("OpenTelemetry metrics initialized")
            
        except Exception as e:
            logger.error(f"Failed to setup OpenTelemetry: {str(e)}")
            
    async def increment_counter(self, name: str, value: float = 1.0, labels: Dict[str, str] = None):
        """Increment a counter metric"""
        labels = labels or {}
        
        async with self.lock:
            # Internal metrics
            if name not in self.metrics:
                self.metrics[name] = Metric(
                    name=name,
                    metric_type=MetricType.COUNTER,
                    labels=labels.copy()
                )
                
            metric = self.metrics[name]
            
            # Add new data point
            if metric.points:
                current_value = metric.points[-1].value + value
            else:
                current_value = value
                
            metric.points.append(MetricPoint(
                timestamp=time.time(),
                value=current_value,
                labels=labels
            ))
            
        # Update external systems
        await self._update_prometheus_counter(name, value, labels)
        await self._update_opentelemetry_counter(name, value, labels)
        
    async def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set a gauge metric value"""
        labels = labels or {}
        
        async with self.lock:
            if name not in self.metrics:
                self.metrics[name] = Metric(
                    name=name,
                    metric_type=MetricType.GAUGE,
                    labels=labels.copy()
                )
                
            metric = self.metrics[name]
            metric.points.append(MetricPoint(
                timestamp=time.time(),
                value=value,
                labels=labels
            ))
            
        # Update external systems
        await self._update_prometheus_gauge(name, value, labels)
        await self._update_opentelemetry_gauge(name, value, labels)
        
    async def observe_histogram(self, name: str, value: float, labels: Dict[str, str] = None):
        """Observe a value for histogram metric"""
        labels = labels or {}
        
        async with self.lock:
            if name not in self.metrics:
                self.metrics[name] = Metric(
                    name=name,
                    metric_type=MetricType.HISTOGRAM,
                    labels=labels.copy()
                )
                
            metric = self.metrics[name]
            metric.points.append(MetricPoint(
                timestamp=time.time(),
                value=value,
                labels=labels
            ))
            
        # Update external systems
        await self._update_prometheus_histogram(name, value, labels)
        
    @asynccontextmanager
    async def timer(self, name: str, labels: Dict[str, str] = None):
        """Context manager for timing operations"""
        start_time = time.time()
        try:
            yield
        finally:
            duration = time.time() - start_time
            await self.observe_histogram(name, duration, labels)
            
    async def _update_prometheus_counter(self, name: str, value: float, labels: Dict[str, str]):
        """Update Prometheus counter"""
        if not PROMETHEUS_AVAILABLE or not self.prometheus_registry:
            return
            
        try:
            # Map to appropriate Prometheus metric
            if name in ['http_requests', 'requests_total']:
                self.prometheus_metrics['requests_total'].labels(**labels).inc(value)
            elif name in ['laser_operations', 'laser_ops']:
                self.prometheus_metrics['laser_operations'].labels(**labels).inc(value)
        except Exception as e:
            logger.warning(f"Failed to update Prometheus counter {name}: {str(e)}")
            
    async def _update_prometheus_gauge(self, name: str, value: float, labels: Dict[str, str]):
        """Update Prometheus gauge"""
        if not PROMETHEUS_AVAILABLE or not self.prometheus_registry:
            return
            
        try:
            if name == 'active_connections':
                self.prometheus_metrics['active_connections'].set(value)
        except Exception as e:
            logger.warning(f"Failed to update Prometheus gauge {name}: {str(e)}")
            
    async def _update_prometheus_histogram(self, name: str, value: float, labels: Dict[str, str]):
        """Update Prometheus histogram"""
        if not PROMETHEUS_AVAILABLE or not self.prometheus_registry:
            return
            
        try:
            if name in ['request_duration', 'http_request_duration']:
                self.prometheus_metrics['request_duration'].labels(**labels).observe(value)
            elif name in ['laser_response_time', 'laser_duration']:
                self.prometheus_metrics['laser_response_time'].labels(**labels).observe(value)
        except Exception as e:
            logger.warning(f"Failed to update Prometheus histogram {name}: {str(e)}")
            
    async def _update_opentelemetry_counter(self, name: str, value: float, labels: Dict[str, str]):
        """Update OpenTelemetry counter"""
        if not OPENTELEMETRY_AVAILABLE or not self.opentelemetry_meter:
            return
            
        try:
            if name in ['http_requests', 'requests_total']:
                self.opentelemetry_instruments['requests_counter'].add(value, labels)
            elif name in ['laser_operations', 'laser_ops']:
                self.opentelemetry_instruments['laser_operations_counter'].add(value, labels)
        except Exception as e:
            logger.warning(f"Failed to update OpenTelemetry counter {name}: {str(e)}")
            
    async def _update_opentelemetry_gauge(self, name: str, value: float, labels: Dict[str, str]):
        """Update OpenTelemetry gauge"""
        if not OPENTELEMETRY_AVAILABLE or not self.opentelemetry_meter:
            return
            
        try:
            if name == 'active_connections':
                self.opentelemetry_instruments['active_connections_gauge'].add(value, labels)
        except Exception as e:
            logger.warning(f"Failed to update OpenTelemetry gauge {name}: {str(e)}")
            
    def get_prometheus_metrics(self) -> str:
        """Get Prometheus formatted metrics"""
        if not PROMETHEUS_AVAILABLE or not self.prometheus_registry:
            return "# Prometheus not available\n"
            
        try:
            return generate_latest(self.prometheus_registry).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to generate Prometheus metrics: {str(e)}")
            return f"# Error generating metrics: {str(e)}\n"
            
    async def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics"""
        async with self.lock:
            summary = {
                "service": self.service_name,
                "timestamp": time.time(),
                "metrics_count": len(self.metrics),
                "backends": {
                    "prometheus": PROMETHEUS_AVAILABLE and self.prometheus_registry is not None,
                    "opentelemetry": OPENTELEMETRY_AVAILABLE and self.opentelemetry_meter is not None
                },
                "metrics": {}
            }
            
            for name, metric in self.metrics.items():
                latest_point = metric.points[-1] if metric.points else None
                
                summary["metrics"][name] = {
                    "type": metric.metric_type.value,
                    "description": metric.description,
                    "unit": metric.unit,
                    "points_count": len(metric.points),
                    "latest_value": latest_point.value if latest_point else None,
                    "latest_timestamp": latest_point.timestamp if latest_point else None,
                    "labels": metric.labels
                }
                
        return summary
        
    async def get_metric_history(self, name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get historical data for a specific metric"""
        async with self.lock:
            if name not in self.metrics:
                return []
                
            metric = self.metrics[name]
            recent_points = list(metric.points)[-limit:] if metric.points else []
            
            return [
                {
                    "timestamp": point.timestamp,
                    "value": point.value,
                    "labels": point.labels
                }
                for point in recent_points
            ]
            
    async def start_background_collection(self):
        """Start background metrics collection tasks"""
        if self.is_running:
            return
            
        self.is_running = True
        
        # Start system metrics collection
        system_task = asyncio.create_task(self._collect_system_metrics())
        self.background_tasks.add(system_task)
        
        logger.info("Started background metrics collection")
        
    async def stop_background_collection(self):
        """Stop background metrics collection"""
        if not self.is_running:
            return
            
        self.is_running = False
        
        # Cancel background tasks
        for task in self.background_tasks:
            task.cancel()
            
        try:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error stopping background collection: {str(e)}")
            
        self.background_tasks.clear()
        logger.info("Stopped background metrics collection")
        
    async def _collect_system_metrics(self):
        """Collect system performance metrics"""
        import psutil
        
        while self.is_running:
            try:
                # CPU usage
                cpu_percent = psutil.cpu_percent(interval=None)
                await self.set_gauge("system_cpu_percent", cpu_percent)
                
                # Memory usage
                memory = psutil.virtual_memory()
                await self.set_gauge("system_memory_percent", memory.percent)
                await self.set_gauge("system_memory_available_mb", memory.available / (1024 * 1024))
                
                # Disk usage
                disk = psutil.disk_usage('/')
                disk_percent = (disk.used / disk.total) * 100
                await self.set_gauge("system_disk_percent", disk_percent)
                
                # Network connections
                connections = len(psutil.net_connections())
                await self.set_gauge("system_connections", connections)
                
                # Process info
                process = psutil.Process()
                await self.set_gauge("process_threads", process.num_threads())
                
                await asyncio.sleep(30)  # Collect every 30 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error collecting system metrics: {str(e)}")
                await asyncio.sleep(10)


# Global metrics collector
metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector(service_name: str = "laser_service") -> MetricsCollector:
    """Get or create global metrics collector"""
    global metrics_collector
    if metrics_collector is None:
        metrics_collector = MetricsCollector(service_name)
    return metrics_collector


# Decorator for automatic method timing
def timed_method(metric_name: str = None, labels: Dict[str, str] = None):
    """Decorator to automatically time method execution"""
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            name = metric_name or f"{func.__name__}_duration"
            collector = get_metrics_collector()
            async with collector.timer(name, labels):
                return await func(*args, **kwargs)
        
        def sync_wrapper(*args, **kwargs):
            import time
            name = metric_name or f"{func.__name__}_duration"
            collector = get_metrics_collector()
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start_time
                # Schedule async metric update
                loop = asyncio.get_event_loop()
                loop.create_task(collector.observe_histogram(name, duration, labels))
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
            
    return decorator