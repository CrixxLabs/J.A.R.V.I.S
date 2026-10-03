import os

with open(r'D:\J.A.R.V.I.S\jarvis.py', 'rb') as f:
    content = f.read()

# Find and replace the init_task_queue call
old = b'asyncio.run(init_task_queue(max_workers=4))'
new = b'asyncio.run(init_task_queue(max_workers=4, use_dedicated_thread=True))'

if old in content:
    content = content.replace(old, new)
    with open(r'D:\J.A.R.V.I.S\jarvis.py', 'wb') as f:
        f.write(content)
    print('Updated jarvis.py')
else:
    print('Pattern not found')