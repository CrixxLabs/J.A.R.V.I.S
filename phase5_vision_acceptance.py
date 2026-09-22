"""One-command Windows acceptance for MARK VII Phase 5 visual routing.

Creates a harmless synthetic image, sends it through vision.describe_image(),
and reports whether the actual MARK VII route used jarvis:latest locally.
No files, models, services, or configuration are modified.
"""
from __future__ import annotations

import cv2
import numpy as np

import brain
import vision


def main() -> int:
    canvas = np.full((360, 1100, 3), 255, dtype=np.uint8)
    cv2.putText(canvas, "JARVIS VISION OK 2026", (55, 205), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 4, cv2.LINE_AA)
    prompt = "Read the large text in this image. Reply with only that text."
    result = vision.describe_image(canvas, prompt)
    provider = brain.get_last_provider_model() or "UNKNOWN"
    status = vision.get_vision_status()
    print("Result:", result)
    print("Provider:", provider)
    print("OLLAMA_VISION evidence:", status["ollama_vision"].get("evidence"))
    print("VISION_ROUTER evidence:", status["vision_router"].get("evidence"))
    expected = "JARVIS VISION OK 2026"
    ok = expected in result.upper() and provider == "OLLAMA: jarvis:latest"
    print("PHASE5_LOCAL_VISION:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
