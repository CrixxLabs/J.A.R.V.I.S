# Vision handlers to add to executor.py
VISION_HANDLERS = '''

# ═══════════════════════════════════════════════════════════════════════════════
# VISION/EYES ACTIONS (MARK VII - Visual Awareness)
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_capture_webcam(action: dict) -> tuple:
    """Capture a frame from the webcam and optionally describe it."""
    try:
        from vision import capture_webcam, describe_image, get_vision_status
        
        camera_index = action.get("camera_index", 0)
        describe = action.get("describe", True)
        prompt = action.get("prompt", "describe what you see in this webcam capture")
        
        frame = capture_webcam(camera_index)
        if frame is None:
            return _stable_failure("Could not access webcam. Make sure it's connected and not in use by another app.")
        
        if describe:
            result = describe_image(frame, prompt)
            return _stable_success(result)
        else:
            return _stable_success("Webcam frame captured successfully.")
            
    except Exception as exc:
        print(f"[DEBUG][executor] capture_webcam error: {exc}")
        return _stable_failure(f"Webcam capture failed: {exc}")


def _handle_capture_screen_region(action: dict) -> tuple:
    """Capture a specific screen region."""
    try:
        from vision import capture_screen_region, describe_image
        import cv2
        
        left = action.get("left", 0)
        top = action.get("top", 0)
        width = action.get("width", 800)
        height = action.get("height", 600)
        describe = action.get("describe", True)
        prompt = action.get("prompt", "describe what you see in this screen region")
        save_path = action.get("save_path", "")
        
        img = capture_screen_region(left, top, width, height)
        
        # Save if requested
        if save_path:
            cv2.imwrite(save_path, img)
            saved_msg = " Saved to " + save_path + "."
        else:
            saved_msg = ""
        
        if describe:
            result = describe_image(img, prompt)
            return _stable_success(result + saved_msg)
        else:
            return _stable_success("Screen region captured (" + str(width) + "x" + str(height) + " at " + str(left) + "," + str(top) + ")" + saved_msg)
            
    except Exception as exc:
        print(f"[DEBUG][executor] capture_screen_region error: {exc}")
        return _stable_failure("Screen region capture failed: " + str(exc))


def _handle_analyze_image_file(action: dict) -> tuple:
    """Analyze an image file using Gemini Vision."""
    try:
        from vision import analyze_image_file
        
        file_path = action.get("file_path", "") or action.get("path", "")
        prompt = action.get("prompt", "describe what you see in this image")
        
        if not file_path:
            return _stable_failure("Which image file should I analyze? Provide a file path.")
        
        result = analyze_image_file(file_path, prompt)
        return _stable_success(result)
        
    except Exception as exc:
        print(f"[DEBUG][executor] analyze_image_file error: {exc}")
        return _stable_failure("Image analysis failed: " + str(exc))


def _handle_read_image_text(action: dict) -> tuple:
    """Extract text from an image file using OCR."""
    try:
        from vision import read_image_text
        
        file_path = action.get("file_path", "") or action.get("path", "")
        
        if not file_path:
            return _stable_failure("Which image file should I read? Provide a file path.")
        
        result = read_image_text(file_path)
        return _stable_success(result)
        
    except Exception as exc:
        print(f"[DEBUG][executor] read_image_text error: {exc}")
        return _stable_failure("Image OCR failed: " + str(exc))


def _handle_vision_status(action: dict) -> tuple:
    """Get status of all vision subsystems."""
    try:
        from vision import get_vision_status
        
        status = get_vision_status()
        lines = []
        for subsystem, info in status.items():
            state = info.get("state", "unknown")
            detail = info.get("detail", "")
            lines.append(subsystem + ": " + state + " -- " + detail)
        
        return _stable_success("Vision subsystem status:\n" + "\n".join(lines))
        
    except Exception as exc:
        print(f"[DEBUG][executor] vision_status error: {exc}")
        return _stable_failure("Couldn't get vision status: " + str(exc))


'''

with open(r'D:\J.A.R.V.I.S\executor.py', 'rb') as f:
    content = f.read()

# Find the MAIN EXECUTE section
marker = b'# MAIN EXECUTE'
pos = content.find(marker)
# Go back to start of the comment block
while pos > 0 and content[pos-1:pos] != b'\n':
    pos -= 1
pos2 = content.rfind(b'\n', 0, pos)
pos = pos2 + 1 if pos2 >= 0 else 0

print(f'Inserting at position: {pos}')
print(f'Context before: {content[pos-50:pos]}')

# Insert the vision handlers
new_content = content[:pos] + VISION_HANDLERS.encode('utf-8') + content[pos:]

with open(r'D:\J.A.R.V.I.S\executor.py', 'wb') as f:
    f.write(new_content)

print('Done - vision handlers inserted')