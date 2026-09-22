# JARVIS MARK VII

JARVIS MARK VII is CrixxLabs' Windows AI assistant/orchestrator. It combines a local-first hybrid brain, voice interaction, local visual understanding, deterministic Windows actions, file/OCR processing, tasks/reminders, web/content tools, runtime truth reporting, and native WPF presentation.

## Brain routing

- **Routine conversation and lightweight work:** local `jarvis:latest` through Ollama.
- **Complex reasoning, architecture, debugging, strategy, and difficult coding:** NVIDIA Nemotron.
- **Vision:** local `jarvis:latest` first, Gemini vision fallback when configured.
- Provider failures use bounded fallbacks; runtime status must not claim a capability is live without current evidence.

## Quick start

Requirements: Windows 11/10, Python 3.11, .NET 8 Desktop Runtime/SDK for the native client, and the Python packages in `requirements.txt`. Optional service integrations are in `requirements-optional.txt`.

```powershell
Set-Location 'D:\J.A.R.V.I.S'
pip install -r requirements.txt
python .\launch_mark_vii.py
```

After an existing WPF build:

```powershell
python .\launch_mark_vii.py --no-build
```

The native WPF client is attach-only when launched directly; `launch_mark_vii.py` is the recommended ownership-aware full-GUI launcher.

## Release acceptance

Before publishing or tagging a release on a target Windows machine:

```powershell
python .\MARK_VII_RELEASE_ACCEPTANCE.py
python .\launch_mark_vii.py --no-build
```

The acceptance runner executes the maintained test suite and verifies the actual local hybrid-brain route, local Qwen vision route, and voice-interrupt safety floor.

## Major capabilities

- Voice input with speaker verification/Whisper where configured and TTS output.
- Hybrid cloud/local language-model routing.
- Local screen/image understanding with `jarvis:latest`.
- Tesseract OCR and TXT/code/PDF/DOCX/XLSX extraction.
- Planner → executor → observer workflow with tasks/reminders and lifecycle management.
- Bounded web search, article summarization, and YouTube transcript summarization.
- Windows app/actions, clipboard and selected optional integrations.
- Runtime status/evidence registry and a native WPF interface.

## Truthful limitations

- PDF OCR is not implemented; PDFs support embedded/selectable text extraction.
- Legacy binary `.doc` and `.xls` are not supported.
- Spotify, Calendar, Gmail, WhatsApp and other external services require their own credentials/dependencies and may be unavailable until configured.
- Image generation is not advertised as implemented. Video generation is experimental and disabled by default.
- SENTINEL and PHOENIX cards remain disconnected unless their real adapters are wired; the UI does not fabricate positive telemetry.
- Some Windows/hardware functions are naturally unavailable on other operating systems.

## Configuration

Copy `.env.example` to `.env` and provide only the services you intend to use. Never commit `.env`, credential/token files, face/voice enrollment data, memories, or other personal runtime state.

More details: `MARK_VII_SETUP.md` and `docs/LAUNCHING_MARK_VII.md`.
