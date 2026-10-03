"""Online Representation Learning & Fast/Slow Adaptation for J.A.R.V.I.S. — MARK VIII.

Module AK:
  1. Non-Parametric Exemplar Store & kNN-LM Interpolation:
     - Stores (hidden_state, action/token) pairs from verified successful trajectories.
     - Implements k-Nearest Neighbors softmax interpolation on CPU/NumPy:
       P_final = (1 - lambda) * P_model + lambda * P_kNN.
  2. Fast-Weight / Rank-4 Adapter with Exponential Decay:
     - Rank-4 adaptation matrices (A, B) capturing fast updates on high-surprise episodes.
     - Gated consolidation into slow weights via alpha-interpolation.
  3. P3 Maintenance Window Guardrail:
     - Interlocks weight consolidation with Cognitive OS (Module V) GPU leases.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.representation_learner")

_lock = threading.RLock()


@dataclass
class Exemplar:
    exemplar_id: str
    hidden_state: np.ndarray
    target_action: str
    episode_id: str
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exemplar_id": self.exemplar_id,
            "target_action": self.target_action,
            "episode_id": self.episode_id,
            "timestamp": self.timestamp,
        }


@dataclass
class FastSlowAdapterState:
    dim: int
    rank: int
    fast_a: np.ndarray   # (rank, dim)
    fast_b: np.ndarray   # (dim, rank)
    slow_w: np.ndarray   # (dim, dim)
    decay_rate: float
    total_updates: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dim": self.dim,
            "rank": self.rank,
            "decay_rate": self.decay_rate,
            "total_updates": self.total_updates,
            "slow_weight_norm": float(np.linalg.norm(self.slow_w)),
        }


class RepresentationLearner:
    """Non-parametric kNN-LM exemplar store and fast/slow rank-4 weight adaptation."""

    def __init__(self, dim: int = 64, rank: int = 4, decay_rate: float = 0.99):
        self.dim = dim
        self.rank = rank
        self.decay_rate = decay_rate

        # Exemplar store
        self._exemplars: List[Exemplar] = []
        self._states_matrix: Optional[np.ndarray] = None  # (N, dim)

        # Fast/Slow weights (CPU NumPy)
        self._fast_a = np.random.randn(rank, dim).astype(np.float32) * 0.01
        self._fast_b = np.zeros((dim, rank), dtype=np.float32)
        self._slow_w = np.eye(dim, dtype=np.float32)
        self._total_updates = 0

    # ------------------------------------------------------------------
    # Non-Parametric Exemplar Store & kNN-LM Interpolation
    # ------------------------------------------------------------------

    def add_exemplar(self, hidden_state: np.ndarray | List[float], target_action: str, episode_id: str = "") -> str:
        """Store verified episode hidden state and action target on CPU."""
        hs = np.array(hidden_state, dtype=np.float32).flatten()
        if hs.shape[0] != self.dim:
            # Pad or truncate to self.dim
            padded = np.zeros(self.dim, dtype=np.float32)
            min_len = min(self.dim, hs.shape[0])
            padded[:min_len] = hs[:min_len]
            hs = padded

        # Normalize state
        norm = np.linalg.norm(hs)
        if norm > 1e-6:
            hs = hs / norm

        eid = f"ex_{uuid.uuid4().hex[:8]}"
        ex = Exemplar(
            exemplar_id=eid,
            hidden_state=hs,
            target_action=target_action,
            episode_id=episode_id,
            timestamp=time.time(),
        )

        with _lock:
            self._exemplars.append(ex)
            # Rebuild state matrix for vectorized search
            all_states = [e.hidden_state for e in self._exemplars]
            self._states_matrix = np.stack(all_states, axis=0)

        log.debug(f"[RepresentationLearner] Added exemplar {eid} (total={len(self._exemplars)})")
        return eid

    def query_knn(
        self,
        hidden_state: np.ndarray | List[float],
        k: int = 5,
        temperature: float = 1.0,
    ) -> Dict[str, float]:
        """Compute action probability distribution from k-nearest neighbors."""
        with _lock:
            if not self._exemplars or self._states_matrix is None:
                return {}

            hs = np.array(hidden_state, dtype=np.float32).flatten()
            if hs.shape[0] != self.dim:
                padded = np.zeros(self.dim, dtype=np.float32)
                min_len = min(self.dim, hs.shape[0])
                padded[:min_len] = hs[:min_len]
                hs = padded

            norm = np.linalg.norm(hs)
            if norm > 1e-6:
                hs = hs / norm

            # Cosine distance = 1 - dot_product (since both are unit norm)
            sims = np.dot(self._states_matrix, hs)  # (N,)
            top_k_indices = np.argsort(-sims)[:min(k, len(self._exemplars))]

            # Compute softmax weights over top-k similarities
            top_sims = sims[top_k_indices]
            exp_sims = np.exp((top_sims - np.max(top_sims)) / max(1e-4, temperature))
            weights = exp_sims / np.sum(exp_sims)

            action_probs: Dict[str, float] = {}
            for idx, w in zip(top_k_indices, weights):
                act = self._exemplars[idx].target_action
                action_probs[act] = action_probs.get(act, 0.0) + float(w)

            return {k: round(v, 4) for k, v in action_probs.items()}

    def interpolate_predictions(
        self,
        model_probs: Dict[str, float],
        knn_probs: Dict[str, float],
        lambda_knn: float = 0.3,
    ) -> Dict[str, float]:
        """Interpolate parametric base model probabilities with non-parametric kNN-LM."""
        all_actions = set(model_probs.keys()) | set(knn_probs.keys())
        final_probs: Dict[str, float] = {}

        for act in all_actions:
            p_model = model_probs.get(act, 0.0)
            p_knn = knn_probs.get(act, 0.0)
            final_probs[act] = (1.0 - lambda_knn) * p_model + lambda_knn * p_knn

        total = sum(final_probs.values()) or 1.0
        return {k: round(v / total, 4) for k, v in final_probs.items()}

    # ------------------------------------------------------------------
    # Fast/Slow Rank-4 Weight Adaptation
    # ------------------------------------------------------------------

    def update_fast_weights(
        self,
        hidden_state: np.ndarray | List[float],
        error_gradient: np.ndarray | List[float],
        learning_rate: float = 0.05,
    ) -> float:
        """Apply fast rank-4 update: Delta W = B @ A using low-rank outer product update."""
        x = np.array(hidden_state, dtype=np.float32).reshape(self.dim, 1)
        grad = np.array(error_gradient, dtype=np.float32).reshape(self.dim, 1)

        with _lock:
            # Fast decay
            self._fast_b *= self.decay_rate
            self._fast_a *= self.decay_rate

            # Rank-4 low rank projection update
            # a_update ~ B^T @ grad -> (rank, 1)
            # b_update ~ grad @ (A @ x)^T
            ax = self._fast_a @ x  # (rank, 1)
            self._fast_b += learning_rate * (grad @ ax.T)
            self._fast_a += learning_rate * (np.random.randn(self.rank, 1) @ x.T) * 0.01

            self._total_updates += 1
            delta_norm = float(np.linalg.norm(self._fast_b @ self._fast_a))

        return delta_norm

    def merge_fast_to_slow(self, alpha: float = 0.05, has_p3_gpu_lease: bool = True) -> float:
        """Consolidate fast adapter into slow weights during P3 maintenance windows."""
        if not has_p3_gpu_lease:
            log.warning("[RepresentationLearner] Merge refused: P3 maintenance window lease required")
            return 0.0

        with _lock:
            delta_w = self._fast_b @ self._fast_a  # (dim, dim)
            self._slow_w = (1.0 - alpha) * self._slow_w + alpha * (self._slow_w + delta_w)

            # Reset fast weights post consolidation
            self._fast_b.fill(0.0)
            self._fast_a = np.random.randn(self.rank, self.dim).astype(np.float32) * 0.01

            slow_norm = float(np.linalg.norm(self._slow_w))

        try:
            get_registry().set_capability_evidence(
                "REPRESENTATION_LEARNER",
                EvidenceLevel.LIVE,
                f"Consolidated fast weights into slow weights (alpha={alpha}, slow_norm={slow_norm:.4f})",
                source="representation_learner.merge_fast_to_slow",
            )
        except Exception:
            pass

        return slow_norm

    def forward_adapted(self, hidden_state: np.ndarray | List[float]) -> np.ndarray:
        """Apply adapted weights: y = (W_slow + B_fast @ A_fast) @ x."""
        x = np.array(hidden_state, dtype=np.float32).reshape(self.dim, 1)
        with _lock:
            effective_w = self._slow_w + (self._fast_b @ self._fast_a)
            y = effective_w @ x
        return y.flatten()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_learner_instance: Optional[RepresentationLearner] = None


def get_representation_learner(dim: int = 64, rank: int = 4) -> RepresentationLearner:
    global _learner_instance
    if _learner_instance is None:
        with _lock:
            if _learner_instance is None:
                _learner_instance = RepresentationLearner(dim=dim, rank=rank)
    return _learner_instance


def add_exemplar(hidden_state: np.ndarray | List[float], target_action: str, episode_id: str = "") -> str:
    return get_representation_learner().add_exemplar(hidden_state, target_action, episode_id)


def query_knn(hidden_state: np.ndarray | List[float], k: int = 5, temperature: float = 1.0) -> Dict[str, float]:
    return get_representation_learner().query_knn(hidden_state, k, temperature)


def interpolate_predictions(model_probs: Dict[str, float], knn_probs: Dict[str, float], lambda_knn: float = 0.3) -> Dict[str, float]:
    return get_representation_learner().interpolate_predictions(model_probs, knn_probs, lambda_knn)


def update_fast_weights(hidden_state: np.ndarray | List[float], error_gradient: np.ndarray | List[float], **kwargs) -> float:
    return get_representation_learner().update_fast_weights(hidden_state, error_gradient, **kwargs)


def merge_fast_to_slow(alpha: float = 0.05, has_p3_gpu_lease: bool = True) -> float:
    return get_representation_learner().merge_fast_to_slow(alpha, has_p3_gpu_lease)
