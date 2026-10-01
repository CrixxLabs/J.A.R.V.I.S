<p align="center">
  <img src="https://img.shields.io/badge/MARK_VII-Operational-00d4aa?style=for-the-badge&labelColor=0d1117" alt="MARK VII Status" />
  <img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/.NET-8.0-512bd4?style=for-the-badge&logo=dotnet&logoColor=white" alt=".NET" />
  <img src="https://img.shields.io/badge/Platform-Windows-0078d4?style=for-the-badge&logo=windows&logoColor=white" alt="Windows" />
  <img src="https://img.shields.io/badge/Tests-223_passing-2ea043?style=for-the-badge" alt="Tests" />
  <img src="https://img.shields.io/badge/License-Proprietary-f85149?style=for-the-badge" alt="License" />
</p>

<h1 align="center">J.A.R.V.I.S — MARK VII</h1>

<p align="center">
  <strong>An autonomous, local-first Windows AI assistant that listens, reasons, plans, executes deterministic actions, and verifies outcomes — engineered for real work, not demonstrations.</strong>
</p>

<p align="center">
  Hybrid Multi-Model Brain · Planner → Executor → Observer Loop · CUDA Pocket-TTS Voice · Resemblyzer Speaker ID<br/>
  Multimodal Vision + Tesseract OCR · 50+ Deterministic System Actions · Native WPF Holographic HUD + Web UI
</p>

---

## 📸 Runtime HUD & Interface

<p align="center">
  <img src="docs/Screenshots/speak.png" alt="JARVIS MARK VII Native HUD - Speaking State" width="90%" />
  <br/>
  <em><strong>Figure 1: Native WPF Desktop Client in Live Speaking State</strong> — Real-time telemetry showing active cognitive routing to <code>OLLAMA: JARVIS:LATEST</code>, memory footprint tracking (92.6%), screen capture vision verification, and streaming vocal output channel.</em>
</p>

<p align="center">
  <img src="docs/Screenshots/Screenshot%202026-10-01%20202125.png" alt="JARVIS MARK VII Native HUD - Thinking State" width="90%" />
  <br/>
  <em><strong>Figure 2: Cognitive Synthesis & Routing</strong> — Multi-model brain evaluating query complexity, routing intent between local and cloud reasoning tiers, and updating active cognitive array status in real time.</em>
</p>

<p align="center">
  <img src="docs/Screenshots/sleep.png" alt="JARVIS MARK VII Native HUD - Dormant State" width="90%" />
  <br/>
  <em><strong>Figure 3: Quiescent Low-Power State</strong> — HUD idling in low-power dormant mode with acoustic double-clap wake detection armed and background system resource monitoring active.</em>
</p>

---

## 🏗️ Architecture: Closed-Loop Agent

JARVIS is built as a **deterministic, closed-loop agent**. Unlike basic conversational chatbots, every request undergoes intent decomposition, capability catalog validation, bounded tool execution, and post-action sensory observation with automatic replanning.

```
                                  ┌────────────────────────┐
                                  │      USER INPUT        │
                                  │  Voice · Web · Desktop │
                                  └───────────┬────────────┘
                                              │
                                              ▼
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │                              ① PLANNER (planner.py)                                    │
 │                                                                                        │
 │   • Intent Classification: [fast | chat | action | reasoning | capability]             │
 │   • Capability Catalog Check (self_model.py): can_do(action) + fallback resolution     │
 │   • Self-Awareness & Diagnostic Query Interception                                     │
 │   • Routes to: Local Rule Handler  OR  Brain Router (LLM)                              │
 │   • Emits: (action_dict | None, spoken_response_str, model_type_str)                   │
 └────────────────────────────────────────────┬───────────────────────────────────────────┘
                                              │
                       ┌──────────────────────┴──────────────────────┐
                       │                                             │
                       ▼                                             ▼
             ┌───────────────────┐                         ┌───────────────────┐
             │ Direct Voice/Text │                         │    ② EXECUTOR     │
             │     Response      │                         │   (executor.py)   │
             └───────────────────┘                         └─────────┬─────────┘
                                                                     │
                                              ┌──────────────────────┴────────────────────┐
                                              │ 50+ Deterministic Action Handlers:        │
                                              │ • Application & Window Automation (PyAuto)│
                                              │ • Winget Installer & Credential Vault     │
                                              │ • File Processor (PDF/DOCX/XLSX/OCR/Media)│
                                              │ • Vision Analysis & Screen Inspection     │
                                              │ • Obligation Engine & Task Scheduler      │
                                              │ • Spotify, Google Calendar, Gmail, News   │
                                              │ • Creative Studio (NVIDIA NIM Generation) │
                                              └──────────────────────┬────────────────────┘
                                                                     │
                                                                     ▼
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │                              ③ OBSERVER (observer.py)                                  │
 │                                                                                        │
 │   • Active Window & Focus Tracker (Win32 API process-to-application mapping)           │
 │   • Screen Delta Hashing (MSS screenshot diff engine)                                  │
 │   • Hardware Telemetry Watcher (psutil CPU, RAM, Battery %, Charging state)            │
 │   • Execution Outcome Verification                                                     │
 └────────────────────────────────────────────┬───────────────────────────────────────────┘
                                              │
                             ┌────────────────┴────────────────┐
                             │                                 │
                     [Outcome Verified]                [Action Failed]
                             │                                 │
                             ▼                                 ▼
                     ┌───────────────┐                 ┌───────────────┐
                     │ Status Update │                 │ ④ REPLANNER   │
                     │  & Final Voice│                 │ (planner.py)  │
                     │   Synthesis   │                 └───────┬───────┘
                     └───────────────┘                         │
                                                               └──▶ Fallback / Alternative Action
```

---

## 🧠 Brain Router & Multi-Model Fallback

All language and multimodal intelligence routes through `brain.py` — the system's single LLM gateway. The brain does not speak or print directly; it returns structured data through complexity-aware routing backed by a thread-safe circuit breaker (`provider_health.py`).

### Routing Policy

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               BRAIN ROUTER (brain.py)                                  │
│                                                                                        │
│  Routine Chat / Fast Queries:                                                          │
│    Primary: Ollama jarvis:latest (Ministral 3 3B)                                      │
│    Fallback 1: NVIDIA Nemotron 3.5 Lightning (30B)                                     │
│    Fallback 2: Google Gemini Flash Lite (3.1)                                          │
│                                                                                        │
│  Complex Reasoning / Architecture / Code:                                              │
│    Primary: NVIDIA Nemotron 3 Super (120B) / Ultra (550B)                              │
│    Fallback 1: Ollama jarvis:latest                                                    │
│    Fallback 2: Google Gemini Flash Lite                                                │
│                                                                                        │
│  Multimodal Vision / Screen Understanding:                                             │
│    Primary: Ollama jarvis:latest (Ministral 3B Vision)                                 │
│    Fallback 1: Google Gemini Vision                                                    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Provider Health & Circuit Breaker Cooldowns

When a provider fails, `provider_health.py` transitions the provider-model pair into a cooldown state and diverts subsequent calls to the next tier in the chain:

| Health State | Cooldown Duration | Trigger Condition |
|---|---|---|
| `LIVE` | — | Successful inference during the active runtime session |
| `TIMEOUT` | **20 seconds** | Request exceeded connection/read deadline |
| `RATE_LIMITED` | **60 seconds** | HTTP 429 Too Many Requests |
| `SERVER_ERROR` | **30 seconds** | HTTP 500/502/503/504 internal server error |
| `OFFLINE` | **15 seconds** | Connection refused / endpoint unreachable |
| `BAD_REQUEST` | **30 minutes** | HTTP 400 invalid parameters or payload |
| `AUTH_ERROR` | **1 hour** | HTTP 401/403 invalid or expired API key |
| `MODEL_UNAVAILABLE` | **1 hour** | HTTP 404 target model not found on endpoint |

---

## ⚡ Evidence-Based Runtime Truth

JARVIS enforces strict **evidence levels** in `status_registry.py` to prevent hallucinated capability claims:

```
UNKNOWN ──▶ CODE ──▶ CONFIGURED ──▶ PROBED ──▶ LIVE
                                                │
             ┌──────────────────────────────────┴──────────────────────────────────┐
             ▼                                  ▼                                  ▼
          BLOCKED                            BROKEN                             DISABLED
    (Missing Auth/Model)              (Runtime Error/Crash)               (Explicit Config)
```

- **`CONFIGURED`**: Environment variable or key exists in `.env`.
- **`PROBED`**: Subsystem responded successfully to a lightweight heartbeat check.
- **`LIVE`**: Subsystem executed a real, end-to-end operation in the **current runtime session**. Old session records expire automatically.

---

## 🎙️ 5-Layer Acoustic & Voice Pipeline

```
  Layer 1: Double-Clap Wake Trigger (3000 energy threshold, 0.15s–0.8s interval)
     │
     ▼
  Layer 2: Local OpenAI Whisper Speech-to-Text (noise-resilient audio ingestion)
     │
     ▼
  Layer 3: Resemblyzer Speaker Verification (voice embedding verification)
     │
     ▼
  Layer 4: Google Speech Recognition Fallback
     │
     ▼
  Layer 5: Resident CUDA Pocket-TTS Synthesis (@ 24,000 Hz with Jarvis.wav voice cloning)
     │        ↳ Fallback 1: Edge-TTS (en-US-GuyNeural via streaming MP3)
     │        ↳ Fallback 2: Windows SAPI (pyttsx3 native system voice)
     ▼
  Real-Time Interrupt Watcher (Energy floor: 500, Grace period: 0.35s, Min duration: 0.35s)
```

---

## 📊 Comprehensive Feature Catalog (50+ Actions)

| Category | Action Identifier | Description & Integration |
|---|---|---|
| **App & System Control** | `open_app`, `close_app` | Launch and terminate Windows desktop applications |
| | `install_app` | Install software automatically via `winget` |
| | `install_and_login` | End-to-end install, launch, and automated credential injection |
| | `open_and_login` | Launch installed app and auto-fill saved credentials |
| | `save_login`, `list_logins`, `delete_login` | Encrypted credential vault management (`keyring`) |
| | `lock_pc`, `shutdown_pc`, `restart_pc` | Windows power and lock state management |
| | `system_control`, `system_info`, `system_status` | Volume, brightness, battery, memory, and CPU metrics |
| **Desktop Automation** | `type_text`, `click`, `scroll` | Deterministic PyAutoGUI mouse and keyboard automation |
| | `voice_type` | Transcribe user voice directly into the active text field |
| | `clipboard_read`, `clipboard_write` | Windows clipboard inspection and buffer injection |
| | `run_sequence` | Multi-step chained action workflow executor |
| **Multimodal & Vision** | `read_screen` | OCR text extraction via Tesseract 5.4+ engine |
| | `screenshot_describe` | Multimodal visual reasoning over the active desktop frame |
| **Universal File Processor** | `process_file` | Automated inspection, summarization, OCR, audio transcription, code review, and format conversion |
| | `open_file`, `list_folder`, `search_file`, `rename_file` | Filesystem navigation and batch file manipulation |
| **Web & Intelligence** | `web_search` | Real-time web querying via DuckDuckGo Search (`ddgs`) |
| | `summarize_url` | Webpage and YouTube video transcript extraction & distillation |
| | `weather`, `news` | OpenWeatherMap and NewsAPI real-time data feeds |
| **Communication** | `send_whatsapp`, `whatsapp_read`, `whatsapp_download` | WhatsApp Web automation via `pywhatkit` |
| | `whatsapp_timetable_update` | Automated schedule extraction from messaging channels |
| | `send_email` | SMTP email composition and dispatch via Gmail |
| | `join_meeting` | Meeting link detection and browser/client launch |
| **Media & Entertainment** | `spotify_play`, `spotify_control` | Spotify playback, search, and queue control via Spotipy OAuth |
| | `play_music`, `media` | System media key control (play/pause/next/prev) |
| **Obligations & Memory** | `add_obligation`, `query_obligations`, `mark_obligation_done` | Academic/professional deadline and obligation tracker |
| | `portal_scan` | Web portal crawler for pending assignments and tasks |
| | `remember`, `recall` | Semantic episodic memory ledger (`memory.py`) |
| | `profile_query`, `profile_remember`, `profile_forget` | User personality, habits, and preferences manager |
| **Self-Awareness & Dev** | `run_diagnostic` | Subsystem integrity and hardware diagnostic sweep |
| | `self_scan`, `self_capabilities`, `self_changes` | AST-based codebase introspection and change analyzer |
| | `creative_status`, `generate_image`, `generate_video` | NVIDIA NIM-backed Creative Studio generative workflow |

---

## ⚙️ Production Setup Guide

### 1. Prerequisites

- **OS:** Windows 10 or 11 (64-bit)
- **Python:** 3.11.x (Recommended)
- **.NET SDK:** 8.0+ (Required for native WPF client)
- **GPU:** NVIDIA CUDA-capable GPU (Optional, recommended for Pocket-TTS and local inference)
- **OCR:** [Tesseract OCR 5.4+](https://github.com/UB-Mannheim/tesseract/wiki) (Added to `PATH` or configured via `TESSERACT_PATH`)

### 2. Installation

```powershell
# Clone the repository
git clone https://github.com/CrixxLabs/J.A.R.V.I.S.git
cd J.A.R.V.I.S

# Install core runtime dependencies
python -m pip install -r requirements.txt

# Install optional packages (Face recognition, Speaker ID, Whisper, Spotify, Google API)
python -m pip install -r requirements-optional.txt
```

### 3. Local Ollama Model Setup

```powershell
# Start Ollama service
ollama serve

# Pull the pinned MARK VII model
ollama pull jarvis:latest

# Verify model availability
ollama list
```

### 4. Environment Configuration (`.env`)

Copy `.env.example` to `.env` and fill in your desired service keys:

```dotenv
# ═══════════════════════════════════════════════════════════════
#  LOCAL-FIRST MODEL CONTRACT (Pinned)
# ═══════════════════════════════════════════════════════════════
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=jarvis:latest
OLLAMA_KEEP_ALIVE=30s
OLLAMA_REQUEST_TIMEOUT=30.0

# ═══════════════════════════════════════════════════════════════
#  CLOUD INTELLIGENCE ROUTING (NVIDIA NIM & Gemini)
# ═══════════════════════════════════════════════════════════════
NVIDIA_API_KEY=nvapi-your-key-here
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_FAST_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_REASONING_MODEL=nvidia/nemotron-3-super-120b-a12b
NVIDIA_DEEP_MODEL=nvidia/nemotron-3-ultra-550b-a55b

GEMINI_API_KEY=your-gemini-key-here
GEMINI_TEXT_MODEL=gemini-3.1-flash-lite
GEMINI_VISION_MODEL=gemini-3.1-flash-lite
GEMINI_VISION_TIMEOUT=15.0

# ═══════════════════════════════════════════════════════════════
#  RESIDENT CUDA POCKET-TTS VOICE ENGINE
# ═══════════════════════════════════════════════════════════════
JARVIS_POCKET_TTS_PYTHON=C:\Users\<username>\AppData\Local\Programs\Python\Python311\python.exe
JARVIS_POCKET_TTS_VOICE=D:\J.A.R.V.I.S\Voices\Jarvis.wav
JARVIS_POCKET_TTS_DEVICE=cuda
JARVIS_POCKET_TTS_PORT=18777
USE_POCKET_TTS=true

# ═══════════════════════════════════════════════════════════════
#  INTEGRATIONS & TOOL PATHS
# ═══════════════════════════════════════════════════════════════
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
WEATHER_API_KEY=your-openweather-key
NEWS_API_KEY=your-newsapi-key
GMAIL_ADDRESS=your-email@gmail.com
GMAIL_PASSWORD=your-app-password
SPOTIFY_CLIENT_ID=your-spotify-client-id
SPOTIFY_CLIENT_SECRET=your-spotify-secret
SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
```

### 5. Build Native WPF Client

```powershell
dotnet build desktop\Jarvis.Desktop.Codex\Jarvis.Desktop.csproj --configuration Debug
```

---

## 🚀 Execution & Launch Modes

```powershell
# 1. PRIMARY: Launch runtime + attach native WPF client (Ownership-Aware)
python launch_mark_vii.py

# 2. Fast Launch with pre-built WPF client
python launch_mark_vii.py --no-build

# 3. Voice Runtime Only (Headless / Console Mode)
python jarvis.py

# 4. Web Interface Only (Accessible at http://127.0.0.1:5000)
python server.py

# 5. Standalone Attach-Only WPF Desktop Client
dotnet run --project desktop\Jarvis.Desktop.Codex\Jarvis.Desktop.csproj --configuration Debug
```

---

## 🧪 Test Suite & Regression Verification

JARVIS includes a test suite covering lifecycle, provider health, OCR reliability, executor safety, memory ledgers, and planner routing:

```powershell
# Execute all 223 unit and regression tests
python -m pytest -q tests

# Run codebase syntax and compile check
python -m compileall -q .
```

```
........................................................................ [ 32%]
........................................................................ [ 64%]
........................................................................ [ 96%]
.......                                                                  [100%]
============================== 223 passed in 36.98s ===============================
```

---

## 📁 Repository Structure

```
J.A.R.V.I.S/
├── jarvis.py                  # Canonical runtime entry point & vocal loop owner
├── launch_mark_vii.py         # Ownership-aware WPF & Python lifecycle coordinator
│
├── brain.py                   # Multi-model LLM router & complexity dispatcher
├── provider_health.py         # Thread-safe circuit breaker & cooldown manager
├── status_registry.py         # Evidence-backed capability truth authority
├── self_model.py              # Dynamic capability catalog & fallback router
├── error_handler.py           # Centralized exception logging & graceful degradation
│
├── planner.py                 # Intent classifier, capability check & action planner
├── executor.py                # 50+ deterministic action execution handlers
├── observer.py                # Background window, screen diff & telemetry observer
├── core.py                    # NLP context tracking, spaCy pipeline & sentiment
│
├── vision.py                  # Screen/webcam capture & visual reasoning
├── ocr_runtime.py             # Tesseract discovery, execution & timeout boundary
├── listener.py                # 5-layer voice system (Clap wake, Whisper, Speaker ID)
├── jarvis_tts.py              # Pocket-TTS streaming bridge & fallback coordinator
├── pocket_tts_worker.py       # Isolated resident CUDA Pocket-TTS worker
│
├── file_processor.py          # Universal format processor (PDF/DOCX/XLSX/OCR/Audio)
├── task_queue.py              # Thread-safe prioritized asynchronous task queue
├── tasks.py                   # Task ledger & reminder dispatcher
├── obligations.py             # Academic/professional obligation tracker
├── proactive_scheduler.py     # Background proactive alert scheduler (Battery/CPU)
├── memory.py                  # Semantic episodic & preference memory storage
├── personality.py             # Behavioral configuration & response styling
├── self_awareness.py          # Introspective codebase structure scanner
├── evolver.py                 # Self-patching & proposed improvement engine
│
├── server.py                  # Flask web backend with SSE bridge & upload handler
├── creative_studio/           # Experimental NVIDIA NIM image & video synthesis
├── skills/                    # Modular runtime-loaded skill plugins
│   ├── pc_doctor.py
│   └── project_dev_assistant.py
│
├── desktop/
│   └── Jarvis.Desktop.Codex/  # Active .NET 8 WPF Desktop Client
├── docs/
│   └── Screenshots/           # Interface and HUD visual captures
└── tests/                     # Comprehensive 223-test regression suite
```

---

## 🔒 Privacy, Isolation & Security

- **Strict Local-First Routing:** All routine queries and vision frames process locally on Ollama before any external API is queried.
- **Secure Key Storage:** App logins and integration secrets are isolated within the OS credential vault (`keyring`), never logged or serialized.
- **Loopback Enforcement:** Desktop SSE bridge and Web backend bind to `127.0.0.1` by default; cross-origin requests require explicit authorization.
- **Zero-Leak Policy:** Personal memory ledgers (`memory.json`), task databases (`tasks.json`), face embeddings (`user_face.pkl`), and `.env` credentials are excluded from version control.

---

## 📜 License & Copyright

Copyright © 2024 Arju Chamling ([CrixxLabs](https://github.com/CrixxLabs)). All rights reserved.

This software and associated documentation files are proprietary and confidential. Unauthorized copying, distribution, or modification is strictly prohibited. See [LICENSE](LICENSE) for terms.

---

<p align="center">
  <strong>JARVIS MARK VII — Built for real work, not demos.</strong>
</p>
