"""Unit tests for Affordance & State-Abstraction Engine (Module W)."""
import pytest

from state_abstractor import (
    StateAbstractor,
    abstract_state,
    sample_exogenous_noise,
    attribute_agency,
    induce_action_space,
)


@pytest.fixture
def abstractor():
    return StateAbstractor()


def test_abstract_state(abstractor):
    raw = {
        "step": 3,
        "score": 1.5,
        "agent_pos": [2, 3],
        "inventory": ["key_silver"],
        "objects": [
            {"obj_id": "door_1", "obj_type": "door", "x": 5, "y": 3, "properties": {"unlocked": False}}
        ],
    }
    abs_state = abstractor.abstract_state(raw)
    assert "agent" in abs_state.objects
    assert abs_state.objects["agent"].x == 2
    assert abs_state.objects["agent"].y == 3
    assert abs_state.objects["agent"].properties["inventory"] == ["key_silver"]
    assert "door_1" in abs_state.objects
    assert abs_state.global_features["score"] == 1.5


def test_sample_exogenous_noise(abstractor):
    # Static environment returns same state
    step_count = 0
    def mock_static_step(action):
        nonlocal step_count
        step_count += 1
        return {"agent_pos": [1, 1], "step": step_count}, 0.0, False, {}

    noise = abstractor.sample_exogenous_noise(mock_static_step, n_samples=4)
    assert noise == 0.0


def test_attribute_agency_and_action_space(abstractor):
    transitions = [
        {
            "action": "RIGHT",
            "pre_state": {"agent_pos": [1, 1]},
            "post_state": {"agent_pos": [2, 1]},
        },
        {
            "action": "DOWN",
            "pre_state": {"agent_pos": [2, 1]},
            "post_state": {"agent_pos": [2, 2]},
        },
        {
            "action": "NOOP",
            "pre_state": {"agent_pos": [2, 2]},
            "post_state": {"agent_pos": [2, 2]},
        },
    ]

    agency = abstractor.attribute_agency(transitions, noise_floor=0.0)
    assert "agent" in agency["controlled_objects"]
    assert "RIGHT" in agency["causal_actions"]
    assert "DOWN" in agency["causal_actions"]
    assert "NOOP" not in agency["causal_actions"]

    actions = abstractor.induce_action_space(transitions)
    assert "RIGHT" in actions
    assert "DOWN" in actions
