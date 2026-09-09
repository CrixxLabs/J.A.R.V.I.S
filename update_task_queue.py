import re

with open(r'D:\J.A.R.V.I.S\task_queue.py', 'r') as f:
    content = f.read()

# 1. Update __init__ to add use_dedicated_thread parameter
old_init = '''def __init__(
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
        }'''

new_init = '''def __init__(
        self,
        max_workers: int = 4,
        max_queue_size: int = 1000,
        default_timeout: float = 300.0,
        retry_base_delay: float = 1.0,
        max_retries: int = 3,
        use_dedicated_thread: bool = False,
    ):
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.default_timeout = default_timeout
        self.retry_base_delay = retry_base_delay
        self.max_retries = max_retries
        self._use_dedicated_thread = use_dedicated_thread
        
        # Internal async state (accessed only from the event loop)
        self._queue: asyncio.PriorityQueue = None
        self._tasks: Dict[str, Task] = {}
        self._lock: asyncio.Lock = None
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._shutdown_event: asyncio.Event = None
        self._worker_semaphore: asyncio.Semaphore = None
        
        # Thread-safe event loop management (only used if use_dedicated_thread=True)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._shutdown_complete = threading.Event()
        
        # Stats
        self._stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "retried": 0,
        }'''

content = content.replace(old_init, new_init)

# 2. Update start method
old_start = '''    async def start(self) -> bool:
        """Start the worker pool."""
        if self._running:
            return True
        
        registry = get_registry()
        registry.set_status("TASK_QUEUE", SubsystemState.READY, "Starting...")
        
        self._running = True
        self._shutdown_event = asyncio.Event()
        
        self._queue = asyncio.PriorityQueue(maxsize=self.max_queue_size)
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._worker_semaphore = asyncio.Semaphore(self.max_workers)
        self._workers = []
        
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        
        # Start stats reporter
        stats_task = asyncio.create_task(self._stats_loop())
        self._workers.append(stats_task)
        
        registry.set_status("TASK_QUEUE", SubsystemState.READY, f"Running with {self.max_workers} workers")
        logger.info(f"TaskQueue started with {self.max_workers} workers")
        return True'''

new_start = '''    async def start(self) -> bool:
        """Start the worker pool."""
        if self._running:
            return True
        
        registry = get_registry()
        registry.set_status("TASK_QUEUE", SubsystemState.READY, "Starting...")
        
        self._running = True
        self._shutdown_event = asyncio.Event()
        
        if self._use_dedicated_thread:
            # Start event loop in dedicated thread
            self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
            self._loop_thread.start()
            
            # Wait for loop to be ready
            self._loop_ready.wait(timeout=5.0)
            if self._loop is None:
                raise RuntimeError("Failed to start event loop")
            
            # Initialize queue and start workers in the loop
            await self._async_start()
        else:
            # Use current event loop
            self._loop = asyncio.get_running_loop()
            await self._async_start()
        return True

    async def _async_start(self) -> bool:
        """Async initialization - runs in the event loop."""
        registry = get_registry()
        registry.set_status("TASK_QUEUE", SubsystemState.READY, "Starting...")
        
        self._queue = asyncio.PriorityQueue(maxsize=self.max_queue_size)
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._worker_semaphore = asyncio.Semaphore(self.max_workers)
        self._workers = []
        
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

    def _run_loop(self):
        """Run the event loop in a dedicated thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()
            self._loop = None
            self._shutdown_complete.set()'''

content = content.replace(old_start, new_start)

# 3. Update shutdown method
old_shutdown = '''    async def shutdown(self, timeout: float = 30.0) -> bool:
        """Graceful shutdown - wait for running tasks or cancel them."""
        if not self._running or self._loop is None:
            return True
        
        logger.info("TaskQueue shutdown initiated")
        
        # Signal shutdown and wait for completion in the loop
        await self._async_shutdown(timeout)
        
        # Wait for loop thread to finish
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5.0)
        
        self._running = False
        return True

    async def _async_shutdown(self, timeout: float) -> bool:
        """Async shutdown - runs in the dedicated loop."""
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
        return True'''

new_shutdown = '''    async def shutdown(self, timeout: float = 30.0) -> bool:
        """Graceful shutdown - wait for running tasks or cancel them."""
        if not self._running or self._loop is None:
            return True
        
        logger.info("TaskQueue shutdown initiated")
        
        # Signal shutdown and wait for completion in the loop
        await self._async_shutdown(timeout)
        
        # Wait for loop thread to finish (only if using dedicated thread)
        if self._use_dedicated_thread and self._loop_thread and self._loop_thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5.0)
        
        self._running = False
        return True'''

content = content.replace(old_shutdown, new_shutdown)

# 4. Update _async_shutdown to match
old_async_shutdown = '''    async def _async_shutdown(self, timeout: float) -> bool:
        """Async shutdown - runs in the dedicated loop."""
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
        return True'''

new_async_shutdown = '''    async def _async_shutdown(self, timeout: float) -> bool:
        """Async shutdown - runs in the event loop."""
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
        return True'''

content = content.replace(old_async_shutdown, new_async_shutdown)

# 5. Update submit method to be async and return awaitable
old_submit = '''    async def submit(
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
        return task_id'''

new_submit = '''    async def submit(
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
        if not self._running or self._loop is None:
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
        return task_id'''

content = content.replace(old_submit, new_submit)

# 6. Update cancel method
old_cancel = '''    async def cancel(self, task_id: str) -> bool:
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
            return True'''

new_cancel = '''    async def cancel(self, task_id: str) -> bool:
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
            return True'''

content = content.replace(old_cancel, new_cancel)

# 7. Update cancel_all method
old_cancel_all = '''    async def cancel_all(self) -> int:
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
            return cancelled'''

new_cancel_all = '''    async def cancel_all(self) -> int:
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
            return cancelled'''

content = content.replace(old_cancel_all, new_cancel_all)

# 8. Update get_status method
old_get_status = '''    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
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
            }'''

new_get_status = '''    async def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status and result."""
        if not self._running or self._loop is None:
            return None
        
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
            }'''

content = content.replace(old_get_status, new_get_status)

# 9. Update wait_for_task method
old_wait_for = '''    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> Any:
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
            
            await asyncio.sleep(0.1)'''

new_wait_for = '''    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> Any:
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
            
            await asyncio.sleep(0.1)'''

content = content.replace(old_wait_for, new_wait_for)

# 10. Update get_stats method
old_get_stats = '''    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            **self._stats,
            "total_tasks": len(self._tasks),
            "pending": sum(1 for t in self._tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.QUEUED)),
            "running": sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING),
            "workers": self.max_workers,
            "queue_size": self._queue.qsize(),
        }'''

new_get_stats = '''    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        async def _get_stats_async():
            return {
                **self._stats,
                "total_tasks": len(self._tasks),
                "pending": sum(1 for t in self._tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.QUEUED)),
                "running": sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING),
                "workers": self.max_workers,
                "queue_size": self._queue.qsize() if self._queue else 0,
            }
        
        if not self._running or self._loop is None:
            return {
                **self._stats,
                "total_tasks": len(self._tasks),
                "pending": 0,
                "running": 0,
                "workers": self.max_workers,
                "queue_size": 0,
            }
        
        # Use a thread pool to avoid deadlock when called from the same event loop
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run_coroutine_threadsafe(_get_stats_async(), self._loop).result())
            return future.result(timeout=10.0)'''

content = content.replace(old_get_stats, new_get_stats)

# 11. Update global functions
old_global = '''# Global task queue instance
_task_queue: Optional[TaskQueue] = None
_task_queue_lock = threading.Lock()


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


def get_task_queue() -> TaskQueue:
    """Get or create the global task queue."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is None:
            _task_queue = TaskQueue()
        return _task_queue'''

new_global = '''# Global task queue instance
_task_queue: Optional[TaskQueue] = None
_task_queue_lock = threading.Lock()


async def init_task_queue(
    max_workers: int = 4,
    max_queue_size: int = 1000,
    use_dedicated_thread: bool = True,
) -> TaskQueue:
    """Initialize and start the global task queue."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is not None:
            await _task_queue.shutdown()
        _task_queue = TaskQueue(max_workers=max_workers, max_queue_size=max_queue_size, use_dedicated_thread=use_dedicated_thread)
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


def get_task_queue() -> TaskQueue:
    """Get or create the global task queue."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is None:
            _task_queue = TaskQueue()
        return _task_queue'''

content = content.replace(old_global, new_global)

# Write the updated content
with open(r'D:\J.A.R.V.I.S\task_queue.py', 'w') as f:
    f.write(content)

print('All updates applied successfully')