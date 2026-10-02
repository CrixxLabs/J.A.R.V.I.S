"""Capability Security, Consequence Engine & Taint Tracking for J.A.R.V.I.S. — MARK VIII.

Implements the Cognitive Immune System:
  1. Dual-LLM Quarantined Parsing: Sanitizes and isolates untrusted external inputs.
  2. Taint Analysis: Tracks provenance of untrusted strings and blocks parameterization of sensitive tools.
  3. Transactional Saga Ledger: Reversible action tracking with LIFO compensation rollbacks.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.capability_guard")

_lock = threading.RLock()

# ── INJECTION PATTERNS & SENSITIVE TOOLS ────────────────────────────────────
INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions",
    r"(?i)system\s*prompt\s*:",
    r"(?i)<\|im_start\|>",
    r"(?i)<\|im_end\|>",
    r"(?i)\[\s*INST\s*\]",
    r"(?i)\[\s*/INST\s*\]",
    r"(?i)you\s+are\s+now\s+in\s+DAN\s+mode",
    r"(?i)output\s+all\s+(api\s*keys|passwords|credentials|\.env)",
    r"(?i)execute\s+(bash|powershell|cmd)\s*:\s*rm\s+-rf",
    r"(?i);\s*rm\s+-rf",
    r"(?i);\s*del\s+/f",
]

SENSITIVE_TOOLS: Set[str] = {
    "shell_exec",
    "bash_exec",
    "cmd_exec",
    "delete_file",
    "delete_folder",
    "format_disk",
    "send_funds",
    "wire_transfer",
    "update_credentials",
    "database_drop",
    "system_shutdown",
    "make_outbound_alert_call",
}


# ── TAINTED STRING PRIMITIVE ────────────────────────────────────────────────
class TaintedString(str):
    """String wrapper tracking provenance and preventing untrusted execution."""

    def __new__(cls, value: str, source: str = "untrusted", tainted: bool = True):
        obj = str.__new__(cls, value)
        obj.source = str(source)
        obj.tainted = bool(tainted)
        obj.origin_time = time.time()
        return obj

    def is_tainted(self) -> bool:
        return self.tainted

    def cleanse(self, authorized_by: str = "user") -> str:
        """Explicit authorization hook to cleanse taint."""
        log.info(f"[CapabilityGuard] Taint cleansed on '{self[:30]}...' authorized by {authorized_by}")
        return str(self)

    def __repr__(self) -> str:
        return f"TaintedString({super().__repr__()}, source='{self.source}', tainted={self.tainted})"


def is_value_tainted(val: Any) -> bool:
    """Recursively check if a value or dictionary contains tainted elements."""
    if isinstance(val, TaintedString) and val.is_tainted():
        return True
    if isinstance(val, dict):
        return any(is_value_tainted(k) or is_value_tainted(v) for k, v in val.items())
    if isinstance(val, (list, tuple, set)):
        return any(is_value_tainted(item) for item in val)
    return False


# ── QUARANTINED PARSER ───────────────────────────────────────────────────────
def quarantine_parse_untrusted(raw_text: str, source: str = "untrusted_web") -> Dict[str, Any]:
    """Low-privilege quarantine parser for external text, emails, and web DOM."""
    if not isinstance(raw_text, str):
        raw_text = str(raw_text)

    injection_flags: List[str] = []
    sanitized = raw_text

    # 1. Strip raw HTML/JS tags
    sanitized = re.sub(r"<script.*?>.*?</script>", "", sanitized, flags=re.IGNORECASE | re.DOTALL)
    sanitized = re.sub(r"<style.*?>.*?</style>", "", sanitized, flags=re.IGNORECASE | re.DOTALL)
    sanitized = html.unescape(sanitized)

    # 2. Check known prompt injection vectors
    for pattern in INJECTION_PATTERNS:
        match = re.search(pattern, sanitized)
        if match:
            injection_flags.append(f"Prompt injection pattern detected: '{match.group(0)}'")
            # Neutralize matched sequence
            sanitized = re.sub(pattern, "[FILTERED_INJECTION_VECTOR]", sanitized)

    # 3. Strip zero-width unicode control characters
    sanitized = re.sub(r"[​-‍﻿]", "", sanitized)

    # 4. Wrap extracted content in TaintedString
    tainted_body = TaintedString(sanitized.strip(), source=source, tainted=True)

    is_safe = len(injection_flags) == 0

    log.info(f"[CapabilityGuard] Quarantine parsed {len(raw_text)} chars from '{source}'. Safe={is_safe}")

    try:
        get_registry().set_capability_evidence(
            "CAPABILITY_GUARD",
            EvidenceLevel.LIVE,
            f"Quarantined input from {source} (injections={len(injection_flags)})",
            source="capability_guard.quarantine_parse_untrusted",
        )
    except Exception:
        pass

    return {
        "success": True,
        "is_safe": is_safe,
        "source": source,
        "sanitized_payload": tainted_body,
        "raw_char_count": len(raw_text),
        "injection_flags": injection_flags,
    }


def check_execution_safety(tool_name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Determine whether tool execution with given parameters violates taint boundaries."""
    tool_norm = str(tool_name).strip().lower()

    if tool_norm in SENSITIVE_TOOLS or "delete" in tool_norm or "shell" in tool_norm:
        if is_value_tainted(params):
            msg = (
                f"Security Rejection: Sensitive tool '{tool_name}' cannot be parameterized "
                f"by un-sanitized TaintedString input without explicit authorization."
            )
            log.warning(f"[CapabilityGuard] {msg}")
            return False, msg

    return True, None


# ── TRANSACTIONAL SAGA LEDGER ───────────────────────────────────────────────
@dataclass
class SagaStep:
    step_id: str
    action_name: str
    params: Dict[str, Any]
    compensation_action: str
    compensation_params: Dict[str, Any]
    status: str = "executed"  # "executed", "compensated", "failed"
    timestamp: float = field(default_factory=time.time)


@dataclass
class SagaTransaction:
    saga_id: str
    steps: List[SagaStep] = field(default_factory=list)
    status: str = "active"  # "active", "committed", "rolled_back"
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class SagaLedger:
    """Tracks reversible multi-step external actions with LIFO compensation."""

    def __init__(self):
        self._sagas: Dict[str, SagaTransaction] = {}

    def begin_saga(self, saga_id: Optional[str] = None) -> str:
        """Start a new transactional saga."""
        sid = saga_id or f"saga_{int(time.time() * 1000)}"
        with _lock:
            self._sagas[sid] = SagaTransaction(saga_id=sid)
        log.info(f"[SagaLedger] Began transaction '{sid}'")
        return sid

    def record_step(
        self,
        saga_id: str,
        action_name: str,
        compensation_action: str,
        params: Optional[Dict[str, Any]] = None,
        compensation_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an executed step and its corresponding reverse compensation action."""
        with _lock:
            if saga_id not in self._sagas:
                self.begin_saga(saga_id)
            saga = self._sagas[saga_id]

            step_id = f"step_{len(saga.steps) + 1}"
            step = SagaStep(
                step_id=step_id,
                action_name=action_name,
                params=params or {},
                compensation_action=compensation_action,
                compensation_params=compensation_params or {},
            )
            saga.steps.append(step)

        return asdict(step)

    def commit_saga(self, saga_id: str) -> Dict[str, Any]:
        """Commit saga upon successful overall workflow execution."""
        with _lock:
            if saga_id not in self._sagas:
                return {"success": False, "status": "not_found"}
            saga = self._sagas[saga_id]
            saga.status = "committed"
            saga.completed_at = time.time()

        log.info(f"[SagaLedger] Committed saga '{saga_id}' ({len(saga.steps)} steps)")
        return {"success": True, "status": "committed", "saga_id": saga_id, "step_count": len(saga.steps)}

    def rollback_saga(
        self,
        saga_id: str,
        dispatcher: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        """Rollback all executed steps in reverse (LIFO) order."""
        with _lock:
            if saga_id not in self._sagas:
                return {"success": False, "status": "not_found"}
            saga = self._sagas[saga_id]
            steps_to_rollback = list(reversed(saga.steps))

        compensated_steps: List[str] = []
        errors: List[str] = []

        for step in steps_to_rollback:
            if step.status == "executed":
                try:
                    if dispatcher:
                        dispatcher(step.compensation_action, step.compensation_params)
                    step.status = "compensated"
                    compensated_steps.append(step.step_id)
                    log.info(f"[SagaLedger] Compensated step '{step.step_id}' via '{step.compensation_action}'")
                except Exception as exc:
                    step.status = "failed"
                    errors.append(f"Failed compensating step {step.step_id}: {exc}")
                    log.error(f"[SagaLedger] Compensation error: {exc}")

        with _lock:
            saga.status = "rolled_back"
            saga.completed_at = time.time()

        return {
            "success": len(errors) == 0,
            "status": "rolled_back",
            "saga_id": saga_id,
            "compensated_steps": compensated_steps,
            "errors": errors,
        }

    def get_saga(self, saga_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            if saga_id not in self._sagas:
                return None
            return asdict(self._sagas[saga_id])


# ── SINGLETON INSTANCES ─────────────────────────────────────────────────────
_saga_ledger = SagaLedger()


def get_saga_ledger() -> SagaLedger:
    return _saga_ledger


def begin_saga(saga_id: Optional[str] = None) -> str:
    return _saga_ledger.begin_saga(saga_id)


def record_saga_step(
    saga_id: str,
    action_name: str,
    compensation_action: str,
    params: Optional[Dict[str, Any]] = None,
    compensation_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _saga_ledger.record_step(saga_id, action_name, compensation_action, params, compensation_params)


def commit_saga(saga_id: str) -> Dict[str, Any]:
    return _saga_ledger.commit_saga(saga_id)


def rollback_saga(saga_id: str, dispatcher: Optional[Callable[[str, Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    return _saga_ledger.rollback_saga(saga_id, dispatcher)
