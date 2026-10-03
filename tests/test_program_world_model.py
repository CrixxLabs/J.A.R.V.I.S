"""Unit tests for Program-Induction World Models (Module Y)."""
import pytest

from program_world_model import (
    ProgramWorldModel,
    score_hypothesis,
    find_counterexamples,
    detect_state_aliasing,
    induce_program_model,
)


@pytest.fixture
def model():
    return ProgramWorldModel()


def test_score_hypothesis_mdl_ranking(model):
    transitions = [
        {"action": "RIGHT", "pre_state": {"agent_pos": [1, 1]}, "post_state": {"agent_pos": [2, 1]}},
        {"action": "DOWN", "pre_state": {"agent_pos": [2, 1]}, "post_state": {"agent_pos": [2, 2]}},
    ]

    standard_code = """
def step(state: dict, action: str) -> dict:
    s = dict(state)
    pos = list(s.get("agent_pos", [1, 1]))
    if action == "RIGHT": pos[0] += 1
    elif action == "DOWN": pos[1] += 1
    s["agent_pos"] = pos
    return s
"""
    inverted_code = """
def step(state: dict, action: str) -> dict:
    s = dict(state)
    pos = list(s.get("agent_pos", [1, 1]))
    if action == "RIGHT": pos[0] -= 1
    elif action == "DOWN": pos[1] -= 1
    s["agent_pos"] = pos
    return s
"""

    h_std = model.score_hypothesis(standard_code, transitions)
    assert h_std.mismatches == 0
    assert h_std.verified is True

    h_inv = model.score_hypothesis(inverted_code, transitions)
    assert h_inv.mismatches == 2
    assert h_std.mdl_score > h_inv.mdl_score


def test_find_counterexamples(model):
    transitions = [
        {"action": "RIGHT", "pre_state": {"agent_pos": [1, 1]}, "post_state": {"agent_pos": [2, 1]}},
        {"action": "LEFT", "pre_state": {"agent_pos": [2, 1]}, "post_state": {"agent_pos": [1, 1]}},
    ]

    incomplete_code = """
def step(state: dict, action: str) -> dict:
    s = dict(state)
    pos = list(s.get("agent_pos", [1, 1]))
    if action == "RIGHT": pos[0] += 1
    s["agent_pos"] = pos
    return s
"""

    counterexamples = model.find_counterexamples(incomplete_code, transitions)
    assert len(counterexamples) == 1
    assert counterexamples[0]["transition"]["action"] == "LEFT"


def test_detect_state_aliasing(model):
    # Same pos [2, 2] and same action UP leads to different states due to hidden switch
    transitions = [
        {"action": "UP", "pre_state": {"agent_pos": [2, 2]}, "post_state": {"agent_pos": [2, 1]}},
        {"action": "UP", "pre_state": {"agent_pos": [2, 2]}, "post_state": {"agent_pos": [2, 3]}},
    ]

    conflicts = model.detect_state_aliasing(transitions)
    assert len(conflicts) == 1
    assert "pos:[2, 2]_act:UP" in conflicts[0]["state_action_key"]


def test_induce_program_model_selection(model):
    transitions = [
        {"action": "UP", "pre_state": {"agent_pos": [1, 2]}, "post_state": {"agent_pos": [1, 1]}},
        {"action": "RIGHT", "pre_state": {"agent_pos": [1, 1]}, "post_state": {"agent_pos": [2, 1]}},
    ]

    best = model.induce_program_model(transitions)
    assert best.mismatches == 0
    assert best.verified is True
