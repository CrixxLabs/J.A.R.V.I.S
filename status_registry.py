# status_registry.py
"""
J.A.R.V.I.S — Runtime Status Registry
======================================
Central source of truth for subsystem health.
States: READY | OFFLINE | DEGRADED | DISABLED | UNKNOWN

Thread-safe. Auto-persists to status_registry.json on every mutation.
Standalone — no hard dependencies on other JARVIS modules.
"""

import json
import os
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Path & Baseline Subsystems
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_REGISTRY_FILE = os.path.join(BASE_DIR, "status_registry.json")

# Canonical baseline subsystems (Phase 1 specification)
KNOWN_SUBSYSTEMS = [
    "VOICE_STT",
    "VOICE_TTS",
    "CAMERA",
    "FACE_RECOGNITION",
    "OLLAMA",
    "GROQ",
    "OPENROUTER",
    "GEMINI",
    "TESSERACT_OCR",
    "MEMORY",
    "TASKS",
    "PLUGINS",
    "WHATSAPP_SEND",
    "SPOTIFY",
    "EMAIL",
    "CALENDAR",
    "FLASK_UI",
]


# ---------------------------------------------------------------------------
# Subsystem States
# ---------------------------------------------------------------------------
class SubsystemState(str, Enum):
    """Valid lifecycle states for any tracked subsystem."""
    READY    = "READY"
    OFFLINE  = "OFFLINE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    UNKNOWN  = "UNKNOWN"

    def __str__(self):
        return self.value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.value.upper() == other.upper()
        if isinstance(other, SubsystemState):
            return self.value == other.value
        return False

    def __hash__(self):
        return hash(self.value)


# ---------------------------------------------------------------------------
# Registry Implementation
# ---------------------------------------------------------------------------
class RuntimeStatus:
    """
    Thread-safe, auto-persisting status registry.

    Usage:
        registry = RuntimeStatus()
        registry.set_status("OLLAMA", SubsystemState.READY, "llama3 responding in 45ms")
        print(registry.get_status("OLLAMA"))
    """

    def __init__(self, path: str = _REGISTRY_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._load()
        self._ensure_known_subsystems()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load registry from disk. Missing/corrupted file results in empty state."""
        if not os.path.exists(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data = raw
            else:
                self._data = {}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[DEBUG][status_registry] Load failed, starting fresh: {exc}")
            self._data = {}

    def _save(self) -> None:
        """Write current state to disk atomically. Caller must hold _lock."""
        tmp_path = f"{self._path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            print(f"[DEBUG][status_registry] Save failed: {exc}")

    def _ensure_known_subsystems(self) -> None:
        """Populate any missing baseline subsystems with UNKNOWN state."""
        changed = False
        now_ts = _now_iso()
        for name in KNOWN_SUBSYSTEMS:
            if name not in self._data:
                self._data[name] = {
                    "state": SubsystemState.UNKNOWN.value,
                    "detail": "Not yet probed",
                    "last_updated": now_ts,
                }
                changed = True
        if changed:
            self._save()

    # -- public API ---------------------------------------------------------

    def set_status(
        self,
        name: str,
        state: SubsystemState | str,
        detail: str = "",
    ) -> None:
        """
        Set or update the status of a subsystem.
        """
        clean_name = str(name).strip().upper()
        if isinstance(state, str):
            state_val = state.strip().upper()
            state_enum = SubsystemState(state_val) if state_val in SubsystemState.__members__ else SubsystemState.UNKNOWN
        else:
            state_enum = state

        now_ts = _now_iso()
        with self._lock:
            self._data[clean_name] = {
                "state": state_enum.value,
                "detail": str(detail).strip(),
                "last_updated": now_ts,
            }
            self._save()

    def get_status(self, name: str) -> Optional[dict]:
        """
        Return a copy of the status dict for a subsystem, or None if untracked.
        """
        clean_name = str(name).strip().upper()
        with self._lock:
            entry = self._data.get(clean_name)
            return dict(entry) if entry else None

    def get_all(self) -> Dict[str, dict]:
        """Return a shallow copy of all tracked subsystem statuses."""
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def is_ready(self, name: str) -> bool:
        """True only if state == READY."""
        entry = self.get_status(name)
        return entry is not None and entry.get("state") == SubsystemState.READY.value

    def is_available(self, name: str) -> bool:
        """True if READY or DEGRADED (usable with fallback/degraded performance)."""
        entry = self.get_status(name)
        if not entry:
            return False
        return entry.get("state") in (SubsystemState.READY.value, SubsystemState.DEGRADED.value)


# ---------------------------------------------------------------------------
# Helpers & Singleton
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_registry_instance: Optional[RuntimeStatus] = None
_instance_lock = threading.Lock()


def get_registry() -> RuntimeStatus:
    """Return the process-wide singleton registry instance."""
    global _registry_instance
    if _registry_instance is None:
        with _instance_lock:
            if _registry_instance is None:
                _registry_instance = RuntimeStatus()
    return _registry_instance


# ---------------------------------------------------------------------------
# Diagnostic CLI Formatter
# ---------------------------------------------------------------------------
def _print_report() -> None:
    reg = get_registry()
    all_status = reg.get_all()

    ordered = [name for name in KNOWN_SUBSYSTEMS if name in all_status]
    extras = sorted([name for name in all_status if name not in KNOWN_SUBSYSTEMS])
    full_list = ordered + extras

    col_name = 22
    col_state = 12
    col_detail = 42
    col_time = 24

    header = (
        f"{'SUBSYSTEM':<{col_name}} "
        f"{'STATE':<{col_state}} "
        f"{'DETAIL':<{col_detail}} "
        f"{'LAST UPDATED':<{col_time}}"
    )
    sep = "=" * len(header)

    print("\n" + sep)
    print("  J.A.R.V.I.S  —  Runtime Status Registry Report")
    print(sep)
    print(header)
    print("-" * len(header))

    colors = {
        "READY":    "\033[92m",  # Green
        "DEGRADED": "\033[93m",  # Yellow
        "OFFLINE":  "\033[91m",  # Red
        "DISABLED": "\033[90m",  # Grey
        "UNKNOWN":  "\033[96m",  # Cyan
    }
    reset = "\033[0m"

    for name in full_list:
        entry = all_status[name]
        state = entry.get("state", "UNKNOWN")
        detail = entry.get("detail", "")
        ts = entry.get("last_updated", "")

        if len(detail) > col_detail:
            detail = detail[:col_detail - 3] + "..."

        color = colors.get(state, "")
        print(
            f"{name:<{col_name}} "
            f"{color}{state:<{col_state}}{reset} "
            f"{detail:<{col_detail}} "
            f"{ts:<{col_time}}"
        )

    print(sep)
    print(f"  Total Subsystems: {len(full_list)} | Baseline: {len(KNOWN_SUBSYSTEMS)} | Dynamic/Extra: {len(extras)}")
    print(f"  Registry Storage: {_REGISTRY_FILE}")
    print(sep + "\n")


if __name__ == "__main__":
    _print_report()