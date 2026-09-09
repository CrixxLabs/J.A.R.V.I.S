with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find _NO_RETRY_ACTIONS closing brace
idx = content.find('_NO_RETRY_ACTIONS = {')
idx2 = content.find('}', idx)

# Add vision actions before the closing }
vision_actions = '''
        "capture_webcam", "capture_screen_region",
        "analyze_image_file", "read_image_text", "vision_status",
'''

new_content = content[:idx2] + vision_actions + content[idx2:]

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Vision actions added to _NO_RETRY_ACTIONS')