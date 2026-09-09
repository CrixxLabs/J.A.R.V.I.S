import asyncio
import sys
sys.path.insert(0, r'D:\J.A.R.V.I.S')

from task_queue import TaskQueue
import logging

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)

async def test_concurrent():
    queue = TaskQueue(max_workers=2, max_queue_size=10)
    await queue.start()
    
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
    
    print(f"Submitted tasks: {task_ids}")
    
    # Check status immediately
    for tid in task_ids:
        status = await queue.get_status(tid)
        print(f"Task {tid} status: {status}")
    
    # Wait a bit for tasks to start
    await asyncio.sleep(0.5)
    
    for tid in task_ids:
        status = await queue.get_status(tid)
        print(f"Task {tid} status after 0.5s: {status}")
    
    results = await asyncio.gather(*[queue.wait_for_task(tid, timeout=5.0) for tid in task_ids])
    print(f"Results: {results}")
    
    print(f"Max concurrent: {max_concurrent}")
    
    await queue.shutdown()

import asyncio
asyncio.run(test_concurrent())