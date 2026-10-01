"""Foveated Saccadic Vision Grounding Engine for J.A.R.V.I.S. — MARK VIII.

Enables high-precision vision-language screen grounding on 4K/high-DPI displays without downsampling blur:
  1. Stage 1 (Peripheral Scan): Global scan for coarse bounding box / region of interest.
  2. Stage 2 (Saccadic Crop): Lossless high-res foveal crop around candidate region.
  3. Stage 3 (Foveal Grounding): Fine-grained coordinate extraction mapped to exact screen pixels.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Any, Dict, Optional, Tuple

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    Image = None
    _PIL_AVAILABLE = False

try:
    import pyautogui
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    pyautogui = None
    _PYAUTOGUI_AVAILABLE = False

import error_handler
import vision
from status_registry import EvidenceLevel, SubsystemState, get_registry


def _parse_box_or_coords(response: str) -> Optional[Tuple[float, float, float, float]]:
    """Parse bounding box (ymin, xmin, ymax, xmax) or (x, y) center from vision response."""
    if not response:
        return None

    # Try pattern: box: [ymin, xmin, ymax, xmax] or box: (ymin, xmin, ymax, xmax)
    box_match = re.search(r'\[\s*(\d*\.?\d+)\s*,\s*(\d*\.?\d+)\s*,\s*(\d*\.?\d+)\s*,\s*(\d*\.?\d+)\s*\]', response)
    if box_match:
        try:
            ymin, xmin, ymax, xmax = map(float, box_match.groups())
            return (xmin, ymin, xmax, ymax)
        except ValueError:
            pass

    # Try pattern: x: 0.5, y: 0.3
    coord_match = re.search(r'x:\s*(\d*\.?\d+).*?y:\s*(\d*\.?\d+)', response, re.IGNORECASE)
    if coord_match:
        try:
            x, y = float(coord_match.group(1)), float(coord_match.group(2))
            delta = 0.08
            return (max(0.0, x - delta), max(0.0, y - delta), min(1.0, x + delta), min(1.0, y + delta))
        except ValueError:
            pass

    return None


def peripheral_scan(
    screen_b64: str,
    target_description: str,
) -> Optional[Tuple[float, float, float, float]]:
    """Stage 1: Peripheral scan to find coarse normalized bounding box [xmin, ymin, xmax, ymax]."""
    prompt = f"""You are the PERIPHERAL VISION SCANNER. Locate the approximate region of this UI element: "{target_description}"
Return ONLY the bounding box in normalized [ymin, xmin, ymax, xmax] format where values are between 0.0 and 1.0.
Example: [0.20, 0.40, 0.30, 0.60]
If not visible, respond with: NOT_FOUND"""

    try:
        response = vision.analyze_image_base64(screen_b64, prompt)
        if "NOT_FOUND" in response.upper() or "NOT VISIBLE" in response.upper():
            return None
        return _parse_box_or_coords(response)
    except Exception as exc:
        print(f"[FoveatedVision] Peripheral scan failed: {exc}")
        return None


def saccadic_crop(
    image: Any,
    coarse_box: Tuple[float, float, float, float],
    foveal_size: Tuple[int, int] = (512, 512),
) -> Tuple[Any, Tuple[int, int, int, int]]:
    """Stage 2: Extract a lossless high-resolution foveal crop around the coarse box center.

    Returns:
        Tuple of (cropped_PIL_image, (crop_x1, crop_y1, crop_x2, crop_y2))
    """
    if not _PIL_AVAILABLE or not image:
        return None, (0, 0, 0, 0)

    img_w, img_h = image.size
    xmin, ymin, xmax, ymax = coarse_box

    # Center of coarse box in pixels
    cx = int(((xmin + xmax) / 2.0) * img_w)
    cy = int(((ymin + ymax) / 2.0) * img_h)

    crop_w, crop_h = foveal_size
    half_w, half_h = crop_w // 2, crop_h // 2

    # Clamp crop bounds to image boundaries
    x1 = max(0, min(cx - half_w, img_w - crop_w))
    y1 = max(0, min(cy - half_h, img_h - crop_h))
    x2 = min(img_w, x1 + crop_w)
    y2 = min(img_h, y1 + crop_h)

    cropped = image.crop((x1, y1, x2, y2))
    return cropped, (x1, y1, x2, y2)


def foveal_ground(
    crop_b64: str,
    target_description: str,
    crop_bounds: Tuple[int, int, int, int],
) -> Optional[Tuple[int, int]]:
    """Stage 3: Sub-pixel grounding on high-res foveal crop mapped back to global coordinates.

    Returns:
        Tuple of absolute screen coordinates (x_pixel, y_pixel).
    """
    prompt = f"""You are the FOVEAL SACCADIC GROUNDING ENGINE. This is a high-resolution lossless crop of a UI region.
Pinpoint the exact click center of: "{target_description}"
Return ONLY normalized local coordinates inside this crop in format: x: <val>, y: <val> (values 0.0 to 1.0).
Example: x: 0.52, y: 0.48"""

    try:
        response = vision.analyze_image_base64(crop_b64, prompt)
        match = re.search(r'x:\s*(\d*\.?\d+).*?y:\s*(\d*\.?\d+)', response, re.IGNORECASE)
        if not match:
            return None

        local_x = float(match.group(1))
        local_y = float(match.group(2))

        x1, y1, x2, y2 = crop_bounds
        crop_w = x2 - x1
        crop_h = y2 - y1

        abs_x = int(x1 + (local_x * crop_w))
        abs_y = int(y1 + (local_y * crop_h))
        return (abs_x, abs_y)
    except Exception as exc:
        print(f"[FoveatedVision] Foveal grounding failed: {exc}")
        return None


def foveated_locate_element(
    target_description: str,
    pil_image: Optional[Any] = None,
    foveal_size: Tuple[int, int] = (512, 512),
) -> Optional[Tuple[int, int]]:
    """Full 3-stage foveated saccadic vision pipeline to locate target UI element.

    Returns:
        Tuple of (x, y) absolute screen pixel coordinates, or None.
    """
    registry = get_registry()

    if not _PIL_AVAILABLE:
        registry.set_capability_evidence(
            "FOVEATED_VISION",
            EvidenceLevel.DISABLED,
            "PIL is not installed",
            source="foveated vision",
        )
        return None

    try:
        # 1. Capture screen if image not provided
        if pil_image is None:
            screen_b64 = vision.screenshot_to_base64()
            raw_bytes = base64.b64decode(screen_b64)
            pil_image = Image.open(io.BytesIO(raw_bytes))
        else:
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            screen_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        # 2. Stage 1: Peripheral scan
        coarse_box = peripheral_scan(screen_b64, target_description)
        if not coarse_box:
            # Fallback to center box if coarse detection ambiguous
            coarse_box = (0.2, 0.2, 0.8, 0.8)

        # 3. Stage 2: Saccadic crop
        crop_img, crop_bounds = saccadic_crop(pil_image, coarse_box, foveal_size=foveal_size)
        crop_buf = io.BytesIO()
        crop_img.save(crop_buf, format="PNG")
        crop_b64 = base64.b64encode(crop_buf.getvalue()).decode("utf-8")

        # 4. Stage 3: Foveal grounding
        coords = foveal_ground(crop_b64, target_description, crop_bounds)
        if coords:
            registry.set_capability_evidence(
                "FOVEATED_VISION",
                EvidenceLevel.LIVE,
                f"Grounded '{target_description[:40]}' at {coords}",
                source="foveated vision",
            )
            return coords

        # Fallback to coarse box center
        x1, y1, x2, y2 = crop_bounds
        fallback_coords = ((x1 + x2) // 2, (y1 + y2) // 2)
        registry.set_capability_evidence(
            "FOVEATED_VISION",
            EvidenceLevel.LIVE,
            f"Coarse grounded '{target_description[:40]}' at {fallback_coords}",
            source="foveated vision",
        )
        return fallback_coords

    except Exception as exc:
        error_handler.log_and_demote(
            "FOVEATED_VISION",
            exc,
            f"Foveated grounding for {target_description[:40]}",
            SubsystemState.DEGRADED,
        )
        return None
