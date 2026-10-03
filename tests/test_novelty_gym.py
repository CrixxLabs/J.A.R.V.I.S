"""Unit tests for Novelty Gym & Evaluation Harness (Module AD)."""
import pytest

from novelty_gym import (
    NoveltyPuzzleEnv,
    NoveltyGym,
    ACTION_UP,
    ACTION_DOWN,
    ACTION_LEFT,
    ACTION_RIGHT,
    ACTION_PICKUP,
    ACTION_USE,
    ACTION_TOGGLE,
)


@pytest.fixture
def gym():
    return NoveltyGym()


def test_env_reset_and_state(gym):
    env = gym.make_env("test_1", width=6, height=6, require_key=True)
    state = env.reset()

    assert state["agent_pos"] == [1, 1]
    assert state["door_unlocked"] is False
    assert len(state["objects"]) >= 2
    assert state["done"] is False


def test_key_pickup_and_door_unlock(gym):
    env = gym.make_env("test_key_door", width=6, height=6, require_key=True)
    env.reset()

    # Move down to key at [1, 4]
    for _ in range(3):
        env.step(ACTION_DOWN)
    assert env.agent_pos == [1, 4]

    # Pickup key
    _, r_pickup, _, info_pickup = env.step(ACTION_PICKUP)
    assert "key" in env.inventory
    assert info_pickup.get("event") == "key_picked_up"

    # Move up to [1, 3] then right to [3, 3] (adjacent to door at [4, 3])
    env.step(ACTION_UP)
    for _ in range(2):
        env.step(ACTION_RIGHT)
    assert env.agent_pos == [3, 3]

    # Use key on door at [4, 3]
    _, _, _, info_use = env.step(ACTION_USE)
    assert env.door_unlocked is True
    assert info_use.get("event") == "door_unlocked"

    # Move through unlocked door to [4, 3] then down to goal at [4, 4]
    env.step(ACTION_RIGHT)
    assert env.agent_pos == [4, 3]
    _, _, done, info_goal = env.step(ACTION_DOWN)
    assert env.agent_pos == [4, 4]
    assert done is True
    assert info_goal.get("event") == "goal_reached"


def test_inverted_controls(gym):
    env = gym.make_env("test_inverted", width=6, height=6, inverted_controls=True, require_key=False)
    env.reset()
    assert env.agent_pos == [1, 1]

    # With inverted controls, UP moves DOWN -> y goes from 1 to 2
    env.step(ACTION_UP)
    assert env.agent_pos == [1, 2]

    # LEFT moves RIGHT -> x goes from 1 to 2
    env.step(ACTION_LEFT)
    assert env.agent_pos == [2, 2]


def test_switch_toggle(gym):
    env = gym.make_env("test_switch", width=6, height=6, has_switch=True, require_key=False)
    env.reset()
    # Switch is at [2, 1]
    env.step(ACTION_RIGHT)
    assert env.agent_pos == [2, 1]
    assert env.switch_active is False

    _, _, _, info = env.step(ACTION_TOGGLE)
    assert env.switch_active is True
    assert info.get("event") == "switch_toggled"


def test_policy_evaluation_metrics(gym):
    env = gym.make_env("test_eval", width=4, height=4, require_key=False)
    # Simple scripted policy: step right then down
    def scripted_policy(state):
        x, y = state["agent_pos"]
        if x < 2:
            return ACTION_RIGHT
        if y < 2:
            return ACTION_DOWN
        return ACTION_RIGHT

    metrics = gym.evaluate_policy(env, scripted_policy, max_episodes=2)
    assert metrics["episodes_evaluated"] == 2
    assert metrics["win_rate"] == 1.0
    assert metrics["avg_transitions_to_first_win"] is not None
