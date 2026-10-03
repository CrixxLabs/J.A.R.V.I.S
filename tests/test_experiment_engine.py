"""Tests for Module X: EIG Query-by-Committee Active Experimentation."""
import pytest
from experiment_engine import (
    ExperimentEngine,
    add_hypothesis,
    compute_eig,
    get_experiment_engine,
    query_by_committee_disagreement,
    record_experiment,
    select_best_experiment,
)


def _step_model_a(s, a):
    """Standard grid step."""
    s = dict(s)
    pos = list(s.get("agent_pos", [1, 1]))
    if a == "UP":
        pos[1] -= 1
    elif a == "DOWN":
        pos[1] += 1
    elif a == "LEFT":
        pos[0] -= 1
    elif a == "RIGHT":
        pos[0] += 1
    s["agent_pos"] = pos
    return s


def _step_model_b(s, a):
    """Inverted controls grid step."""
    s = dict(s)
    pos = list(s.get("agent_pos", [1, 1]))
    if a == "UP":
        pos[1] += 1
    elif a == "DOWN":
        pos[1] -= 1
    elif a == "LEFT":
        pos[0] += 1
    elif a == "RIGHT":
        pos[0] -= 1
    s["agent_pos"] = pos
    return s


def _step_model_c(s, a):
    """Null step (never moves)."""
    return dict(s)


class TestExperimentEngine:
    def test_add_hypothesis(self):
        engine = ExperimentEngine()
        m1 = engine.add_hypothesis(_step_model_a, weight=1.0)
        m2 = engine.add_hypothesis(_step_model_b, weight=1.0)
        assert engine.committee_size() == 2
        assert m1 != m2

    def test_compute_eig_and_disagreement(self):
        engine = ExperimentEngine()
        engine.add_hypothesis(_step_model_a, weight=1.0)
        engine.add_hypothesis(_step_model_b, weight=1.0)

        state = {"agent_pos": [2, 2]}
        actions = ["UP", "DOWN", "LEFT", "RIGHT"]
        eig = engine.compute_eig(state, actions)

        # Since models A and B predict different positions for all movement actions,
        # entropy should be > 0 (in fact = 1.0 bit for 2 equal-weight distinct outcomes)
        for act in actions:
            assert eig[act] > 0.0

        disagreement = engine.query_by_committee_disagreement(state, "UP")
        assert disagreement == 1.0  # 100% disagreement between the two models

    def test_select_best_experiment(self):
        engine = ExperimentEngine()
        engine.add_hypothesis(_step_model_a, weight=1.0)
        engine.add_hypothesis(_step_model_b, weight=1.0)
        engine.add_hypothesis(_step_model_c, weight=1.0)

        state = {"agent_pos": [2, 2]}
        actions = ["UP", "DOWN", "NOOP"]

        # NOOP should have lower EIG because none of the models move on NOOP (or they agree)
        best_act, best_score = engine.select_best_experiment(state, actions)
        assert best_act in ["UP", "DOWN"]
        assert best_score > 0.0

    def test_record_experiment_bayesian_update(self):
        engine = ExperimentEngine()
        engine.add_hypothesis(_step_model_a, weight=1.0, member_id="standard")
        engine.add_hypothesis(_step_model_b, weight=1.0, member_id="inverted")

        state = {"agent_pos": [2, 2]}
        # We execute UP, and the real world observed is inverted (y becomes 3)
        observed = {"agent_pos": [2, 3]}

        res = engine.record_experiment("UP", state, observed, eig_score=1.0)
        assert res.action == "UP"
        assert res.observed_post_state == observed

        # Check that inverted model got boosted relative to standard model
        committee = {mid: w for mid, _, w in engine._committee}
        assert committee["inverted"] > committee["standard"]
