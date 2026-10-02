"""Canonical capability truth and backward-compatible runtime status registry.

This is the single authority for what J.A.R.V.I.S. — MARK VIII can currently claim. It keeps
the old ``SubsystemState``/``state`` API while new decisions use explicit
evidence levels and current-session ownership.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_REGISTRY_FILE = os.path.join(BASE_DIR, "status_registry.json")


class EvidenceLevel(str, Enum):
    UNKNOWN = "UNKNOWN"
    CODE = "CODE"
    CONFIGURED = "CONFIGURED"
    PROBED = "PROBED"
    LIVE = "LIVE"
    BLOCKED = "BLOCKED"
    BROKEN = "BROKEN"
    DISABLED = "DISABLED"
    LEGACY = "LEGACY"

    def __str__(self):
        return self.value


class SubsystemState(str, Enum):
    """Legacy adapter retained for current callers and UI serialization."""
    READY = "READY"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"

    def __str__(self):
        return self.value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.value.upper() == other.upper()
        return isinstance(other, SubsystemState) and self.value == other.value

    def __hash__(self):
        return hash(self.value)


KNOWN_SUBSYSTEMS = [
    "VOICE_STT", "VOICE_TTS", "CAMERA", "FACE_RECOGNITION", "NVIDIA",
    "OLLAMA", "OLLAMA_SERVICE", "OLLAMA_MODEL_JARVIS_MINISTRAL_3B", "GROQ",
    "OPENROUTER", "GEMINI", "TESSERACT_OCR", "MEMORY",
    "PERSONALITY", "TASKS", "PLUGINS", "WHATSAPP_SEND", "SPOTIFY", "EMAIL",
    "CALENDAR", "FLASK_UI", "FILE_PROCESSOR", "FILE_TEXT_PARSER",
    "FILE_PDF_PARSER", "FILE_DOCX_PARSER", "FILE_XLSX_PARSER",
    "FILE_IMAGE_PARSER", "VISION", "WEBCAM",
    "TASK_QUEUE", "DEV_AGENT", "CONFIG", "HARDWARE", "OBSERVER",
    "PROACTIVE_SCHEDULER", "EVOLVER", "RUNTIME_SSE", "WPF_UI", "BROWSER_UI",
    "UI_AUTOMATION", "GLOBAL_WORKSPACE", "CURRICULUM_ENGINE", "SYNAPTIC_ADAPTER",
]

_TEST_ARTIFACT_NAMES = {"FAIL_COMP", "HIGH_PRIORITY", "LOW_PRIORITY", "TEST"}
# Observations produced by a running process are session-bound whether they
# succeeded or failed.  Static facts (CODE/CONFIGURED) may remain useful across
# launches, but an old probe failure is no more current than an old success.
_TRANSIENT = {
    EvidenceLevel.PROBED.value,
    EvidenceLevel.LIVE.value,
    EvidenceLevel.BLOCKED.value,
    EvidenceLevel.BROKEN.value,
}
_ORDER = {"UNKNOWN": 0, "CODE": 1, "CONFIGURED": 2, "PROBED": 3, "LIVE": 4}
_FAILURES = {"BLOCKED", "BROKEN", "DISABLED", "LEGACY"}


@contextmanager
def _registry_file_lock(path: str, timeout: float = 2.0):
    """Take a bounded cross-process lock for registry read/merge/write."""
    lock_path = f"{path}.lock"
    stream = open(lock_path, "a+b")
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    deadline = time.monotonic() + max(0.1, timeout)
    acquired = False
    try:
        while not acquired:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for status-registry file lock")
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()


def _pid_is_running(pid) -> bool:
    """Return whether *pid* is alive without signalling it.

    ``os.kill(pid, 0)`` is the conventional POSIX existence probe, but on
    Windows ``os.kill`` is implemented with console events/TerminateProcess
    semantics and is not a safe liveness probe for the current process.  Use
    the Win32 process API there so status freshness checks can never interrupt
    the pytest/JARVIS process they are inspecting.
    """
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL

            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)

        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _cap(name, category, files, *, actions=(), dependencies=(), optional_dependencies=()):
    return {
        "name": name, "category": category, "implemented_by": list(files),
        "actions": list(actions), "dependencies": list(dependencies),
        "optional_dependencies": list(optional_dependencies),
    }


# Dependencies prefixed with ``cap:`` reference another capability. All other
# dependency names reference subsystem evidence in this same registry.
CAPABILITY_DEFINITIONS = {
    # Provider/model capabilities carry direct granular evidence. A failure of
    # one NVIDIA model must not demote another model through a coarse provider.
    "NVIDIA_NORMAL": _cap("NVIDIA normal conversation", "ai", ["brain.py", "planner.py"]),
    "NVIDIA_REASONING": _cap("NVIDIA reasoning", "ai", ["brain.py", "planner.py"]),
    "GEMINI_FALLBACK": _cap("Gemini fallback", "ai", ["brain.py"]),
    "OLLAMA_LOCAL": _cap("Ollama local generative AI", "ai", ["brain.py"]),
    "OLLAMA_VISION": _cap("Ministral local visual understanding", "vision", ["brain.py", "vision.py"], optional_dependencies=["OLLAMA_MODEL_JARVIS_MINISTRAL_3B"]),
    "GROQ_LEGACY": _cap("Groq legacy provider", "ai", ["brain.py"], dependencies=["GROQ"]),
    "OPENROUTER_LEGACY": _cap("OpenRouter legacy text provider", "ai", ["brain.py"], dependencies=["OPENROUTER"]),
    "PLANNER": _cap("Intent planner", "system", ["planner.py", "core.py"]),
    "EXECUTOR": _cap("Action executor", "system", ["executor.py"]),
    "OBSERVER": _cap("Runtime observer", "system", ["observer.py"], dependencies=["OBSERVER"]),
    "MEMORY": _cap("Persistent memory", "system", ["memory.py"], actions=["remember", "recall"], dependencies=["MEMORY"]),
    "PERSONALITY": _cap("Personality retrieval", "system", ["personality.py", "planner.py"], dependencies=["PERSONALITY"]),
    "REMINDERS": _cap("Reminders and timers", "system", ["tasks.py", "executor.py"], actions=["set_reminder", "list_reminders"], dependencies=["TASKS"]),
    "TASK_QUEUE": _cap("Async task queue", "system", ["task_queue.py"], dependencies=["TASK_QUEUE"]),
    "APP_CONTROL": _cap("Windows application control", "system", ["executor.py", "observer.py"], actions=["open_app", "close_app"]),
    "SYSTEM_CONTROL": _cap("Windows and input control", "system", ["executor.py"], actions=["system_info", "system_status", "media", "scroll", "click", "type_text", "voice_type", "lock_pc", "shutdown_pc", "restart_pc"]),
    "MICROPHONE": _cap("Microphone capture", "voice", ["listener.py"], dependencies=["VOICE_STT"]),
    "STT": _cap("Speech transcription", "voice", ["listener.py", "jarvis.py"], dependencies=["VOICE_STT"]),
    "TTS": _cap("Speech synthesis", "voice", ["jarvis.py", "jarvis_tts.py"], dependencies=["VOICE_TTS"]),
    "SPEAKER_VERIFICATION": _cap("Speaker verification", "voice", ["listener.py"], dependencies=["VOICE_STT"]),
    "WAKE_SYSTEM": _cap("Wake system", "voice", ["listener.py", "jarvis.py"], dependencies=["VOICE_STT"]),
    "SCREEN_CAPTURE": _cap("Screen capture", "vision", ["vision.py"], dependencies=["VISION"]),
    "GEMINI_VISION": _cap("Gemini cloud visual understanding", "vision", ["vision.py", "executor.py"], dependencies=["GEMINI"], optional_dependencies=["cap:SCREEN_CAPTURE"]),
    "VISION_ROUTER": _cap("Local-first visual understanding router", "vision", ["vision.py", "brain.py"], actions=["screenshot_describe", "analyze_image_file", "capture_webcam"], optional_dependencies=["cap:OLLAMA_VISION", "cap:GEMINI_VISION"]),
    "IMAGE_INPUT": _cap("Planner image input", "vision", ["planner.py", "vision.py"], dependencies=["cap:VISION_ROUTER"]),
    "OCR": _cap("Local OCR", "vision", ["ocr_runtime.py", "vision.py", "file_processor.py"], actions=["read_screen", "read_image_text"], dependencies=["TESSERACT_OCR"], optional_dependencies=["cap:SCREEN_CAPTURE"]),
    "WEBCAM": _cap("Webcam capture", "vision", ["vision.py"], dependencies=["CAMERA"]),
    "FACE_RECOGNITION": _cap("Face recognition and presence", "vision", ["face_recognition_module.py", "jarvis.py"], dependencies=["cap:WEBCAM", "FACE_RECOGNITION"]),
    "WEB_SEARCH": _cap("Web search", "content", ["executor.py"], actions=["web_search"]),
    "ARTICLE_SUMMARY": _cap("Article summarization", "content", ["executor.py"], optional_dependencies=["cap:OLLAMA_LOCAL", "cap:NVIDIA_NORMAL", "cap:GEMINI_FALLBACK"]),
    "YOUTUBE_SUMMARY": _cap("YouTube summarization", "content", ["executor.py"], optional_dependencies=["cap:OLLAMA_LOCAL", "cap:NVIDIA_NORMAL", "cap:GEMINI_FALLBACK"]),
    "URL_SUMMARY": _cap("URL article/YouTube summarization", "content", ["executor.py"], actions=["summarize_url"], optional_dependencies=["cap:ARTICLE_SUMMARY", "cap:YOUTUBE_SUMMARY"]),
    "FILE_TEXT": _cap("Text and code file processing", "files", ["file_processor.py"], actions=["process_file", "list_uploaded_files"], dependencies=["FILE_PROCESSOR", "FILE_TEXT_PARSER"]),
    "FILE_PDF": _cap("PDF embedded-text processing", "files", ["file_processor.py"], dependencies=["FILE_PROCESSOR", "FILE_PDF_PARSER"]),
    "FILE_DOCX": _cap("DOCX processing", "files", ["file_processor.py"], dependencies=["FILE_PROCESSOR", "FILE_DOCX_PARSER"]),
    "FILE_XLSX": _cap("XLSX processing", "files", ["file_processor.py"], dependencies=["FILE_PROCESSOR", "FILE_XLSX_PARSER"]),
    "FILE_IMAGE_OCR": _cap("Image OCR processing", "files", ["file_processor.py", "ocr_runtime.py"], dependencies=["FILE_PROCESSOR", "FILE_IMAGE_PARSER", "TESSERACT_OCR"]),
    "FILE_MEDIA": _cap("Audio/video processing", "files", ["file_processor.py"], dependencies=["FILE_PROCESSOR"]),
    "WEATHER": _cap("Weather", "integration", ["executor.py"], actions=["weather"]),
    "NEWS": _cap("News", "integration", ["executor.py"], actions=["news"]),
    "SPOTIFY": _cap("Spotify playback control", "integration", ["executor.py"], actions=["spotify_play", "spotify_control"], dependencies=["SPOTIFY"]),
    "WHATSAPP_SEND": _cap("WhatsApp send", "integration", ["executor.py"], actions=["send_whatsapp"], dependencies=["WHATSAPP_SEND"]),
    "WHATSAPP_READ": _cap("WhatsApp read", "integration", ["whatsapp_fetcher.py", "executor.py"], actions=["whatsapp_read", "whatsapp_download", "whatsapp_timetable_update"], dependencies=["UI_AUTOMATION", "cap:OCR"]),
    "GMAIL": _cap("Gmail send", "integration", ["executor.py"], actions=["send_email"], dependencies=["EMAIL"]),
    "CALENDAR": _cap("Google Calendar", "integration", ["executor.py"], actions=["calendar_today", "calendar_add"], dependencies=["CALENDAR"]),
    "OBLIGATIONS": _cap("Obligation tracking", "system", ["obligations.py", "executor.py"], actions=["add_obligation", "query_obligations", "mark_obligation_done"], dependencies=["TASKS"]),
    "PORTAL_SCAN": _cap("Academic portal scan", "integration", ["portal_scan.py", "executor.py"], actions=["portal_scan"], dependencies=["cap:SCREEN_CAPTURE", "cap:OCR"]),
    "PLUGINS": _cap("Plugin architecture", "development", ["plugin_loader.py"], dependencies=["PLUGINS"]),
    "DEV_AGENT": _cap("Development agent", "development", ["dev_agent.py", "executor.py"], actions=["dev_inspect", "dev_test", "dev_search", "dev_propose", "dev_status"], dependencies=["DEV_AGENT"]),
    "EVOLVER": _cap("Evolver proposal system", "development", ["evolver.py"], dependencies=["EVOLVER"]),
    "SELF_AWARENESS": _cap("Self-awareness and code scan", "development", ["self_awareness.py", "self_model.py"], actions=["self_scan", "self_capabilities", "self_changes"]),
    "PROACTIVE": _cap("Proactive scheduler", "development", ["proactive_scheduler.py", "jarvis.py"], dependencies=["PROACTIVE_SCHEDULER"]),
    "RUNTIME_SSE": _cap("Runtime SSE bridge", "ui", ["runtime_visuals.py", "jarvis.py"], dependencies=["RUNTIME_SSE"]),
    "WPF_UI": _cap("Native WPF UI", "ui", ["desktop/Jarvis.Desktop.Codex"], dependencies=["WPF_UI"], optional_dependencies=["cap:RUNTIME_SSE"]),
    "BROWSER_UI": _cap("Browser UI", "ui", ["server.py", "ui/index.html"], dependencies=["BROWSER_UI"]),
    "IMAGE_GENERATION": _cap("NVIDIA Creative Studio image generation", "generation", ["creative_studio/studio.py", "creative_agent.py", "executor.py"], actions=["generate_image", "cancel_generation"]),
    "VIDEO_GENERATION": _cap("NVIDIA Creative Studio image-to-video generation", "generation", ["creative_studio/studio.py", "creative_agent.py", "executor.py"], actions=["animate_latest_image"]),
    "CREDENTIAL_VAULT": _cap("Credential vault", "security", ["credential_vault.py", "executor.py"], actions=["save_login", "list_logins", "delete_login"]),
    "APP_INSTALLATION": _cap("App installation and login", "system", ["app_installer.py", "login_orchestrator.py"], actions=["install_app", "install_and_login", "open_and_login"]),
    "DYNAMIC_EXECUTOR": _cap("Dynamic sandboxed code execution", "system", ["dynamic_executor.py"], actions=["execute_python_code", "execute_shell_command"]),
    "GUI_AGENT": _cap("Vision-guided GUI automation", "automation", ["gui_agent.py"], actions=["click_element", "type_into", "hover_element"], dependencies=["cap:VISION_ROUTER"], optional_dependencies=["UI_AUTOMATION"]),
    "COGNITIVE_GRAPH": _cap("Episodic and semantic knowledge graph", "memory", ["cognitive_graph.py"], actions=["add_triple", "query_triples", "get_entity_relations", "find_connections"]),
    "MEMORY_CONSOLIDATOR": _cap("Sleep cycle memory consolidation", "memory", ["memory_consolidator.py"], actions=["consolidate_recent_memory", "run_sleep_cycle"], dependencies=["cap:COGNITIVE_GRAPH"]),
    "PROACTIVE_DAEMON": _cap("Context-aware proactive interruption engine", "system", ["proactive_daemon.py"], dependencies=["PROACTIVE_SCHEDULER"]),
    "DELIBERATION_ENGINE": _cap("Adversarial multi-persona deliberation engine", "reasoning", ["deliberation.py"], actions=["deliberate", "propose_plan", "critique_plan", "synthesize_decision"]),
    "PREFLIGHT_SIMULATOR": _cap("Counterfactual pre-flight sandbox simulator", "system", ["preflight_simulator.py"], actions=["simulate_execution", "dry_run_code"], dependencies=["cap:DYNAMIC_EXECUTOR"]),
    "EPISTEMIC_EVALUATOR": _cap("Epistemic uncertainty calibration", "reasoning", ["epistemic_evaluator.py"], actions=["evaluate_uncertainty", "sample_variations"]),
    "CURIOSITY_DAEMON": _cap("Autonomous curiosity and diagnostic synthesis", "system", ["curiosity_daemon.py"], dependencies=["cap:COGNITIVE_GRAPH"]),
    "SKILL_SYNTHESIZER": _cap("Autonomous procedural skill synthesis", "development", ["skill_synthesizer.py"], actions=["synthesize_and_execute_skill", "synthesize_skill"], dependencies=["cap:PREFLIGHT_SIMULATOR", "cap:DYNAMIC_EXECUTOR"]),
    "SELF_INTROSPECTION": _cap("Codebase AST structural introspection", "reasoning", ["self_introspection.py"], actions=["get_codebase_context", "index_codebase"]),
    "AUTOBIOGRAPHY": _cap("Autobiographical evolution and git memory", "memory", ["autobiography.py"], actions=["get_autobiographical_summary", "get_evolution_milestones"]),
    "GOAL_MANAGER": _cap("Hierarchical long-horizon goal management", "planning", ["goal_manager.py"], actions=["create_goal", "decompose_goal", "update_milestone_status", "replan_goal"]),
    "FOVEATED_VISION": _cap("Foveated saccadic visual grounding", "vision", ["foveated_vision.py"], actions=["saccadic_crop_and_ground", "foveated_locate_element"], dependencies=["cap:VISION_ROUTER"]),
    "CAUSAL_ENGINE": _cap("Causal state transition modeling", "reasoning", ["causal_engine.py"], actions=["predict_state_transition", "verify_causal_transition"]),
    "EXPERIENCE_DISTILLER": _cap("Experience distillation for offline fine-tuning", "learning", ["experience_distiller.py"], actions=["distill_execution_sample", "export_dataset"]),
}


def _is_test_artifact(name):
    upper = str(name).strip().upper()
    return upper in _TEST_ARTIFACT_NAMES or upper.startswith("TEST_")


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _evidence(value):
    if isinstance(value, EvidenceLevel):
        return value
    try:
        return EvidenceLevel(str(value).strip().upper())
    except ValueError:
        return EvidenceLevel.UNKNOWN


def evidence_to_legacy_state(value):
    level = _evidence(value)
    if level in (EvidenceLevel.PROBED, EvidenceLevel.LIVE):
        return SubsystemState.READY.value
    if level == EvidenceLevel.BLOCKED:
        return SubsystemState.OFFLINE.value
    if level == EvidenceLevel.BROKEN:
        return SubsystemState.DEGRADED.value
    if level in (EvidenceLevel.DISABLED, EvidenceLevel.LEGACY):
        return SubsystemState.DISABLED.value
    return SubsystemState.UNKNOWN.value


def legacy_state_to_evidence(state, detail=""):
    raw = state.value if isinstance(state, SubsystemState) else str(state).strip().upper()
    if raw == "READY":
        return EvidenceLevel.PROBED
    if raw in ("RUNNING", "LIVE"):
        return EvidenceLevel.LIVE
    if raw == "OFFLINE":
        return EvidenceLevel.BLOCKED
    if raw == "DEGRADED":
        return EvidenceLevel.BROKEN
    if raw == "DISABLED":
        return EvidenceLevel.LEGACY if str(detail).strip().upper().startswith("LEGACY") else EvidenceLevel.DISABLED
    return EvidenceLevel.UNKNOWN


class RuntimeStatus:
    """Thread-safe canonical truth registry with legacy-compatible reads."""

    def __init__(self, path=_REGISTRY_FILE, *, session_id=None, pid=None,
                 wall_clock=time.time, default_ttl=300.0):
        self._path = path
        self._lock = threading.RLock()
        self._wall_clock = wall_clock
        self.default_ttl = float(default_ttl)
        self.session_id = session_id or os.getenv("JARVIS_SESSION_ID") or uuid.uuid4().hex
        self.pid = int(os.getpid() if pid is None else pid)
        self._data: Dict[str, dict] = {}
        self._capability_evidence: Dict[str, dict] = {}
        self._history: Dict[str, dict] = {}
        self._load()
        self._ensure_known_subsystems()

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as stream:
                raw = json.load(stream)
            if not isinstance(raw, dict):
                return
            caps = raw.get("_capabilities", {})
            history = raw.get("_history", {})
            if isinstance(caps, dict):
                self._capability_evidence = {str(k).upper(): dict(v) for k, v in caps.items() if isinstance(v, dict)}
            if isinstance(history, dict):
                self._history = {str(k): dict(v) for k, v in history.items() if isinstance(v, dict)}
            self._data = {str(k).upper(): dict(v) for k, v in raw.items()
                          if not str(k).startswith("_") and isinstance(v, dict) and not _is_test_artifact(k)}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            print(f"[DEBUG][status_registry] Load failed, starting fresh: {exc}")

    def _serialized(self):
        result = {name: dict(value) for name, value in self._data.items()}
        result["_meta"] = {"schema_version": 2, "writer_session_id": self.session_id,
                           "writer_pid": self.pid, "written_at": _now_iso()}
        result["_capabilities"] = {name: dict(value) for name, value in self._capability_evidence.items()}
        if self._history:
            result["_history"] = {name: dict(value) for name, value in self._history.items()}
        return result

    def _save(self, *, subsystems=None, capabilities=None):
        tmp = f"{self._path}.{self.pid}.{threading.get_ident()}.tmp"
        try:
            with _registry_file_lock(self._path):
                existing = {}
                if os.path.exists(self._path):
                    try:
                        with open(self._path, "r", encoding="utf-8") as source:
                            loaded = json.load(source)
                        if isinstance(loaded, dict):
                            existing = loaded
                    except (OSError, json.JSONDecodeError, TypeError):
                        existing = {}

                if subsystems is None and capabilities is None:
                    payload = self._serialized()
                else:
                    payload = dict(existing)
                    for name in subsystems or ():
                        payload[name] = dict(self._data[name])
                    cap_payload = dict(payload.get("_capabilities", {}))
                    for name in capabilities or ():
                        cap_payload[name] = dict(self._capability_evidence[name])
                    payload["_capabilities"] = cap_payload
                    history = dict(payload.get("_history", {}))
                    history.update({name: dict(value) for name, value in self._history.items()})
                    if history:
                        payload["_history"] = history
                    payload["_meta"] = {"schema_version": 2, "writer_session_id": self.session_id,
                                        "writer_pid": self.pid, "written_at": _now_iso()}

                with open(tmp, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, indent=2, ensure_ascii=False, default=str)
                os.replace(tmp, self._path)
        except (OSError, TimeoutError) as exc:
            print(f"[DEBUG][status_registry] Save failed: {exc}")
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    def _make_record(self, evidence, detail, source, ttl=None):
        level = _evidence(evidence)
        now = float(self._wall_clock())
        effective_ttl = self.default_ttl if level.value in _TRANSIENT and ttl is None else ttl
        return {
            "state": evidence_to_legacy_state(level), "evidence": level.value,
            "detail": str(detail).strip(), "evidence_source": str(source).strip() or "unspecified",
            "last_updated": _now_iso(),
            "last_probe": now if level.value in _TRANSIENT else None,
            "session_id": self.session_id if level.value in _TRANSIENT else None,
            "pid": self.pid if level.value in _TRANSIENT else None,
            "expires_at": now + float(effective_ttl) if level.value in _TRANSIENT and effective_ttl is not None else None,
        }

    def _ensure_known_subsystems(self):
        changed = []
        for name in KNOWN_SUBSYSTEMS:
            if name not in self._data:
                self._data[name] = self._make_record(EvidenceLevel.UNKNOWN, "Not yet evidenced", "registry bootstrap")
                changed.append(name)
        if changed:
            self._save(subsystems=changed)

    def _effective_record(self, raw):
        if raw is None:
            return None
        record = dict(raw)
        original = _evidence(record.get("evidence") or legacy_state_to_evidence(record.get("state", "UNKNOWN"), record.get("detail", ""))).value
        record.setdefault("evidence", original)
        record.setdefault("evidence_source", "legacy persisted status")
        record.setdefault("session_id", None)
        record.setdefault("pid", None)
        record.setdefault("expires_at", None)
        stale_reason = ""
        if original in _TRANSIENT:
            if record.get("session_id") != self.session_id or record.get("pid") != self.pid:
                stale_reason = "belongs to a different runtime session/process"
            elif record.get("expires_at") is not None and float(self._wall_clock()) >= float(record["expires_at"]):
                stale_reason = "evidence TTL expired"
        if stale_reason:
            record.update({"state": "UNKNOWN", "evidence": "UNKNOWN",
                           "detail": f"Stale historical {original}: {record.get('detail','')} ({stale_reason})",
                           "current": False, "stale": True, "historical_evidence": original})
        else:
            record.update({"state": evidence_to_legacy_state(original), "current": True,
                           "stale": False, "historical_evidence": None})
        return record

    def set_evidence(self, name, evidence, detail="", *, source="runtime", ttl=None):
        clean = str(name).strip().upper()
        with self._lock:
            if clean in self._data:
                self._history[f"subsystem:{clean}"] = dict(self._data[clean])
            self._data[clean] = self._make_record(evidence, detail, source, ttl)
            if not (os.getenv("TESTING") and clean not in KNOWN_SUBSYSTEMS):
                self._save(subsystems=(clean,))

    record_evidence = set_evidence

    def set_status(self, name, state, detail=""):
        """Compatibility write. READY means PROBED, never LIVE."""
        self.set_evidence(name, legacy_state_to_evidence(state, detail), detail,
                          source="legacy set_status adapter")

    def get_status(self, name):
        with self._lock:
            return self._effective_record(self._data.get(str(name).strip().upper()))

    def get_all(self):
        with self._lock:
            return {name: self._effective_record(value) for name, value in self._data.items()}

    def get_all_external(self):
        """Read persisted evidence for a separate UI process.

        Foreign transient evidence is current only while its originating PID
        is alive and its TTL has not expired.
        """
        with self._lock:
            self._load()
            result = {}
            now = float(self._wall_clock())
            for name, raw in self._data.items():
                record = dict(raw)
                original = _evidence(record.get("evidence") or legacy_state_to_evidence(
                    record.get("state", "UNKNOWN"), record.get("detail", ""))).value
                if original in _TRANSIENT:
                    expires = record.get("expires_at")
                    live_origin = _pid_is_running(record.get("pid"))
                    unexpired = expires is None or now < float(expires)
                    if live_origin and unexpired:
                        record.update({"state": evidence_to_legacy_state(original),
                                       "evidence": original, "current": True,
                                       "stale": False, "historical_evidence": None})
                        result[name] = record
                        continue
                result[name] = self._effective_record(raw)
            return result

    def is_ready(self, name):
        info = self.get_status(name)
        return bool(info and info["evidence"] in ("PROBED", "LIVE"))

    def is_available(self, name):
        info = self.get_status(name)
        return bool(info and info["evidence"] in ("CODE", "CONFIGURED", "PROBED", "LIVE"))

    def set_capability_evidence(self, capability_id, evidence, detail="", *, source="runtime operation", ttl=None):
        cap_id = str(capability_id).strip().upper()
        if cap_id not in CAPABILITY_DEFINITIONS:
            raise KeyError(f"Unknown capability: {cap_id}")
        with self._lock:
            if cap_id in self._capability_evidence:
                self._history[f"capability:{cap_id}"] = dict(self._capability_evidence[cap_id])
            self._capability_evidence[cap_id] = self._make_record(evidence, detail, source, ttl)
            self._save(capabilities=(cap_id,))

    record_capability_evidence = set_capability_evidence

    def _dependency_record(self, dependency, seen):
        if dependency.startswith("cap:"):
            return self.get_capability(dependency[4:], _seen=seen)
        return self.get_status(dependency) or {"evidence": "UNKNOWN", "detail": "No evidence", "current": False}

    @staticmethod
    def _combine(base, dependency_records):
        evidence = base.get("evidence", "CODE")
        detail = base.get("detail", "Implementation is catalogued")
        for dependency, record in dependency_records:
            dep_evidence = record.get("evidence", "UNKNOWN")
            if dep_evidence in _FAILURES:
                return dep_evidence, f"Required dependency {dependency} is {dep_evidence}: {record.get('detail','')}"
            if evidence not in _FAILURES and _ORDER.get(dep_evidence, 0) < _ORDER.get(evidence, 0):
                evidence = dep_evidence
                detail = f"Limited by required dependency {dependency}: {record.get('detail',dep_evidence)}"
        return evidence, detail

    def get_capability(self, capability_id, *, _seen=None):
        cap_id = str(capability_id).strip().upper()
        definition = CAPABILITY_DEFINITIONS.get(cap_id)
        if not definition:
            return None
        seen = set(_seen or ())
        if cap_id in seen:
            return {"id": cap_id, **definition, "evidence": "BROKEN", "state": "DEGRADED", "detail": "Capability dependency cycle"}
        seen.add(cap_id)
        with self._lock:
            explicit = self._effective_record(self._capability_evidence.get(cap_id))

        # Transient capability evidence belongs to the process/session that
        # produced it. Once it becomes stale, it must not hide the static CODE
        # evidence for an implemented capability; otherwise a previous runtime
        # can permanently block an action before it gets a chance to re-probe.
        if explicit and explicit.get("stale"):
            explicit = None

        base = explicit or {"evidence": "CODE", "detail": "Implementation is present; no current operation evidence",
                            "evidence_source": "canonical capability catalog", "current": True,
                            "stale": False, "last_updated": None, "last_probe": None,
                            "session_id": None, "pid": None, "expires_at": None}
        mandatory = [(dep, self._dependency_record(dep, seen)) for dep in definition["dependencies"]]
        effective, detail = self._combine(base, mandatory)
        optional = [(dep, self._dependency_record(dep, seen)) for dep in definition["optional_dependencies"]]
        return {"id": cap_id, **definition, **base, "evidence": effective,
                "state": evidence_to_legacy_state(effective), "status": effective,
                "detail": detail, "status_detail": detail,
                "dependency_evidence": {d: r.get("evidence", "UNKNOWN") for d, r in mandatory},
                "optional_dependency_evidence": {d: r.get("evidence", "UNKNOWN") for d, r in optional}}

    def get_capabilities(self):
        return [self.get_capability(cap_id) for cap_id in CAPABILITY_DEFINITIONS]

    def get_capability_by_action(self, action_name):
        clean = str(action_name).strip().lower()
        for cap_id, definition in CAPABILITY_DEFINITIONS.items():
            if clean in definition["actions"]:
                return cap_id
        return None

    def blocked_capabilities(self):
        return [cap for cap in self.get_capabilities() if cap["evidence"] in _FAILURES or cap["evidence"] == "UNKNOWN"]

    def capability_snapshot(self):
        return {cap["id"]: cap for cap in self.get_capabilities()}


_registry_instance: Optional[RuntimeStatus] = None
_instance_lock = threading.Lock()


def get_registry():
    global _registry_instance
    if _registry_instance is None:
        with _instance_lock:
            if _registry_instance is None:
                _registry_instance = RuntimeStatus()
    return _registry_instance


def get_capability_registry():
    return get_registry()


def _print_report():
    registry = get_registry()
    statuses = registry.get_all()
    ordered = [name for name in KNOWN_SUBSYSTEMS if name in statuses]
    extras = sorted(name for name in statuses if name not in KNOWN_SUBSYSTEMS)
    print("\n" + "=" * 126)
    print("  J.A.R.V.I.S — Current-Session Evidence Report")
    print("=" * 126)
    print(f"{'SUBSYSTEM':<22} {'EVIDENCE':<12} {'LEGACY':<10} {'CURRENT':<8} {'SOURCE':<27} DETAIL")
    print("-" * 126)
    for name in ordered + extras:
        info = statuses[name]
        print(f"{name:<22} {info.get('evidence','UNKNOWN'):<12} {info.get('state','UNKNOWN'):<10} "
              f"{str(info.get('current',False)):<8} {str(info.get('evidence_source',''))[:27]:<27} {str(info.get('detail',''))[:55]}")
    print("=" * 126)
    counts = {}
    for cap in registry.get_capabilities():
        counts[cap["evidence"]] = counts.get(cap["evidence"], 0) + 1
    print(f"Session {registry.session_id[:12]} | PID {registry.pid} | Capabilities: {len(CAPABILITY_DEFINITIONS)} | Evidence: {counts}")
    print("Bootability and full capability acceptance are separate verdicts.\n")


if __name__ == "__main__":
    _print_report()
