import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Union
from dataclasses import dataclass, asdict
from enum import Enum
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    result: Any = None
    error: str = None
    execution_time: float = 0.0
    attempts: int = 0
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0


@dataclass
class Task:
    id: str
    func_name: str
    args: tuple
    kwargs: dict
    priority: TaskPriority = TaskPriority.NORMAL
    max_retries: int = 3
    retry_delay: float = 1.0
    timeout: float = 30.0
    created_at: float = 0.0
    scheduled_at: float = 0.0
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.scheduled_at:
            self.scheduled_at = self.created_at


class AsyncTaskQueue:
    def __init__(self, name: str, max_workers: int = 10, max_queue_size: int = 1000):
        self.name = name
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        
        # Task storage
        self.pending_tasks: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self.processing_tasks: Dict[str, Task] = {}
        self.completed_tasks: Dict[str, TaskResult] = {}
        self.task_functions: Dict[str, Callable] = {}
        
        # Worker management
        self.workers: Set[asyncio.Task] = set()
        self.worker_stats: Dict[int, Dict[str, Any]] = {}
        self.is_running = False
        self.shutdown_event = asyncio.Event()
        
        # Statistics
        self.stats = {
            'total_tasks': 0,
            'completed_tasks': 0,
            'failed_tasks': 0,
            'retried_tasks': 0,
            'cancelled_tasks': 0,
            'avg_execution_time': 0.0,
            'queue_size': 0,
            'workers_busy': 0
        }
        
        # Lock for thread safety
        self.lock = asyncio.Lock()
        
    def register_function(self, func: Callable, name: str = None):
        """Register a function for task execution"""
        func_name = name or func.__name__
        self.task_functions[func_name] = func
        logger.info(f"Registered function '{func_name}' in queue '{self.name}'")
        
    async def enqueue(self, 
                     func_name: str, 
                     *args, 
                     priority: TaskPriority = TaskPriority.NORMAL,
                     max_retries: int = 3,
                     retry_delay: float = 1.0,
                     timeout: float = 30.0,
                     delay: float = 0.0,
                     **kwargs) -> str:
        """Enqueue a task for execution"""
        
        if func_name not in self.task_functions:
            raise ValueError(f"Function '{func_name}' not registered")
            
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            func_name=func_name,
            args=args,
            kwargs=kwargs,
            priority=priority,
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout,
            scheduled_at=time.time() + delay
        )
        
        try:
            # Use negative priority for max heap behavior (higher priority first)
            await self.pending_tasks.put((-priority.value, task.scheduled_at, task))
            
            async with self.lock:
                self.stats['total_tasks'] += 1
                self.stats['queue_size'] = self.pending_tasks.qsize()
                
            logger.debug(f"Enqueued task {task_id} with priority {priority.name}")
            return task_id
            
        except asyncio.QueueFull:
            raise RuntimeError(f"Task queue '{self.name}' is full")
            
    async def start(self):
        """Start the task queue workers"""
        if self.is_running:
            logger.warning(f"Queue '{self.name}' is already running")
            return
            
        self.is_running = True
        self.shutdown_event.clear()
        
        # Start worker tasks
        for worker_id in range(self.max_workers):
            worker = asyncio.create_task(self._worker(worker_id))
            self.workers.add(worker)
            self.worker_stats[worker_id] = {
                'tasks_processed': 0,
                'total_execution_time': 0.0,
                'last_task_at': 0.0,
                'status': 'idle'
            }
            
        logger.info(f"Started task queue '{self.name}' with {self.max_workers} workers")
        
    async def stop(self, timeout: float = 30.0):
        """Stop the task queue gracefully"""
        if not self.is_running:
            return
            
        logger.info(f"Stopping task queue '{self.name}'...")
        self.is_running = False
        self.shutdown_event.set()
        
        # Wait for workers to finish current tasks
        try:
            await asyncio.wait_for(
                asyncio.gather(*self.workers, return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for workers to stop, cancelling...")
            for worker in self.workers:
                worker.cancel()
                
        self.workers.clear()
        logger.info(f"Task queue '{self.name}' stopped")
        
    async def _worker(self, worker_id: int):
        """Worker coroutine to process tasks"""
        logger.debug(f"Worker {worker_id} started in queue '{self.name}'")
        
        while self.is_running:
            try:
                # Get task from queue with timeout
                try:
                    priority, scheduled_time, task = await asyncio.wait_for(
                        self.pending_tasks.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                    
                # Check if task should be delayed
                current_time = time.time()
                if scheduled_time > current_time:
                    # Reschedule task
                    await asyncio.sleep(0.1)  # Small delay before rescheduling
                    await self.pending_tasks.put((priority, scheduled_time, task))
                    continue
                    
                # Move task to processing
                async with self.lock:
                    self.processing_tasks[task.id] = task
                    self.worker_stats[worker_id]['status'] = 'busy'
                    self.stats['workers_busy'] += 1
                    self.stats['queue_size'] = self.pending_tasks.qsize()
                    
                # Process the task
                result = await self._execute_task(worker_id, task)
                
                # Store result
                async with self.lock:
                    self.completed_tasks[task.id] = result
                    self.processing_tasks.pop(task.id, None)
                    
                    # Update statistics
                    if result.status == TaskStatus.COMPLETED:
                        self.stats['completed_tasks'] += 1
                    elif result.status == TaskStatus.FAILED:
                        self.stats['failed_tasks'] += 1
                        
                    # Update average execution time
                    total_completed = self.stats['completed_tasks'] + self.stats['failed_tasks']
                    if total_completed > 0:
                        self.stats['avg_execution_time'] = (
                            (self.stats['avg_execution_time'] * (total_completed - 1) + result.execution_time) /
                            total_completed
                        )
                        
                    self.worker_stats[worker_id]['status'] = 'idle'
                    self.stats['workers_busy'] -= 1
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {str(e)}")
                
        logger.debug(f"Worker {worker_id} stopped in queue '{self.name}'")
        
    async def _execute_task(self, worker_id: int, task: Task) -> TaskResult:
        """Execute a single task"""
        start_time = time.time()
        result = TaskResult(
            task_id=task.id,
            status=TaskStatus.PROCESSING,
            created_at=task.created_at,
            started_at=start_time,
            attempts=1
        )
        
        func = self.task_functions[task.func_name]
        
        for attempt in range(task.max_retries + 1):
            try:
                # Update worker stats
                self.worker_stats[worker_id]['tasks_processed'] += 1
                self.worker_stats[worker_id]['last_task_at'] = start_time
                
                logger.debug(f"Executing task {task.id} (attempt {attempt + 1})")
                
                # Execute with timeout
                task_result = await asyncio.wait_for(
                    func(*task.args, **task.kwargs),
                    timeout=task.timeout
                )
                
                # Success
                end_time = time.time()
                result.status = TaskStatus.COMPLETED
                result.result = task_result
                result.execution_time = end_time - start_time
                result.completed_at = end_time
                result.attempts = attempt + 1
                
                self.worker_stats[worker_id]['total_execution_time'] += result.execution_time
                
                logger.debug(f"Task {task.id} completed successfully")
                break
                
            except asyncio.TimeoutError:
                error_msg = f"Task {task.id} timed out after {task.timeout}s"
                logger.warning(error_msg)
                result.error = error_msg
                result.status = TaskStatus.FAILED
                
            except asyncio.CancelledError:
                result.status = TaskStatus.CANCELLED
                result.error = "Task was cancelled"
                logger.info(f"Task {task.id} was cancelled")
                break
                
            except Exception as e:
                error_msg = f"Task {task.id} failed: {str(e)}"
                logger.warning(error_msg)
                result.error = error_msg
                result.status = TaskStatus.FAILED
                
            # Retry logic
            if attempt < task.max_retries:
                result.status = TaskStatus.RETRYING
                await asyncio.sleep(task.retry_delay * (2 ** attempt))  # Exponential backoff
                logger.debug(f"Retrying task {task.id} (attempt {attempt + 2})")
            else:
                # Max retries reached
                end_time = time.time()
                result.execution_time = end_time - start_time
                result.completed_at = end_time
                result.attempts = attempt + 1
                
        return result
        
    async def get_task_result(self, task_id: str, timeout: float = None) -> Optional[TaskResult]:
        """Get task result, optionally waiting for completion"""
        # Check if already completed
        if task_id in self.completed_tasks:
            return self.completed_tasks[task_id]
            
        # Wait for completion if timeout specified
        if timeout:
            end_time = time.time() + timeout
            while time.time() < end_time:
                if task_id in self.completed_tasks:
                    return self.completed_tasks[task_id]
                await asyncio.sleep(0.1)
                
        return None
        
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or processing task"""
        # Check if task is processing
        if task_id in self.processing_tasks:
            # Can't cancel processing tasks directly
            return False
            
        # Remove from pending queue (this is tricky with PriorityQueue)
        # For now, mark as cancelled when it gets processed
        return False  # TODO: Implement proper cancellation
        
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        return {
            **self.stats,
            'name': self.name,
            'max_workers': self.max_workers,
            'max_queue_size': self.max_queue_size,
            'is_running': self.is_running,
            'registered_functions': list(self.task_functions.keys()),
            'worker_stats': self.worker_stats.copy()
        }
        
    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get list of pending tasks (approximate)"""
        return [
            {
                'id': task.id,
                'func_name': task.func_name,
                'priority': task.priority.name,
                'created_at': task.created_at,
                'scheduled_at': task.scheduled_at
            }
            for _, _, task in list(self.pending_tasks._queue)
        ]
        
    def get_processing_tasks(self) -> List[Dict[str, Any]]:
        """Get list of currently processing tasks"""
        return [
            {
                'id': task.id,
                'func_name': task.func_name,
                'priority': task.priority.name,
                'created_at': task.created_at
            }
            for task in self.processing_tasks.values()
        ]


class AsyncTaskQueueManager:
    """Manager for multiple task queues"""
    
    def __init__(self):
        self.queues: Dict[str, AsyncTaskQueue] = {}
        self.lock = asyncio.Lock()
        
    async def create_queue(self, name: str, max_workers: int = 10, max_queue_size: int = 1000) -> AsyncTaskQueue:
        """Create a new task queue"""
        async with self.lock:
            if name in self.queues:
                raise ValueError(f"Queue '{name}' already exists")
                
            queue = AsyncTaskQueue(name, max_workers, max_queue_size)
            self.queues[name] = queue
            logger.info(f"Created task queue '{name}'")
            return queue
            
    async def get_queue(self, name: str) -> Optional[AsyncTaskQueue]:
        """Get queue by name"""
        return self.queues.get(name)
        
    async def start_all_queues(self):
        """Start all queues"""
        for queue in self.queues.values():
            await queue.start()
            
    async def stop_all_queues(self, timeout: float = 30.0):
        """Stop all queues"""
        stop_tasks = [queue.stop(timeout) for queue in self.queues.values()]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all queues"""
        return {name: queue.get_stats() for name, queue in self.queues.items()}


# Global task queue manager
task_queue_manager = AsyncTaskQueueManager()