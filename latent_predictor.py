"""Latent Predictor (JEPA-lite) for J.A.R.V.I.S. — MARK VIII.

Module AB: Joint Embedding Predictive Architecture (JEPA-lite)
  1. Latent Feature Encoder:
     - Maps raw state dicts into compressed, symbolic feature vectors (latent codes).
  2. Latent Transition Predictor:
     - Predicts the next latent code x̂_{t+1} = f(z_t, a_t) using a lightweight
       linear model over handcrafted symbolic features.
  3. Prediction-Error Tracking:
     - Maintains a rolling window of per-feature prediction errors.
     - Computes surprise = ||z_{t+1} - ẑ_{t+1}||₂ for each observed transition.
  4. Online Update:
     - Gradient-free delta-rule weight update to reduce future prediction errors.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.latent_predictor")

_lock = threading.RLock()

# Feature extraction keys for symbolic state dicts
_FEATURE_KEYS = ["agent_pos_x", "agent_pos_y", "door_unlocked", "switch_active", "inventory_size"]


@dataclass
class LatentCode:
    """Compact symbolic feature vector encoding a state."""
    features: Dict[str, float]      # key -> normalised scalar

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def as_vector(self, keys: List[str]) -> List[float]:
        return [self.features.get(k, 0.0) for k in keys]


@dataclass
class PredictionRecord:
    t: int
    action: str
    actual_code: LatentCode
    predicted_code: LatentCode
    surprise: float                   # L2 norm of prediction error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": self.t,
            "action": self.action,
            "actual_features": self.actual_code.features,
            "predicted_features": self.predicted_code.features,
            "surprise": self.surprise,
        }


class LatentPredictor:
    """JEPA-lite: encode states as latent codes, predict next latent, track surprise."""

    def __init__(self, lr: float = 0.1, window: int = 50):
        self._lr = lr
        self._window = window
        self._keys = list(_FEATURE_KEYS)

        # Per-action linear weight tables: action -> {input_key -> {output_key: weight}}
        self._weights: Dict[str, Dict[str, Dict[str, float]]] = {}

        # Rolling prediction history
        self._history: deque = deque(maxlen=window)
        self._t = 0

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    def encode(self, state: Dict[str, Any]) -> LatentCode:
        """Map a raw state dict to a normalised symbolic feature vector."""
        features: Dict[str, float] = {}

        # agent_pos
        pos = state.get("agent_pos", [0, 0])
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            features["agent_pos_x"] = float(pos[0]) / 10.0   # normalise by max grid ~10
            features["agent_pos_y"] = float(pos[1]) / 10.0
        else:
            features["agent_pos_x"] = 0.0
            features["agent_pos_y"] = 0.0

        features["door_unlocked"] = 1.0 if state.get("door_unlocked") else 0.0
        features["switch_active"] = 1.0 if state.get("switch_active") else 0.0
        features["inventory_size"] = float(len(state.get("inventory", []))) / 5.0

        return LatentCode(features=features)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def _get_weights(self, action: str) -> Dict[str, Dict[str, float]]:
        """Lazily initialise per-action weight table (identity residual)."""
        if action not in self._weights:
            # Start from identity: predict no change
            self._weights[action] = {
                out_k: {in_k: (1.0 if in_k == out_k else 0.0) for in_k in self._keys}
                for out_k in self._keys
            }
        return self._weights[action]

    def predict_next(self, current_code: LatentCode, action: str) -> LatentCode:
        """Predict z_{t+1} = W_a @ z_t (linear, feature-level)."""
        W = self._get_weights(action)
        z = current_code.features

        pred_features: Dict[str, float] = {}
        for out_k in self._keys:
            val = sum(W[out_k].get(in_k, 0.0) * z.get(in_k, 0.0) for in_k in self._keys)
            pred_features[out_k] = round(val, 6)

        return LatentCode(features=pred_features)

    # ------------------------------------------------------------------
    # Observation & online update
    # ------------------------------------------------------------------

    def _l2(self, a: LatentCode, b: LatentCode) -> float:
        return math.sqrt(
            sum((a.features.get(k, 0.0) - b.features.get(k, 0.0)) ** 2 for k in self._keys)
        )

    def observe(
        self,
        pre_state: Dict[str, Any],
        action: str,
        post_state: Dict[str, Any],
    ) -> PredictionRecord:
        """Observe a real transition, compute surprise, and update weights (delta rule)."""
        z_t = self.encode(pre_state)
        z_t1_pred = self.predict_next(z_t, action)
        z_t1_actual = self.encode(post_state)

        surprise = round(self._l2(z_t1_actual, z_t1_pred), 6)

        # Delta-rule weight update: W[out_k][in_k] += lr * error[out_k] * z_t[in_k]
        W = self._get_weights(action)
        with _lock:
            for out_k in self._keys:
                error = z_t1_actual.features.get(out_k, 0.0) - z_t1_pred.features.get(out_k, 0.0)
                for in_k in self._keys:
                    W[out_k][in_k] += self._lr * error * z_t.features.get(in_k, 0.0)

        rec = PredictionRecord(
            t=self._t,
            action=action,
            actual_code=z_t1_actual,
            predicted_code=z_t1_pred,
            surprise=surprise,
        )
        self._history.append(rec)
        self._t += 1

        log.debug(f"[LatentPredictor] t={self._t} action={action!r} surprise={surprise:.4f}")
        return rec

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def mean_surprise(self) -> float:
        """Rolling mean surprise over the history window."""
        if not self._history:
            return 0.0
        return round(sum(r.surprise for r in self._history) / len(self._history), 6)

    def surprise_trend(self) -> float:
        """Positive if surprise is increasing (getting worse), negative if decreasing."""
        history = list(self._history)
        if len(history) < 4:
            return 0.0
        mid = len(history) // 2
        early = sum(r.surprise for r in history[:mid]) / mid
        late = sum(r.surprise for r in history[mid:]) / (len(history) - mid)
        return round(late - early, 6)

    def get_history(self) -> List[PredictionRecord]:
        return list(self._history)

    def register_evidence(self) -> None:
        try:
            get_registry().set_capability_evidence(
                "LATENT_PREDICTOR",
                EvidenceLevel.LIVE,
                f"JEPA-lite tracking {len(self._history)} transitions; "
                f"mean_surprise={self.mean_surprise():.4f}",
                source="latent_predictor.observe",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_predictor_instance: Optional[LatentPredictor] = None


def get_latent_predictor(lr: float = 0.1) -> LatentPredictor:
    global _predictor_instance
    if _predictor_instance is None:
        with _lock:
            if _predictor_instance is None:
                _predictor_instance = LatentPredictor(lr=lr)
    return _predictor_instance


def encode(state: Dict[str, Any]) -> LatentCode:
    return get_latent_predictor().encode(state)


def predict_next(current_code: LatentCode, action: str) -> LatentCode:
    return get_latent_predictor().predict_next(current_code, action)


def observe(
    pre_state: Dict[str, Any],
    action: str,
    post_state: Dict[str, Any],
) -> PredictionRecord:
    rec = get_latent_predictor().observe(pre_state, action, post_state)
    get_latent_predictor().register_evidence()
    return rec


def mean_surprise() -> float:
    return get_latent_predictor().mean_surprise()
