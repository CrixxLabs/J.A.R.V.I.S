# JARVIS MARK VII — Luca Final Handoff

Date: 2026-09-19

## Final code status

This handoff continues from the post-Codex repository state rather than restarting the audit.

Verified in the current build environment:

- Python source compilation: 107 files, 0 errors before the final router regression additions; final compile re-run is part of packaging.
- Focused release suite after final router fix: 100 passed, 1 skipped.
- Linux-compatible maintained regression set: 174 passed, 1 skipped, 15 Windows/dependency-specific tests deselected, repeated three clean runs.
- The 15 deselected tests are tied to unavailable build-environment dependencies/platform APIs: Windows `os.startfile` / Win32 GUI / pyautogui display, PyPDF2, Ollama SDK/service, and SpeechRecognition. They are not silently treated as passes.

The final Luca change tightens hybrid routing so ordinary `explain/how/why` requests remain on local Qwen, while explicit architecture/debugging/analysis/implementation work escalates to NVIDIA Nemotron. Regression coverage was added for both routes.

## Prior live Windows evidence preserved from the interrupted Codex pass

The prior on-device pass reported successful live verification of:

- routine request -> `OLLAMA: qwen3-vl:4b`
- Qwen local vision reading `JARVIS RELEASE OK 2026`
- complex technical request -> NVIDIA Nemotron
- WPF Debug build with 0 warnings / 0 errors
- ownership-aware launcher shutdown
- first post-fix complete suite: 186 passed at that point in the Windows run

That Codex session crashed before its final three-suite/acceptance closure, so the final release gate below still belongs on the target Windows machine.

## Required final target-PC gate

From `D:\J.A.R.V.I.S` with the existing `.env`, Ollama installation, personal memory/profile, and voice/face enrollment left in place:

```powershell
python .\MARK_VII_RELEASE_ACCEPTANCE.py
python .\launch_mark_vii.py --no-build
```

The acceptance script must end with:

```text
MARK_VII_RELEASE_ACCEPTANCE: PASS
```

The GUI smoke should confirm a routine request reports local Qwen, a genuinely complex architecture/debug request reports NVIDIA/Nemotron, multi-sentence speech is not interrupted by low ambient noise, and launcher-owned runtime exits when WPF closes.

## Publication hygiene

The public release package excludes `.env`, credentials/tokens, personal memories/profile, face/voice enrollment, uploads/logs, runtime state, caches, generated build output, and scratch repair scripts. The user's working copy is not deleted or reset.
