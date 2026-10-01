r"""Causal World State Transition Modeling for J.A.R.V.I.S. — MARK VIII.

Implements forward environment state prediction and causal surprise detection:
  1. Captures pre-action environment state S_t.
  2. Predicts expected state delta \hat{S}_{t+1}.
  3. Observes actual post-action state S_{t+1}.
  4. Computes prediction surprise; raises StateSurpriseException upon high causal divergence.
"""
from __future__ import annotations

import datetime
import os
import psutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()


class StateSurpriseException(Exception):
    """Raised when post-execution environmental state significantly deviates from causal prediction."""
    def __init__(self, message: str, surprise_score: float = 1.0, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.surprise_score = surprise_score
        self.details = details or {}


def capture_current_state() -> Dict[str, Any]:
    """Capture a lightweight environment state snapshot."""
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    processes = [p.name().lower() for p in psutil.process_iter(['name'])][:50]

    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cpu_percent": cpu,
        "ram_percent": ram,
        "sample_processes": processes,
    }


def predict_state_transition(
    action: str,
    params: Dict[str, Any],
    initial_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    r"""Predict the expected state delta \hat{\Delta S} resulting from an action."""
    act = (action or "").lower().strip()
    expected = {
        "action": act,
        "expected_success": True,
        "expected_effects": {},
    }

    if act in ("open_app", "launch_app"):
        app_name = params.get("app", "").lower()
        expected["expected_effects"]["process_started"] = app_name
    elif act in ("create_file", "write_file", "save_file"):
        path = params.get("path") or params.get("file_path", "")
        expected["expected_effects"]["file_created"] = str(path)
    elif act in ("delete_file", "remove_file"):
        path = params.get("path") or params.get("file_path", "")
        expected["expected_effects"]["file_deleted"] = str(path)
    elif act in ("media", "play_song"):
        expected["expected_effects"]["audio_state_change"] = True
    elif act == "synthesize_skill":
        expected["expected_effects"]["skill_mounted"] = True

    return expected


def verify_causal_transition(
    action: str,
    params: Dict[str, Any],
    initial_state: Dict[str, Any],
    final_state: Dict[str, Any],
    expected_delta: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Tuple[bool, float, Dict[str, Any]]:
    """Verify observed state transition against predicted delta.

    Returns:
        Tuple of (verified: bool, surprise_score: float, details: dict)
    Raises:
        StateSurpriseException if strict=True and surprise_score > 0.6.
    """
    registry = get_registry()
    predicted = expected_delta or predict_state_transition(action, params, initial_state)
    effects = predicted.get("expected_effects", {})

    discrepancies = []
    surprise_score = 0.0

    # 1. File creation check
    if "file_created" in effects:
        target_path = effects["file_created"]
        if target_path and not os.path.exists(target_path):
            discrepancies.append(f"Expected file '{target_path}' was not created.")
            surprise_score += 0.8

    # 2. File deletion check
    if "file_deleted" in effects:
        target_path = effects["file_deleted"]
        if target_path and os.path.exists(target_path):
            discrepancies.append(f"Expected file '{target_path}' still exists.")
            surprise_score += 0.8

    # 3. Process started check
    if "process_started" in effects:
        app_name = effects["process_started"]
        proc_names = final_state.get("sample_processes", [])
        if app_name and not any(app_name in p for p in proc_names):
            # Moderate surprise as process name might differ slightly from app name
            surprise_score += 0.3

    surprise_score = min(1.0, surprise_score)
    verified = surprise_score < 0.5

    details = {
        "action": action,
        "surprise_score": surprise_score,
        "discrepancies": discrepancies,
        "verified": verified,
    }

    if not verified:
        msg = f"Causal violation on '{action}': {'; '.join(discrepancies)}"
        registry.set_capability_evidence(
            "CAUSAL_ENGINE",
            EvidenceLevel.BROKEN,
            msg[:80],
            source="causal engine",
        )
        if strict and surprise_score > 0.6:
            raise StateSurpriseException(msg, surprise_score=surprise_score, details=details)
    else:
        registry.set_capability_evidence(
            "CAUSAL_ENGINE",
            EvidenceLevel.LIVE,
            f"State transition nominal for '{action}' (surprise={surprise_score:.2f})",
            source="causal engine",
        )

    return verified, surprise_score, details
