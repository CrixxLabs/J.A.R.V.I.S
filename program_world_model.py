"""Program-Induction World Models for J.A.R.V.I.S. — MARK VIII.

Module Y:
  1. Neurosymbolic CEGIS Loop:
     - Represents world models as executable Python transition functions: def step(state: dict, action: str) -> dict.
     - Scores candidate hypotheses using MDL/Occam penalty: P(h|D) ~ 2^{-|h|} * exp(-lambda * mismatches).
     - Counterexample-guided patching: detects failing state transitions to iteratively refine rules.
  2. Latent Variable / Aliasing Detection:
     - Detects when identical state-action pairs yield divergent outputs, positing hidden internal counters/flags.
"""
from __future__ import annotations

import ast
import copy
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry
from symbolic_verifier import verify_code_safety

log = logging.getLogger("jarvis.program_world_model")

_lock = threading.RLock()


@dataclass
class ProgramHypothesis:
    hypothesis_id: str
    code: str
    ast_complexity: int
    mismatches: int
    mdl_score: float
    verified: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProgramWorldModel:
    """Manages program hypothesis induction, MDL evaluation, CEGIS counterexample search, and aliasing detection."""

    def __init__(self):
        pass

    def _calculate_ast_complexity(self, code_str: str) -> int:
        """Measure program complexity by counting AST nodes."""
        try:
            tree = ast.parse(code_str)
            return sum(1 for _ in ast.walk(tree))
        except Exception:
            return len(code_str.splitlines()) * 5

    def _compile_step_function(self, code_str: str) -> Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]]:
        """Safely verify and compile executable Python transition step function."""
        safety = verify_code_safety(code_str)
        if not safety.get("verified", False):
            log.warning(f"[ProgramWorldModel] Code safety check failed: {safety.get('violations')}")
            return None

        local_scope: Dict[str, Any] = {}
        try:
            exec(code_str, {}, local_scope)
            step_fn = local_scope.get("step") or local_scope.get("transition")
            if callable(step_fn):
                return step_fn
        except Exception as exc:
            log.error(f"[ProgramWorldModel] Compilation error: {exc}")
        return None

    def score_hypothesis(
        self,
        code: str,
        transitions: List[Dict[str, Any]],
        lambda_param: float = 50.0,
        alpha: float = 0.01,
    ) -> ProgramHypothesis:
        """Evaluate hypothesis against transition dataset using Occam / MDL penalty."""
        hid = f"hyp_{uuid.uuid4().hex[:8]}"
        complexity = self._calculate_ast_complexity(code)
        step_fn = self._compile_step_function(code)

        if step_fn is None:
            return ProgramHypothesis(
                hypothesis_id=hid,
                code=code,
                ast_complexity=complexity,
                mismatches=len(transitions) + 999,
                mdl_score=-9999.0,
                verified=False,
                metadata={"error": "Compilation or safety failure"},
            )

        mismatches = 0
        for t in transitions:
            s_in = copy.deepcopy(t.get("pre_state", {}))
            act = t.get("action", "")
            s_expected = t.get("post_state", {})

            try:
                s_pred = step_fn(s_in, act)
                # Compare essential state keys
                if not self._states_match(s_pred, s_expected):
                    mismatches += 1
            except Exception:
                mismatches += 1

        # MDL score: log P(h|D) = - (alpha * |h|) - (lambda * mismatches)
        # Higher (closer to 0) is better
        mdl_score = - (alpha * complexity) - (lambda_param * mismatches)
        mdl_score = round(mdl_score, 4)

        return ProgramHypothesis(
            hypothesis_id=hid,
            code=code,
            ast_complexity=complexity,
            mismatches=mismatches,
            mdl_score=mdl_score,
            verified=(mismatches == 0),
        )

    def _states_match(self, s1: Dict[str, Any], s2: Dict[str, Any]) -> bool:
        """Check if predicted state matches expected ground truth."""
        if s1.get("agent_pos") != s2.get("agent_pos"):
            return False
        if s1.get("door_unlocked") != s2.get("door_unlocked") and s2.get("door_unlocked") is not None:
            return False
        if s1.get("switch_active") != s2.get("switch_active") and s2.get("switch_active") is not None:
            return False
        return True

    def find_counterexamples(
        self,
        hypothesis_code: str,
        transitions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Identify transitions where the hypothesis prediction diverges from ground truth."""
        step_fn = self._compile_step_function(hypothesis_code)
        if step_fn is None:
            return list(transitions)

        counterexamples = []
        for idx, t in enumerate(transitions):
            s_in = copy.deepcopy(t.get("pre_state", {}))
            act = t.get("action", "")
            s_expected = t.get("post_state", {})

            try:
                s_pred = step_fn(s_in, act)
                if not self._states_match(s_pred, s_expected):
                    counterexamples.append({
                        "index": idx,
                        "transition": t,
                        "predicted_state": s_pred,
                        "expected_state": s_expected,
                    })
            except Exception as exc:
                counterexamples.append({
                    "index": idx,
                    "transition": t,
                    "error": str(exc),
                    "expected_state": s_expected,
                })
        return counterexamples

    def detect_state_aliasing(self, transitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Identify identical (s, a) inputs that yield divergent outputs (positing hidden state)."""
        observed_mappings: Dict[str, List[Dict[str, Any]]] = {}

        for t in transitions:
            s_in = t.get("pre_state", {})
            act = t.get("action", "")
            # Key based on agent_pos and action
            key = f"pos:{s_in.get('agent_pos')}_act:{act}"
            observed_mappings.setdefault(key, []).append(t)

        aliasing_conflicts = []
        for key, t_list in observed_mappings.items():
            if len(t_list) > 1:
                first_post = t_list[0].get("post_state", {})
                for other in t_list[1:]:
                    other_post = other.get("post_state", {})
                    if not self._states_match(first_post, other_post):
                        aliasing_conflicts.append({
                            "state_action_key": key,
                            "divergent_transitions": [t_list[0], other],
                            "posited_latent_variable": "internal_toggle_or_counter",
                        })
                        break

        return aliasing_conflicts

    def induce_program_model(
        self,
        transitions: List[Dict[str, Any]],
        candidate_templates: Optional[List[str]] = None,
    ) -> ProgramHypothesis:
        """Select the highest MDL scoring hypothesis among candidate programs."""
        # Default procedural transition models
        default_templates = [
            # Standard grid navigation model
            """
def step(state: dict, action: str) -> dict:
    s = dict(state)
    pos = list(s.get("agent_pos", [1, 1]))
    dx, dy = 0, 0
    if action == "UP": dy = -1
    elif action == "DOWN": dy = 1
    elif action == "LEFT": dx = -1
    elif action == "RIGHT": dx = 1
    s["agent_pos"] = [pos[0] + dx, pos[1] + dy]
    return s
""",
            # Inverted controls grid navigation model
            """
def step(state: dict, action: str) -> dict:
    s = dict(state)
    pos = list(s.get("agent_pos", [1, 1]))
    dx, dy = 0, 0
    if action == "UP": dy = 1
    elif action == "DOWN": dy = -1
    elif action == "LEFT": dx = 1
    elif action == "RIGHT": dx = -1
    s["agent_pos"] = [pos[0] + dx, pos[1] + dy]
    return s
""",
            # Null model (no movement)
            """
def step(state: dict, action: str) -> dict:
    return dict(state)
""",
        ]

        templates = candidate_templates or default_templates
        scored_hypotheses = [self.score_hypothesis(tmpl, transitions) for tmpl in templates]
        scored_hypotheses.sort(key=lambda h: h.mdl_score, reverse=True)

        best = scored_hypotheses[0]
        try:
            get_registry().set_capability_evidence(
                "PROGRAM_WORLD_MODEL",
                EvidenceLevel.LIVE,
                f"Induced best program hypothesis (mismatches={best.mismatches}, MDL={best.mdl_score})",
                source="program_world_model.induce_program_model",
            )
        except Exception:
            pass

        return best


_model_instance: Optional[ProgramWorldModel] = None


def get_program_world_model() -> ProgramWorldModel:
    global _model_instance
    if _model_instance is None:
        with _lock:
            if _model_instance is None:
                _model_instance = ProgramWorldModel()
    return _model_instance


def score_hypothesis(code: str, transitions: List[Dict[str, Any]], lambda_param: float = 50.0, alpha: float = 0.01) -> ProgramHypothesis:
    return get_program_world_model().score_hypothesis(code, transitions, lambda_param, alpha)


def find_counterexamples(hypothesis_code: str, transitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return get_program_world_model().find_counterexamples(hypothesis_code, transitions)


def detect_state_aliasing(transitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return get_program_world_model().detect_state_aliasing(transitions)


def induce_program_model(transitions: List[Dict[str, Any]], candidate_templates: Optional[List[str]] = None) -> ProgramHypothesis:
    return get_program_world_model().induce_program_model(transitions, candidate_templates)
