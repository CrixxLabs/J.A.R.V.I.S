"""Metacognitive Governor & Risk-Scaled Autonomy Engine for J.A.R.V.I.S. — MARK VIII.

Module N:
  1. Outcome-Calibrated Competence:
     - Persistent outcome ledger (data/competence_outcomes.json) tracking verified successes vs failures per task domain.
     - Conformal confidence prediction: extracts lightweight calibration signals (prompt length,
       retrieval similarity, sample agreement, domain base success rates) and computes calibrated bounds.
  2. Risk-Scaled Autonomy:
     - Calibrated thresholding across LOW, MEDIUM, HIGH, and CRITICAL stake levels.
     - Escalation to user with source attribution or downgrade from autonomous execution
       to confirmation request when confidence is below risk boundary.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.metacognitive_governor")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
OUTCOMES_FILE = DATA_DIR / "competence_outcomes.json"

_lock = threading.RLock()

# Risk / Stake Levels
STAKE_LOW = "LOW"
STAKE_MEDIUM = "MEDIUM"
STAKE_HIGH = "HIGH"
STAKE_CRITICAL = "CRITICAL"

# Default autonomy lower-bound thresholds per stake level
AUTONOMY_THRESHOLDS = {
    STAKE_LOW: 0.40,
    STAKE_MEDIUM: 0.65,
    STAKE_HIGH: 0.80,
    STAKE_CRITICAL: 0.92,
}

# Autonomy Tiers
TIER_FULL_AUTONOMOUS = "FULL_AUTONOMOUS"
TIER_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
TIER_ESCALATION_REQUIRED = "ESCALATION_REQUIRED"


@dataclass
class CalibratedScore:
    domain: str
    point_score: float
    calibrated_lower_bound: float
    uncertainty_margin: float
    sample_size: int
    calibration_factors: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutonomyDecision:
    can_execute_autonomously: bool
    requires_confirmation: bool
    autonomy_tier: str
    calibrated_score: float
    calibrated_lower_bound: float
    stake_level: str
    threshold_required: float
    reason: str
    source_attribution: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MetacognitiveGovernor:
    """Manages empirical competence calibration, conformal bounds, and risk-scaled autonomy."""

    def __init__(self, ledger_path: Optional[Path] = None):
        self.ledger_path = Path(ledger_path).resolve() if ledger_path else OUTCOMES_FILE
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._domain_stats: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []
        self._load_ledger()

    def _load_ledger(self) -> None:
        with _lock:
            if self.ledger_path.exists():
                try:
                    with open(self.ledger_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._domain_stats = data.get("domains", {})
                    self._history = data.get("history", [])
                    log.debug(f"[MetacognitiveGovernor] Loaded ledger with {len(self._domain_stats)} domains")
                except Exception as exc:
                    log.warning(f"[MetacognitiveGovernor] Could not load ledger: {exc}. Starting fresh.")
                    self._domain_stats = {}
                    self._history = []
            else:
                self._domain_stats = {}
                self._history = []

    def _save_ledger(self) -> None:
        with _lock:
            try:
                payload = {
                    "updated_at": time.time(),
                    "domains": self._domain_stats,
                    "history": self._history[-500:],  # keep last 500 events
                }
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception as exc:
                log.error(f"[MetacognitiveGovernor] Failed to save ledger: {exc}")

    def record_task_outcome(
        self,
        domain: str,
        task_id: str,
        success: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record verified execution outcome (success vs failure) for a specific domain."""
        domain_key = str(domain).strip().lower()
        now = time.time()

        with _lock:
            stats = self._domain_stats.setdefault(
                domain_key,
                {"successes": 0, "failures": 0, "total": 0, "last_updated": now},
            )
            if success:
                stats["successes"] += 1
            else:
                stats["failures"] += 1
            stats["total"] += 1
            stats["last_updated"] = now

            event = {
                "task_id": task_id,
                "domain": domain_key,
                "success": bool(success),
                "timestamp": now,
                "metadata": metadata or {},
            }
            self._history.append(event)
            self._save_ledger()

            try:
                get_registry().set_capability_evidence(
                    "METACOGNITIVE_GOVERNOR",
                    EvidenceLevel.LIVE,
                    f"Recorded outcome for domain '{domain_key}' (total={stats['total']}, success_rate={stats['successes']/stats['total']:.2f})",
                    source="metacognitive_governor.record_task_outcome",
                )
            except Exception:
                pass

            return {
                "recorded": True,
                "domain": domain_key,
                "domain_successes": stats["successes"],
                "domain_failures": stats["failures"],
                "domain_total": stats["total"],
                "empirical_success_rate": stats["successes"] / stats["total"],
            }

    def predict_competence(
        self,
        domain: str,
        task_params: Optional[Dict[str, Any]] = None,
    ) -> CalibratedScore:
        """Compute conformal confidence bounds and calibrated competence estimate."""
        domain_key = str(domain).strip().lower()
        params = task_params or {}

        with _lock:
            stats = self._domain_stats.get(domain_key, {"successes": 0, "failures": 0, "total": 0})
            s = stats["successes"]
            f = stats["failures"]
            n = stats["total"]

            # 1. Bayesian Beta Conjugate Prior Formulation
            # Base prior: alpha0=2.0, beta0=1.0 (benign prior)
            alpha_0 = 2.0
            beta_0 = 1.0

            # Task evidence signals
            retrieval_sim = float(params.get("retrieval_similarity", 0.75))
            retrieval_sim = max(0.0, min(1.0, retrieval_sim))

            sample_agreement = float(params.get("sample_agreement", 0.85))
            sample_agreement = max(0.0, min(1.0, sample_agreement))

            prompt = params.get("prompt")
            len_penalty = 0.0
            if prompt is not None:
                prompt_len = len(str(prompt))
                if prompt_len == 0:
                    len_penalty = 0.10
                elif prompt_len > 4000:
                    len_penalty = min(0.15, (prompt_len - 4000) / 10000.0)

            complexity = float(params.get("complexity", 0.2))
            complexity = max(0.0, min(1.0, complexity))
            complexity_discount = complexity * 0.10

            # Combine instance signals as virtual pseudo-observations
            instance_evidence = (0.55 * sample_agreement) + (0.45 * retrieval_sim)
            pseudo_n = 4.0
            alpha_instance = instance_evidence * pseudo_n
            beta_instance = (1.0 - instance_evidence) * pseudo_n

            # Posterior parameters
            alpha = alpha_0 + float(s) + alpha_instance
            beta = beta_0 + float(f) + beta_instance
            total_mass = alpha + beta

            # Posterior mean
            raw_point = alpha / total_mass
            point_score = max(0.05, min(0.99, raw_point - len_penalty - complexity_discount))

            # Posterior variance and standard deviation
            variance = (alpha * beta) / ((total_mass ** 2) * (total_mass + 1.0))
            std_err = math.sqrt(variance)

            # Conformal ~90% conservative lower bound
            margin = min(0.35, 1.645 * std_err)
            calibrated_lower = max(0.01, round(point_score - margin, 4))
            point_score = round(point_score, 4)
            margin = round(margin, 4)

            factors = {
                "alpha": round(alpha, 3),
                "beta": round(beta, 3),
                "retrieval_similarity": retrieval_sim,
                "sample_agreement": sample_agreement,
                "prompt_length_penalty": round(len_penalty, 4),
                "complexity_discount": round(complexity_discount, 4),
            }

            return CalibratedScore(
                domain=domain_key,
                point_score=point_score,
                calibrated_lower_bound=calibrated_lower,
                uncertainty_margin=margin,
                sample_size=n,
                calibration_factors=factors,
            )

    def evaluate_autonomy_level(
        self,
        domain: str,
        task_params: Optional[Dict[str, Any]] = None,
        stake_level: str = STAKE_MEDIUM,
    ) -> AutonomyDecision:
        """Evaluate whether task can be autonomously executed or requires human confirmation/escalation."""
        stake_norm = str(stake_level).strip().upper()
        if stake_norm not in AUTONOMY_THRESHOLDS:
            stake_norm = STAKE_MEDIUM

        required_threshold = AUTONOMY_THRESHOLDS[stake_norm]
        calibrated = self.predict_competence(domain, task_params)
        score_lower = calibrated.calibrated_lower_bound
        point_score = calibrated.point_score

        source_attribution = (
            f"Domain '{calibrated.domain}' (N={calibrated.sample_size}, "
            f"point_score={point_score:.2f}, lower_bound={score_lower:.2f})"
        )

        if score_lower >= required_threshold:
            return AutonomyDecision(
                can_execute_autonomously=True,
                requires_confirmation=False,
                autonomy_tier=TIER_FULL_AUTONOMOUS,
                calibrated_score=point_score,
                calibrated_lower_bound=score_lower,
                stake_level=stake_norm,
                threshold_required=required_threshold,
                reason=f"Calibrated confidence ({score_lower:.2f}) satisfies {stake_norm} stake requirement ({required_threshold:.2f}).",
                source_attribution=source_attribution,
            )

        # If below threshold, determine if it is a confirmation request or critical escalation
        if stake_norm == STAKE_CRITICAL or score_lower < (required_threshold - 0.25):
            tier = TIER_ESCALATION_REQUIRED
            reason = (
                f"Confidence lower bound ({score_lower:.2f}) is substantially below {stake_norm} stake threshold ({required_threshold:.2f}). "
                f"Escalating for explicit user authorization and policy review."
            )
        else:
            tier = TIER_CONFIRMATION_REQUIRED
            reason = (
                f"Confidence lower bound ({score_lower:.2f}) is below {stake_norm} threshold ({required_threshold:.2f}). "
                f"Requesting user confirmation prior to tool dispatch."
            )

        return AutonomyDecision(
            can_execute_autonomously=False,
            requires_confirmation=True,
            autonomy_tier=tier,
            calibrated_score=point_score,
            calibrated_lower_bound=score_lower,
            stake_level=stake_norm,
            threshold_required=required_threshold,
            reason=reason,
            source_attribution=source_attribution,
        )

    def get_domain_metrics(self, domain: Optional[str] = None) -> Dict[str, Any]:
        """Return metrics across all domains or a single domain."""
        with _lock:
            if domain:
                d_key = str(domain).strip().lower()
                return self._domain_stats.get(d_key, {"successes": 0, "failures": 0, "total": 0})
            return {
                "total_domains": len(self._domain_stats),
                "total_recorded_events": len(self._history),
                "domains": dict(self._domain_stats),
            }


_governor_instance: Optional[MetacognitiveGovernor] = None


def get_metacognitive_governor() -> MetacognitiveGovernor:
    global _governor_instance
    if _governor_instance is None:
        with _lock:
            if _governor_instance is None:
                _governor_instance = MetacognitiveGovernor()
    return _governor_instance


def record_task_outcome(
    domain: str,
    task_id: str,
    success: bool,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return get_metacognitive_governor().record_task_outcome(domain, task_id, success, metadata)


def predict_competence(
    domain: str,
    task_params: Optional[Dict[str, Any]] = None,
) -> CalibratedScore:
    return get_metacognitive_governor().predict_competence(domain, task_params)


def evaluate_autonomy_level(
    domain: str,
    task_params: Optional[Dict[str, Any]] = None,
    stake_level: str = STAKE_MEDIUM,
) -> AutonomyDecision:
    return get_metacognitive_governor().evaluate_autonomy_level(domain, task_params, stake_level)


def get_domain_metrics(domain: Optional[str] = None) -> Dict[str, Any]:
    return get_metacognitive_governor().get_domain_metrics(domain)
