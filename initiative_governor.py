"""Trust-Calibrated Mixed-Initiative Governor for J.A.R.V.I.S. — MARK VIII.

Module AV:
  1. Expected Utility Decision Engine:
     - Evaluates candidate interventions across four actions:
       EU(act) = p * U_benefit - (1 - p) * (C_wrong + C_undo) - C_attention
       EU(suggest) = p * U_benefit * a_accept - C_attention
       EU(ask) = p_resolve * U_info - C_attention - C_latency
       EU(silent) = 0
  2. Automation Level Escalation:
     - Maintains Beta(1+s, 1+f) acceptance distributions per capability:
       OBSERVE (0) -> SUGGEST (1) -> ACT_WITH_PREVIEW (2) -> ACT_THEN_REPORT (3) -> ACT_SILENTLY (4).
     - Promotes autonomy only when 95% lower credible bounds clear safety gates;
       demotes immediately on rejection.
  3. Worktree Isolation:
     - Directs system-initiated code modifications strictly to isolated git worktrees/branches;
       prohibits unprompted edits to the user's active checkout.
  4. Hourly Interruption Budget:
     - Enforces strict proactive utterance caps, bypassing limits only for verified
       high-severity invariant warnings.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Tuple

from scipy.stats import beta as beta_dist

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.initiative_governor")

_lock = threading.RLock()


class InitiativeAction(str, Enum):
    ACT = "ACT"
    SUGGEST = "SUGGEST"
    ASK = "ASK"
    SILENT = "SILENT"


class AutonomyTier(IntEnum):
    OBSERVE = 0
    SUGGEST = 1
    ACT_WITH_PREVIEW = 2
    ACT_THEN_REPORT = 3
    ACT_SILENTLY = 4


@dataclass
class CapabilityTrustState:
    capability_name: str
    current_tier: AutonomyTier = AutonomyTier.SUGGEST
    successes: int = 0
    failures: int = 0
    total_trials: int = 0
    last_updated: float = field(default_factory=time.time)

    def lower_credible_bound(self, confidence: float = 0.95) -> float:
        """Compute the (1 - confidence) percentile of Beta(1 + successes, 1 + failures)."""
        alpha = 1.0 + self.successes
        beta_param = 1.0 + self.failures
        # 95% lower credible bound -> 5th percentile
        return float(beta_dist.ppf(1.0 - confidence, alpha, beta_param))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["current_tier"] = self.current_tier.value
        d["lcb_95"] = round(self.lower_credible_bound(0.95), 4)
        return d


@dataclass
class InitiativeVerdict:
    selected_action: InitiativeAction
    utility_scores: Dict[str, float]
    allowed_by_tier: bool
    budget_available: bool
    worktree_isolated: bool
    rationale: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["selected_action"] = self.selected_action.value
        return d


class InitiativeGovernor:
    """Trust-Calibrated Mixed-Initiative Arbiter and Worktree Safety Gate."""

    def __init__(
        self,
        hourly_budget: int = 6,
        promotion_lcb_threshold: float = 0.75,
        min_trials_for_promotion: int = 5,
        default_cost_attention: float = 0.20,
    ):
        self.hourly_budget = hourly_budget
        self.promotion_lcb_threshold = promotion_lcb_threshold
        self.min_trials_for_promotion = min_trials_for_promotion
        self.default_cost_attention = default_cost_attention

        self._trust_states: Dict[str, CapabilityTrustState] = {}
        self._interruption_timestamps: List[float] = []
        self._history: List[InitiativeVerdict] = []

    # ------------------------------------------------------------------
    # 1. Expected Utility Decision Engine
    # ------------------------------------------------------------------

    def evaluate_intervention(
        self,
        capability_name: str,
        p_success: float,
        u_benefit: float,
        c_wrong: float = 1.5,
        c_undo: float = 0.5,
        c_attention: Optional[float] = None,
        a_accept: float = 0.85,
        p_resolve: float = 0.90,
        u_info: float = 1.0,
        c_latency: float = 0.10,
        is_critical_safety: bool = False,
        proposed_worktree_path: Optional[str] = None,
        modifies_codebase: bool = False,
    ) -> InitiativeVerdict:
        """Evaluate candidate intervention across ACT, SUGGEST, ASK, and SILENT using Expected Utility."""
        c_att = self.default_cost_attention if c_attention is None else c_attention
        p = max(0.0, min(1.0, float(p_success)))

        # Expected Utility Calculations
        eu_act = p * u_benefit - (1.0 - p) * (c_wrong + c_undo) - c_att
        eu_suggest = p * u_benefit * a_accept - c_att
        eu_ask = p_resolve * u_info - c_att - c_latency
        eu_silent = 0.0

        utilities = {
            InitiativeAction.ACT.value: round(eu_act, 4),
            InitiativeAction.SUGGEST.value: round(eu_suggest, 4),
            InitiativeAction.ASK.value: round(eu_ask, 4),
            InitiativeAction.SILENT.value: 0.0,
        }

        # Check trust tier for capability
        with _lock:
            state = self._get_or_create_trust_state(capability_name)
            current_tier = state.current_tier

        # Enforce worktree isolation for code modifications
        worktree_ok = True
        if modifies_codebase:
            worktree_ok = self.enforce_worktree_isolation(proposed_worktree_path)

        # Check hourly interruption budget
        budget_ok = self.check_interruption_budget(is_critical_safety=is_critical_safety)

        # Candidate selection based on max EU
        sorted_candidates = sorted(
            [
                (InitiativeAction.ACT, eu_act),
                (InitiativeAction.SUGGEST, eu_suggest),
                (InitiativeAction.ASK, eu_ask),
                (InitiativeAction.SILENT, eu_silent),
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        best_action, best_eu = sorted_candidates[0]

        # Tier & Safety Clamping
        if best_action == InitiativeAction.ACT:
            if not worktree_ok:
                # Fall back to SUGGEST or ASK if worktree not isolated
                best_action = InitiativeAction.SUGGEST if eu_suggest > 0 else InitiativeAction.SILENT
                rationale = "ACT downgraded: code modifications must run in isolated worktree"
            elif current_tier < AutonomyTier.ACT_WITH_PREVIEW:
                # If tier is SUGGEST or OBSERVE, cannot ACT directly
                best_action = InitiativeAction.SUGGEST if current_tier >= AutonomyTier.SUGGEST and eu_suggest > 0 else InitiativeAction.SILENT
                rationale = f"ACT clamped to {best_action.value} by autonomy tier {current_tier.name}"
            else:
                rationale = f"ACT approved at tier {current_tier.name} (EU={eu_act:.3f})"
        elif best_action in (InitiativeAction.SUGGEST, InitiativeAction.ASK):
            if not budget_ok and not is_critical_safety:
                best_action = InitiativeAction.SILENT
                rationale = "Intervention suppressed by hourly interruption budget limit"
            elif current_tier < AutonomyTier.SUGGEST and best_action == InitiativeAction.SUGGEST:
                best_action = InitiativeAction.SILENT
                rationale = f"SUGGEST suppressed by autonomy tier {current_tier.name}"
            else:
                rationale = f"{best_action.value} approved (EU={best_eu:.3f})"
        else:
            best_action = InitiativeAction.SILENT
            rationale = "Silence is optimal (EU(silent) >= non-zero options)"

        # Record interruption if non-silent
        if best_action in (InitiativeAction.SUGGEST, InitiativeAction.ASK):
            self._record_interruption()

        verdict = InitiativeVerdict(
            selected_action=best_action,
            utility_scores=utilities,
            allowed_by_tier=True,
            budget_available=budget_ok,
            worktree_isolated=worktree_ok,
            rationale=rationale,
        )

        with _lock:
            self._history.append(verdict)

        try:
            get_registry().set_capability_evidence(
                "INITIATIVE_GOVERNOR",
                EvidenceLevel.LIVE,
                f"Selected {best_action.value} for {capability_name} (EU={utilities.get(best_action.value, 0.0)})",
                source="initiative_governor.evaluate_intervention",
            )
        except Exception:
            pass

        return verdict

    # ------------------------------------------------------------------
    # 2. Autonomy Tier Escalation & Trust Tracking
    # ------------------------------------------------------------------

    def _get_or_create_trust_state(self, capability_name: str) -> CapabilityTrustState:
        if capability_name not in self._trust_states:
            self._trust_states[capability_name] = CapabilityTrustState(
                capability_name=capability_name,
                current_tier=AutonomyTier.SUGGEST,
            )
        return self._trust_states[capability_name]

    def record_feedback(self, capability_name: str, accepted: bool) -> CapabilityTrustState:
        """Update Beta(1+s, 1+f) posterior on feedback; promote/demote accordingly."""
        with _lock:
            state = self._get_or_create_trust_state(capability_name)
            state.total_trials += 1
            state.last_updated = time.time()

            if accepted:
                state.successes += 1
                # Check for promotion
                lcb = state.lower_credible_bound(0.95)
                if (
                    lcb >= self.promotion_lcb_threshold
                    and state.total_trials >= self.min_trials_for_promotion
                    and state.current_tier < AutonomyTier.ACT_SILENTLY
                ):
                    old_tier = state.current_tier
                    state.current_tier = AutonomyTier(state.current_tier + 1)
                    log.info(f"[InitiativeGovernor] Promoted {capability_name}: {old_tier.name} -> {state.current_tier.name} (LCB={lcb:.3f})")
            else:
                state.failures += 1
                # Immediate demotion on rejection
                if state.current_tier > AutonomyTier.OBSERVE:
                    old_tier = state.current_tier
                    state.current_tier = AutonomyTier(max(AutonomyTier.OBSERVE, state.current_tier - 1))
                    log.warning(f"[InitiativeGovernor] Demoted {capability_name}: {old_tier.name} -> {state.current_tier.name} on rejection")

            return state

    # ------------------------------------------------------------------
    # 3. Worktree Isolation & Interruption Budget
    # ------------------------------------------------------------------

    @staticmethod
    def enforce_worktree_isolation(proposed_worktree_path: Optional[str]) -> bool:
        """Verify that system-initiated code modifications are strictly isolated in a worktree."""
        if not proposed_worktree_path:
            return False
        clean = proposed_worktree_path.replace("\\", "/").lower()
        # Must be in worktrees directory or an isolated branch path
        is_worktree = "worktree" in clean or ".claude/worktrees" in clean or "proactive_" in clean or "jarvis-proactive" in clean
        return bool(is_worktree)

    def check_interruption_budget(self, now: Optional[float] = None, is_critical_safety: bool = False) -> bool:
        """Enforce strict hourly proactive utterance caps; bypass only for critical invariant warnings."""
        if is_critical_safety:
            return True

        t_now = time.time() if now is None else now
        with _lock:
            # Clean entries older than 3600 seconds
            self._interruption_timestamps = [t for t in self._interruption_timestamps if (t_now - t) <= 3600.0]
            return len(self._interruption_timestamps) < self.hourly_budget

    def _record_interruption(self, now: Optional[float] = None) -> None:
        t_now = time.time() if now is None else now
        with _lock:
            self._interruption_timestamps.append(t_now)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_governor_instance: Optional[InitiativeGovernor] = None


def get_initiative_governor() -> InitiativeGovernor:
    global _governor_instance
    if _governor_instance is None:
        with _lock:
            if _governor_instance is None:
                _governor_instance = InitiativeGovernor()
    return _governor_instance


def evaluate_intervention(capability_name: str, p_success: float, u_benefit: float, **kwargs) -> InitiativeVerdict:
    return get_initiative_governor().evaluate_intervention(capability_name, p_success, u_benefit, **kwargs)


def record_feedback(capability_name: str, accepted: bool) -> CapabilityTrustState:
    return get_initiative_governor().record_feedback(capability_name, accepted)


def check_interruption_budget(**kwargs) -> bool:
    return get_initiative_governor().check_interruption_budget(**kwargs)
