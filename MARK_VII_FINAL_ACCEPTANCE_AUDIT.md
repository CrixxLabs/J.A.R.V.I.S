# JARVIS MARK VII — Final Capability & End-to-End Acceptance Audit

> Historical 2026-09-11 audit. It is retained for traceability and must not be
> read as current-session capability evidence. Current release truth comes from
> the runtime evidence registry and the latest final release acceptance run.

Audit date: 2026-09-11 (Asia/Calcutta)  
Repository: `D:\J.A.R.V.I.S`  
Audit posture: read-only implementation audit plus safe live-machine tests. No backlog fixes were implemented.

## A. FINAL VERDICT

**MARK VII FUNCTIONAL BUT INCOMPLETE**

MARK VII has a real, bootable assistant runtime and several genuinely live paths: NVIDIA chat and reasoning, Gemini fallback and image understanding, deterministic Windows app control, persistent memory, reminders, task-queue execution, weather/news retrieval, screen capture, plugin routing, personality retrieval, and the Python SSE visual bridge. It is not complete because core advertised paths are blocked or broken (local Ollama, OCR, YouTube summarization, browser live voice, `vision_status`), external integrations are not live-accepted, lifecycle/status truth is unreliable, and the native UI contains simulated/static telemetry and does not receive vision state. Voice recognition, audible output, face identity, WhatsApp, Spotify, calendar, email delivery, and the visible WPF connection still require manual acceptance or configuration.

The boot harness's final `[PASS]` is not an acceptance verdict. It passes when one LLM provider is live and selected local persistence/import checks succeed; it also labels several integrations READY after checking only packages or non-empty credentials.

## B. EXECUTIVE SUMMARY

- Repository state was dirty before the audit: tracked source, tests, runtime JSON, generated Python bytecode, and WPF build outputs were modified; `Data/`, `personality.py`, `provider_health.py`, and three tests were untracked. No pre-existing change was reverted.
- `jarvis.py` is the MARK VII runtime. `jarvis_runtime.py` explicitly declares itself deprecated/legacy.
- Mandatory automated suite: **93 passed, 0 failed, 2 warnings in 52.86 seconds**.
- Real provider generations: NVIDIA fast passed; NVIDIA reasoning passed; Gemini passed boot, later timed out once, then passed a later direct call. A forced NVIDIA circuit-breaker condition routed a real response through Gemini. Ollama executable and Python SDK exist, but the daemon on `127.0.0.1:11434` is offline.
- Real desktop action: `Open Notepad` was deterministically planned, executed, observer-verified, then the audit-created Notepad was closed.
- Real vision: a 1920×1080 screen was captured; Gemini read exact text from a synthetic image; `Describe my screen` completed planner → executor → capture → Gemini end-to-end. Tesseract is not installed, so local OCR failed exactly as expected.
- Real state/data paths: temporary memory write + semantic retrieval passed; a two-second reminder persisted, fired, and became inactive; a dedicated-thread task queue executed and returned a task; text file rename and file extraction passed in a temporary directory.
- Runtime startup completed. The microphone calibrated, observers/tasks/queue/proactive scheduler/face watcher/evolver/self-scan/SSE started, and “Systems up” was synthesized/played. Ctrl+C stopped the task queue and bridge, but the active runtime does not call the centralized lifecycle manager or explicit stop functions for every subsystem.
- Native WPF builds with 0 warnings/errors. Its SSE client contract matches the Python bridge, but visible WPF launch/connect could not be automated under the execution policy, so connection is READY, not LIVE.
- `VISION UNWIRED` is integration case **B**: vision works, but `runtime_visuals` never publishes `vision_status`. It is not evidence that vision itself is absent.
- The persisted status registry is not a reliable live source: it retains old “Running” entries across process exits, and several probes overclaim authentication/readiness.

## Audit status legend

`LIVE` = completed end-to-end on this machine during this audit. `READY` = code is wired and prerequisites appear present, but a user/external action is still required. `PARTIAL` = only part of the intended path works. `UNWIRED` = implementation exists but the active runtime does not reach or publish it. `BLOCKED` = a dependency/config/environment prevents use. `STUB` = placeholder/simulation. `BROKEN` = connected path failed. `ABSENT` = expected feature has no implementation. `LEGACY` = old code excluded from MARK VII.

In the registry below, “Tests” means automated coverage; it never upgrades a capability to LIVE. “Live” identifies the audit action. “Work” is the minimum needed for LIVE.

## C. COMPLETE CAPABILITY MATRIX

### Runtime, planning, AI, memory, and personality

| Capability | Status | Source / active entry / trigger | Planner and executor route | Dependency / config / UI | Tests | Live evidence | Limitation / work for LIVE |
|---|---|---|---|---|---|---|---|
| MARK VII runtime boot | LIVE | `jarvis.py:886` `startup()`; `python jarvis.py` | Direct runtime composition | Mic, audio, camera, Python deps; WPF bridge starts | Startup is mostly outside `tests/` | Calibrated mic; started observers, tasks, 4-worker queue, proactive scheduler, face watcher, evolver, self-scan, SSE | Boot mutates runtime ledgers; make boot diagnostics truthful and add a noninteractive acceptance mode |
| Runtime shutdown | PARTIAL | `jarvis.py:1090`; Ctrl+C/finally | Shuts queue, saves session, sets dormant, stops bridge | Process exit cleans daemon threads | Lifecycle tests cover a separate manager | Ctrl+C printed queue shutdown and bridge stopped; process exit code was 1 | Active runtime never calls `tasks.stop_tasks`, observer/proactive/face stops, or `lifecycle.py`; integrate one coordinated shutdown and require exit 0 |
| Central lifecycle manager | UNWIRED | `lifecycle.py`; no active `jarvis.py` call | Separate registration API only | None; registry representation can be stale | Covered with mocks | Not part of real startup/shutdown | Register active components and make `jarvis.py` use it |
| Deterministic/NLP planner | LIVE | `planner.py:623`, `core.py:1062`; text/voice commands | Local intent → normalized action → self-model gate | spaCy/VADER/langdetect/deep-translator loaded | Planner tests, many mocks | “Open Notepad” returned `open_app` without LLM | Add live route coverage for every deterministic intent and fix self-model dependency mappings |
| NVIDIA normal conversation | LIVE | `brain.py:717`; any knowledge/chat input | Planner → `ask_llm` → `_nvidia_call` fast model | Configured key/base/model; provider/model field is published late | Provider routing tests mock network | Exact `ACCEPTANCE-NORMAL`, 7.59 s, NVIDIA fast model | Add stable live-provider acceptance test and redact provider failures |
| NVIDIA hard reasoning | LIVE | `brain.py:717`; reasoning markers/long prompt | Planner classification → reasoning model with thinking budget | Reasoning/deep model defaults used because `.env` entries are absent | Route selection mocked | Correct logic answer, 5.90 s, `nemotron-3-super-120b-a12b` | Explicitly configure/version-pin reasoning models and test hard/deep routes regularly |
| Gemini text fallback | LIVE | `brain.py:415,717` | NVIDIA failure/cooldown → Gemini → Ollama | Gemini key configured; model uses default | Fallback tests mock calls | Real Gemini boot generation; forced NVIDIA cooldown produced `ACCEPTANCE-FALLBACK`; one transient timeout also observed | Add bounded retry/recovery test and persist health if cross-process history is intended |
| Ollama local/offline inference | BLOCKED at audit time | `brain.py:474,717` | Final automatic fallback | Ollama SDK/exe present; daemon/model unavailable at audit time | Routing mocked only | Direct call returned `unavailable`; port 11434 daemon unreachable | Superseded by the MARK VII remediation: use only `qwen3-vl:4b` and verify a real fallback. |
| Groq provider | LEGACY | `_groq_call` remains in `brain.py` | Explicitly excluded from automatic route | Key exists; boot labels LEGACY/DISABLED | Test asserts it is excluded | Not invoked | Remove legacy claim/code or document a deliberate manual route |
| OpenRouter text provider | LEGACY | `_openrouter_call` remains in `brain.py` | Explicitly excluded from automatic route | Key exists; registry forces LEGACY/DISABLED | Test asserts exclusion | Not invoked | Remove legacy text route or expose a deliberate supported route |
| Provider health/circuit breaker | LIVE | `provider_health.py`; called by active providers | Provider/model keyed cooldown controls routing | In-memory only; registry shows only coarse provider state | Dedicated mocked tests | Artificial NVIDIA rate limit produced COOLDOWN and live Gemini routing; later Gemini call succeeded | Add same-process timeout→cooldown→expiry→recovery integration coverage and UI exposure |
| Persistent factual memory | LIVE | `memory.py`; planner context and remember/recall actions | Planner/executor and prompt context | `memory.json`; no UI fact counter | Memory tests | Temporary write created JSON and semantic query retrieved expected value | Isolate runtime/audit storage and test a later planner response using retrieved memory |
| Session/activity/usage memory | LIVE | `memory.py`; observer/executor/runtime log calls | Direct instrumentation | `memory.json`; browser activity endpoint uses it | Partial memory coverage | Runtime boot and Notepad action wrote real activity/usage | Add retention/privacy policy and avoid audit pollution of the user ledger |
| Personality retrieval | LIVE | `personality.py`, `planner.py:608` | Retrieved contract injected into NVIDIA/Gemini/Ollama prompts | `Data/personality`; no WPF representation | Strong unit coverage | Real seed loaded with 0 issues; six relevant entries selected | Add provider-output consistency acceptance, not only context equality |
| Explicit learned corrections | LIVE | `planner.py:647`, `personality.py:415` | Feedback capture short-circuits planner; later retrieval precedes profile | Learned JSONL; no current real learned correction records | Good coverage | Temporary learned store saved “Be shorter,” reloaded it, and applied its rule | Add user-facing list/forget controls and live later-response proof; current production learned set is empty |
| Conversation history/context | READY | `conversation_manager.py`, planner history | Updated around LLM turns; cleared on sleep/exit | In-memory plus contextual memory; no dedicated UI truth metric | Indirect planner tests | Used by live provider and plugin paths, but multi-turn correctness not manually exercised | Manual multi-turn pronoun/follow-up acceptance and persistence policy |
| Malayalam/language support | READY | `core.py` language detection/translation | Preprocessing before intent classification | `deep-translator` and `langdetect` loaded; network may be required | No focused tests | Initialization succeeded; no spoken/text translation acceptance | Manual bilingual commands and fallback tests |
| Plugin architecture | LIVE | `plugin_loader.py`; imported by planner | Skill priority/can-handle before intents/LLM | One local example plugin; no UI inventory | No dedicated tests | “hello skill” returned the example skill response | Replace/demo-isolate the example plugin; add collision and sandbox tests |
| Example skill | STUB | `skills/example_skill.py` | Broad triggers including `example` | None | No direct test | Triggered successfully | It is a template, and it can hijack article summarization; remove from production or narrow scope |

### Voice, vision, UI, and observation

| Capability | Status | Source / active entry / trigger | Planner and executor route | Dependency / config / UI | Tests | Live evidence | Limitation / work for LIVE |
|---|---|---|---|---|---|---|---|
| Microphone/STT initialization | READY | `listener.py:137,633`; active `jarvis.listen` | Mic → SpeechRecognition capture → local Whisper → speaker verification | 36 devices; mic index 1; Whisper small cached | No real-audio tests | Hardware discovery and ambient calibration succeeded | Human must speak known commands; verify transcript accuracy, timeout, interruption, and offline mode |
| Local Whisper transcription | READY | `listener.py:170,239` | Active listener lazy-loads Whisper | `whisper`, `torch`, cached small model; GPU/CPU choice | None live | Package/model cache present; not loaded/transcribed during audit | Manual clean/noisy speech acceptance and latency benchmark |
| Speaker/voice verification | READY | `listener.py:191,215` | Audio embedding comparison before accepting transcript | `user_voice.npy`, resemblyzer path | No focused tests | Enrollment artifact exists; no human voice sample was supplied | Manual owner/non-owner acceptance and false accept/reject thresholds |
| Wake activation/double clap | READY | `listener.py:383`, `jarvis.py:376` | Dormant loop → clap detection → active | Microphone | No focused tests | Runtime entered dormant listener path; no human clap | Manual clap, accidental-noise, and recovery acceptance |
| TTS generation | LIVE | `jarvis.py:243`; Edge-TTS default, SAPI fallback | Response → jarvisify → Edge file → pygame | Network Edge-TTS; local SAPI; optional F5 disabled | No nonmocked test | Temporary Edge-TTS MP3 generated (15,264 bytes); startup executed “Systems up” pipeline | Audible quality and interruption require human confirmation |
| TTS audible playback/interruption | READY | `jarvis.py:276-347`, listener interrupt watcher | Pygame playback polls stop/interrupt | Audio output devices present | No real playback test | Playback code ran during startup; auditor cannot verify audibility | Human hearing, device selection, barge-in, cleanup acceptance |
| F5 voice clone | BLOCKED | `jarvis_tts.py`; optional branch in `jarvis.speak` | Used only if `USE_F5_TTS=true` | Reference WAV exists; `f5_tts` package absent/feature disabled | None | Not run | Install model/package, enable, benchmark CPU, manually accept voice and interruption |
| Camera capture | READY | `vision.py:224`; boot/face watcher | Executor `capture_webcam` or face watcher | Camera 0 captured a frame | No live tests in suite | Boot probe captured; watcher started | Manual privacy/permission and actual scene acceptance |
| Face recognition/presence | READY | `face_recognition_module.py`, `jarvis._init_face_recognition` | Face watcher may wake dormant runtime and welcome user | `face_recognition`, camera, `user_face.pkl` | No focused tests | Watcher initialized and reported active | Human owner/non-owner acceptance; boot only proves encoding + camera, not correct identity |
| Screen capture | LIVE | `vision.py:30`; observer and vision actions | MSS → NumPy/OpenCV | Display session | Observer tests mock; no capture test | Captured 1920×1080×3 frame | Add multi-monitor/DPI/locked-session tests |
| Gemini screen/image understanding | LIVE | `vision.py:58,260`; “Describe my screen” | Deterministic planner → `screenshot_describe` → capture → Gemini | Gemini key/model | No real vision test | Synthetic image exact text read; actual screen command returned a 1,148-character description end-to-end | Add privacy confirmation/redaction and response assertions |
| Image input passed to planner | BROKEN | `planner.py:640` | Any non-null `image_b64` returns “Vision is disabled for now” | Contradicts working vision module | None | Source-proven connected rejection | Route supplied images to `vision.describe_image` or remove parameter/claim |
| Local OCR / read screen text | BLOCKED | `vision.py:151,335`, `executor.read_screen_text` | `read_screen`/`read_image_text` → Tesseract | Python wrapper installed; configured executable path does not exist; command missing | None live | Synthetic image OCR raised TesseractNotFoundError | Install Tesseract, validate configured path, then test screen and file OCR |
| `vision_status` action | BROKEN | `executor.py:1709`, `vision.py:358` | Executor formats GEMINI/TESSERACT/WEBCAM status | `WEBCAM` is not a baseline registry entry until capture | None | Direct executor call failed: `NoneType` has no attribute `get` | Handle missing status entries or register WEBCAM before formatting; add regression test |
| Observer/context tracking | LIVE | `observer.py`; started by runtime | Window/system/screen/YouTube threads → memory/context | Win32, psutil, MSS | Verification mostly mocked | Runtime detected VS Code and published real RAM; verified Notepad process | Add clean stop and real window/screen regression tests |
| Action outcome verification/replan | LIVE | `jarvis.execute_with_feedback`, `observer.py:268` | Pre-state → execute/retry → verify → optional replan | Per-action verifier coverage varies | Mock-heavy observer/executor tests | Notepad open was observer-verified | Expand real acceptance for close/join/vision; avoid LLM fallback for unsafe actions |
| Python runtime visual bridge | LIVE | `runtime_visuals.py`; `jarvis.startup()` | Runtime decorators/updates → snapshot/SSE on localhost:8765 | Local TCP only | Good hub tests | Live `/v1/snapshot` returned sequence/state/RAM/response; bridge closed on exit | Publish all claimed metrics and add client contract test |
| Native WPF shell/build | LIVE | `desktop/Jarvis.Desktop.Codex`; built executable | WPF project build | .NET SDK/Desktop runtime 8 available | No C# tests | Build succeeded, 0 errors/warnings | Build is LIVE; the interactive app/connection is separately only READY |
| WPF ↔ live backend connection | READY | `JarvisRuntimeClient.cs` → `/v1/events` | SSE deserialize → `ApplyVisualSignals` | Runtime bridge must be running | Python bridge tests only | Contract inspected and bridge live; GUI launch/connect was blocked by automation policy | Human launch with live runtime; verify reconnect, every state, and shutdown |
| Browser/Flask chat UI | PARTIAL | `server.py`, `ui/index.html`; `python server.py` | `/api/chat` loads planner/executor and speaks | Flask deps installed; separate process/runtime state | No browser E2E tests | Flask test client served page and core endpoints | It is a second runtime, not the WPF bridge; chat/action must be manually accepted |
| Browser live voice mode | BROKEN | JS calls `/api/live/start`, `/stop`, `/status` | No matching server routes; only GET `/api/live` exists | Browser/mic | None | Test client: missing live-status path and method failures | Implement matching endpoints or remove toggle; add browser E2E |
| Browser tasks panel | BROKEN | `syncUI` expects `tasks.tasks`; `/api/tasks` returns stats only | Poll every 2 s | Queue may not be started in standalone server | None | Actual response had counters, no `tasks` list | Align schema and queue ownership; render real tasks/progress |
| Browser subsystem panel | UNWIRED | `/api/subsystems` exists; UI calls only `/api/status` and reads `status.subsystems` | Server status omits subsystems | Persisted registry itself can be stale | None | `/api/subsystems` returned data but UI never fetches it | Fetch dedicated endpoint or include verified statuses in status payload |
| Browser Sentinel/Phoenix/process metrics | STUB | Static HTML fields | No update code for CPU/MEM/GPU/TEMP/Phoenix/process | None | None | Source has OFFLINE/zeros/`PROC --` defaults | Connect to real telemetry/task queue or remove labels |

### Actions, productivity, integrations, files, and autonomy

| Capability | Status | Source / active entry / trigger | Planner and executor route | Dependency / config / UI | Tests | Live evidence | Limitation / work for LIVE |
|---|---|---|---|---|---|---|---|
| Windows app open/close | LIVE | `executor.py:666,684`; “open/close X” | Deterministic intent → executor → observer | Windows shell/process APIs | Mock tests | Notepad opened, process verified, closed; no leftover process | Add allow/deny policy and real regression set |
| Media/volume/mouse/typing | READY | executor dispatch | LLM/deterministic action → pyautogui | Foreground focus; user session | Some mocked volume/clipboard tests | Not exercised to avoid changing user focus/input | Manual acceptance in disposable app; add focus guard |
| Lock/shutdown/restart | READY | executor dispatch; confirmation for shutdown/restart only | LLM action → self-model gate → executor | Windows commands | No live tests | Intentionally not executed | Manual controlled acceptance; lock should also be explicitly confirmed |
| System telemetry/status | READY | `system_monitor.py`, observer, executor | Fast command or local intent | psutil; optional GPU libs | Little coverage | Boot reported CPU/RAM/disk/GPU; WPF RAM updated | Validate metrics against OS; publish CPU/GPU/latency truth to UI |
| Reminders/timers | LIVE | `tasks.py`; “remind me…” | Deterministic planner → executor → persisted task/thread → speak | `tasks.json`, active task engine | Strong task-queue tests, little reminder live coverage | Temp 2-second reminder persisted, fired callback, active count became 0 | Fix explicit task-engine thread ownership/stop; add reboot rehydration E2E |
| Async task queue | LIVE | `task_queue.py`; runtime startup/server file processing | Dedicated owner loop with workers | None | Extensive tests | Safe coroutine completed with result/status/stats; runtime started/stopped 4 workers | Expose actual tasks consistently to UIs |
| Legacy `task_integration.py` | UNWIRED | Separate integration abstraction | Not imported by active runtime/server | None | None | Source only | Remove or integrate; avoid duplicate queue abstractions |
| Obligations/deadlines | READY | `obligations.py`, planner/executor/proactive | Deterministic add/query/done actions | `obligations.json` | No dedicated suite tests | Code traced; no user ledger mutation performed | Temp-ledger E2E for add/query/done and scheduler notification |
| Portal scanning | BLOCKED | `portal_scan.py` → `vision.read_screen` | Deterministic portal intent → OCR → proposal → confirmation | Tesseract missing; active browser required | None | Not run; dependency definitively offline | Install OCR, use fixture portal, test extraction/confirmation without real account |
| Proactive suggestions/check-ins | READY | `jarvis.proactive_loop`, `proactive_scheduler.py` | Observer/memory/obligations → periodic decision → speech/pending message | Long-lived runtime and timing/user context | None | Scheduler and proactive thread started | Time-compressed/manual acceptance; publish state and add rate-limit/privacy controls |
| Weather | LIVE | `executor.get_weather`; weather intent | Deterministic action → OpenWeather | Key configured | Mocked indirectly | Live London response returned temperature/conditions/humidity | Validate city parsing and HTTPS endpoint |
| News | LIVE | `executor.get_news`; LLM action/morning briefing | NewsAPI request | Key configured | No live tests | Live English headlines returned | Add source/date validation and structured response tests |
| Web search | BROKEN | `executor.web_search`; search intent | DuckDuckGo HTML scrape → optional planner summary | Network/site markup | Mocked request only | Live query returned “I couldn't find anything” | Replace brittle selector/scrape with supported search source; test live and fixtures |
| Article summarization | PARTIAL | `executor.summarize_article`; URL action | HTTP extraction → `_ask_fn` (entire planner) | Network + provider | None | Example.com fetch succeeded, but example plugin hijacked summary | Call brain summarization directly with actions/plugins disabled; validate HTTP/status/content |
| YouTube summarization | BROKEN | `executor.summarize_youtube` | Transcript API → planner summary | Installed API version lacks called static method | None | Failed immediately: `YouTubeTranscriptApi` has no `get_transcript` | Update to installed API contract, add transcript fixtures and a live public-video test |
| Morning briefing | PARTIAL | `executor.morning_briefing`, scheduled task | Time + weather + calendar + news | Calendar blocked; default city literal `your city` | None | Components traced; not run as a user event | Configure location/calendar and test scheduled spoken briefing |
| Basic file management | READY | `file_ops.py`/executor open/list/search/rename | LLM actions → common user folders | User filesystem, Windows shell | Minimal/mocked | Safe rename passed in temp, but active common-folder search was not exercised | Add configurable sandbox roots, deterministic intents, and temp-root E2E |
| Text/code file processing | LIVE | `file_processor.py`, executor/server endpoints | Upload/path action → processor | Core Python only for text/code | No dedicated tests | Temp `.txt` extract succeeded and returned expected text | Add security/size/encoding tests and UI upload E2E |
| PDF/DOCX/XLSX processing | BLOCKED | `file_processor.py` | Same processor actions | Missing PyPDF2, python-docx, openpyxl | None | Boot reported missing core dependencies | Install/pin packages and accept representative documents |
| Image OCR in file processor | BLOCKED | `_action_ocr` | File processor → pytesseract | Wrapper present; executable absent | None | Same live OCR failure | Install Tesseract and test PNG/PDF OCR |
| Audio/video processing, transcription, conversion | BLOCKED | file processor | Whisper/ffmpeg-python branches | Whisper exists; ffmpeg executable and ffmpeg-python/moviepy unavailable | None | Not run | Install/pin FFmpeg stack and accept WAV/MP3/MP4 samples |
| Clipboard read/write | READY | executor | LLM action → pyperclip | Windows clipboard | Mock write test | Not exercised to preserve clipboard | Manual round trip with restore or isolated clipboard harness |
| WhatsApp send | READY | `executor.send_whatsapp` | LLM action → pywhatkit browser automation | Package/Chrome configured; browser login/recipient required | None | Not sent; boot only checked import | Manual consented test to a test recipient; verify delivery, login, time rollover |
| WhatsApp read/download/timetable | BLOCKED | `whatsapp_fetcher.py`, executor stable handlers | Deterministic “check WhatsApp” → UI automation + OCR | Tesseract missing; WhatsApp UI/session required | None | Not run | Install OCR and use a test account/window fixture with coordinate/DPI resilience |
| Spotify playback/control | BLOCKED | `executor.get_spotify/play/control` | LLM actions → SpotifyOAuth/Web API | Client keys configured; `.spotify_cache` absent; user OAuth/device required | None | Not invoked to avoid launching OAuth/browser/playback | Complete OAuth, verify scopes/token refresh and a test device manually; fix boot probe claim |
| Gmail send | READY | `executor.send_email` | LLM action → SMTP SSL | Address/app password configured | None | SMTP authentication succeeded in 5.09 s; no email sent | Manual send to consented test inbox and verify receipt |
| Google Calendar | BLOCKED | executor calendar service/actions | LLM action → Google OAuth/API | Neither `credentials.json` nor `gcal_token.json` exists | None | Boot correctly reported DISABLED | Configure OAuth; list and create/delete a test event |
| Secure credential vault | READY | `credential_vault.py`, save/list/delete actions | Deterministic login intents → DPAPI vault/dialog | Windows user context; user secrets | None | Code path traced; not mutated | Manual save/retrieve/delete test with a disposable credential and redaction audit |
| App installation + auto-login | READY | `app_installer.py`, `login_orchestrator.py`, profiles | Deterministic install/login intents → winget/window automation/vault | winget, UI focus, supported profiles, credentials | None | Not run because it changes the machine/accounts | Disposable-app manual acceptance, cancellation, rollback, and focus safety |
| Dev Agent inspect/status | LIVE | `dev_agent.py`, executor dev actions | LLM-generated action or direct executor | pytest/git available; black/ruff/mypy missing | No focused suite | `dev_status` and `dev_inspect planner.py` succeeded | Add deterministic planner intents, fix AST inspection reporting (live output reported 0 functions for a function-rich file), and acceptance tests |
| Dev Agent tests/proposals | READY | dev agent executor actions | LLM action → constrained command/proposal | Tool allowlist | None | Not invoked beyond status/inspect | Run in copied temp repo; verify no application without review |
| Evolver analysis/proposals | PARTIAL | `evolver.py`; started after boot | Memory analysis → JSON proposal → schema-only “sandbox” | Runtime memory, `patches/`; first run delayed 5 min | None | Background thread started; historical proposals exist | “Sandbox” is only schema validation; add real diff/test sandbox, lifecycle stop, status publication, and manual approval workflow |
| Evolver patch application | UNWIRED | `evolver.apply_patch` | Callable function, not invoked by evolution cycle | Writes/backups core files | None | Not run | Keep outside autonomous runtime or build explicit human approval plus real validation |
| Image generation | STUB | `executor.generate_image` | Planner routes generation intent, self-model may block on legacy provider | Hard-coded empty key; function only says not configured | None | Source-proven placeholder | Implement a supported provider and save/return asset; align capability catalog |
| Video generation | BROKEN | `executor.generate_video` | Planner routes intent, then self-model blocks because OPENROUTER is deliberately DISABLED | OpenRouter key configured but provider marked legacy; unverified Kling schema | None | Not invoked; active validation prevents route | Choose supported provider, correct capability dependency, validate async media schema/download, then live accept |
| Self-awareness/code scan | LIVE | `self_awareness.py`; boot and self actions | Runtime boot thread / executor | Repository access and JSON state | No focused suite | Scanned 78 files, 357 functions, 1 skill; syntax scan reported 91 files OK | Stop treating inventory as capability proof; fix status/detail corruption and isolate generated state |
| Boot diagnostics/status registry | BROKEN | `boot_check.py`, `status_registry.py` | Probe results persisted to JSON | Many probes are import/config-only; no expiry/process ownership | Isolation tests only | Harness ran and returned PASS, but claimed Spotify “authenticated,” WhatsApp “active,” task workers, and SMTP readiness without those live checks; stale “Running” states survived exits | Introduce evidence levels/TTL/PID/session IDs; make probe wording exact; fail acceptance on core broken paths |

## D. LIVE VERIFIED CAPABILITIES

- MARK VII startup boundary and Python SSE bridge.
- NVIDIA fast conversation and NVIDIA reasoning model generation.
- Gemini direct generation, real fallback routing, synthetic-image understanding, and actual screen understanding.
- Deterministic planner → executor → Windows → observer flow for Notepad open/close.
- Screen capture, RAM publication, and observer foreground-app detection.
- Temporary persistent memory write and semantic retrieval.
- Personality profile retrieval and temporary learned-correction persistence/retrieval.
- Two-second reminder lifecycle and dedicated-thread async task execution/shutdown.
- Temporary file rename and text extraction.
- Edge-TTS audio-file generation.
- Weather, news, plugin routing, Dev Agent status/inspection, self/code scan.
- WPF compilation (the GUI connection itself was not live-verified).

## E. READY BUT REQUIRES MANUAL ACCEPTANCE

- Spoken STT, local Whisper accuracy, wake clap, voice identity, audible TTS quality/barge-in.
- Camera scene acceptance and owner/non-owner face-recognition accuracy.
- WPF launch, live SSE connection, reconnection, and visible behavior across all states.
- WhatsApp send, Gmail delivery, Spotify after OAuth, credential vault, installation/login automation.
- Mouse/keyboard/media/clipboard and destructive Windows controls in a controlled session.
- Multi-turn conversation, Malayalam, obligations, proactive check-ins, morning briefing after configuration.

## F. PARTIAL / UNWIRED CAPABILITIES

- Partial: coordinated shutdown, Flask/browser UI, article summarization, morning briefing, evolver, broad file intelligence.
- Unwired: `lifecycle.py` from the active runtime, `task_integration.py`, evolver patch application, WPF vision publication, WPF affect/amplitude telemetry, browser subsystem endpoint.
- The active runtime uses two reminder/background mechanisms (`tasks.py` and `task_queue.py`) plus an unused integration layer; UI terminology conflates them.

## G. BLOCKED / BROKEN CAPABILITIES

- Blocked: Ollama daemon/model; Tesseract OCR; portal and WhatsApp read paths; PDF/DOCX/XLSX processing; FFmpeg-based media processing; Calendar OAuth; Spotify OAuth/device; optional F5 TTS.
- Broken: `vision_status`, planner-supplied `image_b64`, live web search result extraction, installed YouTube transcript API call, browser live voice endpoints, browser task/subsystem display, video-generation routing, and boot/status truth.

## H. ABSENT / STUB / LEGACY CAPABILITIES

- Stub: image generation and the example skill; WPF audio amplitudes and several HUD readouts are simulations/static presentation.
- Legacy: `jarvis_runtime.py`, Groq automatic routing, OpenRouter automatic text routing, and desktop `Jarvis.Desktop.Base` relative to the active Codex project.
- Absent as an honest end-user feature: true autonomous self-improvement. The evolver only creates review proposals; its “sandbox” validates proposal keys, not code behavior. There is no autonomous approved/tested deployment loop.
- No separate general-purpose “offline assistant” is live today because Ollama is offline. Deterministic local commands remain available offline.

## I. UI TRUTH AUDIT

### Native WPF (`desktop/Jarvis.Desktop.Codex`)

| Visible state/metric | Truth | Evidence |
|---|---|---|
| DORMANT | REAL | Python sets it at startup, inactive loop, and shutdown; SSE carries it |
| IDLE | REAL | Python active loop sets it |
| LISTENING | REAL state, SIMULATED amplitude | `jarvis.listen` owns a visual activity; backend never publishes listening amplitude, so `JarvisCore` synthesizes it |
| THINKING | REAL state | Planner decorator publishes it |
| SPEAKING | REAL state, SIMULATED amplitude | `jarvis.speak` decorator publishes state/text; speech amplitude remains null and is synthesized |
| EXECUTING | REAL state, MISLEADING count | Executor decorator publishes it; WPF also hard-codes task count `03` in executing state, while backend counts executor activity scopes rather than async queue work |
| ALERT | REAL but narrow | Boot syntax warning can publish alert; no general registry-health-to-alert binding |
| Runtime ownership LIVE/WAITING | REAL | Driven by SSE connection event |
| Memory percentage | REAL | Observer publishes psutil RAM percentage |
| Vision | UNWIRED | Backend snapshot default is null and no publisher updates it; WPF renders null as `UNWIRED` |
| Provider/model | BROKEN/usually invisible | Python publishes provider only after `planner.ask` ends its THINKING activity; WPF updates the label only while state is THINKING |
| Voice status | DERIVED, not health | `RECEIVING/OUTPUT/STANDBY/QUIET` comes only from operational state |
| Cognitive status | DERIVED/STATIC | `ROUTING/ISOLATING/PASSIVE/ONLINE` is state decoration, not provider health |
| System nominal/attention | DERIVED | Generated from visual state, not the status registry |
| Task queue | STALE/MISLABELED | Uses active executor visual scopes; it is not `task_queue.py` depth/statistics; `03` is hard-coded during state application |
| Local core “STABLE / 07 ms” | STATIC | XAML literal; no latency source |
| Core-load bar | SIMULATED | Width is selected by state, not measured load |
| CPU/process analysis | STATIC/SIMULATED | XAML literals `CPU 23%`, Chrome/Ollama/VS Code values; mainly shown in dev override |
| User/JARVIS text | REAL body, STATIC metadata | Bodies come from bridge; timestamp and “LOCAL RESPONSE” headers are literals |
| Affect/tension | UNWIRED | Contract fields exist; no active Python publisher |
| Dev override | INTENTIONAL SIMULATION | Manually forces all states and reveals prototype telemetry |

Why the UI says `VISION UNWIRED`: **B — vision works, but `runtime_visuals` does not publish it.** Gemini screen understanding was LIVE. The bridge snapshot returned `vision_status: null`; WPF consumes that field correctly and substitutes `UNWIRED`.

### Browser UI (`ui/index.html` + `server.py`)

- REAL/PARTIAL: chat request state, history in that server process, activity list, and browser-page uptime.
- BROKEN: live voice start/stop/status endpoint contract; task list schema; subsystem grid wiring.
- STATIC/STUB: Sentinel OFFLINE, Phoenix IDLE/zero counters, process count, and several decorative health metrics.
- Security issue for backlog: the HTML `esc()` helper replaces dangerous characters with themselves, so it is not escaping content before assigning `innerHTML`.
- The source contains visible mojibake/corrupted glyph strings in multiple labels and messages.

## J. EXPECTATION VS REALITY MATRIX

| Expected / claimed capability | Actual status | Why different | Required |
|---|---|---|---|
| Natural conversation | LIVE online, not offline | NVIDIA/Gemini work; Ollama does not | Bring Ollama live and manually accept multi-turn behavior |
| Voice assistant | READY manual | Hardware and pipeline initialize; no human speech/hearing acceptance | Owner speech, noisy room, speaker verification, barge-in tests |
| Offline assistant | PARTIAL | Local commands work; local generative model is offline | Start/pin Ollama model and test network-disconnected fallback |
| Windows control | LIVE for app open/close; READY broader | One safe real action verified; other UI/destructive actions intentionally not run | Controlled manual suite and safety gates |
| Screen awareness | LIVE | Real screen command succeeded | Privacy/redaction and regression tests |
| OCR | BLOCKED | Wrapper exists, binary missing | Install Tesseract and accept fixtures/screen |
| Image understanding | LIVE by Gemini | Working code conflicts with planner `image_b64` rejection/UI null | Wire image input and UI state |
| Face recognition/presence | READY manual | Watcher/encoding/camera exist, identity correctness not proven | Owner/non-owner live test |
| Persistent memory | LIVE mechanics | Storage/retrieval work; later-answer influence not proven | Multi-session response acceptance |
| Personality adaptation | LIVE context; READY behavioral output | Retrieval works; provider response consistency not accepted | Compare actual outputs across providers |
| Learned corrections | LIVE mechanics; production set empty | Capture/retrieval works with temporary correction | Record/inspect/forget real correction with user consent |
| Reminders/background tasks | LIVE | Short reminder and queue task worked | Reboot rehydration and UI truth |
| Proactive suggestions | READY | Threads start; timed/behavioral outcome not observed | Time-compressed/manual acceptance |
| Spotify | BLOCKED | Keys are not user OAuth; no token cache/device | Complete OAuth and manual playback/control |
| WhatsApp | READY send / BLOCKED read | Send requires manual external action; read requires missing OCR/UI | Consent test + OCR installation |
| Calendar | BLOCKED | OAuth files absent | Configure and test temporary event |
| Weather/news | LIVE | Real APIs responded | Add freshness/source assertions |
| Article summary | PARTIAL | Fetch works, but planner/plugin can hijack summarizer | Direct non-action LLM summarizer |
| YouTube summary | BROKEN | Installed library API changed | Update integration and test |
| File management | READY | Code wired; user folders not mutated | Sandboxed/manual folder acceptance |
| Document understanding | LIVE text only / BLOCKED office formats | Core document packages absent | Install dependencies and fixtures |
| Clipboard | READY manual | Avoided altering user clipboard | Restoring round-trip harness |
| Autonomous behavior | PARTIAL | Schedulers and proposals exist; no general autonomous agent loop | Define bounded behaviors, permissions, and acceptance |
| Plugins | LIVE architecture, STUB content | Only an example template is installed | Production plugin and collision tests |
| Dev Agent | LIVE inspect/status, READY broader | Some outputs are inaccurate; test/proposal paths not accepted | Fix AST report and temp-repo acceptance |
| Evolver/self-improvement | PARTIAL, not autonomous | Generates review JSON; sandbox is schema-only; patch call unwired | Real isolated validation and explicit human approval |
| Self-awareness/health | BROKEN truth layer | Inventory is real, statuses stale/overclaimed/mis-mapped | Evidence levels, TTL/process ownership, correct dependencies |
| Native UI | LIVE build / READY connection / PARTIAL truth | SSE is real; metrics incomplete/simulated; no visible acceptance | Human UI E2E plus telemetry fixes |
| Provider fallback | LIVE NVIDIA→Gemini; BLOCKED local | Cloud fallback succeeded; Ollama offline | Network-failure and recovery acceptance with Ollama |
| Image generation | STUB | Explicit placeholder | Implement provider/output lifecycle |
| Video generation | BROKEN | Planner self-model blocks legacy provider dependency | Supported provider and verified API contract |

## K. PROVIDER / AI BRAIN STATUS

| Provider | Config | Live status | Routing |
|---|---|---|---|
| NVIDIA NIM | CONFIGURED | LIVE fast and reasoning | Primary; fast fallback after stronger NVIDIA failure |
| Gemini | CONFIGURED | LIVE, with one observed transient timeout | Secondary cloud fallback; also vision |
| Ollama | Defaults used; executable/SDK installed | BLOCKED: daemon unreachable, model unverified | Final automatic fallback |
| Groq | Key present | LEGACY/UNTESTED | Deliberately excluded |
| OpenRouter | Key present | LEGACY for text; media route contradictory | Deliberately excluded from automatic brain routing |

Routing order in active code is NVIDIA selected model → NVIDIA fast (if stronger model failed) → Gemini → Ollama. The health tracker is real but process-local. Provider/model publication to WPF is timed incorrectly and usually cannot display.

## L. VOICE STATUS

STT hardware initialization and calibration are READY. Local Whisper, the user voice embedding, and speaker-verification code are present, but real speech recognition was not claimed LIVE without a human utterance. Edge-TTS generation is LIVE; the startup speech pipeline executed, but audibility, quality, correct output device, and interruption remain manual acceptance items. F5-TTS is disabled and missing its Python package despite having a reference WAV.

## M. VISION / SCREEN / OCR STATUS

Screen capture and Gemini vision are LIVE. Actual “Describe my screen” routing worked end-to-end. Camera capture initialized, and face watcher startup is READY pending identity acceptance. Local OCR is BLOCKED by the missing Tesseract executable. `vision_status` is BROKEN on a missing registry entry. WPF vision is UNWIRED because the backend does not publish `vision_status`.

## N. MEMORY / PERSONALITY STATUS

JSON persistence, semantic retrieval, activity logging, relevant personality selection, privacy filtering in code/tests, and explicit correction storage/retrieval all work. The production correction stores are currently empty, so no historical learned correction was available to demonstrate. A temporary correction proved the mechanism without changing the user's learned profile. A future acceptance must show that retrieved memory/correction changes a later real provider response and that forgetting/removal works.

## O. WINDOWS ACTION / EXECUTOR STATUS

App open/close is LIVE with observer verification. The executor exposes a very broad action surface, but most actions are LLM-generated rather than deterministically parsed, and live safety/permission coverage is sparse. Shutdown/restart require a repeated command; lock does not. Mouse, typing, file, clipboard, installer, login, messaging, and media actions need controlled manual acceptance because they can affect foreground state or external systems.

## P. REMINDERS / BACKGROUND TASK STATUS

The reminder countdown/persistence callback and async task queue both passed live tests. Rehydration exists in code but was not reboot-tested. `tasks.py` does not retain the thread object it starts, so `stop_tasks()` signals it but cannot join that thread. The active shutdown does not call it anyway. The WPF queue metric counts visual executor scopes, while the browser expects a list the server does not return.

## Q. EXTERNAL INTEGRATIONS STATUS

- LIVE: OpenWeather and NewsAPI.
- READY manual: Gmail sending (SMTP authentication valid), WhatsApp send (requires recipient/login/browser), Spotify only after OAuth/device, app login/vault.
- BLOCKED: Calendar, WhatsApp read/portal OCR, Spotify current OAuth state.
- BROKEN/PARTIAL: web search, YouTube, article summarization.
- Not tested by sending/changing external data: email delivery, WhatsApp delivery, Spotify playback, calendar writes.

## R. AUTONOMY / DEV AGENT / EVOLVER STATUS

JARVIS has scheduled observations, reminders, proactive check-ins, a constrained development helper, and a proposal generator. It does **not** have accepted autonomous self-improvement. The evolver's background cycle starts but waits five minutes, reads usage/failure data, writes review proposals, and performs only schema validation as its sandbox. Its file-patching function is not invoked by that cycle. Dev Agent inspection/status works, but its live inspection incorrectly reported zero functions for `planner.py`, so its analysis quality is not acceptance-ready.

## S. DEPENDENCY & CONFIGURATION GAPS

Secrets were never printed. Statuses below mean only presence/validity tested during this audit.

| Item | Status | Evidence / gap |
|---|---|---|
| Python | CONFIGURED | 3.11.0 |
| .NET SDK/Desktop runtime | CONFIGURED | SDK 8.0.424; WindowsDesktop runtime 8.0 present |
| Git/pytest | CONFIGURED | Executables found |
| NVIDIA credentials | CONFIGURED and VALID | Live fast/reasoning generations |
| Gemini credentials | CONFIGURED and VALID | Live text and vision generations; one timeout observed |
| Ollama executable/SDK | CONFIGURED | Executable and Python SDK found |
| Ollama daemon/model | MISSING/UNTESTED | Port unreachable; configured/default model could not generate |
| Tesseract | MISSING | Config points to absent executable; command absent |
| FFmpeg executable | MISSING | Command absent |
| Whisper small model | CONFIGURED | Package and cached model found; live transcription untested |
| PyPDF2/python-docx/openpyxl | MISSING | Imports unavailable |
| Pillow/pandas | CONFIGURED | Imports available |
| ffmpeg-python/python-magic/moviepy | MISSING | Imports unavailable (pydub present) |
| SpeechRecognition/PyAudio/Edge-TTS/pygame | CONFIGURED | Imports and hardware/TTS checks passed |
| Camera | CONFIGURED | Live boot frame capture |
| Microphone | CONFIGURED | 36 PyAudio devices; mic index 1 calibrated |
| Face/voice enrollment | CONFIGURED | `user_face.pkl` and `user_voice.npy` present |
| Spotify client configuration | CONFIGURED | Client fields present |
| Spotify user OAuth token/device | MISSING/UNTESTED | No `.spotify_cache`; no user action performed |
| Gmail credentials | CONFIGURED and VALID | SMTP login succeeded; delivery untested |
| Calendar OAuth | MISSING | Both credential/token files absent |
| Weather/News keys | CONFIGURED and VALID | Live calls returned data |
| Chrome path | CONFIGURED | Path value present; browser/login state untested |
| Requirements/lockfile | MISSING | No requirements, pyproject, Pipfile, or equivalent dependency manifest found |

`.env` omits explicit NVIDIA reasoning/deep, Gemini text/vision, and Ollama host/model/keep-alive values; code defaults are currently used. That is operational for NVIDIA/Gemini today but not reproducible configuration.

## T. TEST RESULTS

Command: `python -m pytest tests/ -q`

- **93 passed, 0 failed**.
- **2 warnings**: Python 3.13 deprecations for `aifc` and `audioop`, emitted through `speech_recognition`.
- One pytest-asyncio configuration deprecation warning was printed before collection because `asyncio_default_fixture_loop_scope` is unset.
- Test runtime: 52.86 s.

What the suite does prove: core memory operations, queue mechanics, lifecycle class behavior, visual hub state behavior, personality schema/retrieval/privacy/correction logic, provider routing decisions, planner normalization, and executor/observer logic under mocks.

What it does **not** prove: live providers, microphone transcription, audible TTS, owner voice/face recognition, camera/screen privacy, Tesseract, Ollama daemon/model, live Windows shell effects, real web APIs/pages, WhatsApp/Spotify/Gmail/Calendar, file formats beyond test logic, WPF rendering/SSE connection, browser JavaScript contracts, proactive timing, evolver quality, or real clean shutdown. Many executor/observer/planner/provider/lifecycle tests patch OS/network/callback dependencies.

Important paths with no meaningful acceptance coverage include voice, vision, face, boot truth, server/browser UI, WPF, external integrations, file processor, obligations, proactive scheduler, plugin collision, self-awareness accuracy, Dev Agent, and evolver.

## U. COMPLETION BACKLOG

### P0 — advertised/core capability broken or fake

1. **Make runtime health truthful.** Root cause: import/config probes are labeled as authentication/live readiness; statuses have no TTL/session owner and survive stopped processes; self-awareness writes misleading details. Files: `boot_check.py`, `status_registry.py`, `self_awareness.py`, `self_model.py`. Fix: evidence levels (`CODE/CONFIGURED/PROBED/LIVE`), timestamps/TTL/PID/session, exact probe names, and boot failure policy. Dependencies: none. Acceptance: stopped subsystems become stale/offline; Spotify/WhatsApp/email/task queue cannot say LIVE without their stated check. Risk: high—planner gates actions on these states. Complexity: LARGE.
2. **Remove false/simulated native UI telemetry and publish real fields.** Root cause: no vision/amplitude/affect/provider timing publishers; hard-coded queue/core/analysis values. Files: `runtime_visuals.py`, `jarvis.py`, `observer.py`, WPF `MainWindow.xaml(.cs)`, `JarvisCore.cs`. Fix: publish verified vision/provider/queue/health; label prototype animations; remove hard-coded data. Acceptance: each visible value traces to a backend timestamp/source; absent values show UNKNOWN, never fabricated numbers. Risk: medium. Complexity: MEDIUM.
3. **Repair browser UI/server contract and escaping.** Root cause: nonexistent live endpoints, task schema mismatch, subsystem endpoint ignored, ineffective HTML escaping, duplicate IDs. Files: `ui/index.html`, `server.py`. Acceptance: browser E2E for chat, mic state, tasks, activity, subsystems, file upload; injection fixture renders as text. Risk: high/security. Complexity: MEDIUM.
4. **Restore local OCR.** Root cause: Tesseract executable absent. Files/config: installer/docs, `.env`, `vision.py`, executor/portal/WhatsApp/file processor. Acceptance: synthetic text exact-enough OCR, real screen OCR, and portal fixture extraction. Risk: low. Complexity: SMALL.
5. **Fix connected broken content tools.** Root causes: obsolete YouTube API call, brittle DuckDuckGo scrape, article summarizer routes through the full planner and can trigger plugins. Files: `executor.py`, plugin/planner boundary. Acceptance: fixture + live tests for search, one article, one captioned video; summarizers cannot emit actions or skill responses. Risk: medium. Complexity: MEDIUM.
6. **Fix active vision contradictions.** Root cause: planner rejects `image_b64`; `vision_status` assumes non-null WEBCAM registry record. Files: `planner.py`, `vision.py`, `executor.py`, status baseline. Acceptance: image attachment and status command both succeed before any webcam capture. Risk: low. Complexity: SMALL.
7. **Unify active lifecycle and clean shutdown.** Root cause: `lifecycle.py` is unwired; active finally block stops only queue/bridge. Files: `jarvis.py`, `lifecycle.py`, observer/tasks/proactive/face/evolver. Acceptance: SIGINT/SIGTERM exit 0, every owned thread/service stops, port closes, statuses reflect shutdown, repeated start/stop passes. Risk: high/concurrency. Complexity: MEDIUM.
8. **Make capability claims match executable routes.** Root cause: `AVAILABLE_ACTIONS`, `self_model`, boot registry, and UI independently claim different truths; video depends on deliberately disabled OpenRouter and image generation is a stub. Files: `executor.py`, `self_model.py`, `brain.py`, `boot_check.py`. Acceptance: generated registry from one source; every advertised capability has an accepted route or is visibly unavailable. Risk: high. Complexity: LARGE.

### P1 — important MARK VII capability incomplete

1. **Bring Ollama offline brain live.** Start service, pin/pull model, configure host/model, add network-disconnected planner acceptance. Files: `.env.example`, `brain.py`, boot checks. Dependencies: Ollama/model disk/RAM. Risk: medium/resource use. Complexity: MEDIUM.
2. **Complete manual voice/identity acceptance.** Test microphones, Whisper latency/accuracy, owner/non-owner voice and face, audible TTS, barge-in, wake clap, fallback. Files: listener/face/jarvis. Dependencies: human and hardware. Risk: privacy/false acceptance. Complexity: MEDIUM.
3. **Install/pin universal file dependencies.** Add reproducible dependency manifest and PDF/DOCX/XLSX/FFmpeg/Tesseract fixtures. Files: file processor/build docs. Risk: native tool/version conflicts. Complexity: MEDIUM.
4. **Complete WPF live acceptance.** Launch against runtime; test reconnect and all states/fields. Add C# contract tests. Risk: UI thread/reconnect. Complexity: MEDIUM.
5. **Complete external integrations with safe test accounts.** Spotify OAuth/device, Calendar OAuth/test event, WhatsApp test recipient, Gmail receipt. Correct boot probes. Risk: external writes/credentials. Complexity: LARGE.
6. **Harden reminders/proactive system.** Own/join threads, test reboot rehydration/missed reminders, rate limits, user quiet hours, WPF/browser task truth. Risk: concurrency/duplicate firing. Complexity: MEDIUM.
7. **Fix Dev Agent analysis correctness.** Its live inspection reported zero functions for `planner.py`. Add AST fixtures and temp-repo test/proposal acceptance. Risk: wrong code advice. Complexity: SMALL.
8. **Define supported media generation.** Implement image generation; either validate/fix video provider or remove from MARK VII. Dependencies: provider/account/storage. Risk: cost/content/output schemas. Complexity: LARGE.

### P2 — useful integration/quality gaps

1. Add end-to-end tests using dependency injection and temporary ledgers so audits do not alter `memory.json`, `status_registry.json`, `self_model.json`, or session logs. Complexity: MEDIUM; risk: low.
2. Add configuration schema/validator with placeholder detection, default disclosure, and secret-safe diagnostics. Complexity: SMALL; risk: low.
3. Add multi-monitor/DPI/window-focus handling for screen, mouse, WhatsApp, installer, and login automation. Complexity: MEDIUM; risk: medium.
4. Add privacy consent/redaction before cloud screen/image analysis and profile context transmission. Complexity: MEDIUM; risk: medium.
5. Add deterministic planner intents for common executor actions and structured action authorization. Complexity: MEDIUM; risk: medium.
6. Remove migration/update scripts and generated bytecode/build outputs from product inventory or move them to tooling/archive paths. Complexity: SMALL; risk: low.
7. Resolve mojibake/source encoding and add UTF-8 UI snapshot tests. Complexity: SMALL; risk: low.

### VII.x — enhancements, not required for MARK VII completion

1. Real audio amplitude visualizers and richer affect visualization after core UI truth is fixed. Complexity: MEDIUM.
2. Additional production plugins with permissions, isolation, signatures, and marketplace metadata. Complexity: LARGE.
3. Advanced semantic/vector memory after current JSON privacy/retention behavior is accepted. Complexity: LARGE.
4. A real isolated evolver sandbox with patch diffing, tests, rollback, and explicit approval—not autonomous production writes. Complexity: LARGE.
5. Broader multilingual/offline speech models and per-device audio profiles. Complexity: LARGE.

## V. EXACT DEFINITION OF “MARK VII COMPLETE”

Arju can honestly say “JARVIS MARK VII is finished” only when all of these objective criteria pass on the target machine:

1. A clean checkout has a reproducible Python/.NET/native dependency manifest and one documented start command.
2. Boot reports evidence levels truthfully and has no stale READY/RUNNING status from an earlier process.
3. NVIDIA normal/reasoning, Gemini fallback, and Ollama offline fallback each complete real prompts; cooldown and recovery are exercised.
4. Human acceptance passes for wake, microphone transcription, owner voice/face recognition, TTS audibility, interruption, and camera permissions.
5. Deterministic Windows actions, screen capture, Gemini vision, OCR, reminders with reboot, memory across sessions, personality, and learned correction influence all pass end-to-end.
6. Every advertised integration is either live-accepted with a test account or clearly removed/declared optional—not shown READY from credentials/imports alone.
7. Search, article, YouTube, text/PDF/DOCX/XLSX/image/audio/video file processing pass representative fixtures and safe live checks.
8. Native WPF connects/reconnects to the real runtime, and every visible metric/state is sourced, timestamped, and non-simulated unless explicitly labeled DEV/DEMO.
9. Browser UI is either removed from MARK VII scope or its endpoint schemas, live voice, tasks, activity, subsystems, upload, and output escaping pass browser E2E tests.
10. Clean shutdown on normal exit, SIGINT, SIGTERM, and UI close stops every owned service/thread, closes ports/devices, persists safely, and exits 0.
11. The complete automated suite plus a documented manual acceptance checklist passes from a clean machine with zero core failures.
12. The capability registry contains no STUB, BROKEN, UNWIRED, or misleading LIVE/READY item among the declared MARK VII feature set; any remaining BLOCKED item is explicitly optional and visibly reported as such.

## W. RECOMMENDED NEXT MISSION

Run a **MARK VII P0 Truth & Reliability Remediation Mission**, not a feature-expansion mission. Scope it to: unify the capability/status source of truth; add evidence levels, TTL/session ownership, and correct probes; repair WPF/browser telemetry contracts; install and accept Tesseract; fix `vision_status`, planner image input, search/article/YouTube paths; wire coordinated lifecycle shutdown; and add isolated end-to-end tests for those fixes. The mission must preserve existing user data, use temporary ledgers/fixtures, perform manual voice/face/UI acceptance with Arju, and end by rerunning this exact audit before any VII.x enhancement work.

## Audit side effects and safety record

No feature/backlog implementation was performed. Safe tests used temporary directories except where the real runtime inherently writes its status, session, self-awareness, self-model, and memory ledgers. The Notepad instance created by the audit was closed. Temporary test directories cleaned themselves. The WPF build refreshed already-tracked build outputs. Runtime/boot tests updated `memory.json`, `status_registry.json`, `self_awareness_state.json`, `self_model.json`, Python bytecode, and created a session log; these files were already dirty/generated in the initial repository state, and the audit did not revert them.
