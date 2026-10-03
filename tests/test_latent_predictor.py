"""Tests for Module AB: JEPA-lite Latent Feature Transition Predictor."""
import pytest
from latent_predictor import (
    LatentCode,
    LatentPredictor,
    PredictionRecord,
    encode,
    mean_surprise,
    observe,
    predict_next,
)


_GRID_STATE = {"agent_pos": [2, 3], "door_unlocked": False, "switch_active": True, "inventory": ["key"]}
_NEXT_STATE = {"agent_pos": [2, 4], "door_unlocked": False, "switch_active": True, "inventory": ["key"]}


class TestLatentPredictor:
    def test_encode_state(self):
        lp = LatentPredictor()
        code = lp.encode(_GRID_STATE)
        assert isinstance(code, LatentCode)
        assert code.features["agent_pos_x"] == pytest.approx(0.2, abs=0.01)
        assert code.features["agent_pos_y"] == pytest.approx(0.3, abs=0.01)
        assert code.features["switch_active"] == 1.0
        assert code.features["door_unlocked"] == 0.0
        assert code.features["inventory_size"] == pytest.approx(0.2, abs=0.01)

    def test_predict_next_returns_latent_code(self):
        lp = LatentPredictor()
        z = lp.encode(_GRID_STATE)
        pred = lp.predict_next(z, "DOWN")
        assert isinstance(pred, LatentCode)
        assert set(pred.features.keys()) == set(z.features.keys())

    def test_observe_reduces_surprise_over_time(self):
        """After many identical transitions, the predictor should improve (surprise falls)."""
        lp = LatentPredictor(lr=0.3)

        pre = {"agent_pos": [1, 1], "door_unlocked": False, "switch_active": False, "inventory": []}
        post = {"agent_pos": [1, 2], "door_unlocked": False, "switch_active": False, "inventory": []}

        # Warm up with many identical transitions
        initial_surprises = []
        for _ in range(20):
            rec = lp.observe(pre, "DOWN", post)
            initial_surprises.append(rec.surprise)

        # Surprise at end should be lower than at start
        assert initial_surprises[-1] < initial_surprises[0], (
            f"Surprise did not decrease: first={initial_surprises[0]:.4f}, "
            f"last={initial_surprises[-1]:.4f}"
        )

    def test_mean_surprise_computed(self):
        lp = LatentPredictor()
        pre = {"agent_pos": [3, 3], "door_unlocked": True, "switch_active": False, "inventory": []}
        post = {"agent_pos": [4, 3], "door_unlocked": True, "switch_active": False, "inventory": []}
        lp.observe(pre, "RIGHT", post)
        ms = lp.mean_surprise()
        assert isinstance(ms, float)
        assert ms >= 0.0

    def test_surprise_trend_negative_after_learning(self):
        """Trend should be negative (falling surprise) after repeated identical transitions."""
        lp = LatentPredictor(lr=0.5, window=50)
        pre = {"agent_pos": [0, 0], "door_unlocked": False, "switch_active": False, "inventory": []}
        post = {"agent_pos": [1, 0], "door_unlocked": False, "switch_active": False, "inventory": []}
        for _ in range(40):
            lp.observe(pre, "RIGHT", post)
        trend = lp.surprise_trend()
        assert trend <= 0.0, f"Expected non-positive trend after learning, got {trend}"

    def test_history_length_bounded_by_window(self):
        lp = LatentPredictor(window=10)
        for _ in range(25):
            lp.observe(
                {"agent_pos": [1, 1], "door_unlocked": False, "switch_active": False, "inventory": []},
                "LEFT",
                {"agent_pos": [0, 1], "door_unlocked": False, "switch_active": False, "inventory": []},
            )
        assert len(lp.get_history()) == 10
