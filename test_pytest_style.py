import asyncio
import sys
sys.path.insert(0, r'D:\J.A.R.V.I.S')
import logging
logging.basicConfig(level=logging.DEBUG)

import pytest
from task_queue import TaskQueue

@pytest.fixture
async def queue():
    """Create a fresh task queue for each test."""
    q = TaskQueue(max_workers=2, max_queue_size=10)
    await q.start()
    try:
        yield q
    finally:
        await queue.shutdown()

@pytest.mark.asyncio
async def test_concurrent_task_limit(queue):
    """Test that max_workers limits concurrent execution."""
    running_count = 0
    max_concurrent = 0
    
    async def monitored_task():
        nonlocal running_count, max_concurrent
        running_count += 1
        max_concurrent = max(max_concurrent, running_count)
        print(f"Task started, running_count={running_count}")
        await asyncio.sleep(0.1)
        running_count -= 1
        print(f"Task ended, running_count={running_count}")
        return "done"
    
    # Submit more tasks than workers
    task_ids = [
        await queue.submit(monitored_task, name=f"task_{i}")
        for i in range(5)
    ]
    
    print(f"Submitted tasks: {task_ids}")
    
    results = await asyncio.gather(*[queue.wait_for_task(tid, timeout=2.0) for tid in task_ids])
    print(f"Results: {results}")
    
    # With 2 workers, max concurrent should be 2
    assert max_concurrent == 2
    print(f"Max concurrent: {max_concurrent}")

asyncio.run(pytest.main([__file__, "-v"]))