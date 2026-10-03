"""Tests for Module AK: Online Representation Learning & Fast/Slow Adaptation."""
import numpy as np
import pytest
from representation_learner import (
    RepresentationLearner,
    add_exemplar,
    get_representation_learner,
    interpolate_predictions,
    merge_fast_to_slow,
    query_knn,
    update_fast_weights,
)


class TestRepresentationLearner:
    def test_exemplar_store_and_knn_query(self):
        learner = RepresentationLearner(dim=16)

        # Create distinct clusters for actions "UP" and "DOWN"
        s1 = np.array([1.0] * 8 + [0.0] * 8, dtype=np.float32)  # cluster 1
        s2 = np.array([0.9] * 8 + [0.1] * 8, dtype=np.float32)  # cluster 1
        s3 = np.array([0.0] * 8 + [1.0] * 8, dtype=np.float32)  # cluster 2

        learner.add_exemplar(s1, "UP", "ep_1")
        learner.add_exemplar(s2, "UP", "ep_2")
        learner.add_exemplar(s3, "DOWN", "ep_3")

        # Query near cluster 1
        query_s = np.array([0.95] * 8 + [0.05] * 8, dtype=np.float32)
        probs = learner.query_knn(query_s, k=2)

        assert "UP" in probs
        assert probs["UP"] > 0.80

    def test_knn_lm_interpolation(self):
        learner = RepresentationLearner()
        model_probs = {"UP": 0.5, "DOWN": 0.5}
        knn_probs = {"UP": 0.9, "DOWN": 0.1}

        interp = learner.interpolate_predictions(model_probs, knn_probs, lambda_knn=0.4)
        # UP = 0.6 * 0.5 + 0.4 * 0.9 = 0.30 + 0.36 = 0.66
        # DOWN = 0.6 * 0.5 + 0.4 * 0.1 = 0.30 + 0.04 = 0.34
        assert interp["UP"] == pytest.approx(0.66, abs=0.01)
        assert interp["DOWN"] == pytest.approx(0.34, abs=0.01)

    def test_fast_weight_update_and_slow_merge(self):
        learner = RepresentationLearner(dim=8, rank=2)
        x = np.ones(8, dtype=np.float32)
        grad = np.ones(8, dtype=np.float32) * 2.0

        # Perform fast adaptation
        dnorm = learner.update_fast_weights(x, grad, learning_rate=0.1)
        assert dnorm >= 0.0

        y_adapted = learner.forward_adapted(x)
        assert y_adapted.shape == (8,)

        # Merge fast to slow without P3 lease -> Refused
        res_no_lease = learner.merge_fast_to_slow(has_p3_gpu_lease=False)
        assert res_no_lease == 0.0

        # Merge fast to slow with P3 lease -> Success
        res_lease = learner.merge_fast_to_slow(has_p3_gpu_lease=True)
        assert res_lease > 0.0
