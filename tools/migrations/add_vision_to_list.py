with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find AVAILABLE_ACTIONS_LIST closing bracket
idx = content.find('AVAILABLE_ACTIONS_LIST = [')
idx2 = content.find(']', idx)

# Add vision actions before the closing ]
vision_actions = '''
    # Vision/Eyes (MARK VII)
    "capture_webcam",           # capture and describe webcam frame
    "capture_screen_region",    # capture and describe screen region
    "analyze_image_file",       # analyze image file with Gemini Vision
    "read_image_text",          # OCR text extraction from image file
    "vision_status",            # get vision subsystem status
'''

new_content = content[:idx2] + vision_actions + content[idx2:]

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Vision actions added to AVAILABLE_ACTIONS_LIST')