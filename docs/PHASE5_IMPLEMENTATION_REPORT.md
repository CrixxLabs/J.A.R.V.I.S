# MARK VII Phase 5 — Vision Router Implementation

## Implemented

- jarvis:latest is the primary visual-understanding provider through `brain.local_vision_request`.
- Gemini Vision is a bounded cloud fallback and is called only after the local visual route fails.
- Planner `image_b64` input now enters the same visual router directly; the old temporary-file/Gemini-first/OCR fallback path is removed.
- Screen, image-file, screen-region, and webcam descriptions share the same local-first router.
- OCR remains a separate Tesseract text-extraction capability; it is no longer presented as general visual understanding.
- Separate capability truth exists for `OLLAMA_VISION`, `GEMINI_VISION`, `VISION_ROUTER`, and `IMAGE_INPUT`.
- Ollama's separate `thinking` field is not exposed.
- Webcam handles are released in `finally`.
- Gemini visual requests and Ollama requests remain bounded by configured timeouts.

## Sandbox verification

- `python -m py_compile` passed for all modified Python modules.
- New Phase 5 router tests: **8 passed**.
- Reliability/status/lifecycle/provider subset: **52 passed** (one Linux-only warning because `win32gui` is unavailable in the sandbox).
- The complete Windows suite cannot be truthfully reproduced in this Linux sandbox because Windows GUI/input dependencies and several project dependencies are unavailable here. The uploaded Phase 4 baseline reported 163 passing tests on the user's Windows machine.
- Live Ollama, screen, webcam, WPF, and Gemini credentials are not reachable from this sandbox and therefore are intentionally not claimed as live-verified here.

## Windows live acceptance

Run from `D:\J.A.R.V.I.S` after copying the patched project:

```powershell
python .\phase5_vision_acceptance.py
```

Expected key output:

```text
Provider: OLLAMA: jarvis:latest
OLLAMA_VISION evidence: LIVE
VISION_ROUTER evidence: LIVE
PHASE5_LOCAL_VISION: PASS
```

Then run the maintained suite and launcher regression:

```powershell
python -m pytest -q tests
python .\launch_mark_vii.py --no-build
```

This is the machine-specific acceptance gate; no code should be changed merely to force it green.
