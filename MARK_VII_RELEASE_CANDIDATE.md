# JARVIS MARK VII — Release Candidate Audit

> Historical RC audit. Superseded by the current final release audit and
> `MARK_VII_RELEASE_ACCEPTANCE.py`; the environment-specific results below are
> retained as provenance, not current runtime truth.

## Status

**Code audit/fix pass complete. Windows release acceptance required before final tag.**

The release candidate was produced from the known-good post-Phase-5 baseline that had 172 tests passing on the target Windows machine and verified local Qwen vision. The release pass preserved working architecture and repaired concrete defects rather than redesigning stable subsystems.

## Release-pass changes

- Hybrid brain routing: routine/local Qwen; complex/Nemotron; bounded fallback.
- Internal lightweight analysis changed to local-first with cloud fallback.
- Local-first vision remains Qwen3-VL, with Gemini fallback.
- False TTS interruptions hardened with a minimum energy floor, minimum duration and playback-start grace; the watcher is armed only when playback begins.
- LLM prompts may not claim all systems/capabilities are live without runtime evidence.
- Import-time Google translation network probing removed.
- Search/article/YouTube paths hardened with bounded network use, modern transcript API support, URL normalization, and private/local target rejection.
- File/video metadata parsing removed unsafe runtime `eval` use.
- Browser file-upload/process contract repaired; absolute upload paths are not returned.
- Browser TTS is lazy/opt-in rather than initialized during server import.
- WPF provider status no longer reports generic fake ONLINE state.
- SENTINEL/PHOENIX browser cards remain truthfully disconnected when no real adapter data is present.
- Optional integrations convert error strings into truthful executor failures.
- Image-generation stub no longer advertises success; experimental video generation is disabled by default.
- Shutdown/restart confirmation expires rather than persisting indefinitely.
- Example plugin template disabled so it cannot hijack production routing.
- App launch profiles no longer pass profile-controlled commands through a shell; executable+argument profiles are parsed and launched directly.
- Capability action ownership duplicates removed.
- Cross-platform imports degrade gracefully where practical without changing Windows behavior.

## Verification in build environment

- Python compile: **105 source files, 0 errors**.
- High-value lifecycle/status/provider/vision/personality suite: **97 passed**.
- New web/safety suite: **6 passed**.
- Full sandbox collection: **168 passed, 15 failed, 1 skipped**. Every remaining failure is caused by unavailable Windows/project dependencies in the Linux build environment (`os.startfile`, pyautogui display, win32gui, PyPDF2, Ollama SDK/service, SpeechRecognition). These paths were already verified on the target Windows baseline before this release pass and therefore require the supplied Windows acceptance run rather than false green claims here.

## Final Windows gate

Run:

```powershell
python .\MARK_VII_RELEASE_ACCEPTANCE.py
python .\launch_mark_vii.py --no-build
```

The first command must end with `MARK_VII_RELEASE_ACCEPTANCE: PASS`. During GUI smoke testing, verify a normal conversational request reports the local Ollama provider, a genuinely complex debugging/architecture request reports NVIDIA/Nemotron, a multi-sentence spoken answer finishes without low-energy false interruption, and closing WPF cleanly shuts down only launcher-owned runtime processes.

Only after that gate should the release be tagged/published.
