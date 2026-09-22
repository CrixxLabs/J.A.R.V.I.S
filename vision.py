"""MARK VII visual awareness.

Visual understanding is local-first: jarvis:latest (Ministral 3 3B) through brain.py, with Gemini
as a cloud fallback. OCR remains a separate Tesseract capability.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import cv2
import numpy as np
import requests
from dotenv import load_dotenv
try:
    from mss import mss
except Exception:
    mss = None

import error_handler
from ocr_runtime import (
    OCREmptyResultError, OCRTimeoutError, OCRUnavailableError,
    extract_image_text, probe_tesseract,
)
from status_registry import EvidenceLevel, SubsystemState, get_registry

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-3.1-flash-lite")
GEMINI_VISION_TIMEOUT = float(os.getenv("GEMINI_VISION_TIMEOUT", "15.0"))


def discover_tesseract() -> str | None:
    return probe_tesseract().path


def capture_screen():
    """Capture the primary desktop as a BGR numpy image."""
    try:
        if mss is None:
            raise RuntimeError("MSS screen capture dependency is unavailable")
        with mss() as sct:
            screenshot = np.array(sct.grab(sct.monitors[1]))
        img = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
        registry = get_registry()
        registry.set_evidence("VISION", EvidenceLevel.LIVE, "Desktop frame captured", source="mss capture")
        registry.set_capability_evidence("SCREEN_CAPTURE", EvidenceLevel.LIVE, "Desktop frame captured", source="mss capture")
        return img
    except Exception as exc:
        error_handler.log_and_demote("VISION", exc, "Capture screen mss interface", SubsystemState.DEGRADED)
        raise


def image_to_base64(img: np.ndarray) -> str:
    if img is None or not isinstance(img, np.ndarray) or img.size == 0:
        raise ValueError("Image is empty")
    success, buffer = cv2.imencode(".png", img)
    if not success:
        raise ValueError("Failed to encode image as PNG")
    return base64.b64encode(buffer).decode("ascii")


def screenshot_to_base64() -> str:
    return image_to_base64(capture_screen())


def _extract_gemini_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    content = candidates[0].get("content", {}) if isinstance(candidates[0], dict) else {}
    parts = content.get("parts", []) if isinstance(content, dict) else []
    texts = [part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text")]
    return "\n".join(texts).strip()


def _gemini_vision(prompt: str, image_b64: str, mime_type: str = "image/png") -> tuple[str, str]:
    """Bounded Gemini visual fallback. Returns (content, status)."""
    registry = get_registry()
    if not GEMINI_API_KEY:
        registry.set_capability_evidence("GEMINI_VISION", EvidenceLevel.DISABLED, "GEMINI_API_KEY is not configured", source="vision fallback")
        return "", "unavailable"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_VISION_MODEL}:generateContent"
        payload = {"contents": [{"parts": [
            {"text": str(prompt or "Describe this image accurately.")},
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
        ]}]}
        response = requests.post(url, headers={"x-goog-api-key": GEMINI_API_KEY}, json=payload, timeout=GEMINI_VISION_TIMEOUT)
        response.raise_for_status()
        text = _extract_gemini_text(response.json())
        if not text:
            registry.set_capability_evidence("GEMINI_VISION", EvidenceLevel.BROKEN, "Gemini returned no visual content", source="vision fallback")
            return "", "empty"
        registry.set_evidence("GEMINI", EvidenceLevel.LIVE, f"Gemini vision succeeded ({GEMINI_VISION_MODEL})", source="vision generation")
        registry.set_capability_evidence("GEMINI_VISION", EvidenceLevel.LIVE, "Cloud multimodal response succeeded", source="vision generation")
        return text, "ok"
    except requests.exceptions.Timeout:
        registry.set_capability_evidence("GEMINI_VISION", EvidenceLevel.BLOCKED, "Gemini vision request timed out", source="vision fallback")
        return "", "timeout"
    except Exception as exc:
        registry.set_capability_evidence("GEMINI_VISION", EvidenceLevel.BROKEN, f"{type(exc).__name__}: {str(exc)[:140]}", source="vision fallback")
        return "", "error"


def analyze_image_base64(image_b64: str, prompt: str = "describe what you see", mime_type: str = "image/png") -> str:
    """Local-first visual router: jarvis:latest (Ministral 3 3B), then Gemini cloud fallback."""
    registry = get_registry()
    if not isinstance(image_b64, str) or not image_b64.strip():
        registry.set_capability_evidence("VISION_ROUTER", EvidenceLevel.BROKEN, "Image payload is empty", source="vision router")
        return "Visual analysis failed: image payload is empty."
    payload = image_b64.strip()
    if payload.startswith("data:"):
        header, separator, encoded = payload.partition(",")
        if not separator or ";base64" not in header.lower():
            registry.set_capability_evidence("VISION_ROUTER", EvidenceLevel.BROKEN, "Unsupported image data URL", source="vision router")
            return "Visual analysis failed: invalid image payload."
        declared_mime = header[5:].split(";", 1)[0].strip()
        if declared_mime.startswith("image/"):
            mime_type = declared_mime
        payload = encoded.strip()
    try:
        # Validate base64 before any provider call. This prevents malformed UI
        # payloads from being misreported as provider failures.
        base64.b64decode(payload, validate=True)
    except Exception:
        registry.set_capability_evidence("VISION_ROUTER", EvidenceLevel.BROKEN, "Image payload is not valid base64", source="vision router")
        return "Visual analysis failed: invalid image payload."

    brain_module = None
    try:
        import brain as brain_module
        local_text, local_status = brain_module.local_vision_request(prompt, payload)
    except Exception as exc:
        local_text, local_status = "", "error"
        registry.set_capability_evidence("OLLAMA_VISION", EvidenceLevel.BROKEN, f"Router error: {type(exc).__name__}", source="vision router")

    if local_status == "ok" and local_text.strip():
        registry.set_capability_evidence("OLLAMA_VISION", EvidenceLevel.LIVE, "jarvis:latest handled visual request locally", source="vision router")
        registry.set_capability_evidence("VISION_ROUTER", EvidenceLevel.LIVE, "jarvis:latest handled visual request locally", source="vision router")
        registry.set_capability_evidence("IMAGE_INPUT", EvidenceLevel.LIVE, "Visual input handled by local vision route", source="vision router")
        return local_text.strip()

    cloud_text, cloud_status = _gemini_vision(prompt, payload, mime_type)
    if cloud_status == "ok" and cloud_text.strip():
        if brain_module is not None:
            brain_module.record_provider_success("GEMINI", GEMINI_VISION_MODEL)
        registry.set_capability_evidence("VISION_ROUTER", EvidenceLevel.LIVE, f"Gemini fallback succeeded after local status={local_status}", source="vision router")
        registry.set_capability_evidence("IMAGE_INPUT", EvidenceLevel.LIVE, "Visual input handled by Gemini fallback", source="vision router")
        return cloud_text.strip()

    registry.set_capability_evidence("VISION_ROUTER", EvidenceLevel.BLOCKED, f"No visual provider succeeded (local={local_status}, gemini={cloud_status})", source="vision router")
    return f"Visual analysis unavailable (local: {local_status}; cloud fallback: {cloud_status})."


def describe_image(img: np.ndarray, prompt: str = "describe what you see") -> str:
    return analyze_image_base64(image_to_base64(img), prompt, "image/png")


def describe_screen(prompt: str = "describe what you see") -> str:
    return describe_image(capture_screen(), prompt)


def read_screen() -> str:
    """Read screen text with local OCR. This is deliberately separate from VLM vision."""
    registry = get_registry()
    if not probe_tesseract().available:
        registry.set_evidence("TESSERACT_OCR", EvidenceLevel.BLOCKED, "Tesseract executable not found", source="OCR pre-check")
        registry.set_capability_evidence("OCR", EvidenceLevel.BLOCKED, "Tesseract executable not found", source="OCR pre-check")
        return "OCR unavailable: Tesseract executable not installed."
    try:
        img = capture_screen()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        return extract_image_text(thresh)[:500]
    except OCREmptyResultError:
        return "No text detected on screen."
    except OCRTimeoutError:
        return "OCR timed out."
    except OCRUnavailableError:
        return "OCR unavailable: Tesseract executable not installed."
    except Exception as exc:
        error_handler.log_and_demote("TESSERACT_OCR", exc, "Local screen OCR", SubsystemState.OFFLINE)
        return "OCR failed."


def analyze_screen_context() -> str:
    try:
        import conversation_manager
        description = describe_screen("Describe the current screen in detail, including visible apps, text, and important elements.")
        return f"Screen: {description}\n\nContext: {conversation_manager.get_context_block()}"
    except Exception:
        return describe_screen("Describe what you see on this screen.")


def capture_screen_region(left: int, top: int, width: int, height: int):
    if width <= 0 or height <= 0:
        raise ValueError("Screen region width and height must be positive")
    try:
        if mss is None:
            raise RuntimeError("MSS screen capture dependency is unavailable")
        with mss() as sct:
            shot = np.array(sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)}))
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
    except Exception as exc:
        error_handler.log_and_demote("VISION", exc, "Capture screen region", SubsystemState.DEGRADED)
        raise


def capture_webcam(camera_index: int = 0):
    registry = get_registry()
    cap = None
    try:
        backend = getattr(cv2, "CAP_DSHOW", 0) if os.name == "nt" else 0
        cap = cv2.VideoCapture(int(camera_index), backend) if backend else cv2.VideoCapture(int(camera_index))
        if not cap.isOpened():
            registry.set_evidence("WEBCAM", EvidenceLevel.BLOCKED, "Webcam not accessible", source="cv2 capture")
            return None
        ok, frame = cap.read()
        if not ok or frame is None:
            registry.set_evidence("WEBCAM", EvidenceLevel.BROKEN, "Webcam opened but frame capture failed", source="cv2 capture")
            return None
        registry.set_evidence("WEBCAM", EvidenceLevel.LIVE, "Webcam frame captured", source="cv2 capture")
        registry.set_capability_evidence("WEBCAM", EvidenceLevel.LIVE, "Webcam frame captured", source="cv2 capture")
        return frame
    except Exception as exc:
        error_handler.log_and_demote("WEBCAM", exc, "Webcam capture", SubsystemState.OFFLINE)
        return None
    finally:
        if cap is not None:
            cap.release()


def analyze_image_file(file_path: str, prompt: str = "describe what you see") -> str:
    path = Path(file_path)
    if not path.is_file():
        return f"File not found: {file_path}"
    img = cv2.imread(str(path))
    if img is None:
        return f"Could not read image file: {file_path}"
    return describe_image(img, prompt)


def read_image_text(file_path: str) -> str:
    registry = get_registry()
    if not probe_tesseract().available:
        registry.set_evidence("TESSERACT_OCR", EvidenceLevel.BLOCKED, "Tesseract executable not found", source="OCR pre-check")
        registry.set_capability_evidence("OCR", EvidenceLevel.BLOCKED, "Tesseract executable not found", source="OCR pre-check")
        return "OCR unavailable: Tesseract executable not installed."
    path = Path(file_path)
    if not path.is_file():
        return f"File not found: {file_path}"
    try:
        img = cv2.imread(str(path))
        if img is None:
            return f"Could not read image file: {file_path}"
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        return extract_image_text(thresh)[:1000]
    except OCREmptyResultError:
        return "No text detected in image."
    except OCRTimeoutError:
        return "OCR timed out."
    except OCRUnavailableError:
        return "OCR unavailable: Tesseract executable not installed."
    except Exception as exc:
        error_handler.log_and_demote("TESSERACT_OCR", exc, f"OCR file {file_path}", SubsystemState.OFFLINE)
        return f"OCR failed: {exc}"


def get_vision_status() -> dict:
    registry = get_registry()
    def status(name: str) -> dict:
        return registry.get_status(name) or {"state": "UNKNOWN", "evidence": "UNKNOWN", "detail": "No evidence recorded", "current": False}
    def capability(name: str) -> dict:
        return registry.get_capability(name) or {"state": "UNKNOWN", "evidence": "UNKNOWN", "detail": "No evidence recorded", "current": False}
    return {
        "screen_capture": status("VISION"),
        "ollama_vision": capability("OLLAMA_VISION"),
        "vision_router": capability("VISION_ROUTER"),
        "gemini": capability("GEMINI_VISION"),
        "tesseract_ocr": status("TESSERACT_OCR"),
        "webcam": status("WEBCAM"),
    }
