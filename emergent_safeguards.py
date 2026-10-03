"""Emergent Dynamics Safeguards for J.A.R.V.I.S. — MARK VIII.

Workstream 1:
  1. Governor Conflict Resolver:
     - Split velocity metrics for user-requested vs self-initiated background tasks.
     - Enforces cooldown timers (default 120s) and hysteresis margin (1.5) to prevent flapping
       between CUSUM demotions (Module AJ) and autonomy promotions (Module AV).
  2. Correlated Learners Auditor:
     - Audits Module AO compiled habits on separate held-out samples.
     - Calculates effective sample size n_eff adjusting for lag-1 autocorrelation (r1).
     - Triggers automatic decompilation if held-out accuracy breaches the safety floor (< 80%).
  3. Surprise Poisoning Guard:
     - Gates Module AK exemplar store additions and fast-LoRA updates through quarantine/taint
       and symbolic verifiers.
     - Hard caps kNN-LM interpolation factor lambda (max 0.25).
  4. Sycophancy & Dissent Floor Tracker:
     - Enforces invariant high-severity dissent floor (>= 90% dissent rate on severe bugs).
     - Raises sycophancy alert if J.A.R.V.I.S. fails to dissent on critical regressions.
  5. Crying Wolf (AT) Precision/Recall Tracker:
     - Logs confirmed vs overridden speculative pre-mortem dissents.
     - Computes precision and recall to calibrate intervention thresholds.
  6. Capability Probing Detector:
     - Detects repeated permission-denied goals from Module AN (>= 3 in 300s),
       rate-limits AN and emits security probe alerts.
  7. Substrate Version Tracker:
     - Tags learned artifacts with canonical substrate hashes.
     - Automatically purges mismatched artifacts upon an AP substrate swap.
  8. Rollout & Kill Switches:
     - Manages observe_only mode and single-command instant kill switches per module.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.emergent_safeguards")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 1. Governor Conflict Resolver
# ---------------------------------------------------------------------------

@dataclass
class TierTransitionRecord:
    timestamp: float
    from_tier: str
    to_tier: str
    is_promotion: bool
    allowed: bool
    root_cause: str
    velocity_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GovernorConflictResolver:
    """Arbitrates CUSUM drift demotions (AJ) and autonomy promotions (AV) with hysteresis."""

    def __init__(self, cooldown_seconds: float = 120.0, hysteresis_margin: float = 1.5):
        self.cooldown_seconds = cooldown_seconds
        self.hysteresis_margin = hysteresis_margin

        self.user_requested_velocity: float = 0.0
        self.self_initiated_velocity: float = 0.0
        self.last_transition_time: float = 0.0
        self.last_transition_was_demotion: bool = False
        self.transition_history: List[TierTransitionRecord] = []

    def record_activity(self, count: int, is_user_requested: bool) -> None:
        with _lock:
            if is_user_requested:
                self.user_requested_velocity += float(count)
            else:
                self.self_initiated_velocity += float(count)

    def request_tier_change(
        self,
        current_tier: str,
        target_tier: str,
        is_promotion: bool,
        root_cause: str,
        now: Optional[float] = None,
    ) -> Tuple[bool, str, str]:
        """Evaluate if tier transition is allowed or rejected by cooldown / hysteresis."""
        t_now = time.time() if now is None else now
        with _lock:
            elapsed = t_now - self.last_transition_time
            total_velocity = self.user_requested_velocity + self.self_initiated_velocity
            velocity_ratio = (
                self.self_initiated_velocity / max(1.0, self.user_requested_velocity)
                if self.user_requested_velocity > 0
                else self.self_initiated_velocity
            )

            # Rule 1: Always allow demotions (safety priority)
            if not is_promotion:
                self.last_transition_time = t_now
                self.last_transition_was_demotion = True
                rec = TierTransitionRecord(
                    timestamp=t_now,
                    from_tier=current_tier,
                    to_tier=target_tier,
                    is_promotion=False,
                    allowed=True,
                    root_cause=root_cause,
                    velocity_ratio=round(velocity_ratio, 3),
                )
                self.transition_history.append(rec)
                log.warning(f"[ConflictResolver] Demotion approved: {current_tier} -> {target_tier} ({root_cause})")
                return True, target_tier, "Demotion accepted immediately for safety"

            # Rule 2: For promotions, enforce cooldown timer if recently demoted
            if self.last_transition_was_demotion and elapsed < self.cooldown_seconds:
                rec = TierTransitionRecord(
                    timestamp=t_now,
                    from_tier=current_tier,
                    to_tier=target_tier,
                    is_promotion=True,
                    allowed=False,
                    root_cause=f"Cooldown active ({elapsed:.1f}s < {self.cooldown_seconds}s)",
                    velocity_ratio=round(velocity_ratio, 3),
                )
                self.transition_history.append(rec)
                return False, current_tier, f"Promotion blocked by anti-flapping cooldown ({elapsed:.1f}s < {self.cooldown_seconds}s)"

            # Rule 3: Enforce hysteresis margin on self-initiated velocity
            if velocity_ratio > self.hysteresis_margin:
                rec = TierTransitionRecord(
                    timestamp=t_now,
                    from_tier=current_tier,
                    to_tier=target_tier,
                    is_promotion=True,
                    allowed=False,
                    root_cause=f"High self-initiated velocity ratio {velocity_ratio:.2f} > {self.hysteresis_margin:.2f}",
                    velocity_ratio=round(velocity_ratio, 3),
                )
                self.transition_history.append(rec)
                return False, current_tier, f"Promotion blocked: self-initiated velocity ratio {velocity_ratio:.2f} exceeds hysteresis threshold {self.hysteresis_margin:.2f}"

            # Approved promotion
            self.last_transition_time = t_now
            self.last_transition_was_demotion = False
            rec = TierTransitionRecord(
                timestamp=t_now,
                from_tier=current_tier,
                to_tier=target_tier,
                is_promotion=True,
                allowed=True,
                root_cause=root_cause,
                velocity_ratio=round(velocity_ratio, 3),
            )
            self.transition_history.append(rec)
            log.info(f"[ConflictResolver] Promotion approved: {current_tier} -> {target_tier}")
            return True, target_tier, "Promotion approved"


# ---------------------------------------------------------------------------
# 2. Correlated Learners Auditor
# ---------------------------------------------------------------------------

@dataclass
class AuditReport:
    rule_id: str
    held_out_accuracy: float
    safety_floor: float
    effective_sample_size: float
    raw_sample_size: int
    lag1_autocorrelation: float
    decompiled: bool
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CorrelatedLearnersAuditor:
    """Audits compiled habits on held-out samples with autocorrelation correction."""

    def __init__(self, safety_floor: float = 0.80):
        self.safety_floor = safety_floor
        self._audit_history: List[AuditReport] = []
        self._total_audits: int = 0
        self._total_decompilations: int = 0

    @staticmethod
    def compute_lag1_autocorrelation(series: List[float]) -> float:
        """Calculate lag-1 autocorrelation r_1."""
        if len(series) < 3:
            return 0.0
        arr = np.array(series, dtype=np.float64)
        mean = np.mean(arr)
        denom = np.sum((arr - mean) ** 2)
        if denom < 1e-9:
            return 0.0
        nom = np.sum((arr[:-1] - mean) * (arr[1:] - mean))
        r1 = float(nom / denom)
        return max(-0.99, min(0.99, r1))

    def audit_habit_rule(
        self,
        rule_id: str,
        held_out_outcomes: List[int],  # 1 for success, 0 for failure
    ) -> AuditReport:
        """Audit habit on held-out dataset and trigger decompilation if below safety floor."""
        with _lock:
            self._total_audits += 1
            n = len(held_out_outcomes)
            if n == 0:
                acc = 0.0
                r1 = 0.0
                n_eff = 0.0
            else:
                acc = float(sum(held_out_outcomes)) / n
                r1 = self.compute_lag1_autocorrelation([float(x) for x in held_out_outcomes])
                # Effective sample size n_eff = n * (1 - r1) / (1 + r1)
                n_eff = float(n * (1.0 - r1) / (1.0 + r1))
                n_eff = max(1.0, min(float(n), n_eff))

            decompiled = acc < self.safety_floor
            if decompiled:
                self._total_decompilations += 1
                rationale = f"Decompilation triggered: Held-out accuracy {acc:.3f} < safety floor {self.safety_floor:.2f} (n_eff={n_eff:.1f})"
                log.warning(f"[CorrelatedLearnersAuditor] {rationale} for rule {rule_id}")
            else:
                rationale = f"Audit passed: Held-out accuracy {acc:.3f} >= {self.safety_floor:.2f} (n_eff={n_eff:.1f})"

            report = AuditReport(
                rule_id=rule_id,
                held_out_accuracy=round(acc, 4),
                safety_floor=self.safety_floor,
                effective_sample_size=round(n_eff, 2),
                raw_sample_size=n,
                lag1_autocorrelation=round(r1, 4),
                decompiled=decompiled,
                rationale=rationale,
            )
            self._audit_history.append(report)
            return report

    def get_decompilation_rate(self) -> float:
        with _lock:
            if self._total_audits == 0:
                return 0.0
            return float(self._total_decompilations) / float(self._total_audits)


# ---------------------------------------------------------------------------
# 3. Surprise Poisoning Guard
# ---------------------------------------------------------------------------

class SurprisePoisoningGuard:
    """Gates exemplar memory and LoRA adaptation against tainted/unverified episodes."""

    MAX_KNN_LAMBDA: float = 0.25

    @classmethod
    def clamp_knn_lambda(cls, requested_lambda: float) -> float:
        """Enforce strict hard cap on kNN-LM interpolation factor lambda <= 0.25."""
        return max(0.0, min(cls.MAX_KNN_LAMBDA, float(requested_lambda)))

    @staticmethod
    def verify_exemplar_safety(
        episode_payload: Dict[str, Any],
        taint_tags: Optional[List[str]] = None,
        symbolic_check_passed: bool = True,
    ) -> Tuple[bool, str]:
        """Ensure exemplar is not tainted by unverified/quarantined executions."""
        if taint_tags:
            for t in taint_tags:
                if t.lower() in ("tainted", "quarantined", "unsafe", "untrusted_network", "injection_risk"):
                    return False, f"Exemplar rejected: blocked taint tag '{t}'"

        if not symbolic_check_passed:
            return False, "Exemplar rejected: failed symbolic verifier postcondition check"

        # Check for banned shell/AST injection strings in payload
        payload_str = str(episode_payload).lower()
        if "__import__" in payload_str or "os.system" in payload_str or "subprocess.popen" in payload_str:
            return False, "Exemplar rejected: dangerous AST execution call in episode payload"

        return True, "Exemplar verified and safe for memory storage"


# ---------------------------------------------------------------------------
# 4. Sycophancy & Dissent Floor Tracker
# ---------------------------------------------------------------------------

@dataclass
class SycophancyCheckResult:
    is_safe: bool
    sycophancy_alert: bool
    dissent_rate: float
    severity: float
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SycophancyDissentTracker:
    """Enforces invariant high-severity dissent floor (>= 90% dissent rate required on severe bugs)."""

    def __init__(self, high_severity_threshold: float = 0.70, required_dissent_floor: float = 0.90):
        self.high_severity_threshold = high_severity_threshold
        self.required_dissent_floor = required_dissent_floor

        self._severe_bug_events: int = 0
        self._severe_dissent_events: int = 0
        self._sycophancy_alerts: int = 0

    def evaluate_dissent(self, severity: float, dissented: bool) -> SycophancyCheckResult:
        """Record defect severity and check if dissent floor invariant is satisfied."""
        with _lock:
            is_severe = severity >= self.high_severity_threshold
            if is_severe:
                self._severe_bug_events += 1
                if dissented:
                    self._severe_dissent_events += 1

            dissent_rate = (
                float(self._severe_dissent_events) / float(self._severe_bug_events)
                if self._severe_bug_events > 0
                else 1.0
            )

            # Sycophancy occurs if severe bug exists but system remained silent or sycophantically agreed
            sycophancy_alert = False
            if is_severe and not dissented:
                sycophancy_alert = True
                self._sycophancy_alerts += 1
                rationale = (
                    f"SYCOPHANCY ALERT: Severe bug (severity={severity:.2f} >= {self.high_severity_threshold:.2f}) "
                    f"was not dissented! Current severe dissent rate={dissent_rate:.1%}"
                )
                log.error(f"[SycophancyTracker] {rationale}")
            elif dissent_rate < self.required_dissent_floor:
                sycophancy_alert = True
                rationale = f"SYCOPHANCY RISK: Dissent rate {dissent_rate:.1%} breached floor {self.required_dissent_floor:.1%}"
            else:
                rationale = f"Dissent floor verified (rate={dissent_rate:.1%})"

            return SycophancyCheckResult(
                is_safe=not sycophancy_alert,
                sycophancy_alert=sycophancy_alert,
                dissent_rate=round(dissent_rate, 4),
                severity=round(severity, 3),
                rationale=rationale,
            )


# ---------------------------------------------------------------------------
# 5. Crying Wolf (AT) Precision/Recall Tracker
# ---------------------------------------------------------------------------

class CryingWolfTracker:
    """Tracks precision and recall of speculative pre-mortem dissents (Module AT)."""

    def __init__(self, initial_dissent_threshold: float = 0.40):
        self.dissent_threshold = initial_dissent_threshold
        self.true_positives: int = 0
        self.false_positives: int = 0
        self.false_negatives: int = 0

    def record_outcome(self, dissented: bool, was_actual_defect: bool) -> None:
        with _lock:
            if dissented and was_actual_defect:
                self.true_positives += 1
            elif dissented and not was_actual_defect:
                self.false_positives += 1
            elif not dissented and was_actual_defect:
                self.false_negatives += 1

            # Auto-calibrate intervention threshold
            prec = self.get_precision()
            if prec < 0.60 and (self.true_positives + self.false_positives) >= 5:
                # Too many false alarms -> raise threshold
                self.dissent_threshold = min(0.85, self.dissent_threshold + 0.05)
                log.info(f"[CryingWolfTracker] Low precision ({prec:.2f}) -> raised dissent threshold to {self.dissent_threshold:.2f}")
            elif self.get_recall() < 0.70 and (self.true_positives + self.false_negatives) >= 5:
                # Missing real bugs -> lower threshold
                self.dissent_threshold = max(0.20, self.dissent_threshold - 0.05)
                log.info(f"[CryingWolfTracker] Low recall ({self.get_recall():.2f}) -> lowered dissent threshold to {self.dissent_threshold:.2f}")

    def get_precision(self) -> float:
        with _lock:
            denom = self.true_positives + self.false_positives
            return float(self.true_positives) / denom if denom > 0 else 1.0

    def get_recall(self) -> float:
        with _lock:
            denom = self.true_positives + self.false_negatives
            return float(self.true_positives) / denom if denom > 0 else 1.0


# ---------------------------------------------------------------------------
# 6. Capability Probing Detector
# ---------------------------------------------------------------------------

class CapabilityProbingDetector:
    """Detects repeated permission-denied goal proposals from Module AN (>= 3 in 300s)."""

    def __init__(self, window_sec: float = 300.0, max_denials: int = 3):
        self.window_sec = window_sec
        self.max_denials = max_denials
        self._denial_timestamps: List[float] = []
        self._is_rate_limited: bool = False

    def record_denial(self, goal_id: str, now: Optional[float] = None) -> Tuple[bool, bool, str]:
        """Record goal denial and check if security probe alert and rate-limiting should fire.

        Returns (is_rate_limited, security_probe_alert, rationale).
        """
        t_now = time.time() if now is None else now
        with _lock:
            self._denial_timestamps = [t for t in self._denial_timestamps if (t_now - t) <= self.window_sec]
            self._denial_timestamps.append(t_now)

            if len(self._denial_timestamps) >= self.max_denials:
                self._is_rate_limited = True
                rationale = (
                    f"SECURITY PROBE ALERT: Goal generator AN exceeded {self.max_denials} permission denials "
                    f"within {self.window_sec:.0f}s window! Module AN is now rate-limited."
                )
                log.error(f"[CapabilityProbingDetector] {rationale}")
                return True, True, rationale

            return False, False, f"Denial recorded ({len(self._denial_timestamps)}/{self.max_denials} in window)"

    def is_rate_limited(self) -> bool:
        with _lock:
            return self._is_rate_limited

    def reset_rate_limit(self) -> None:
        with _lock:
            self._is_rate_limited = False
            self._denial_timestamps.clear()


# ---------------------------------------------------------------------------
# 7. Substrate Version Tracker
# ---------------------------------------------------------------------------

class SubstrateVersionTracker:
    """Tags learned artifacts with canonical substrate hashes and purges on substrate swap."""

    def __init__(self, current_substrate_hash: str = "claude-sonnet-5-5-base"):
        self.current_substrate_hash = current_substrate_hash
        self._artifact_registry: Dict[str, Tuple[str, str]] = {}  # artifact_id -> (type, substrate_hash)

    def register_artifact(self, artifact_id: str, artifact_type: str, substrate_hash: Optional[str] = None) -> None:
        with _lock:
            shash = substrate_hash or self.current_substrate_hash
            self._artifact_registry[artifact_id] = (artifact_type, shash)

    def on_substrate_swap(self, new_substrate_hash: str) -> List[str]:
        """Purge all artifacts generated by previous substrate models."""
        with _lock:
            old_hash = self.current_substrate_hash
            self.current_substrate_hash = new_substrate_hash
            purged = []

            for aid, (atype, shash) in list(self._artifact_registry.items()):
                if shash != new_substrate_hash:
                    purged.append(aid)
                    del self._artifact_registry[aid]

            log.info(f"[SubstrateVersionTracker] Substrate swap {old_hash} -> {new_substrate_hash}: purged {len(purged)} mismatched artifacts")
            return purged


# ---------------------------------------------------------------------------
# 8. Rollout & Kill Switches
# ---------------------------------------------------------------------------

class RolloutAndKillSwitchManager:
    """Manages observe_only modes and instant single-command kill switches per module."""

    def __init__(self):
        self._observe_only_modules: Set[str] = set()
        self._killed_modules: Set[str] = set()

    def set_observe_only(self, module_id: str, enable: bool = True) -> None:
        with _lock:
            mid = module_id.upper().strip()
            if enable:
                self._observe_only_modules.add(mid)
                log.info(f"[KillSwitchManager] Module {mid} set to OBSERVE_ONLY mode")
            else:
                self._observe_only_modules.discard(mid)
                log.info(f"[KillSwitchManager] Module {mid} returned to ACTIVE mode")

    def toggle_kill_switch(self, module_id: str, killed: bool = True) -> None:
        with _lock:
            mid = module_id.upper().strip()
            if killed:
                self._killed_modules.add(mid)
                log.warning(f"[KillSwitchManager] KILL SWITCH ENGAGED for Module {mid}")
            else:
                self._killed_modules.discard(mid)
                log.info(f"[KillSwitchManager] Kill switch released for Module {mid}")

    def is_module_active(self, module_id: str) -> bool:
        with _lock:
            return module_id.upper().strip() not in self._killed_modules

    def is_observe_only(self, module_id: str) -> bool:
        with _lock:
            return module_id.upper().strip() in self._observe_only_modules


# ---------------------------------------------------------------------------
# Global Singleton & Helper Interface
# ---------------------------------------------------------------------------

_governor_resolver = GovernorConflictResolver()
_correlated_auditor = CorrelatedLearnersAuditor()
_poisoning_guard = SurprisePoisoningGuard()
_sycophancy_tracker = SycophancyDissentTracker()
_crying_wolf_tracker = CryingWolfTracker()
_probing_detector = CapabilityProbingDetector()
_substrate_tracker = SubstrateVersionTracker()
_kill_switch_manager = RolloutAndKillSwitchManager()


def get_governor_conflict_resolver() -> GovernorConflictResolver:
    return _governor_resolver


def get_correlated_learners_auditor() -> CorrelatedLearnersAuditor:
    return _correlated_auditor


def get_surprise_poisoning_guard() -> SurprisePoisoningGuard:
    return _poisoning_guard


def get_sycophancy_tracker() -> SycophancyDissentTracker:
    return _sycophancy_tracker


def get_crying_wolf_tracker() -> CryingWolfTracker:
    return _crying_wolf_tracker


def get_probing_detector() -> CapabilityProbingDetector:
    return _probing_detector


def get_substrate_version_tracker() -> SubstrateVersionTracker:
    return _substrate_tracker


def get_kill_switch_manager() -> RolloutAndKillSwitchManager:
    return _kill_switch_manager
