JARVIS MARK VII - CURRENT RELEASE-CANDIDATE NOTES

Verified stabilization baseline:
- Regression suite: 190 passed.
- Native WPF project: builds with 0 errors / 0 warnings.
- Supported local Ollama model: jarvis:latest (Ministral 3 3B).
- Canonical launcher: python .\launch_mark_vii.py
- WPF is attach-only; runtime_instance.py guards against duplicate MARK VII runtimes.
- Pocket-TTS CUDA, local text routing, screen vision, interruption, and clean shutdown
  have been live-verified on the Acer runtime.

Release validation:
1. Run: python .\MARK_VII_RELEASE_CHECK.py
2. Run: python .\MARK_VII_RELEASE_ACCEPTANCE.py
3. Run: python .\launch_mark_vii.py --no-build
4. Verify routine speech routes to OLLAMA jarvis:latest and a complex
   debugging/architecture prompt can escalate to NVIDIA/Nemotron.
5. Close WPF normally and confirm the owned runtime exits cleanly.

FFmpeg is an external executable. If it is not on PATH, audio/video file
processing that depends on FFmpeg is unavailable/degraded; this does not block
the core MARK VII voice/text/vision runtime.

Never publish .env, credentials, memory/profile/state JSON, face/voice
enrollment, uploads, logs, generated media, or local stabilization backups.
