# self_model.py — Central Self-Model Layer for J.A.R.V.I.S
# =======================================================================
# Manages structured capabilities, maps actions to implementation files,
# checks live health dependencies, and tracks user-visible action outcomes.
#
# Thread-safe. Auto-persists experience metrics to self_model.json.

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import status_registry
from status_registry import SubsystemState

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_EXPERIENCE_FILE = os.path.join(BASE_DIR, "self_model.json")

# ---------------------------------------------------------------------------
# Core Capability Catalog (User-Meaningful Capabilities)
# ---------------------------------------------------------------------------
CAPABILITIES = {
    "SPEECH_INPUT": {
        "name": "Speech Input (Listening)",
        "category": "voice",
        "description": "Capture microphone voice stream, calibrate ambient noise, and transcribe speech to text.",
        "implemented_by": ["listener.py", "jarvis.py"],
        "dependencies": ["VOICE_STT"],
        "actions": [],
        "fallbacks": [],
        "verification": "Real-time noise calibration and speech recognition engine checks."
    },
    "SPEECH_OUTPUT": {
        "name": "Speech Output (Speaking)",
        "category": "voice",
        "description": "Speak text naturally using local F5-TTS, cloud-based Edge-TTS, or SAPI fallback.",
        "implemented_by": ["jarvis.py", "jarvis_tts.py"],
        "dependencies": ["VOICE_TTS"],
        "actions": [],
        "fallbacks": [],
        "verification": "Temporary audio file generation and pygame mixer playback verification."
    },
    "APP_MANAGEMENT": {
        "name": "App Execution & Control",
        "category": "apps",
        "description": "Open and close local Windows applications by name.",
        "implemented_by": ["executor.py"],
        "dependencies": ["PLUGINS"],
        "actions": ["open_app", "close_app"],
        "fallbacks": [],
        "verification": "Foreground window title check and psutil process tree matching."
    },
    "SYSTEM_CONTROL": {
        "name": "System Control & Telemetry",
        "category": "system",
        "description": "Control system volume, lock the workstation, restart or shutdown the PC, and read resource metrics.",
        "implemented_by": ["executor.py", "observer.py", "system_monitor.py"],
        "dependencies": ["TASKS"],
        "actions": ["lock_pc", "shutdown_pc", "restart_pc", "system_info", "system_status", "media", "scroll", "click", "type_text", "voice_type"],
        "fallbacks": [],
        "verification": "Workstation lock commands or mouse and keyboard API simulation callbacks."
    },
    "WEB_SEARCH": {
        "name": "Web Search & Summarization",
        "category": "productivity",
        "description": "Search the web via DuckDuckGo and generate concise AI summaries.",
        "implemented_by": ["executor.py"],
        "dependencies": ["GROQ", "OLLAMA"],
        "actions": ["web_search"],
        "fallbacks": [],
        "verification": "DuckDuckGo HTML selector validation and HTTP status code verification."
    },
    "OCR_READ": {
        "name": "Optical Character Recognition (OCR)",
        "category": "vision",
        "description": "Extract readable text directly from active screen frames.",
        "implemented_by": ["executor.py", "vision.py"],
        "dependencies": ["TESSERACT_OCR"],
        "actions": ["read_screen"],
        "fallbacks": [],
        "verification": "Tesseract binary call checks and desktop resolution coordinate checks."
    },
    "SCREEN_UNDERSTANDING": {
        "name": "Screen AI Vision Analysis",
        "category": "vision",
        "description": "Capture screen layouts and generate deep visual analysis using cloud-based AI models.",
        "implemented_by": ["executor.py", "vision.py"],
        "dependencies": ["GEMINI"],
        "actions": ["screenshot_describe"],
        "fallbacks": ["OCR_READ"],
        "verification": "Active pixel capture checks and multimodal model endpoint responses."
    },
    "REMINDERS_SET": {
        "name": "Reminders & Timers",
        "category": "productivity",
        "description": "Set future speech alerts and manage task lists.",
        "implemented_by": ["tasks.py", "executor.py"],
        "dependencies": ["TASKS"],
        "actions": ["set_reminder", "list_reminders"],
        "fallbacks": [],
        "verification": "Task list size comparisons and scheduled threading callback loops."
    },
    "OBLIGATIONS_TRACK": {
        "name": "Obligation & Deadline Engine",
        "category": "productivity",
        "description": "Log, query, and manage exams, assignments, and academic deadlines.",
        "implemented_by": ["obligations.py", "executor.py"],
        "dependencies": ["TASKS"],
        "actions": ["add_obligation", "query_obligations", "mark_obligation_done"],
        "fallbacks": [],
        "verification": "JSON ledger mutation confirmations and deadline date sorting validation."
    },
    "PORTAL_SCAN": {
        "name": "Academic Portal Scanning",
        "category": "productivity",
        "description": "Read active browser portals to automatically extract and log deadlines.",
        "implemented_by": ["portal_scan.py", "executor.py"],
        "dependencies": ["TESSERACT_OCR"],
        "actions": ["portal_scan"],
        "fallbacks": [],
        "verification": "OCR regex match logs and obligation proposal structures."
    },
    "WHATSAPP_AUTOMATION": {
        "name": "WhatsApp Group Message Fetcher",
        "category": "communication",
        "description": "Read WhatsApp chat windows and parse timetables or class meeting links.",
        "implemented_by": ["whatsapp_fetcher.py", "executor.py"],
        "dependencies": ["TESSERACT_OCR"],
        "actions": ["whatsapp_read", "whatsapp_download", "whatsapp_timetable_update"],
        "fallbacks": [],
        "verification": "OCR window extraction logs and batch time regex captures."
    },
    "WHATSAPP_SEND": {
        "name": "Send WhatsApp Messages",
        "category": "communication",
        "description": "Automate sending WhatsApp messages via PyWhatKit.",
        "implemented_by": ["executor.py"],
        "dependencies": ["WHATSAPP_SEND"],
        "actions": ["send_whatsapp"],
        "fallbacks": [],
        "verification": "Browser interface activation and pywhatkit scheduling logs."
    },
    "SPOTIFY_CONTROL": {
        "name": "Spotify Playback Control",
        "category": "media",
        "description": "Search, play, skip, pause, and like Spotify music tracks.",
        "implemented_by": ["executor.py"],
        "dependencies": ["SPOTIFY"],
        "actions": ["spotify_play", "spotify_control"],
        "fallbacks": [],
        "verification": "Active playback device checks and Spotify Web API OAuth tokens."
    },
    "EMAIL_SEND": {
        "name": "Send Gmail Emails",
        "category": "communication",
        "description": "Draft and send plain text emails via Gmail SMTP.",
        "implemented_by": ["executor.py"],
        "dependencies": ["EMAIL"],
        "actions": ["send_email"],
        "fallbacks": [],
        "verification": "Gmail SMTP server TLS connection handshakes."
    },
    "CALENDAR_SYNC": {
        "name": "Google Calendar Integration",
        "category": "productivity",
        "description": "View and schedule calendar events in Google Calendar.",
        "implemented_by": ["executor.py"],
        "dependencies": ["CALENDAR"],
        "actions": ["calendar_today", "calendar_add"],
        "fallbacks": [],
        "verification": "OAuth flow validation and calendar resource updates."
    },
    "SECURE_VAULT": {
        "name": "Secure Windows Credential Vault",
        "category": "security",
        "description": "Store and retrieve application passwords securely using Windows DPAPI.",
        "implemented_by": ["credential_vault.py", "executor.py"],
        "dependencies": ["PLUGINS"],
        "actions": ["save_login", "list_logins", "delete_login"],
        "fallbacks": [],
        "verification": "DPAPI encrypt and decrypt byte-matching loops."
    },
    "APP_INSTALLATION": {
        "name": "Automated Software Provisioning",
        "category": "apps",
        "description": "Install software via Winget and run automated auto-login recipes.",
        "implemented_by": ["login_orchestrator.py", "executor.py", "auto_login_profiles.py"],
        "dependencies": ["PLUGINS"],
        "actions": ["install_app", "install_and_login", "open_and_login"],
        "fallbacks": [],
        "verification": "Winget download stream regex parse and window handle confirmation."
    },
    "IMAGE_GENERATION": {
        "name": "AI Image Generation",
        "category": "generation",
        "description": "Generate custom graphics from a descriptive text prompt.",
        "implemented_by": ["executor.py"],
        "dependencies": ["OPENROUTER"],
        "actions": ["generate_image"],
        "fallbacks": [],
        "verification": "Endpoint HTTP 200 payload checks."
    },
    "VIDEO_GENERATION": {
        "name": "AI Video Generation",
        "category": "generation",
        "description": "Generate videos using Kling-v3 via OpenRouter.",
        "implemented_by": ["executor.py"],
        "dependencies": ["OPENROUTER"],
        "actions": ["generate_video"],
        "fallbacks": [],
        "verification": "Kling endpoint response code and MP4 content download validations."
    },
    "CODE_SCANNING": {
        "name": "Codebase Scanning & Self-Introspection",
        "category": "self",
        "description": "Analyze existing files, extract functions, and track incremental updates since last boot.",
        "implemented_by": ["self_awareness.py", "executor.py"],
        "dependencies": ["MEMORY"],
        "actions": ["self_scan", "self_capabilities", "self_changes"],
        "fallbacks": [],
        "verification": "Abstract Syntax Tree parse validation and cache serialization checks."
    }
}


class SelfCapabilityModel:
    """Manages static, live, and experiential knowledge for Jarvis's capability catalog."""

    def __init__(self, path: str = _EXPERIENCE_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._experience: Dict[str, dict] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self._experience = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._experience = data
            else:
                self._experience = {}
        except Exception as exc:
            print(f"[DEBUG][self_model] Load failed, starting fresh: {exc}")
            self._experience = {}

    def _save(self) -> None:
        tmp_path = f"{self._path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._experience, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            print(f"[DEBUG][self_model] Save experience failed: {exc}")

    def _get_experience_entry(self, cap_id: str) -> dict:
        if cap_id not in self._experience:
            self._experience[cap_id] = {
                "success_count": 0,
                "failure_count": 0,
                "last_attempt": None,
                "last_success": None,
                "last_failure": None,
                "last_error": None
            }
        return self._experience[cap_id]

    # -- capability resolution & status mapping -----------------------------

    def get_capability_by_action(self, action_name: str) -> Optional[str]:
        """Resolve which capability ID corresponds to a specific executor action."""
        clean_act = str(action_name).strip().lower()
        for cap_id, definition in CAPABILITIES.items():
            if clean_act in definition["actions"]:
                return cap_id
        return None

    def _get_live_status(self, cap_id: str) -> Tuple[str, str]:
        """Evaluate status from status_registry.py based on dependencies."""
        definition = CAPABILITIES.get(cap_id)
        if not definition:
            return SubsystemState.UNKNOWN.value, "Capability definition missing"

        deps = definition.get("dependencies", [])
        if not deps:
            return SubsystemState.READY.value, "No critical hardware dependencies defined"

        reg = status_registry.get_registry()
        worst_state = SubsystemState.READY.value
        worst_detail = "All systems operational"

        # State ranking order (worst to best) — UNKNOWN is worse than READY
        state_ranks = {
            SubsystemState.DISABLED.value: 5,
            SubsystemState.OFFLINE.value:  4,
            SubsystemState.DEGRADED.value: 3,
            SubsystemState.UNKNOWN.value:  2,
            SubsystemState.READY.value:    1,
        }

        for dep in deps:
            info = reg.get_status(dep)
            if not info:
                # Dependency not in registry = UNKNOWN (treated as potentially problematic)
                state = SubsystemState.UNKNOWN.value
                detail = "not in registry"
            else:
                state = info.get("state", SubsystemState.UNKNOWN.value)
                detail = info.get("detail", "")

            rank_current = state_ranks.get(state, 2)  # default to UNKNOWN rank
            rank_worst = state_ranks.get(worst_state, 1)

            if rank_current > rank_worst:
                worst_state = state
                worst_detail = f"{dep} is {state.lower()}" + (f" ({detail})" if detail else "")

        return worst_state, worst_detail

    # -- Public API ---------------------------------------------------------

    def get_capability(self, cap_id: str) -> Optional[dict]:
        """Return full structured model representing a capability."""
        definition = CAPABILITIES.get(cap_id)
        if not definition:
            return None

        # Resolve dynamic state from status registry
        live_status, status_detail = self._get_live_status(cap_id)

        # Resolve dynamic experiential logs
        with self._lock:
            exp = dict(self._get_experience_entry(cap_id))

        # Compile completed capability representation
        return {
            "id": cap_id,
            "name": definition["name"],
            "category": definition["category"],
            "description": definition["description"],
            "status": live_status,
            "status_detail": status_detail,
            "implemented_by": definition["implemented_by"],
            "dependencies": definition["dependencies"],
            "actions": definition["actions"],
            "fallbacks": definition["fallbacks"],
            "verification": {
                "supported": bool(definition["verification"]),
                "method": definition["verification"]
            },
            "last_attempt": exp["last_attempt"],
            "last_success": exp["last_success"],
            "last_failure": exp["last_failure"],
            "success_count": exp["success_count"],
            "failure_count": exp["failure_count"],
            "last_error": exp["last_error"]
        }

    def get_capabilities(self) -> List[dict]:
        """Return list of all compiled capabilities."""
        return [self.get_capability(cap_id) for cap_id in CAPABILITIES if self.get_capability(cap_id)]

    def get_available_capabilities(self) -> List[dict]:
        """Return list of fully ready or degraded (but usable) capabilities."""
        return [c for c in self.get_capabilities() if c["status"] in (SubsystemState.READY.value, SubsystemState.DEGRADED.value)]

    def get_unavailable_capabilities(self) -> List[dict]:
        """Return list of offline, disabled, or unprobed capabilities."""
        return [c for c in self.get_capabilities() if c["status"] in (SubsystemState.OFFLINE.value, SubsystemState.DISABLED.value, SubsystemState.UNKNOWN.value)]

    def can_do(self, action_name: str) -> Tuple[bool, str]:
        """
        Check if Jarvis can perform a specific action right now.
        Returns (can_execute, human_readable_status_detail).
        """
        cap_id = self.get_capability_by_action(action_name)
        if not cap_id:
            # Actions not explicitly mapped in catalog default to pass-through
            return True, "Passthrough allowed"

        cap = self.get_capability(cap_id)
        if not cap:
            return False, "Capability definitions missing"

        state = cap["status"]
        if state in (SubsystemState.OFFLINE.value, SubsystemState.DISABLED.value):
            return False, f"Subsystem dependency failure: {cap['status_detail']}"

        return True, cap["status_detail"]

    def explain_capability(self, cap_id: str) -> str:
        """Construct an honest natural response explaining a capability's state."""
        cap = self.get_capability(cap_id)
        if not cap:
            return f"I don't have records for any capability matching '{cap_id}'."

        lines = [
            f"Ability: {cap['name']} ({cap['category']})",
            f"Description: {cap['description']}",
            f"Status: {cap['status']} — {cap['status_detail']}"
        ]

        if cap["success_count"] > 0 or cap["failure_count"] > 0:
            lines.append(
                f"Experience: Succeeded {cap['success_count']} times, failed {cap['failure_count']} times. "
                f"Last run: {cap['last_attempt']}."
            )
            if cap["last_error"]:
                lines.append(f"Last error encountered: {cap['last_error']}")

        return "\n".join(lines)

    def record_outcome(self, action_name: str, success: bool, error_message: Optional[str] = None) -> None:
        """Log outcome of a user-meaningful action outcome thread-safely."""
        cap_id = self.get_capability_by_action(action_name)
        if not cap_id:
            return  # Ignore trivial/untracked background activities

        now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._lock:
            entry = self._get_experience_entry(cap_id)
            entry["last_attempt"] = now_ts

            if success:
                entry["success_count"] += 1
                entry["last_success"] = now_ts
            else:
                entry["failure_count"] += 1
                entry["last_failure"] = now_ts
                entry["last_error"] = str(error_message).strip() if error_message else "Verification failed"

            self._save()

    def get_self_summary(self, compact: bool = True) -> str:
        """Generate a concise operational summary of Jarvis's abilities."""
        caps = self.get_capabilities()
        ready_caps = []
        degraded_caps = []
        failed_caps = []

        for c in caps:
            state = c["status"]
            name = c["name"]
            if state == SubsystemState.READY.value:
                ready_caps.append(name)
            elif state == SubsystemState.DEGRADED.value:
                degraded_caps.append(f"{name} ({c['status_detail']})")
            elif state in (SubsystemState.OFFLINE.value, SubsystemState.DISABLED.value):
                failed_caps.append(f"{name} (offline)")

        if compact:
            prompt_lines = [
                f"Abilities Online: {', '.join(ready_caps[:8])}...",
            ]
            if degraded_caps:
                prompt_lines.append(f"Degraded: {', '.join(degraded_caps[:3])}")
            if failed_caps:
                prompt_lines.append(f"Offline: {', '.join(failed_caps[:3])}")
            return " | ".join(prompt_lines)

        # Detailed breakdown
        lines = ["Jarvis Current Capability Model Summary:", ""]
        if ready_caps:
            lines.append("Operational Capabilities:")
            lines.extend(f"  ✓ {r}" for r in ready_caps)
        if degraded_caps:
            lines.append("\nDegraded Capabilities:")
            lines.extend(f"  ~ {d}" for d in degraded_caps)
        if failed_caps:
            lines.append("\nOffline/Unavailable Capabilities:")
            lines.extend(f"  ✗ {f}" for f in failed_caps)

        return "\n".join(lines)


# Initialize singleton instance
_model_instance: Optional[SelfCapabilityModel] = None
_instance_lock = threading.Lock()


def get_model() -> SelfCapabilityModel:
    global _model_instance
    if _model_instance is None:
        with _instance_lock:
            if _model_instance is None:
                _model_instance = SelfCapabilityModel()
    return _model_instance