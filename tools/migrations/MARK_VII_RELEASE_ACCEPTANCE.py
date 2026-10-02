"""JARVIS MARK VII release-candidate acceptance runner for the target Windows PC.

This script does not modify models, credentials, or user data. It runs the maintained
regression suite and then verifies the real local brain + local vision routes that require
Arju's Windows/Ollama environment.
"""
from __future__ import annotations

import subprocess
import sys


def run_tests() -> bool:
    print("\n=== 1/4 FULL REGRESSION SUITE ===")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], check=False)
    print("PYTEST:", "PASS" if proc.returncode == 0 else f"FAIL ({proc.returncode})")
    return proc.returncode == 0


def local_brain() -> bool:
    print("\n=== 2/4 LOCAL BRAIN ===")
    import brain
    result = brain.ask_llm("Reply with exactly: HYBRID_LOCAL_OK", allow_actions=False)
    provider = brain.get_last_provider_model() or "UNKNOWN"
    print("Result:", result)
    print("Provider:", provider)
    ok = "HYBRID_LOCAL_OK" in str(result).upper() and provider == "OLLAMA: jarvis:latest"
    print("HYBRID_ROUTINE_LOCAL:", "PASS" if ok else "FAIL")
    return ok


def local_vision() -> bool:
    print("\n=== 3/4 LOCAL VISION ===")
    import cv2
    import numpy as np
    import brain
    import vision

    canvas = np.full((360, 1100, 3), 255, dtype=np.uint8)
    cv2.putText(canvas, "JARVIS RELEASE OK 2026", (45, 205), cv2.FONT_HERSHEY_SIMPLEX,
                1.8, (0, 0, 0), 4, cv2.LINE_AA)
    result = vision.describe_image(canvas, "Read the large text. Reply with only that text.")
    provider = brain.get_last_provider_model() or "UNKNOWN"
    status = vision.get_vision_status()
    print("Result:", result)
    print("Provider:", provider)
    print("OLLAMA_VISION evidence:", status["ollama_vision"].get("evidence"))
    print("VISION_ROUTER evidence:", status["vision_router"].get("evidence"))
    ok = "JARVIS RELEASE OK 2026" in str(result).upper() and provider == "OLLAMA: jarvis:latest"
    print("LOCAL_VISION:", "PASS" if ok else "FAIL")
    return ok


def interrupt_guard() -> bool:
    print("\n=== 4/4 VOICE INTERRUPT GUARD ===")
    import listener
    threshold = listener._interrupt_voice_threshold()
    floor = float(getattr(listener, "INTERRUPT_ENERGY_FLOOR", 500))
    known_false_interrupt_energy = 21.0
    print("Current interrupt threshold:", threshold)
    print("Configured floor:", floor)
    print("Known ambient-noise sample:", known_false_interrupt_energy)
    ok = threshold >= floor >= 500 and known_false_interrupt_energy < threshold
    print("VOICE_INTERRUPT_GUARD:", "PASS" if ok else "FAIL")
    return ok


def main() -> int:
    checks = []
    try:
        checks.append(("pytest", run_tests()))
    except Exception as exc:
        print("PYTEST CHECK ERROR:", type(exc).__name__, exc)
        checks.append(("pytest", False))
    for name, fn in [("hybrid local brain", local_brain), ("local vision", local_vision), ("voice interrupt", interrupt_guard)]:
        try:
            checks.append((name, fn()))
        except Exception as exc:
            print(f"{name.upper()} ERROR:", type(exc).__name__, exc)
            checks.append((name, False))

    print("\n=== RELEASE ACCEPTANCE SUMMARY ===")
    for name, ok in checks:
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    passed = all(ok for _, ok in checks)
    print("MARK_VII_RELEASE_ACCEPTANCE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
