import asyncio
import sys
sys.path.insert(0, r'D:\J.A.R.V.I.S')
import logging
logging.basicConfig(level=logging.DEBUG)

from task_queue import TaskQueue

async def test_concurrent():
    queue = TaskQueue(max_workers=2, max_queue_size=10)
    await queue.start()
    
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
    
    # Wait for all tasks
    results = await asyncio.gather(*[queue.wait_for_task(tid, timeout=2.0) for tid in task_ids])
    print(f"Results: {results}")
    
    # Check max concurrent
    print(f"Max concurrent: {max_concurrent}")
    
    await queue.shutdown()

asyncio.run(test_concurrent())