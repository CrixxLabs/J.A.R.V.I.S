"""Homeostatic Drive & Curiosity Engine for J.A.R.V.I.S. — MARK VIII.

Module AA: Learning-Progress Intrinsic Motivation
  1. Learning Progress (LP) Curiosity:
     - Tracks prediction error per skill/domain window.
     - Computes LP as the rate of improvement: LP(t) = error(t-1) - error(t).
     - Allocates attention (exploration budget) proportional to LP magnitude.
  2. Homeostatic Drive Regulation:
     - Maintains target drive levels (novelty, competence, energy) and emits
       corrective signals when drives deviate from homeostatic set-points.
  3. Drive Urgency:
     - Urgency = |current - setpoint| / setpoint; drives above threshold are
       flagged for immediate rebalancing.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.homeostatic_drive")

_lock = threading.RLock()

# Default drive set-points (0–1 range)
DEFAULT_SETPOINTS: Dict[str, float] = {
    "novelty": 0.5,
    "competence": 0.7,
    "energy": 0.8,
}

URGENCY_THRESHOLD = 0.3  # |deviation| / setpoint above which we flag as urgent


@dataclass
class DriveState:
    drive_id: str
    current: float
    setpoint: float
    urgency: float
    is_urgent: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LearningProgressRecord:
    domain: str
    window_size: int
    recent_errors: List[float]
    learning_progress: float      # positive = improving, negative = regressing
    attention_weight: float       # normalised allocation

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HomeostaticDrive:
    """Regulates intrinsic drives and allocates exploration attention via Learning Progress."""

    def __init__(self, setpoints: Optional[Dict[str, float]] = None, window: int = 10):
        self._setpoints = dict(setpoints or DEFAULT_SETPOINTS)
        self._drives: Dict[str, float] = {k: v for k, v in self._setpoints.items()}
        self._window = window

        # Per-domain rolling error history
        self._error_log: Dict[str, deque] = {}

    # ------------------------------------------------------------------
    # Drive management
    # ------------------------------------------------------------------

    def update_drive(self, drive_id: str, value: float) -> DriveState:
        """Set current level of a drive and compute urgency."""
        value = float(max(0.0, min(1.0, value)))
        with _lock:
            if drive_id not in self._setpoints:
                self._setpoints[drive_id] = 0.5
            self._drives[drive_id] = value

        setpoint = self._setpoints[drive_id]
        deviation = abs(value - setpoint)
        urgency = round(deviation / max(setpoint, 1e-9), 4)
        is_urgent = urgency > URGENCY_THRESHOLD

        ds = DriveState(
            drive_id=drive_id,
            current=value,
            setpoint=setpoint,
            urgency=urgency,
            is_urgent=is_urgent,
        )
        log.debug(f"[HomeostaticDrive] Drive '{drive_id}': level={value:.3f}, urgency={urgency:.4f}")
        return ds

    def get_drive_states(self) -> List[DriveState]:
        """Return current regulatory state of all tracked drives."""
        result = []
        for did, cur in self._drives.items():
            setpoint = self._setpoints[did]
            deviation = abs(cur - setpoint)
            urgency = round(deviation / max(setpoint, 1e-9), 4)
            result.append(
                DriveState(
                    drive_id=did,
                    current=cur,
                    setpoint=setpoint,
                    urgency=urgency,
                    is_urgent=urgency > URGENCY_THRESHOLD,
                )
            )
        return result

    def urgent_drives(self) -> List[DriveState]:
        """Return drives requiring immediate corrective action."""
        return [ds for ds in self.get_drive_states() if ds.is_urgent]

    # ------------------------------------------------------------------
    # Learning-progress curiosity
    # ------------------------------------------------------------------

    def record_prediction_error(self, domain: str, error: float) -> None:
        """Log a prediction error for a skill/domain (0 = perfect, 1 = max error)."""
        error = float(max(0.0, error))
        with _lock:
            if domain not in self._error_log:
                self._error_log[domain] = deque(maxlen=self._window)
            self._error_log[domain].append(error)

    def compute_learning_progress(self, domain: str) -> float:
        """LP = mean(first half errors) - mean(second half errors).

        Positive → improving, negative → regressing.
        """
        with _lock:
            history = list(self._error_log.get(domain, []))

        if len(history) < 2:
            return 0.0

        mid = len(history) // 2
        first_half = history[:mid]
        second_half = history[mid:]

        lp = (sum(first_half) / len(first_half)) - (sum(second_half) / len(second_half))
        return round(lp, 6)

    def attention_allocation(self) -> List[LearningProgressRecord]:
        """Allocate exploration budget proportional to |LP| across all domains."""
        with _lock:
            domains = list(self._error_log.keys())

        lp_values: Dict[str, float] = {}
        for d in domains:
            lp_values[d] = abs(self.compute_learning_progress(d))

        total_lp = sum(lp_values.values())

        records = []
        for d in domains:
            with _lock:
                history = list(self._error_log[d])
            lp = self.compute_learning_progress(d)
            weight = round(lp_values[d] / max(total_lp, 1e-9), 6)

            records.append(
                LearningProgressRecord(
                    domain=d,
                    window_size=len(history),
                    recent_errors=history[-5:],     # last 5 for compactness
                    learning_progress=lp,
                    attention_weight=weight,
                )
            )

        # Highest |LP| first
        records.sort(key=lambda r: abs(r.learning_progress), reverse=True)

        try:
            get_registry().set_capability_evidence(
                "HOMEOSTATIC_DRIVE",
                EvidenceLevel.LIVE,
                f"Computed LP for {len(records)} domains; "
                f"top domain: {records[0].domain if records else 'none'}",
                source="homeostatic_drive.attention_allocation",
            )
        except Exception:
            pass

        return records

    def most_learnable_domain(self) -> Optional[str]:
        """Return the domain with the highest absolute learning progress."""
        alloc = self.attention_allocation()
        return alloc[0].domain if alloc else None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_drive_instance: Optional[HomeostaticDrive] = None


def get_homeostatic_drive(setpoints: Optional[Dict[str, float]] = None) -> HomeostaticDrive:
    global _drive_instance
    if _drive_instance is None:
        with _lock:
            if _drive_instance is None:
                _drive_instance = HomeostaticDrive(setpoints=setpoints)
    return _drive_instance


def update_drive(drive_id: str, value: float) -> DriveState:
    return get_homeostatic_drive().update_drive(drive_id, value)


def record_prediction_error(domain: str, error: float) -> None:
    get_homeostatic_drive().record_prediction_error(domain, error)


def compute_learning_progress(domain: str) -> float:
    return get_homeostatic_drive().compute_learning_progress(domain)


def attention_allocation() -> List[LearningProgressRecord]:
    return get_homeostatic_drive().attention_allocation()
