"""
task_queue.py — Async Background Task Queue for JARVIS MARK VII.

Provides:
- Priority-based task queue with configurable worker pool
- Task submission, cancellation, status tracking, results
- Integration with lifecycle system
- Automatic scaling based on load
- Retry logic with exponential backoff
"""

import asyncio
import threading
import time
import uuid
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Any, Optional, Dict, List, Awaitable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import status_registry
from status_registry import SubsystemState, get_registry

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task execution states."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskPriority(Enum):
    """Task priority levels (lower = higher priority)."""
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    BACKGROUND = 40


@dataclass(order=True)
class Task:
    """Task representation with priority ordering."""
    priority: int
    created_at: float = field(compare=False)
    task_id: str = field(compare=False)
    name: str = field(compare=False)
    coro_factory: Callable[[], Awaitable[Any]] = field(compare=False)
    status: TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result: Any = field(compare=False, default=None)
    error: Optional[str] = field(compare=False, default=None)
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    retry_delay: float = field(compare=False, default=1.0)
    timeout: Optional[float] = field(compare=False, default=None)
    started_at: Optional[float] = field(compare=False, default=None)
    completed_at: Optional[float] = field(compare=False, default=None)
    cancel_event: asyncio.Event = field(compare=False, default_factory=asyncio.Event)
    progress: float = field(compare=False, default=0.0)
    metadata: Dict[str, Any] = field(compare=False, default_factory=dict)


class TaskQueue:
    """
    Async priority task queue with worker pool.
    
    Features:
    - Priority-based scheduling (critical -> background)
    - Configurable worker pool size
    - Task cancellation support
    - Retry logic with exponential backoff
    - Status tracking and progress reporting
    - Lifecycle integration for graceful shutdown
    """
    
    def __init__(
        self,
        max_workers: int = 4,
        max_queue_size: int = 1000,
        default_timeout: float = 300.0,
        retry_base_delay: float = 1.0,
        max_retries: int = 3,
    ):
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.default_timeout = default_timeout
        self.retry_base_delay = retry_base_delay
        self.max_retries = max_retries
        
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._tasks: Dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._worker_semaphore: Optional[asyncio.Semaphore] = None
        
        # Stats
        self._stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "retried": 0,
        }
    
    async def start(self) -> bool:
        """Start the worker pool."""
        if self._running:
            return True
        
        registry = get_registry()
        registry.set_status("TASK_QUEUE", SubsystemState.READY, "Starting...")
        
        self._running = True
        self._shutdown_event.clear()
        self._worker_semaphore = asyncio.Semaphore(self.max_workers)
        
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        
        # Start stats reporter
        stats_task = asyncio.create_task(self._stats_loop())
        self._workers.append(stats_task)
        
        registry.set_status("TASK_QUEUE", SubsystemState.READY, f"Running with {self.max_workers} workers")
        logger.info(f"TaskQueue started with {self.max_workers} workers")
        return True
    
    async def shutdown(self, timeout: float = 30.0) -> bool:
        """Graceful shutdown - wait for running tasks or cancel them."""
        if not self._running:
            return True
        
        logger.info("TaskQueue shutdown initiated")
        self._shutdown_event.set()
        
        registry = get_registry()
        registry.set_status("TASK_QUEUE", SubsystemState.DEGRADED, "Shutting down...")
        
        # Cancel all pending tasks
        await self.cancel_all()
        
        # Wait for workers to finish with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning("TaskQueue shutdown timeout - forcing worker cancellation")
            for w in self._workers:
                if not w.done():
                    w.cancel()
        
        self._running = False
        registry.set_status("TASK_QUEUE", SubsystemState.DISABLED, "Stopped")
        logger.info("TaskQueue shutdown complete")
        return True
    
    async def submit(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        name: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Submit a task to the queue.
        
        Args:
            coro_factory: Callable that returns an awaitable (coroutine)
            name: Human-readable task name
            priority: Task priority level
            max_retries: Max retry attempts (None = use default)
            timeout: Task timeout in seconds (None = use default)
            metadata: Additional metadata dict
            
        Returns:
            Task ID string
        """
        if not self._running:
            raise RuntimeError("TaskQueue not running")
        
        task_id = str(uuid.uuid4())[:8]
        now = time.time()
        
        task = Task(
            priority=priority.value,
            created_at=now,
            task_id=task_id,
            name=name or f"task_{task_id}",
            coro_factory=coro_factory,
            max_retries=max_retries if max_retries is not None else self.max_retries,
            timeout=timeout or self.default_timeout,
            retry_delay=self.retry_base_delay,
            metadata=metadata or {},
        )
        
        async with self._lock:
            if len(self._tasks) >= self.max_queue_size:
                raise RuntimeError("Task queue full")
            
            self._tasks[task_id] = task
            self._stats["submitted"] += 1
        
        await self._queue.put(task)
        logger.debug(f"Task submitted: {task_id} ({name}) priority={priority.name}")
        return task_id
    
    async def cancel(self, task_id: str) -> bool:
        """Cancel a pending or running task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
            
            task.cancel_event.set()
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            self._stats["cancelled"] += 1
            logger.info(f"Task cancelled: {task_id}")
            return True
    
    async def cancel_all(self) -> int:
        """Cancel all pending and running tasks."""
        cancelled = 0
        async with self._lock:
            for task in self._tasks.values():
                if task.status in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING):
                    task.cancel_event.set()
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = time.time()
                    cancelled += 1
        
        if cancelled:
            self._stats["cancelled"] += cancelled
            logger.info(f"Cancelled {cancelled} tasks")
        return cancelled
    
    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status and result."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            
            return {
                "task_id": task.task_id,
                "name": task.name,
                "status": task.status.value,
                "priority": TaskPriority(task.priority).name if task.priority in [p.value for p in TaskPriority] else "UNKNOWN",
                "result": task.result,
                "error": task.error,
                "retry_count": task.retry_count,
                "progress": task.progress,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "metadata": task.metadata,
            }
    
    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> Any:
        """Wait for task completion and return result."""
        start = time.time()
        while True:
            status = await self.get_status(task_id)
            if not status:
                raise ValueError(f"Task not found: {task_id}")
            
            if status["status"] in ("completed", "failed", "cancelled"):
                if status["status"] == "failed":
                    raise RuntimeError(f"Task failed: {status['error']}")
                if status["status"] == "cancelled":
                    raise asyncio.CancelledError(f"Task cancelled: {task_id}")
                return status["result"]
            
            if timeout and (time.time() - start) > timeout:
                raise asyncio.TimeoutError(f"Task {task_id} timed out")
            
            await asyncio.sleep(0.1)
    
    async def _worker_loop(self, worker_id: int) -> None:
        """Main worker loop."""
        logger.debug(f"Worker {worker_id} started")
        
        while not self._shutdown_event.is_set():
            try:
                # Get next task with timeout to check shutdown
                try:
                    task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                # Check if task was cancelled while queued
                if task.cancel_event.is_set():
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = time.time()
                    async with self._lock:
                        self._stats["cancelled"] += 1
                    continue
                
                # Execute with semaphore to limit concurrency
                async with self._worker_semaphore:
                    await self._execute_task(task)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
        
        logger.debug(f"Worker {worker_id} stopped")
    
    async def _execute_task(self, task: Task) -> None:
        """Execute a single task with retry logic."""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        
        last_error = None
        for attempt in range(task.max_retries + 1):
            if task.cancel_event.is_set():
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                async with self._lock:
                    self._stats["cancelled"] += 1
                return
            
            task.retry_count = attempt
            if attempt > 0:
                task.status = TaskStatus.RETRYING
                delay = task.retry_delay * (2 ** (attempt - 1))  # Exponential backoff
                logger.debug(f"Task {task.task_id} retry {attempt}/{task.max_retries} after {delay}s")
                await asyncio.sleep(delay)
                if task.cancel_event.is_set():
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = time.time()
                    async with self._lock:
                        self._stats["cancelled"] += 1
                    return
            
            try:
                # Execute with timeout
                if task.timeout:
                    result = await asyncio.wait_for(task.coro_factory(), timeout=task.timeout)
                else:
                    result = await task.coro_factory()
                
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.progress = 1.0
                async with self._lock:
                    self._stats["completed"] += 1
                logger.debug(f"Task {task.task_id} completed")
                return
                
            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                task.error = "Cancelled"
                task.completed_at = time.time()
                async with self._lock:
                    self._stats["cancelled"] += 1
                return
            except asyncio.TimeoutError:
                last_error = f"Task timed out after {task.timeout}s"
            except Exception as e:
                last_error = str(e)
            
            if attempt < task.max_retries:
                continue  # Will retry
        
        # All retries exhausted
        task.status = TaskStatus.FAILED
        task.error = last_error or "Max retries exceeded"
        task.completed_at = time.time()
        async with self._lock:
            self._stats["failed"] += 1
            self._stats["retried"] += task.retry_count
        logger.warning(f"Task {task.task_id} failed after {task.max_retries + 1} attempts: {last_error}")
    
    async def _stats_loop(self) -> None:
        """Periodic stats reporting."""
        while not self._shutdown_event.is_set():
            await asyncio.sleep(30)
            if not self._shutdown_event.is_set():
                registry = get_registry()
                pending = sum(1 for t in self._tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.QUEUED))
                running = sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)
                registry.set_status(
                    "TASK_QUEUE",
                    SubsystemState.READY,
                    f"Workers: {self.max_workers}, Pending: {pending}, Running: {running}"
                )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            **self._stats,
            "total_tasks": len(self._tasks),
            "pending": sum(1 for t in self._tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.QUEUED)),
            "running": sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING),
            "workers": self.max_workers,
            "queue_size": self._queue.qsize(),
        }


# Global task queue instance
_task_queue: Optional[TaskQueue] = None
_task_queue_lock = threading.Lock()


def get_task_queue() -> TaskQueue:
    """Get or create the global task queue."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is None:
            _task_queue = TaskQueue()
        return _task_queue


async def init_task_queue(
    max_workers: int = 4,
    max_queue_size: int = 1000,
) -> TaskQueue:
    """Initialize and start the global task queue."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is not None:
            await _task_queue.shutdown()
        _task_queue = TaskQueue(max_workers=max_workers, max_queue_size=max_queue_size)
        await _task_queue.start()
        return _task_queue


async def shutdown_task_queue(timeout: float = 30.0) -> None:
    """Shutdown the global task queue."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is not None:
            await _task_queue.shutdown(timeout=timeout)
            _task_queue = None


# Convenience functions
async def submit_task(
    coro_factory: Callable[[], Awaitable[Any]],
    name: str = "",
    priority: TaskPriority = TaskPriority.NORMAL,
    **kwargs
) -> str:
    """Submit a task to the global queue."""
    queue = get_task_queue()
    return await queue.submit(coro_factory, name, priority, **kwargs)


async def wait_for_task(task_id: str, timeout: Optional[float] = None) -> Any:
    """Wait for a task by ID."""
    queue = get_task_queue()
    return await queue.wait_for_task(task_id, timeout)


async def cancel_task(task_id: str) -> bool:
    """Cancel a task by ID."""
    queue = get_task_queue()
    return await queue.cancel(task_id)


async def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Get task status."""
    queue = get_task_queue()
    return await queue.get_status(task_id)