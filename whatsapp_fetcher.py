# whatsapp_fetcher.py — WhatsApp OCR + Window Automation
# Responsibilities: open WhatsApp, navigate to group, capture and clean text.
# Returns clean readable strings. Never speaks or makes decisions.

from __future__ import annotations

import re
import time

import cv2
import numpy as np
import pyautogui
import pytesseract
from mss import mss

# Reliability imports
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler

try:
    import pygetwindow as gw
    _GW_AVAILABLE = True
except Exception:
    gw = None
    _GW_AVAILABLE = False

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.08

_MAX_RUNTIME_S = 9.5
_SCROLL_STEPS  = 5
_GROUP_DEFAULT = "24 BATCH 3"

# ── Noise filters ──────────────────────────────────────────────────────────────
_SKIP_EXACT = frozenset((
    "whatsapp", "search", "type a message", "chats", "updates", "calls",
    "status", "starred messages", "new chat", "settings", "archived",
    "muted", "online", "typing", "today", "yesterday", "read more",
    "messages and calls are end-to-end encrypted",
))

_SKIP_PARTIAL = (
    "end-to-end encrypted", "messages are end-to-end",
    "tap to learn more", "delivered", "message reactions",
    "use whatsapp", "download whatsapp",
)

_NOISE_REGEX = re.compile(r"^[|_\-=\[\]{}<>\\/\s.,:;!?@#$%^&*()]+$")


def _is_noise(line: str) -> bool:
    """Return True if the line is considered noise and should be dropped."""
    stripped = line.strip()
    if len(stripped) < 5:
        return True
    if _NOISE_REGEX.match(stripped):
        return True
    lower = stripped.lower()
    if lower in _SKIP_EXACT:
        return True
    if any(p in lower for p in _SKIP_PARTIAL):
        return True
    # Drop lines that are overwhelmingly non-alphanumeric
    alnum_count = sum(c.isalnum() for c in stripped)
    if alnum_count < 4 or alnum_count / max(len(stripped), 1) < 0.25:
        return True
    # Drop OCR artefacts: strings of random symbols
    if re.match(r"^[\W_]{4,}$", stripped):
        return True
    return False


def _deduplicate(lines: list[str]) -> list[str]:
    """Remove exact duplicates while preserving order."""
    seen: set[str] = set()
    result = []
    for line in lines:
        key = line.strip().lower()
        if key not in seen:
            seen.add(key)
            result.append(line)
    return result


# ── OCR ────────────────────────────────────────────────────────────────────────

def extract_whatsapp_text() -> str:
    """
    Capture the current screen, OCR it, and return clean readable text.
    Priority lines (those containing links) are surfaced first.
    """
    registry = get_registry()
    try:
        with mss() as sct:
            img = np.array(sct.grab(sct.monitors[1]))

        # Pre-process for better OCR accuracy
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        gray = cv2.fastNlMeansDenoising(gray, h=10)

        raw = pytesseract.image_to_string(gray, config=r"--oem 3 --psm 6")

        priority_lines: list[str] = []
        content_lines:  list[str] = []

        link_pattern = re.compile(
            r"(https?://|meet\.google\.com|drive\.google\.com)", re.IGNORECASE
        )

        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if _is_noise(line):
                continue
            if link_pattern.search(line):
                priority_lines.append(line)
            else:
                content_lines.append(line)

        merged = _deduplicate(priority_lines + content_lines)
        result = "\n".join(merged).strip()
        print(f"[DEBUG][whatsapp_fetcher] extracted {len(result)} chars from OCR")

        registry.set_status(
            "TESSERACT_OCR",
            SubsystemState.READY,
            "WhatsApp OCR extraction succeeded"
        )
        return result or "No readable WhatsApp text found."

    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="TESSERACT_OCR",
            exception=exc,
            context="WhatsApp screen OCR text extraction",
            demote_to=SubsystemState.DEGRADED
        )
        return "WhatsApp OCR failed."


# ── Deadline helpers ───────────────────────────────────────────────────────────

def _deadline_start() -> float:
    return time.monotonic() + _MAX_RUNTIME_S


def _time_left(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _sleep_budgeted(deadline: float, seconds: float):
    remaining = min(seconds, _time_left(deadline))
    if remaining > 0:
        time.sleep(remaining)


# ── Window management ──────────────────────────────────────────────────────────

def _find_whatsapp_window():
    if not _GW_AVAILABLE:
        return None
    try:
        for window in gw.getAllWindows():
            if window.title and "WhatsApp" in window.title:
                return window
    except Exception as exc:
        print(f"[DEBUG][whatsapp_fetcher] window lookup failed: {exc}")
    return None


def open_whatsapp(deadline: float | None = None) -> bool:
    if deadline is None:
        deadline = _deadline_start()

    print("[DEBUG][whatsapp_fetcher] opening WhatsApp")
    window = _find_whatsapp_window()
    if window is not None:
        try:
            if window.isMinimized:
                window.restore()
            window.activate()
        except Exception as exc:
            print(f"[DEBUG][whatsapp_fetcher] activate existing window failed: {exc}")
        _sleep_budgeted(deadline, 0.4)
        return True

    try:
        pyautogui.press("win")
        _sleep_budgeted(deadline, 0.4)
        pyautogui.write("WhatsApp", interval=0.03)
        _sleep_budgeted(deadline, 0.3)
        pyautogui.press("enter")
        _sleep_budgeted(deadline, 2.8)
        return True
    except Exception as exc:
        print(f"[DEBUG][whatsapp_fetcher] open failed: {exc}")
        return False


def open_group(group_name: str, deadline: float | None = None) -> bool:
    if deadline is None:
        deadline = _deadline_start()

    try:
        print(f"[DEBUG][whatsapp_fetcher] opening group: {group_name}")
        pyautogui.hotkey("ctrl", "f")
        _sleep_budgeted(deadline, 0.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        _sleep_budgeted(deadline, 0.2)
        pyautogui.write(group_name, interval=0.05)
        _sleep_budgeted(deadline, 1.2)
        pyautogui.press("down")
        _sleep_budgeted(deadline, 0.2)
        pyautogui.press("enter")
        _sleep_budgeted(deadline, 1.5)
        return True
    except Exception as exc:
        print(f"[DEBUG][whatsapp_fetcher] open_group failed: {exc}")
        return False


def scan_and_download(deadline: float | None = None) -> bool:
    if deadline is None:
        deadline = _deadline_start()

    try:
        print("[DEBUG][whatsapp_fetcher] scanning messages")
        width, height = pyautogui.size()
        click_x = int(width  * 0.72)
        click_y = int(height * 0.48)
        for _ in range(_SCROLL_STEPS):
            if _time_left(deadline) < 0.4:
                break
            pyautogui.scroll(4)
            _sleep_budgeted(deadline, 0.22)
        if _time_left(deadline) < 0.15:
            return False
        pyautogui.click(click_x, click_y)
        _sleep_budgeted(deadline, 0.2)
        for _ in range(3):
            if _time_left(deadline) < 0.05:
                break
            pyautogui.press("down")
            _sleep_budgeted(deadline, 0.06)
        pyautogui.press("enter")
        _sleep_budgeted(deadline, 0.15)
        return True
    except Exception as exc:
        print(f"[DEBUG][whatsapp_fetcher] scan_and_download failed: {exc}")
        return False


def minimize_whatsapp(deadline: float | None = None):
    if deadline is None:
        deadline = _deadline_start()
    window = _find_whatsapp_window()
    if window is None:
        return
    try:
        window.minimize()
    except Exception as exc:
        print(f"[DEBUG][whatsapp_fetcher] minimize failed: {exc}")
    _sleep_budgeted(deadline, 0.15)


def update_timetable(group_name: str = _GROUP_DEFAULT) -> bool:
    deadline = _deadline_start()
    previous_window = None
    if _GW_AVAILABLE:
        try:
            previous_window = gw.getActiveWindow()
        except Exception:
            previous_window = None

    try:
        if not open_whatsapp(deadline):
            return False
        if not open_group(group_name, deadline):
            return False
        _sleep_budgeted(deadline, 1.0)
        return scan_and_download(deadline)
    except Exception as exc:
        print(f"[DEBUG][whatsapp_fetcher] update_timetable error: {exc}")
        return False
    finally:
        minimize_whatsapp(deadline)
        if previous_window is not None:
            try:
                previous_window.activate()
            except Exception:
                pass


def extract_timetable_for_batch(text: str, batch_name: str) -> str:
    """Extract the section of OCR text relevant to a specific batch."""
    lines           = text.splitlines()
    section_lines: list[str] = []
    in_section      = False
    batch_lower     = batch_name.strip().lower()

    for line in lines:
        stripped = line.strip()
        lower    = stripped.lower()
        if not stripped:
            if in_section and section_lines:
                section_lines.append("")
            continue
        if batch_lower in lower and not in_section:
            in_section = True
            section_lines.append(stripped)
            continue
        if in_section:
            if "batch" in lower and batch_lower not in lower:
                break
            section_lines.append(stripped)

    # Remove consecutive blanks and duplicate lines
    cleaned: list[str] = []
    for line in section_lines:
        if line == "":
            if cleaned and cleaned[-1] != "":
                cleaned.append(line)
        elif line not in cleaned:
            cleaned.append(line)

    return "\n".join(cleaned).strip()