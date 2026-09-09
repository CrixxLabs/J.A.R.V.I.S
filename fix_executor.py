with open(r'D:\J.A.R.V.I.S\executor.py', 'rb') as f:
    content = f.read()

# Find the bad code - search for "Vision subsystem status"
bad_start = content.find(b'Vision subsystem status')
if bad_start == -1:
    bad_start = content.find(b'vision subsystem status')

# Find the end of the bad insertion - look for the next function definition or class
bad_end = content.find(b'\ndef _', bad_start)
if bad_end == -1:
    bad_end = content.find(b'\n# MAIN EXECUTE', bad_start)
if bad_end == -1:
    bad_end = content.find(b'\n# VISION/EYES', bad_start)

print(f'Bad section from {bad_start} to {bad_end}')

# Remove the bad section
new_content = content[:bad_start-1] + content[bad_end:]

with open(r'D:\J.A.R.V.I.S\executor.py', 'wb') as f:
    f.write(new_content)

print('Removed bad section')