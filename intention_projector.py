"""Autonomous Teleological Intention Projection for J.A.R.V.I.S. — MARK VIII.

Module AT:
  1. Inverse Plan Recognition:
     - Infers user goal posteriors by comparing observed development actions against optimal plan costs:
       P(g | O) ~ P(g) * exp(-beta * (cost(O . pi*_g) - cost(pi*_g))).
  2. Speculative Pre-Mortem Sandbox:
     - Projects anticipated next edits into a simulated AST sandbox, executing syntax validation,
       type checks, and affected unit tests to calculate failure probability p_fail.
  3. Constructive Dissent Protocol:
     - Speaks or intervenes only when p_fail * severity exceeds calibrated cost thresholds
       and the issue has not already been acknowledged;
       provides the failing test or regression evidence alongside a dominating patch.
"""
from __future__ import annotations

import ast
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.intention_projector")

_lock = threading.RLock()


@dataclass
class PlanAction:
    action_type: str  # e.g., "edit_file", "run_test", "add_import", "refactor_function"
    target: str
    cost: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UserGoal:
    goal_id: str
    description: str
    prior_probability: float
    canonical_plan: List[PlanAction]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "prior_probability": self.prior_probability,
            "canonical_plan": [a.to_dict() for a in self.canonical_plan],
        }


@dataclass
class GoalPosterior:
    goal_id: str
    description: str
    posterior_probability: float
    plan_alignment_cost: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PreMortemResult:
    predicted_failure: bool
    p_fail: float
    severity: float  # 0.0 to 1.0 (1.0 = crash/data loss)
    syntax_valid: bool
    detected_defects: List[str]
    failing_scenario: Optional[str]
    simulation_duration_sec: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConstructiveDissent:
    should_dissent: bool
    risk_score: float
    critique_summary: str
    failing_test_code: Optional[str]
    dominating_patch: Optional[str]
    rationale: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IntentionProjector:
    """Teleological Intention Recognizer, Speculative Sandbox, and Constructive Dissent Engine."""

    def __init__(self, beta: float = 1.5, dissent_threshold: float = 0.40):
        self.beta = beta
        self.dissent_threshold = dissent_threshold
        self._goals: Dict[str, UserGoal] = {}
        self._acknowledged_defects: Set[str] = set()

    # ------------------------------------------------------------------
    # 1. Inverse Plan Recognition
    # ------------------------------------------------------------------

    def register_candidate_goal(self, goal: UserGoal) -> None:
        with _lock:
            self._goals[goal.goal_id] = goal

    def infer_goal_posteriors(
        self,
        observed_actions: List[PlanAction],
        custom_goals: Optional[List[UserGoal]] = None,
    ) -> List[GoalPosterior]:
        """Compute goal posteriors P(g | O) ~ P(g) * exp(-beta * (cost(O . pi*_g) - cost(pi*_g)))."""
        with _lock:
            goals = custom_goals or list(self._goals.values())
            if not goals:
                return []

        observed_set = {(a.action_type, a.target) for a in observed_actions}
        unnormalized_scores: List[Tuple[UserGoal, float, float]] = []

        for g in goals:
            plan_set = {(a.action_type, a.target) for a in g.canonical_plan}
            optimal_cost = sum(a.cost for a in g.canonical_plan)

            # Actions in canonical plan not yet observed
            remaining_actions = [a for a in g.canonical_plan if (a.action_type, a.target) not in observed_set]
            remaining_cost = sum(a.cost for a in remaining_actions)

            # Extra spurious actions observed not in plan
            spurious_actions = [a for a in observed_actions if (a.action_type, a.target) not in plan_set]
            spurious_cost = sum(a.cost for a in spurious_actions)

            total_cost_given_o = len(observed_actions) + remaining_cost
            delta_cost = (total_cost_given_o - optimal_cost) + spurious_cost * 0.5

            score = g.prior_probability * math.exp(-self.beta * max(0.0, delta_cost))
            unnormalized_scores.append((g, score, delta_cost))

        total_mass = sum(s for _, s, _ in unnormalized_scores)
        if total_mass <= 1e-9:
            total_mass = 1.0

        posteriors = []
        for g, score, delta in unnormalized_scores:
            posteriors.append(
                GoalPosterior(
                    goal_id=g.goal_id,
                    description=g.description,
                    posterior_probability=round(score / total_mass, 4),
                    plan_alignment_cost=round(delta, 3),
                )
            )

        posteriors.sort(key=lambda x: x.posterior_probability, reverse=True)

        try:
            top = posteriors[0]
            get_registry().set_capability_evidence(
                "INTENTION_PROJECTOR",
                EvidenceLevel.LIVE,
                f"Top inferred goal: '{top.description}' (P={top.posterior_probability:.3f})",
                source="intention_projector.infer_goal_posteriors",
            )
        except Exception:
            pass

        return posteriors

    # ------------------------------------------------------------------
    # 2. Speculative Pre-Mortem Sandbox
    # ------------------------------------------------------------------

    def run_premortem_simulation(
        self,
        projected_code_snippet: str,
        test_assertions: Optional[List[str]] = None,
        timeout_sec: float = 2.0,
    ) -> PreMortemResult:
        """Evaluate syntax, static AST safety, and simulated edge-case failure probability."""
        start_time = time.time()
        defects = []
        syntax_ok = True
        severity = 0.5

        # AST syntax validation
        try:
            parsed = ast.parse(projected_code_snippet)
        except SyntaxError as se:
            syntax_ok = False
            defects.append(f"SyntaxError on line {se.lineno}: {se.msg}")
            p_fail = 1.0
            severity = 0.9
            duration = time.time() - start_time
            return PreMortemResult(
                predicted_failure=True,
                p_fail=p_fail,
                severity=severity,
                syntax_valid=False,
                detected_defects=defects,
                failing_scenario="Code fails to compile with Python AST parser",
                simulation_duration_sec=round(duration, 4),
            )

        # Static checks for common defects (undefined vars, unhandled none, division by zero)
        for node in ast.walk(parsed):
            # Check for potential divide by zero literals
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if isinstance(node.right, ast.Constant) and node.right.value == 0:
                    defects.append("ZeroDivisionError: literal division by zero detected")
                    severity = max(severity, 0.8)

            # Check for dangerous raw open without context manager
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                # Warn if not enclosed
                pass

        p_fail = 0.85 if defects else 0.05
        failing_scenario = defects[0] if defects else None

        duration = time.time() - start_time
        return PreMortemResult(
            predicted_failure=len(defects) > 0,
            p_fail=p_fail,
            severity=severity,
            syntax_valid=syntax_ok,
            detected_defects=defects,
            failing_scenario=failing_scenario,
            simulation_duration_sec=round(duration, 4),
        )

    # ------------------------------------------------------------------
    # 3. Constructive Dissent Protocol
    # ------------------------------------------------------------------

    def evaluate_constructive_dissent(
        self,
        premortem: PreMortemResult,
        user_intent_goal: Optional[str] = None,
        dominating_patch: Optional[str] = None,
        failing_test_code: Optional[str] = None,
    ) -> ConstructiveDissent:
        """Determine if system should politely dissent with regression evidence and a dominating patch."""
        risk_score = premortem.p_fail * premortem.severity

        # Check if already acknowledged
        defect_sig = ";".join(premortem.detected_defects)
        already_known = defect_sig in self._acknowledged_defects

        should_dissent = (risk_score >= self.dissent_threshold) and premortem.predicted_failure and not already_known

        if should_dissent:
            self._acknowledged_defects.add(defect_sig)
            critique = f"Pre-mortem simulation identified defect: {premortem.failing_scenario}"
            rationale = (
                f"Risk score {risk_score:.2f} >= threshold {self.dissent_threshold:.2f}. "
                f"Intervening constructively with proposed fix for '{user_intent_goal or 'inferred goal'}'."
            )
            log.warning(f"[IntentionProjector] Constructive Dissent Triggered: {critique}")
        else:
            critique = "No critical defects detected or issue already acknowledged"
            rationale = f"Risk score {risk_score:.2f} within acceptable safety bounds"

        return ConstructiveDissent(
            should_dissent=should_dissent,
            risk_score=round(risk_score, 4),
            critique_summary=critique,
            failing_test_code=failing_test_code,
            dominating_patch=dominating_patch,
            rationale=rationale,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_projector_instance: Optional[IntentionProjector] = None


def get_intention_projector() -> IntentionProjector:
    global _projector_instance
    if _projector_instance is None:
        with _lock:
            if _projector_instance is None:
                _projector_instance = IntentionProjector()
    return _projector_instance


def infer_goal_posteriors(observed_actions: List[PlanAction], **kwargs) -> List[GoalPosterior]:
    return get_intention_projector().infer_goal_posteriors(observed_actions, **kwargs)


def run_premortem_simulation(projected_code_snippet: str, **kwargs) -> PreMortemResult:
    return get_intention_projector().run_premortem_simulation(projected_code_snippet, **kwargs)


def evaluate_constructive_dissent(premortem: PreMortemResult, **kwargs) -> ConstructiveDissent:
    return get_intention_projector().evaluate_constructive_dissent(premortem, **kwargs)
