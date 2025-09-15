import asyncio
import signal
import sys
import time
from typing import List, Callable, Dict, Any, Optional, Set
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ShutdownPhase(Enum):
    STARTING = "starting"
    STOP_NEW_REQUESTS = "stop_new_requests"
    DRAIN_CONNECTIONS = "drain_connections"  
    CLEANUP_RESOURCES = "cleanup_resources"
    FORCE_SHUTDOWN = "force_shutdown"
    COMPLETED = "completed"


@dataclass
class ShutdownHandler:
    name: str
    handler_func: Callable
    timeout: float = 30.0
    priority: int = 100  # Lower number = higher priority
    phase: ShutdownPhase = ShutdownPhase.CLEANUP_RESOURCES
    required: bool = True  # If True, failure blocks shutdown


class GracefulShutdownManager:
    def __init__(self, service_name: str = "service"):
        self.service_name = service_name
        self.handlers: Dict[str, ShutdownHandler] = {}
        self.shutdown_event = asyncio.Event()
        self.is_shutting_down = False
        self.shutdown_start_time: Optional[float] = None
        self.current_phase = ShutdownPhase.STARTING
        self.signals_registered = False
        self.force_shutdown_timeout = 300.0  # 5 minutes max shutdown time
        
    def register_handler(self, 
                        name: str,
                        handler_func: Callable,
                        timeout: float = 30.0,
                        priority: int = 100,
                        phase: ShutdownPhase = ShutdownPhase.CLEANUP_RESOURCES,
                        required: bool = True):
        """Register a shutdown handler"""
        if name in self.handlers:
            logger.warning(f"Replacing existing shutdown handler '{name}'")
            
        handler = ShutdownHandler(
            name=name,
            handler_func=handler_func,
            timeout=timeout,
            priority=priority,
            phase=phase,
            required=required
        )
        
        self.handlers[name] = handler
        logger.info(f"Registered shutdown handler '{name}' (phase: {phase.value}, priority: {priority})")
        
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        if self.signals_registered:
            return
            
        def signal_handler(signum, frame):
            signal_name = signal.Signals(signum).name
            logger.info(f"Received shutdown signal: {signal_name}")
            
            # Create shutdown task
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.shutdown())
            else:
                loop.run_until_complete(self.shutdown())
                
        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Windows specific
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, signal_handler)
            
        self.signals_registered = True
        logger.info("Registered shutdown signal handlers")
        
    async def shutdown(self, reason: str = "Shutdown requested"):
        """Initiate graceful shutdown process"""
        if self.is_shutting_down:
            logger.warning("Shutdown already in progress")
            return
            
        self.is_shutting_down = True
        self.shutdown_start_time = time.time()
        self.shutdown_event.set()
        
        logger.info(f"Starting graceful shutdown of '{self.service_name}': {reason}")
        
        try:
            # Phase 1: Stop accepting new requests
            await self._execute_phase(ShutdownPhase.STOP_NEW_REQUESTS)
            
            # Phase 2: Drain existing connections
            await self._execute_phase(ShutdownPhase.DRAIN_CONNECTIONS)
            
            # Phase 3: Cleanup resources
            await self._execute_phase(ShutdownPhase.CLEANUP_RESOURCES)
            
            self.current_phase = ShutdownPhase.COMPLETED
            shutdown_time = time.time() - self.shutdown_start_time
            logger.info(f"Graceful shutdown completed in {shutdown_time:.2f}s")
            
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {str(e)}")
            await self._force_shutdown()
            
    async def _execute_phase(self, phase: ShutdownPhase):
        """Execute all handlers for a specific shutdown phase"""
        self.current_phase = phase
        phase_handlers = [h for h in self.handlers.values() if h.phase == phase]
        
        if not phase_handlers:
            logger.debug(f"No handlers registered for phase {phase.value}")
            return
            
        # Sort by priority (lower number = higher priority)
        phase_handlers.sort(key=lambda h: h.priority)
        
        logger.info(f"Executing shutdown phase: {phase.value} ({len(phase_handlers)} handlers)")
        
        # Track phase start time
        phase_start = time.time()
        
        # Execute handlers
        for handler in phase_handlers:
            await self._execute_handler(handler)
            
        phase_time = time.time() - phase_start
        logger.info(f"Completed shutdown phase {phase.value} in {phase_time:.2f}s")
        
    async def _execute_handler(self, handler: ShutdownHandler):
        """Execute a single shutdown handler"""
        logger.debug(f"Executing shutdown handler '{handler.name}'")
        start_time = time.time()
        
        try:
            # Execute with timeout
            await asyncio.wait_for(
                handler.handler_func(),
                timeout=handler.timeout
            )
            
            execution_time = time.time() - start_time
            logger.info(f"Shutdown handler '{handler.name}' completed in {execution_time:.2f}s")
            
        except asyncio.TimeoutError:
            execution_time = time.time() - start_time
            error_msg = f"Shutdown handler '{handler.name}' timed out after {handler.timeout}s"
            
            if handler.required:
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            else:
                logger.warning(f"{error_msg} (non-required, continuing)")
                
        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"Shutdown handler '{handler.name}' failed: {str(e)}"
            
            if handler.required:
                logger.error(error_msg)
                raise
            else:
                logger.warning(f"{error_msg} (non-required, continuing)")
                
    async def _force_shutdown(self):
        """Force shutdown if graceful shutdown fails"""
        self.current_phase = ShutdownPhase.FORCE_SHUTDOWN
        logger.warning("Initiating force shutdown")
        
        # Cancel all running tasks
        current_task = asyncio.current_task()
        all_tasks = asyncio.all_tasks()
        
        tasks_to_cancel = [task for task in all_tasks if task != current_task]
        
        if tasks_to_cancel:
            logger.info(f"Cancelling {len(tasks_to_cancel)} running tasks")
            
            for task in tasks_to_cancel:
                task.cancel()
                
            # Wait a bit for tasks to cancel
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("Some tasks did not cancel within timeout")
                
        logger.error("Force shutdown completed")
        
    async def wait_for_shutdown(self):
        """Wait for shutdown signal"""
        await self.shutdown_event.wait()
        
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested"""
        return self.is_shutting_down
        
    def get_shutdown_status(self) -> Dict[str, Any]:
        """Get current shutdown status"""
        status = {
            "service": self.service_name,
            "is_shutting_down": self.is_shutting_down,
            "current_phase": self.current_phase.value if self.current_phase else None,
            "shutdown_start_time": self.shutdown_start_time,
            "handlers": {}
        }
        
        if self.shutdown_start_time:
            status["shutdown_duration"] = time.time() - self.shutdown_start_time
            
        # Add handler information
        for name, handler in self.handlers.items():
            status["handlers"][name] = {
                "phase": handler.phase.value,
                "priority": handler.priority,
                "timeout": handler.timeout,
                "required": handler.required
            }
            
        return status


# Global shutdown manager
shutdown_manager: Optional[GracefulShutdownManager] = None


def get_shutdown_manager(service_name: str = "laser_service") -> GracefulShutdownManager:
    """Get or create global shutdown manager"""
    global shutdown_manager
    if shutdown_manager is None:
        shutdown_manager = GracefulShutdownManager(service_name)
    return shutdown_manager


# Decorator for shutdown handlers
def shutdown_handler(name: str = None, 
                    timeout: float = 30.0,
                    priority: int = 100,
                    phase: ShutdownPhase = ShutdownPhase.CLEANUP_RESOURCES,
                    required: bool = True):
    """Decorator to register shutdown handlers"""
    def decorator(func):
        handler_name = name or func.__name__
        manager = get_shutdown_manager()
        manager.register_handler(
            handler_name, func, timeout, priority, phase, required
        )
        return func
    return decorator


# Context manager for shutdown-aware operations
class ShutdownAwareContext:
    def __init__(self, manager: GracefulShutdownManager):
        self.manager = manager
        
    async def __aenter__(self):
        if self.manager.is_shutdown_requested():
            raise RuntimeError("Service is shutting down")
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
        
    def check_shutdown(self):
        """Check if shutdown was requested during operation"""
        if self.manager.is_shutdown_requested():
            raise RuntimeError("Service shutdown requested")


def shutdown_aware_context():
    """Create a context manager that checks for shutdown"""
    manager = get_shutdown_manager()
    return ShutdownAwareContext(manager)