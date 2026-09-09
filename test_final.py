import asyncio
import sys
sys.path.insert(0, r'D:\J.A.R.V.I.S')
from task_queue import TaskQueue

async def test():
    queue = TaskQueue(max_workers=2, max_queue_size=10)
    await queue.start()
    
    async def task():
        await asyncio.sleep(0.1)
        return 'done'
    
    # Submit tasks
    ids = [await queue.submit(lambda: asyncio.sleep(0.1), name=f'task_{i}') for i in range(5)]
    print(f'Submitted: {ids}')
    
    # Wait for all
    results = await asyncio.gather(*[queue.wait_for_task(tid, timeout=5.0) for tid in ids])
    print(f'Results: {results}')
    
    # Check stats
    stats = queue.get_stats()
    print(f'Stats: {stats}')
    
    await queue.shutdown()
    print('Shutdown complete')

asyncio.run(test())