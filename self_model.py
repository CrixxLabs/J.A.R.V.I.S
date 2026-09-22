"""Experience and natural-language adapter for the canonical truth registry.

Capability definitions and current truth live only in :mod:`status_registry`.
This module deliberately owns only historical action outcomes and presentation.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import status_registry
from status_registry import CAPABILITY_DEFINITIONS, EvidenceLevel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_EXPERIENCE_FILE = os.path.join(BASE_DIR, "self_model.json")

# Compatibility export: the exact canonical object, not a copied catalog.
CAPABILITIES = CAPABILITY_DEFINITIONS
_USABLE_FOR_ATTEMPT = {"CODE", "CONFIGURED", "PROBED", "LIVE", "BROKEN"}


class SelfCapabilityModel:
    """Add experience metrics and truthful explanations to the canonical model."""

    def __init__(self, path: str = _EXPERIENCE_FILE, registry=None):
        self._path = path
        self._registry = registry or status_registry.get_capability_registry()
        self._lock = threading.RLock()
        self._experience: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            self._experience = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError):
            self._experience = {}

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as stream:
                json.dump(self._experience, stream, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError as exc:
            print(f"[DEBUG][self_model] Save experience failed: {exc}")

    def _experience_entry(self, cap_id: str) -> dict:
        return self._experience.setdefault(cap_id, {
            "success_count": 0, "failure_count": 0, "last_attempt": None,
            "last_success": None, "last_failure": None, "last_error": None,
        })

    def get_capability_by_action(self, action_name: str) -> Optional[str]:
        return self._registry.get_capability_by_action(action_name)

    def get_capability(self, cap_id: str) -> Optional[dict]:
        capability = self._registry.get_capability(cap_id)
        if not capability:
            return None
        with self._lock:
            experience = dict(self._experience_entry(capability["id"]))
        result = dict(capability)
        result.setdefault("description", capability["name"])
        result.setdefault("fallbacks", [])
        result["verification"] = {
            "supported": capability["evidence"] in ("PROBED", "LIVE"),
            "method": capability.get("evidence_source", "No current verification"),
        }
        result.update(experience)
        return result

    def get_capabilities(self) -> List[dict]:
        return [self.get_capability(cap_id) for cap_id in CAPABILITIES]

    def get_available_capabilities(self) -> List[dict]:
        return [c for c in self.get_capabilities() if c["evidence"] in ("PROBED", "LIVE")]

    def get_unavailable_capabilities(self) -> List[dict]:
        return [c for c in self.get_capabilities() if c["evidence"] not in ("PROBED", "LIVE")]

    def can_do(self, action_name: str) -> Tuple[bool, str]:
        cap_id = self.get_capability_by_action(action_name)
        if not cap_id:
            return True, "Action has no capability gate"
        cap = self.get_capability(cap_id)
        if not cap:
            return False, "Capability definition is missing"
        if cap["evidence"] not in _USABLE_FOR_ATTEMPT:
            return False, f"{cap['name']} is {cap['evidence']}: {cap['status_detail']}"
        return True, f"{cap['evidence']}: {cap['status_detail']}"

    def explain_capability(self, cap_id: str) -> str:
        cap = self.get_capability(cap_id)
        if not cap:
            return f"I have no canonical capability named {cap_id}."
        return (f"{cap['name']}: {cap['evidence']}. {cap['status_detail']} "
                f"Evidence source: {cap.get('evidence_source', 'unspecified')}.")

    def record_outcome(self, action_name: str, success: bool,
                       error_message: Optional[str] = None) -> None:
        cap_id = self.get_capability_by_action(action_name)
        if not cap_id:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            entry = self._experience_entry(cap_id)
            entry["last_attempt"] = now
            if success:
                entry["success_count"] += 1
                entry["last_success"] = now
                entry["last_error"] = None
            else:
                entry["failure_count"] += 1
                entry["last_failure"] = now
                entry["last_error"] = str(error_message or "Operation failed")[:300]
            self._save()
        evidence = EvidenceLevel.LIVE if success else EvidenceLevel.BROKEN
        detail = ("Verified user operation succeeded" if success else
                  f"User operation failed: {str(error_message or 'unknown')[:180]}")
        self._registry.set_capability_evidence(
            cap_id, evidence, detail, source=f"executor outcome: {action_name}")

    def get_self_summary(self, compact: bool = True) -> str:
        caps = self.get_capabilities()
        if compact:
            groups = {}
            for cap in caps:
                groups.setdefault(cap["evidence"], []).append(cap["name"])
            return "; ".join(f"{level}: {', '.join(names[:6])}" for level, names in groups.items())
        lines = ["Current-session capability evidence (bootability is not full acceptance):"]
        lines.extend(f"- {cap['name']}: {cap['evidence']} — {cap['status_detail']}" for cap in caps)
        return "\n".join(lines)

    def answer_capability_question(self, text: str) -> Optional[str]:
        """Answer safety-critical self-truth questions without an LLM."""
        query = (text or "").lower()
        groups = None
        if "provider" in query or "model" in query or "llm" in query:
            groups = ["NVIDIA_NORMAL", "NVIDIA_REASONING", "GEMINI_FALLBACK", "OLLAMA_LOCAL", "GROQ_LEGACY", "OPENROUTER_LEGACY"]
        elif "fully offline" in query or "without internet" in query:
            local = self.get_capability("OLLAMA_LOCAL")
            return ("Deterministic local actions can run without the internet. "
                    f"Local generative AI is {local['evidence']}: {local['status_detail']}")
        elif "see" in query and ("screen" in query or "display" in query):
            groups = ["SCREEN_CAPTURE", "GEMINI_VISION"]
        elif "ocr" in query or "read the screen" in query:
            groups = ["OCR"]
        elif "spotify" in query:
            groups = ["SPOTIFY"]
        elif "calendar" in query:
            groups = ["CALENDAR"]
        elif "youtube" in query:
            groups = ["YOUTUBE_SUMMARY"]
        elif "blocked" in query or "unavailable" in query or "offline" in query:
            blocked = self._registry.blocked_capabilities()
            if not blocked:
                return "No capability is currently BLOCKED, BROKEN, DISABLED, LEGACY, or UNKNOWN."
            return "Currently not operationally evidenced: " + "; ".join(
                f"{cap['name']} ({cap['evidence']}: {cap['status_detail']})" for cap in blocked[:12])
        if not groups:
            return None
        caps = [self.get_capability(cap_id) for cap_id in groups]
        return "; ".join(f"{cap['name']}: {cap['evidence']} — {cap['status_detail']}" for cap in caps if cap)


_model_instance: Optional[SelfCapabilityModel] = None
_instance_lock = threading.Lock()


def get_model() -> SelfCapabilityModel:
    global _model_instance
    if _model_instance is None:
        with _instance_lock:
            if _model_instance is None:
                _model_instance = SelfCapabilityModel()
    return _model_instance
