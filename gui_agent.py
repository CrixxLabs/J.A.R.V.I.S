"""Vision-to-action GUI automation agent.

Provides screen-aware element interaction via vision description → coordinate translation
using MSS screen capture and Ministral/Gemini vision pipeline.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

try:
    import pyautogui
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    pyautogui = None
    _PYAUTOGUI_AVAILABLE = False

import vision
import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry


def _parse_coordinates_from_vision(vision_response: str) -> Optional[Tuple[float, float]]:
    """Extract normalized coordinates from vision model response.

    Expected formats:
    - "x: 0.5, y: 0.3"
    - "coordinates: (0.45, 0.67)"
    - "at position 0.5, 0.3"

    Returns:
        Tuple of (x, y) normalized coordinates [0.0-1.0], or None if not found
    """
    if not vision_response:
        return None

    # Try pattern: x: 0.5, y: 0.3
    match = re.search(r'x:\s*(\d*\.?\d+).*?y:\s*(\d*\.?\d+)', vision_response, re.IGNORECASE)
    if match:
        try:
            x, y = float(match.group(1)), float(match.group(2))
            if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                return (x, y)
        except ValueError:
            pass

    # Try pattern: (0.45, 0.67)
    match = re.search(r'\((\d*\.?\d+),\s*(\d*\.?\d+)\)', vision_response)
    if match:
        x, y = float(match.group(1)), float(match.group(2))
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            return (x, y)

    # Try pattern: position 0.5, 0.3 or at 0.5, 0.3
    match = re.search(r'(?:position|at)\s+(\d*\.?\d+)[\s,]+(\d*\.?\d+)', vision_response, re.IGNORECASE)
    if match:
        x, y = float(match.group(1)), float(match.group(2))
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            return (x, y)

    return None


def click_element(description: str, confidence_threshold: float = 0.7) -> bool:
    """Click on a GUI element identified by natural language description.

    Args:
        description: Natural language description of the element to click
                    (e.g., "the blue Submit button in the center")
        confidence_threshold: Minimum confidence for coordinate extraction

    Returns:
        True if click was executed, False otherwise
    """
    registry = get_registry()

    if not _PYAUTOGUI_AVAILABLE:
        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.DISABLED,
            "pyautogui not available", source="gui agent"
        )
        return False

    if not description or not description.strip():
        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.BROKEN,
            "Empty element description", source="gui agent"
        )
        return False

    try:
        # ── Primary: Foveated Saccadic Vision Grounding (MARK VIII) ──
        try:
            import foveated_vision
            foveal_coords = foveated_vision.foveated_locate_element(description)
            if foveal_coords:
                x_pixel, y_pixel = foveal_coords
                screen_width, screen_height = pyautogui.size()
                if 0 <= x_pixel < screen_width and 0 <= y_pixel < screen_height:
                    pyautogui.click(x_pixel, y_pixel)
                    registry.set_capability_evidence(
                        "GUI_AGENT", EvidenceLevel.LIVE,
                        f"Clicked element at ({x_pixel}, {y_pixel}) [Foveated]",
                        source="gui agent"
                    )
                    print(f"[GUIAgent] Clicked '{description}' via Foveated Saccade at ({x_pixel}, {y_pixel})")
                    return True
        except Exception as _fov_err:
            print(f"[GUIAgent] Foveated grounding fallback: {_fov_err}")

        # Capture current screen
        screen_b64 = vision.screenshot_to_base64()

        # Query vision model for element location
        prompt = f"""Analyze this screenshot and locate the following element: "{description}"

Return ONLY the normalized coordinates in the format: x: <value>, y: <value>
where x and y are decimal values between 0.0 and 1.0 representing the position on screen.
For example: x: 0.5, y: 0.3

If the element is not visible, respond with: NOT_FOUND"""

        vision_response = vision.analyze_image_base64(screen_b64, prompt)

        if "NOT_FOUND" in vision_response.upper() or "NOT VISIBLE" in vision_response.upper():
            registry.set_capability_evidence(
                "GUI_AGENT", EvidenceLevel.BROKEN,
                f"Element not found: {description[:60]}", source="gui agent"
            )
            print(f"[GUIAgent] Element not found on screen: {description}")
            return False

        # Parse coordinates
        coords = _parse_coordinates_from_vision(vision_response)
        if not coords:
            registry.set_capability_evidence(
                "GUI_AGENT", EvidenceLevel.BROKEN,
                "Failed to extract coordinates from vision response",
                source="gui agent"
            )
            print(f"[GUIAgent] Could not parse coordinates from: {vision_response[:100]}")
            return False

        # Convert normalized coordinates to screen pixels
        screen_width, screen_height = pyautogui.size()
        x_pixel = int(coords[0] * screen_width)
        y_pixel = int(coords[1] * screen_height)

        # Bounds check
        if not (0 <= x_pixel < screen_width and 0 <= y_pixel < screen_height):
            registry.set_capability_evidence(
                "GUI_AGENT", EvidenceLevel.BROKEN,
                f"Coordinates out of bounds: {x_pixel}, {y_pixel}",
                source="gui agent"
            )
            return False

        # Execute click
        pyautogui.click(x_pixel, y_pixel)

        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.LIVE,
            f"Clicked element at ({x_pixel}, {y_pixel})",
            source="gui agent"
        )
        print(f"[GUIAgent] Clicked '{description}' at screen position ({x_pixel}, {y_pixel})")
        return True

    except Exception as exc:
        error_handler.log_and_demote(
            "GUI_AGENT", exc,
            f"Vision-guided click for: {description[:60]}",
            SubsystemState.DEGRADED
        )
        return False


def type_into(description: str, text: str) -> bool:
    """Type text into a GUI element identified by natural language description.

    Args:
        description: Natural language description of the input field
        text: Text to type into the field

    Returns:
        True if typing was executed, False otherwise
    """
    registry = get_registry()

    if not _PYAUTOGUI_AVAILABLE:
        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.DISABLED,
            "pyautogui not available", source="gui agent"
        )
        return False

    if not description or not description.strip():
        return False

    if not text:
        text = ""

    try:
        # First click on the element to focus it
        if not click_element(description):
            return False

        # Small delay for focus
        pyautogui.pause = 0.3

        # Type the text
        pyautogui.typewrite(text, interval=0.05)

        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.LIVE,
            f"Typed {len(text)} chars into field",
            source="gui agent"
        )
        print(f"[GUIAgent] Typed text into '{description}'")
        return True

    except Exception as exc:
        error_handler.log_and_demote(
            "GUI_AGENT", exc,
            f"Vision-guided typing for: {description[:60]}",
            SubsystemState.DEGRADED
        )
        return False


def hover_element(description: str) -> bool:
    """Move mouse over a GUI element identified by natural language description.

    Args:
        description: Natural language description of the element

    Returns:
        True if hover was executed, False otherwise
    """
    registry = get_registry()

    if not _PYAUTOGUI_AVAILABLE:
        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.DISABLED,
            "pyautogui not available", source="gui agent"
        )
        return False

    try:
        screen_b64 = vision.screenshot_to_base64()

        prompt = f"""Locate the following element in this screenshot: "{description}"

Return ONLY normalized coordinates: x: <value>, y: <value>
Values must be between 0.0 and 1.0."""

        vision_response = vision.analyze_image_base64(screen_b64, prompt)
        coords = _parse_coordinates_from_vision(vision_response)

        if not coords:
            return False

        screen_width, screen_height = pyautogui.size()
        x_pixel = int(coords[0] * screen_width)
        y_pixel = int(coords[1] * screen_height)

        pyautogui.moveTo(x_pixel, y_pixel, duration=0.3)

        registry.set_capability_evidence(
            "GUI_AGENT", EvidenceLevel.LIVE,
            f"Hovered over element at ({x_pixel}, {y_pixel})",
            source="gui agent"
        )
        return True

    except Exception as exc:
        error_handler.log_and_demote(
            "GUI_AGENT", exc,
            f"Vision-guided hover for: {description[:60]}",
            SubsystemState.DEGRADED
        )
        return False
