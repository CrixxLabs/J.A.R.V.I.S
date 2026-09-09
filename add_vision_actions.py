with open(r'D:\J.A.R.V.I.S\executor.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find where to add action handlers in execute() - after self_changes handler
idx = content.find('if act == "self_changes":')
idx2 = content.find('return _stable_failure("Unsupported action.")', idx)

# The vision action handlers to insert
vision_actions = '''
        # Vision/Eyes actions (MARK VII)
        if act == "capture_webcam":
            return _handle_capture_webcam(action)
        if act == "capture_screen_region":
            return _handle_capture_screen_region(action)
        if act == "analyze_image_file":
            return _handle_analyze_image_file(action)
        if act == "read_image_text":
            return _handle_read_image_text(action)
        if act == "vision_status":
            return _handle_vision_status(action)

'''

# Insert before "Unsupported action"
new_content = content[:idx2] + vision_actions + content[idx2:]

with open(r'D:\J.A.R.V.I.S\executor.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Vision action handlers inserted')