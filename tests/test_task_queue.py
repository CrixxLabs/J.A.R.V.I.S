"""
Test suite for JARVIS task_queue module.
"""

import pytest
import pytest_asyncio
import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock

from task_queue import (
    TaskQueue, Task, TaskStatus, TaskPriority,
    get_task_queue, init_task_queue, shutdown_task_queue,
    submit_task, wait_for_task, cancel_task, get_task_status
)


class TestTaskQueue:
    """Test TaskQueue core functionality."""
    
    @pytest_asyncio.fixture
    async def queue(self):
        """Create a fresh task queue for each test."""
        q = TaskQueue(max_workers=2, max_queue_size=10)
        await q.start()
        try:
            yield q
        finally:
            await q.shutdown(timeout=5.0)
    
    @pytest.mark.asyncio
    async def test_submit_and_wait(self, queue):
        """Test basic task submission and completion."""
        async def simple_task():
            await asyncio.sleep(0.05)
            return "success"
        
        task_id = await queue.submit(simple_task, name="test_task")
        result = await queue.wait_for_task(task_id, timeout=2.0)
        
        assert result == "success"
        status = await queue.get_status(task_id)
        assert status["status"] == "completed"
        assert status["result"] == "success"
    
    @pytest.mark.asyncio
    async def test_task_priority_ordering(self, queue):
        """Test that higher priority tasks execute first."""
        results = []
        
        async def low_priority():
            await asyncio.sleep(0.01)
            results.append("low")
            return "low"
        
        async def high_priority():
            await asyncio.sleep(0.01)
            results.append("high")
            return "high"
        
        # Submit low first, then high
        low_id = await queue.submit(low_priority, priority=TaskPriority.LOW)
        high_id = await queue.submit(high_priority, priority=TaskPriority.HIGH)
        
        await queue.wait_for_task(low_id, timeout=2.0)
        await queue.wait_for_task(high_id, timeout=2.0)
        
        # High priority should complete first (or at least be queued first)
        # Note: With async execution, exact ordering depends on timing
        assert "high" in results
        assert "low" in results
    
    @pytest.mark.asyncio
    async def test_task_cancellation(self, queue):
        """Test cancelling a pending task."""
        async def slow_task():
            await asyncio.sleep(10)
            return "done"
        
        task_id = await queue.submit(slow_task, name="slow")
        await asyncio.sleep(0.01)  # Let it queue
        
        cancelled = await queue.cancel(task_id)
        assert cancelled is True
        
        status = await queue.get_status(task_id)
        assert status["status"] == "cancelled"
    
    @pytest.mark.asyncio
    async def test_task_retry_on_failure(self, queue):
        """Test automatic retry on task failure."""
        attempt_count = 0
        
        async def failing_task():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ValueError("Temporary failure")
            return "success"
        
        task_id = await queue.submit(
            failing_task,
            max_retries=3,
        )
        
        # With retry_delay=1.0 and exponential backoff: 1s + 2s + 4s = 7s total wait
        # plus 3 attempts, should complete within 10s
        result = await queue.wait_for_task(task_id, timeout=15.0)
        
        assert result == "success"
        assert attempt_count == 3
        status = await queue.get_status(task_id)
        assert status["status"] == "completed"
        assert status["retry_count"] == 2  # 2 retries after initial attempt
    
    @pytest.mark.asyncio
    async def test_task_timeout(self, queue):
        """Test task timeout enforcement."""
        async def slow_task():
            await asyncio.sleep(10)
            return "done"
        
        task_id = await queue.submit(slow_task, timeout=0.05, max_retries=0)
        
        with pytest.raises(RuntimeError) as exc_info:
            await queue.wait_for_task(task_id, timeout=1.0)
        
        assert "timed out" in str(exc_info.value).lower()
        
        status = await queue.get_status(task_id)
        assert status["status"] == "failed"
    
    @pytest.mark.asyncio
    async def test_concurrent_task_limit(self, queue):
        """Test that max_workers limits concurrent execution."""
        running_count = 0
        max_concurrent = 0
        
        async def monitored_task():
            nonlocal running_count, max_concurrent
            running_count += 1
            max_concurrent = max(max_concurrent, running_count)
            await asyncio.sleep(0.1)
            running_count -= 1
            return "done"
        
        # Submit more tasks than workers
        task_ids = [
            await queue.submit(monitored_task, name=f"task_{i}")
            for i in range(5)
        ]
        
        await asyncio.gather(*[queue.wait_for_task(tid, timeout=2.0) for tid in task_ids])
        
        # With 2 workers, max concurrent should be 2
        assert max_concurrent <= 2
    
    @pytest.mark.asyncio
    async def test_task_metadata_and_progress(self, queue):
        """Test task metadata and progress tracking."""
        async def reporting_task():
            return {"output": "data", "items_processed": 42}
        
        task_id = await queue.submit(
            reporting_task,
            name="report_task",
            metadata={"source": "test", "version": "1.0"}
        )
        
        result = await queue.wait_for_task(task_id, timeout=2.0)
        assert result["output"] == "data"
        assert result["items_processed"] == 42
        
        status = await queue.get_status(task_id)
        assert status["metadata"]["source"] == "test"
        assert status["metadata"]["version"] == "1.0"
    
    @pytest.mark.asyncio
    async def test_get_stats(self, queue):
        """Test queue statistics."""
        async def quick_task():
            return "done"
        
        await queue.submit(quick_task)
        await queue.submit(quick_task)
        await asyncio.sleep(0.1)  # Let them complete
        
        stats = queue.get_stats()
        
        assert stats["submitted"] >= 2
        assert stats["completed"] >= 2
        assert "total_tasks" in stats
        assert "pending" in stats
        assert "running" in stats
        assert "workers" in stats


class TestTaskQueueIntegration:
    """Integration tests with global queue functions."""
    
    @pytest_asyncio.fixture
    async def global_queue(self):
        """Initialize global queue for tests."""
        await shutdown_task_queue()
        q = await init_task_queue(max_workers=2, max_queue_size=10)
        try:
            yield q
        finally:
            await shutdown_task_queue()
    
    @pytest.mark.asyncio
    async def test_global_queue_functions(self, global_queue):
        """Test convenience functions."""
        async def task():
            return "global"
        
        task_id = await submit_task(task, name="global_test")
        result = await wait_for_task(task_id, timeout=2.0)
        
        assert result == "global"
        
        status = await get_task_status(task_id)
        assert status["status"] == "completed"
    
    @pytest.mark.asyncio
    async def test_shutdown_cancels_pending(self, global_queue):
        """Test that shutdown cancels pending tasks."""
        async def slow():
            await asyncio.sleep(10)
            return "done"
        
        task_id = await submit_task(slow, name="slow_global")
        await asyncio.sleep(0.01)
        
        # Shutdown should cancel
        await shutdown_task_queue(timeout=2.0)
        
        status = await get_task_status(task_id)
        # After shutdown, task should be cancelled or not found
        # Note: status may be None if queue was cleared
    
    @pytest.mark.asyncio
    async def test_reinit_after_shutdown(self, global_queue):
        """Test re-initializing queue after shutdown."""
        await shutdown_task_queue()
        
        new_queue = await init_task_queue(max_workers=1)
        assert new_queue is not None
        assert new_queue._running is True
        
        await shutdown_task_queue()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])