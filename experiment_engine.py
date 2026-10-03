"""Active Experiment Engine for J.A.R.V.I.S. — MARK VIII.

Module X: EIG Query-by-Committee Active Experimentation
  1. Expected Information Gain (EIG):
     - Maintains a committee of world-model hypotheses.
     - Scores proposed actions by the expected reduction in hypothesis entropy.
     - Selects the most discriminative experiment to run next.
  2. Query-by-Committee (QbC):
     - Detects disagreement among committee members on a candidate action's outcome.
     - High-disagreement actions are surfaced for active probing.
  3. Experiment Log:
     - Records experiment results and updates committee weights via Bayesian likelihood.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.experiment_engine")

_lock = threading.RLock()


@dataclass
class HypothesisCommitteeMember:
    member_id: str
    # step_fn: Callable[[dict, str], dict] stored separately in ExperimentEngine
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentResult:
    experiment_id: str
    action: str
    pre_state: Dict[str, Any]
    observed_post_state: Dict[str, Any]
    eig_score: float
    committee_predictions: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ExperimentEngine:
    """EIG Query-by-Committee active experiment selector and result integrator."""

    def __init__(self):
        # Committee: list of (member_id, step_fn, weight)
        self._committee: List[Tuple[str, Callable[[Dict[str, Any], str], Dict[str, Any]], float]] = []
        self._experiment_log: List[ExperimentResult] = []

    # ------------------------------------------------------------------
    # Committee management
    # ------------------------------------------------------------------

    def add_hypothesis(
        self,
        step_fn: Callable[[Dict[str, Any], str], Dict[str, Any]],
        weight: float = 1.0,
        member_id: Optional[str] = None,
    ) -> str:
        """Register a new world-model hypothesis in the committee."""
        mid = member_id or f"member_{uuid.uuid4().hex[:8]}"
        with _lock:
            self._committee.append((mid, step_fn, weight))
        log.debug(f"[ExperimentEngine] Added hypothesis member {mid} (weight={weight})")
        return mid

    def committee_size(self) -> int:
        return len(self._committee)

    # ------------------------------------------------------------------
    # EIG computation
    # ------------------------------------------------------------------

    def _member_predictions(
        self, state: Dict[str, Any], action: str
    ) -> List[Tuple[str, Dict[str, Any], float]]:
        """Collect (member_id, predicted_next_state, weight) for each committee member."""
        predictions = []
        for mid, step_fn, weight in self._committee:
            try:
                import copy
                pred = step_fn(copy.deepcopy(state), action)
                predictions.append((mid, pred, weight))
            except Exception as exc:
                log.warning(f"[ExperimentEngine] Member {mid} raised on action {action!r}: {exc}")
                predictions.append((mid, {}, weight))
        return predictions

    def _prediction_entropy(
        self, predictions: List[Tuple[str, Dict[str, Any], float]]
    ) -> float:
        """Compute entropy over weighted hypothesis predictions.

        Hypotheses that agree on the predicted outcome are grouped; Shannon
        entropy is computed over the normalised weight distribution.
        """
        if not predictions:
            return 0.0

        total_weight = sum(w for _, _, w in predictions)
        if total_weight <= 0:
            return 0.0

        # Group by a fingerprint of the predicted state
        group_weights: Dict[str, float] = {}
        for _, pred, w in predictions:
            key = str(sorted(pred.items())) if pred else "__empty__"
            group_weights[key] = group_weights.get(key, 0.0) + w

        entropy = 0.0
        for gw in group_weights.values():
            p = gw / total_weight
            if p > 0:
                entropy -= p * math.log2(p)
        return entropy

    def compute_eig(self, state: Dict[str, Any], candidate_actions: List[str]) -> Dict[str, float]:
        """Compute Expected Information Gain for each candidate action.

        EIG(a) is approximated as the current committee entropy after observing
        the committee's predictions for action `a` (Query-by-Committee proxy).
        """
        eig_scores: Dict[str, float] = {}
        for action in candidate_actions:
            preds = self._member_predictions(state, action)
            eig_scores[action] = round(self._prediction_entropy(preds), 6)
        return eig_scores

    def select_best_experiment(
        self,
        state: Dict[str, Any],
        candidate_actions: List[str],
    ) -> Tuple[str, float]:
        """Return the action with highest EIG (most informative experiment)."""
        if not candidate_actions:
            raise ValueError("candidate_actions must be non-empty")
        if not self._committee:
            raise RuntimeError("Committee is empty — add hypotheses first")

        eig_scores = self.compute_eig(state, candidate_actions)
        best_action = max(eig_scores, key=lambda a: eig_scores[a])
        return best_action, eig_scores[best_action]

    # ------------------------------------------------------------------
    # QbC disagreement
    # ------------------------------------------------------------------

    def query_by_committee_disagreement(
        self, state: Dict[str, Any], action: str
    ) -> float:
        """Return fraction of committee member pairs that disagree on the outcome."""
        preds = self._member_predictions(state, action)
        if len(preds) < 2:
            return 0.0

        pred_states = [pred for _, pred, _ in preds]
        n = len(pred_states)
        disagreements = 0
        total_pairs = 0
        for i in range(n):
            for j in range(i + 1, n):
                total_pairs += 1
                if pred_states[i] != pred_states[j]:
                    disagreements += 1
        return round(disagreements / max(1, total_pairs), 4)

    # ------------------------------------------------------------------
    # Bayesian weight update
    # ------------------------------------------------------------------

    def record_experiment(
        self,
        action: str,
        pre_state: Dict[str, Any],
        observed_post_state: Dict[str, Any],
        eig_score: float = 0.0,
    ) -> ExperimentResult:
        """Record observed outcome and update committee member weights (Bayesian likelihood)."""
        preds = self._member_predictions(pre_state, action)
        committee_predictions = [
            {"member_id": mid, "predicted": pred, "weight_before": w}
            for mid, pred, w in preds
        ]

        # Update weights: boost members whose prediction matched the observation
        new_committee = []
        for mid, step_fn, weight in self._committee:
            try:
                import copy
                pred = step_fn(copy.deepcopy(pre_state), action)
                if pred == observed_post_state:
                    new_weight = weight * 2.0  # likelihood boosted
                else:
                    new_weight = weight * 0.5  # penalise mismatch
            except Exception:
                new_weight = weight * 0.25
            new_committee.append((mid, step_fn, max(new_weight, 1e-6)))

        with _lock:
            self._committee = new_committee

        result = ExperimentResult(
            experiment_id=f"exp_{uuid.uuid4().hex[:8]}",
            action=action,
            pre_state=pre_state,
            observed_post_state=observed_post_state,
            eig_score=eig_score,
            committee_predictions=committee_predictions,
        )
        self._experiment_log.append(result)
        log.info(
            f"[ExperimentEngine] Recorded experiment {result.experiment_id} "
            f"(action={action!r}, eig={eig_score:.4f})"
        )

        try:
            get_registry().set_capability_evidence(
                "EXPERIMENT_ENGINE",
                EvidenceLevel.LIVE,
                f"Ran experiment #{len(self._experiment_log)} via QbC EIG "
                f"(action={action!r}, eig={eig_score:.4f})",
                source="experiment_engine.record_experiment",
            )
        except Exception:
            pass

        return result

    def get_experiment_log(self) -> List[ExperimentResult]:
        return list(self._experiment_log)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_engine_instance: Optional[ExperimentEngine] = None


def get_experiment_engine() -> ExperimentEngine:
    global _engine_instance
    if _engine_instance is None:
        with _lock:
            if _engine_instance is None:
                _engine_instance = ExperimentEngine()
    return _engine_instance


def add_hypothesis(
    step_fn: Callable[[Dict[str, Any], str], Dict[str, Any]],
    weight: float = 1.0,
    member_id: Optional[str] = None,
) -> str:
    return get_experiment_engine().add_hypothesis(step_fn, weight, member_id)


def compute_eig(state: Dict[str, Any], candidate_actions: List[str]) -> Dict[str, float]:
    return get_experiment_engine().compute_eig(state, candidate_actions)


def select_best_experiment(
    state: Dict[str, Any], candidate_actions: List[str]
) -> Tuple[str, float]:
    return get_experiment_engine().select_best_experiment(state, candidate_actions)


def query_by_committee_disagreement(state: Dict[str, Any], action: str) -> float:
    return get_experiment_engine().query_by_committee_disagreement(state, action)


def record_experiment(
    action: str,
    pre_state: Dict[str, Any],
    observed_post_state: Dict[str, Any],
    eig_score: float = 0.0,
) -> ExperimentResult:
    return get_experiment_engine().record_experiment(action, pre_state, observed_post_state, eig_score)
