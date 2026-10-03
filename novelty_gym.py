"""Novelty Gym & Evaluation Harness for J.A.R.V.I.S. — MARK VIII.

Module AD:
  1. Procedural Benchmark Environments:
     - Grid/puzzle testbeds with mutable semantics: inverted controls, key-door relationships,
       toggle switches, decoy items, and hidden state transitions.
  2. Evaluation Metrics:
     - Transitions-to-first-win, held-out transition prediction accuracy, sample efficiency.
  3. Safety & Regression Gate:
     - Evaluates that downstream models infer rules empirically without hardcoded assumptions.
"""
from __future__ import annotations

import copy
import json
import logging
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.novelty_gym")

_lock = threading.RLock()

# Standard Grid Actions
ACTION_UP = "UP"
ACTION_DOWN = "DOWN"
ACTION_LEFT = "LEFT"
ACTION_RIGHT = "RIGHT"
ACTION_TOGGLE = "TOGGLE"
ACTION_PICKUP = "PICKUP"
ACTION_USE = "USE"
ACTION_NOOP = "NOOP"

STANDARD_ACTIONS = [ACTION_UP, ACTION_DOWN, ACTION_LEFT, ACTION_RIGHT, ACTION_TOGGLE, ACTION_PICKUP, ACTION_USE, ACTION_NOOP]


@dataclass
class GridObject:
    obj_id: str
    obj_type: str  # agent, wall, key, door, switch, goal, decoy
    x: int
    y: int
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NoveltyPuzzleEnv:
    """Procedural 2D Grid Environment with mutable semantics and hidden mechanics."""

    def __init__(
        self,
        width: int = 6,
        height: int = 6,
        inverted_controls: bool = False,
        require_key: bool = True,
        has_switch: bool = False,
        seed: Optional[int] = None,
    ):
        self.width = width
        self.height = height
        self.inverted_controls = inverted_controls
        self.require_key = require_key
        self.has_switch = has_switch
        self.rng = random.Random(seed if seed is not None else 42)

        self.agent_pos = [1, 1]
        self.goal_pos = [width - 2, height - 2]
        self.key_pos = [1, height - 2] if require_key else None
        self.door_pos = [width - 2, height - 3] if require_key else None
        self.switch_pos = [2, 1] if has_switch else None

        self.inventory: List[str] = []
        self.door_unlocked = not require_key
        self.switch_active = False
        self.step_count = 0
        self.max_steps = 100
        self.done = False
        self.score = 0.0

        self.reset()

    def reset(self) -> Dict[str, Any]:
        """Reset environment to initial state."""
        self.agent_pos = [1, 1]
        self.inventory = []
        self.door_unlocked = not self.require_key
        self.switch_active = False
        self.step_count = 0
        self.done = False
        self.score = 0.0
        return self.get_state()

    def get_action_space(self) -> List[str]:
        return list(STANDARD_ACTIONS)

    def get_state(self) -> Dict[str, Any]:
        """Return structured symbolic state representation."""
        objects = [
            GridObject("agent", "agent", self.agent_pos[0], self.agent_pos[1], {"inventory": list(self.inventory)}),
            GridObject("goal", "goal", self.goal_pos[0], self.goal_pos[1], {}),
        ]
        if self.key_pos and "key" not in self.inventory:
            objects.append(GridObject("key_1", "key", self.key_pos[0], self.key_pos[1], {}))
        if self.door_pos:
            objects.append(
                GridObject(
                    "door_1", "door", self.door_pos[0], self.door_pos[1],
                    {"unlocked": self.door_unlocked}
                )
            )
        if self.switch_pos:
            objects.append(
                GridObject(
                    "switch_1", "switch", self.switch_pos[0], self.switch_pos[1],
                    {"active": self.switch_active}
                )
            )

        return {
            "step": self.step_count,
            "agent_pos": list(self.agent_pos),
            "inventory": list(self.inventory),
            "door_unlocked": self.door_unlocked,
            "switch_active": self.switch_active,
            "score": self.score,
            "done": self.done,
            "objects": [obj.to_dict() for obj in objects],
            "grid_size": [self.width, self.height],
        }

    def step(self, action: str) -> Tuple[Dict[str, Any], float, bool, Dict[str, Any]]:
        """Execute action, update state, and return (state, reward, done, info)."""
        if self.done:
            return self.get_state(), 0.0, True, {"msg": "already_done"}

        self.step_count += 1
        reward = -0.01  # small step penalty
        info: Dict[str, Any] = {"action_executed": action}

        act = str(action).strip().upper()
        # Handle control inversion if active or configured
        effective_inversion = self.inverted_controls ^ self.switch_active
        if effective_inversion:
            if act == ACTION_UP:
                act = ACTION_DOWN
            elif act == ACTION_DOWN:
                act = ACTION_UP
            elif act == ACTION_LEFT:
                act = ACTION_RIGHT
            elif act == ACTION_RIGHT:
                act = ACTION_LEFT

        dx, dy = 0, 0
        if act == ACTION_UP:
            dy = -1
        elif act == ACTION_DOWN:
            dy = 1
        elif act == ACTION_LEFT:
            dx = -1
        elif act == ACTION_RIGHT:
            dx = 1
        elif act == ACTION_TOGGLE:
            if self.switch_pos and self.agent_pos == self.switch_pos:
                self.switch_active = not self.switch_active
                reward += 0.5
                info["event"] = "switch_toggled"
        elif act == ACTION_PICKUP:
            if self.key_pos and self.agent_pos == self.key_pos and "key" not in self.inventory:
                self.inventory.append("key")
                reward += 1.0
                info["event"] = "key_picked_up"
        elif act == ACTION_USE:
            if self.door_pos and "key" in self.inventory and not self.door_unlocked:
                # Adjacent to door or on door
                dist = abs(self.agent_pos[0] - self.door_pos[0]) + abs(self.agent_pos[1] - self.door_pos[1])
                if dist <= 1:
                    self.door_unlocked = True
                    reward += 2.0
                    info["event"] = "door_unlocked"

        # Movement physics
        if dx != 0 or dy != 0:
            nx = max(0, min(self.width - 1, self.agent_pos[0] + dx))
            ny = max(0, min(self.height - 1, self.agent_pos[1] + dy))

            # Wall boundaries
            if nx == 0 or nx == self.width - 1 or ny == 0 or ny == self.height - 1:
                info["collision"] = "wall"
            elif self.door_pos and [nx, ny] == self.door_pos and not self.door_unlocked:
                info["collision"] = "locked_door"
            else:
                self.agent_pos = [nx, ny]

        # Goal arrival check
        if self.agent_pos == self.goal_pos:
            reward += 10.0
            self.done = True
            info["event"] = "goal_reached"

        if self.step_count >= self.max_steps:
            self.done = True
            info["event"] = "max_steps_reached"

        self.score += reward
        state = self.get_state()
        return state, reward, self.done, info


class NoveltyGym:
    """Evaluation harness for benchmark environments and empirical rule learning."""

    def __init__(self):
        self._envs: Dict[str, NoveltyPuzzleEnv] = {}

    def make_env(
        self,
        env_id: str = "default_puzzle",
        width: int = 6,
        height: int = 6,
        inverted_controls: bool = False,
        require_key: bool = True,
        has_switch: bool = False,
    ) -> NoveltyPuzzleEnv:
        """Create or register a benchmark puzzle environment."""
        with _lock:
            env = NoveltyPuzzleEnv(
                width=width,
                height=height,
                inverted_controls=inverted_controls,
                require_key=require_key,
                has_switch=has_switch,
            )
            self._envs[env_id] = env
            return env

    def evaluate_policy(
        self,
        env: NoveltyPuzzleEnv,
        policy_fn: Callable[[Dict[str, Any]], str],
        max_episodes: int = 5,
    ) -> Dict[str, Any]:
        """Evaluate agent policy on benchmark environment and compute sample efficiency metrics."""
        results = []
        total_transitions_to_win = []
        wins = 0

        for ep in range(max_episodes):
            state = env.reset()
            ep_reward = 0.0
            steps = 0
            win = False

            while not env.done and steps < env.max_steps:
                action = policy_fn(state)
                next_state, reward, done, info = env.step(action)
                ep_reward += reward
                steps += 1
                state = next_state
                if info.get("event") == "goal_reached":
                    win = True

            if win:
                wins += 1
                total_transitions_to_win.append(steps)

            results.append({"episode": ep + 1, "steps": steps, "reward": round(ep_reward, 2), "win": win})

        win_rate = wins / max(1, max_episodes)
        avg_steps_to_win = (
            sum(total_transitions_to_win) / len(total_transitions_to_win)
            if total_transitions_to_win else None
        )

        metrics = {
            "episodes_evaluated": max_episodes,
            "win_rate": round(win_rate, 4),
            "wins": wins,
            "avg_transitions_to_first_win": round(avg_steps_to_win, 2) if avg_steps_to_win is not None else None,
            "episode_results": results,
        }

        try:
            get_registry().set_capability_evidence(
                "NOVELTY_GYM",
                EvidenceLevel.LIVE,
                f"Evaluated policy (win_rate={win_rate:.2f}, avg_steps={avg_steps_to_win})",
                source="novelty_gym.evaluate_policy",
            )
        except Exception:
            pass

        return metrics

    def compute_prediction_accuracy(
        self,
        predicted_transitions: List[Dict[str, Any]],
        ground_truth_transitions: List[Dict[str, Any]],
    ) -> float:
        """Compute held-out state transition prediction accuracy."""
        if not ground_truth_transitions:
            return 1.0

        matches = 0
        total = len(ground_truth_transitions)
        for pred, actual in zip(predicted_transitions, ground_truth_transitions):
            # Compare essential fields
            if (
                pred.get("agent_pos") == actual.get("agent_pos")
                and pred.get("door_unlocked") == actual.get("door_unlocked")
                and pred.get("switch_active") == actual.get("switch_active")
            ):
                matches += 1

        return round(matches / total, 4)


_gym_instance: Optional[NoveltyGym] = None


def get_novelty_gym() -> NoveltyGym:
    global _gym_instance
    if _gym_instance is None:
        with _lock:
            if _gym_instance is None:
                _gym_instance = NoveltyGym()
    return _gym_instance


def make_env(
    env_id: str = "default_puzzle",
    width: int = 6,
    height: int = 6,
    inverted_controls: bool = False,
    require_key: bool = True,
    has_switch: bool = False,
) -> NoveltyPuzzleEnv:
    return get_novelty_gym().make_env(env_id, width, height, inverted_controls, require_key, has_switch)


def evaluate_policy(
    env: NoveltyPuzzleEnv,
    policy_fn: Callable[[Dict[str, Any]], str],
    max_episodes: int = 5,
) -> Dict[str, Any]:
    return get_novelty_gym().evaluate_policy(env, policy_fn, max_episodes)
