# JARVIS MARK VII

### CrixxLabs Flagship AI Assistant

JARVIS MARK VII is a Windows AI assistant built for real, local-first work: it listens, reasons, plans, executes deterministic actions, verifies outcomes, and keeps runtime status evidence honest. It combines voice, visual understanding, file processing, native and browser interfaces, and a hybrid model router into one operational assistant—not a UI demonstration.

> **Note:** Screenshots and demo GIFs coming soon.

## ✨ Features

- **Hybrid brain routing:** routine conversation and lightweight work use local Ollama `jarvis:latest`; architecture, debugging, strategy, and multi-stage reasoning escalate to NVIDIA Nemotron.
- **Multimodal vision:** image and screen understanding are local-first through `jarvis:latest`, with configured Gemini Vision fallback.
- **Voice interaction:** Pocket-TTS can use a dedicated CUDA environment, with Edge-TTS and Windows SAPI fallback. Energy-floor interrupt detection includes grace periods to reduce false interruptions.
- **OCR and universal file processing:** Tesseract OCR plus text, PDF, DOCX, XLSX, image, audio, and media workflows where their dependencies are available.
- **Planner → Executor → Observer:** JARVIS plans actions, executes bounded operations, verifies results, and can replan selected failures.
- **Runtime truth and evidence:** capability claims use session-aware evidence rather than treating configuration alone as a live service.
- **Two user interfaces:** a native WPF desktop client via SSE and a Flask browser interface with chat, uploads, and telemetry.
- **Memory and learning:** persistent context, user preferences, personality data, self-awareness, and task/obligation tracking.
- **Creative Studio:** experimental NVIDIA NIM-backed image generation and image-to-video workflows, with results reported only after real output is produced.
- **Managed lifecycle:** duplicate-runtime protection, ownership-aware launch, bounded workers, and orderly shutdown.

## 🚀 Quick Start

### Prerequisites

Required:

- Windows 10 or 11
- Python 3.11+
- .NET SDK 8.0+ for the WPF client
- Ollama with the `jarvis:latest` model
- Tesseract OCR 5.4+ for OCR features

Optional:

- NVIDIA API key for Nemotron reasoning and Creative Studio
- Gemini API key for cloud vision/text fallback
- FFmpeg executable for media processing and conversion
- CUDA-capable GPU and a separately provisioned Pocket-TTS environment

### Installation

1. Clone the repository:

   ```powershell
   git clone https://github.com/CrixxLabs/J.A.R.V.I.S.git
   Set-Location .\J.A.R.V.I.S
   ```

2. Install Ollama, start it, and install the required local model:

   ```powershell
   ollama serve
   ollama pull jarvis:latest
   ollama list
   ```

3. Install the Python dependencies:

   ```powershell
   python -m pip install -r requirements.txt

   # Optional: face recognition, speaker verification, Whisper, and integrations.
   python -m pip install -r requirements-optional.txt
   ```

4. Install Tesseract OCR from a trusted package source. Make `tesseract` available on `PATH`, or configure `TESSERACT_PATH` with the executable path.

5. Create local configuration:

   ```powershell
   Copy-Item .env.example .env
   ```

   Add only the service credentials and optional integrations you intend to use. Do not commit `.env`.

6. Build the WPF client when using the native desktop interface:

   ```powershell
   dotnet build .\desktop\Jarvis.Desktop.Codex\Jarvis.Desktop.csproj --configuration Debug
   ```

### Usage

```powershell
# Recommended: launch an ownership-aware JARVIS runtime and WPF client.
python .\launch_mark_vii.py

# Use an existing WPF Debug build.
python .\launch_mark_vii.py --no-build

# Run only the canonical voice/runtime process.
python .\jarvis.py

# Run the separate browser backend, then open http://127.0.0.1:5000.
python .\server.py

# Run only the attach-only WPF client. Start jarvis.py separately first.
dotnet run --project .\desktop\Jarvis.Desktop.Codex\Jarvis.Desktop.csproj --configuration Debug
```

`launch_mark_vii.py` starts and owns a runtime only when one is not already available. A directly launched WPF client attaches to the SSE bridge but never starts or stops Python.

## 🧠 Brain Routing Logic

JARVIS uses bounded, provider-aware routing:

| Request type | Preferred route | Fallback route |
| --- | --- | --- |
| Routine chat, explanation, and lightweight work | Ollama `jarvis:latest` | NVIDIA Lightning → Gemini |
| Complex architecture, debugging, strategy, and multi-stage reasoning | NVIDIA Nemotron reasoning model | Ollama `jarvis:latest` → Gemini |
| Image and screen understanding | Ollama `jarvis:latest` | Gemini Vision |

Provider failures are tracked with cooldown-aware health state. A configured key or installed package is not treated as proof that a provider is live.

## 📁 Project Structure

| Path | Purpose |
| --- | --- |
| `jarvis.py` | Canonical voice runtime and lifecycle owner |
| `launch_mark_vii.py` | Ownership-aware runtime and WPF coordinator |
| `brain.py`, `vision.py` | Hybrid text and visual model routing |
| `planner.py`, `executor.py`, `observer.py` | Plan, execute, verify, and retry workflow |
| `status_registry.py` | Capability definitions and evidence-backed runtime truth |
| `jarvis_tts.py`, `pocket_tts_worker.py` | Pocket-TTS bridge and worker protocol |
| `ocr_runtime.py` | Tesseract discovery, probing, and OCR boundary |
| `server.py` | Separate Flask browser interface |
| `desktop/Jarvis.Desktop.Codex/` | Active native WPF client |
| `tests/` | Maintained regression suite with 222 test functions |
| `creative_studio/` | Experimental image and image-to-video provider layer |
| `skills/` | Optional production skills loaded by the runtime |

`jarvis_runtime.py` and `desktop/Jarvis.Desktop.Base/` are legacy paths; use the launcher and `Jarvis.Desktop.Codex` for MARK VII.

## 🔧 Configuration

See [MARK_VII_SETUP.md](MARK_VII_SETUP.md) for the full setup and operational guidance. Common `.env` settings include:

```dotenv
# Local-first model contract
OLLAMA_MODEL=jarvis:latest
OLLAMA_HOST=http://127.0.0.1:11434

# Optional cloud routing
NVIDIA_API_KEY=
GEMINI_API_KEY=

# Optional local tools
TESSERACT_PATH=
JARVIS_POCKET_TTS_PYTHON=
JARVIS_POCKET_TTS_VOICE=
JARVIS_POCKET_TTS_DEVICE=cuda
```

`brain.py` enforces `jarvis:latest` as the supported local model. Pocket-TTS requires both `JARVIS_POCKET_TTS_PYTHON` and `JARVIS_POCKET_TTS_VOICE`; without them, JARVIS can continue with its Edge-TTS/SAPI fallback.

## 🧪 Testing

Run the maintained regression suite:

```powershell
python -m pytest -q tests
```

Run target-machine release checks after Ollama, dependencies, and local configuration are ready:

```powershell
python .\MARK_VII_RELEASE_ACCEPTANCE.py
python .\phase5_vision_acceptance.py
```

The automated suite uses mocks and temporary state for most provider, OCR, lifecycle, and routing tests. Live voice, face, cloud-provider, hardware, and UI acceptance still require a deliberate target-machine check.

## 📋 Known Limitations

- PDF OCR is not implemented; PDF support is limited to embedded/selectable text extraction.
- Legacy binary `.doc` and `.xls` files are not supported.
- Creative Studio image generation and image-to-video remain experimental; text-to-video and arbitrary image editing are not live-verified.
- Face recognition, speaker verification, and Whisper require optional dependencies and appropriate local hardware.
- Pocket-TTS requires a separately provisioned CUDA-capable environment and explicit environment-variable configuration.
- External services such as Spotify, Gmail, Calendar, WhatsApp, weather, and news require their own credentials and dependencies.

## 🔒 Privacy & Security

- JARVIS is local-first: routine text and vision routes prefer Ollama before configured cloud fallbacks.
- Keep `.env`, credentials, enrollment data, memory ledgers, task data, uploads, and session logs local and out of Git.
- The browser interface defaults to loopback-only binding; non-loopback use requires explicit authentication configuration.
- Review `git status` and the Git index before every publication. Never commit credentials, personal voice/face data, or runtime state.
- Inspect sensitive images before allowing a cloud vision fallback.

## 🏗️ CrixxLabs Ecosystem

- **JARVIS MARK VII** — this Windows AI assistant and orchestration runtime.
- **SENTINEL** — CrixxLabs v1.0.0 release.
- **PHOENIX** — standalone CrixxLabs project.
- **ORION** — standalone CrixxLabs project.

## 📜 License

**License pending:** add the chosen license file before public release (for example, MIT or Apache-2.0).

## 🤝 Contributing

Contribution guidelines are pending. Until they are published, open an issue before proposing a substantial feature, integration, or architecture change.

## 📞 Contact

GitHub: [CrixxLabs](https://github.com/CrixxLabs)

---

**JARVIS MARK VII — Built for real work, not demos.**
