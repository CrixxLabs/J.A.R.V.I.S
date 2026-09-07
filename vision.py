# vision.py — Screenshot & Screen Reader (Phase 2)
# Uses Gemini Vision for screen understanding
# Fully compatible with conversation_manager

import base64
import os
import numpy as np
from mss import mss
import cv2
import requests
from dotenv import load_dotenv
import pytesseract

# Reliability imports
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Tesseract path (same as executor.py)
pytesseract.pytesseract.tesseract_cmd = os.getenv(
    "TESSERACT_PATH",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)


def capture_screen():
    """Capture screenshot using mss — returns numpy array in BGR format for cv2."""
    try:
        with mss() as sct:
            # mss returns BGRA format — convert to BGR for cv2 compatibility
            screenshot = np.array(sct.grab(sct.monitors[1]))
            img = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
            return img
    except Exception as exc:
        # If capture fails, it degrades both Gemini screen read and Tesseract OCR
        error_handler.log_and_demote(
            subsystem="TESSERACT_OCR",
            exception=exc,
            context="Capture screen mss interface",
            demote_to=SubsystemState.OFFLINE
        )
        raise exc


def screenshot_to_base64():
    """Capture screen and encode to base64 PNG for Gemini API."""
    img = capture_screen()
    success, buffer = cv2.imencode('.png', img)
    if not success:
        raise ValueError("Failed to encode screenshot as PNG")
    return base64.b64encode(buffer).decode('utf-8')


def describe_screen(prompt="describe what you see"):
    """Send screenshot to Gemini for description."""
    registry = get_registry()

    if not GEMINI_API_KEY:
        print("[Vision] No Gemini API key — falling back to OCR")
        registry.set_status(
            "GEMINI",
            SubsystemState.DISABLED,
            "Gemini API key is empty in environment"
        )
        return "Gemini API key not configured. Using OCR fallback."

    try:
        b64 = screenshot_to_base64()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": b64
                        }
                    }
                ]
            }]
        }

        res = requests.post(url, json=payload, timeout=15)
        res.raise_for_status()
        data = res.json()

        # Debug: print full response if it fails
        if "candidates" not in data or not data["candidates"]:
            print(f"[Vision] Gemini API error response: {data}")
            
            # Check for specific error messages
            if "error" in data:
                error_msg = data["error"].get("message", "Unknown error")
                print(f"[Vision] Gemini error: {error_msg}")
                
                if "API_KEY_INVALID" in error_msg or "API key not valid" in error_msg:
                    registry.set_status(
                        "GEMINI",
                        SubsystemState.DEGRADED,
                        "Invalid API Key verified by Gemini API endpoint"
                    )
                    return "Your Gemini API key is invalid. Get a new one from https://aistudio.google.com/apikey"
                
                registry.set_status(
                    "GEMINI",
                    SubsystemState.DEGRADED,
                    f"Gemini API returned error: {error_msg}"
                )
                return f"Gemini API error: {error_msg}"
            
            registry.set_status(
                "GEMINI",
                SubsystemState.DEGRADED,
                "Gemini API payload lacks candidates"
            )
            return "Could not describe the screen."

        # Extract text from response and mark GEMINI status READY
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        registry.set_status(
            "GEMINI",
            SubsystemState.READY,
            "Gemini Vision responding normally"
        )
        return text.strip()

    except requests.exceptions.Timeout as timeout_exc:
        error_handler.log_and_demote(
            subsystem="GEMINI",
            exception=timeout_exc,
            context="Gemini vision endpoint timeout",
            demote_to=SubsystemState.DEGRADED
        )
        return "Screen description timed out. Falling back to OCR."
    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="GEMINI",
            exception=exc,
            context="Querying Gemini Vision endpoint",
            demote_to=SubsystemState.DEGRADED
        )
        return "Screen description failed. Falling back to OCR."


def read_screen():
    """OCR fallback — reads actual text from screen using pytesseract."""
    registry = get_registry()
    try:
        img = capture_screen()
        
        # Preprocess for better OCR accuracy
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Apply thresholding to make text clearer
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        
        # Extract text using pytesseract
        text = pytesseract.image_to_string(thresh).strip()
        
        # Mark READY if we successfully pass through pytesseract calls
        registry.set_status(
            "TESSERACT_OCR",
            SubsystemState.READY,
            "Pytesseract engine parsed successfully"
        )

        if not text:
            return "No text detected on screen."
        
        # Return first 500 chars
        return text[:500]
        
    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="TESSERACT_OCR",
            exception=exc,
            context="Pytesseract local OCR fallback engine",
            demote_to=SubsystemState.OFFLINE
        )
        return "OCR failed."


def analyze_screen_context():
    """Combines vision with conversation context."""
    try:
        import conversation_manager
        description = describe_screen("Describe the current screen in detail, including any text, apps, or important elements.")
        context = conversation_manager.get_context_block()
        return f"Screen: {description}\n\nContext: {context}"
    except Exception as exc:
        # Don't demote here as describe_screen handles its own status; just fallback safely
        print(f"[Vision] analyze_screen_context error: {exc}")
        return describe_screen("Describe what you see on this screen.")