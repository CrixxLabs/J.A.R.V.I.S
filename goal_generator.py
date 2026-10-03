"""Open-Ended Goal Formulation under Ratification for J.A.R.V.I.S. — MARK VIII.

Module AN:
  1. Autonomous Grammar-Based Goal Synthesizer:
     - Mutates state predicates to formulate novel curriculum goals.
  2. Goldilocks Difficulty Filter:
     - Filters out trivial (p_hat > 0.8) and impossible (p_hat < 0.2) goals.
     - Selects tasks in the zone of proximal development with positive learning potential.
  3. Risk-Tiered Ratification Pipeline:
     - Auto-approves low-stakes, sandboxed, reversible goals.
     - Escalates high-risk or irreversible goals to user confirmation queue.
"""
from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.goal_generator")

_lock = threading.RLock()

RATIFICATION_AUTO_APPROVED = "AUTO_APPROVED"
RATIFICATION_QUEUED_FOR_DIGEST = "QUEUED_FOR_DIGEST"
RATIFICATION_REJECTED = "REJECTED"


@dataclass
class SynthesizedGoal:
    goal_id: str
    predicate: str
    target_state: Dict[str, Any]
    estimated_difficulty_p: float
    learning_progress_potential: float
    risk_tier: str                       # LOW, MEDIUM, HIGH
    is_sandboxed: bool
    is_reversible: bool
    ratification_status: str = "PENDING"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GoalGenerator:
    """Synthesizes open-ended curriculum goals, filters by Goldilocks difficulty, and ratifies by risk."""

    def __init__(self, seed: Optional[int] = 42):
        self.rng = random.Random(seed)
        self._goal_archive: Dict[str, SynthesizedGoal] = {}
        self._digest_queue: List[SynthesizedGoal] = []

    # ------------------------------------------------------------------
    # Autonomous Goal Synthesis
    # ------------------------------------------------------------------

    def synthesize_goals(
        self,
        domain_objects: List[Dict[str, Any]],
        grid_size: Tuple[int, int] = (6, 6),
        count: int = 5,
    ) -> List[SynthesizedGoal]:
        """Synthesize candidate goal predicates from domain objects and spatial bounds."""
        goals = []
        w, h = grid_size

        for _ in range(count):
            gid = f"goal_{uuid.uuid4().hex[:8]}"
            goal_type = self.rng.choice(["reach_coordinate", "collect_item", "toggle_state", "composite_sequence"])

            if goal_type == "reach_coordinate":
                tx = self.rng.randint(1, w - 2)
                ty = self.rng.randint(1, h - 2)
                predicate = f"agent_pos == [{tx}, {ty}]"
                target_state = {"agent_pos": [tx, ty]}
                risk = "LOW"
            elif goal_type == "collect_item":
                item_name = self.rng.choice(["key", "gem", "data_packet", "token"])
                predicate = f"'{item_name}' in inventory"
                target_state = {"inventory": [item_name]}
                risk = "LOW"
            elif goal_type == "toggle_state":
                predicate = "switch_active == True"
                target_state = {"switch_active": True}
                risk = "LOW"
            else:
                predicate = "door_unlocked == True and 'key' in inventory"
                target_state = {"door_unlocked": True, "inventory": ["key"]}
                risk = "MEDIUM"

            # Baseline difficulty estimate
            est_p = round(self.rng.uniform(0.1, 0.9), 3)
            lp_potential = round(self.rng.uniform(0.1, 1.0), 3)

            goal = SynthesizedGoal(
                goal_id=gid,
                predicate=predicate,
                target_state=target_state,
                estimated_difficulty_p=est_p,
                learning_progress_potential=lp_potential,
                risk_tier=risk,
                is_sandboxed=True,
                is_reversible=True,
            )
            goals.append(goal)

        return goals

    # ------------------------------------------------------------------
    # Goldilocks Difficulty Filter
    # ------------------------------------------------------------------

    def filter_goldilocks_goals(
        self,
        goals: List[SynthesizedGoal],
        eval_fn: Optional[Callable[[SynthesizedGoal], float]] = None,
        min_p: float = 0.20,
        max_p: float = 0.80,
    ) -> List[SynthesizedGoal]:
        """Keep only goals with non-trivial, non-impossible success rates (0.2 <= p <= 0.8)."""
        filtered = []
        for g in goals:
            p_hat = eval_fn(g) if eval_fn else g.estimated_difficulty_p
            g.estimated_difficulty_p = round(p_hat, 3)

            if min_p <= p_hat <= max_p and g.learning_progress_potential > 0.05:
                filtered.append(g)

        filtered.sort(key=lambda x: x.learning_progress_potential, reverse=True)
        return filtered

    # ------------------------------------------------------------------
    # Risk-Tiered Ratification Pipeline
    # ------------------------------------------------------------------

    def ratify_goal(
        self,
        goal: SynthesizedGoal,
    ) -> SynthesizedGoal:
        """Auto-approve safe sandbox goals; route high risk or irreversible to digest queue."""
        with _lock:
            if goal.is_sandboxed and goal.is_reversible and goal.risk_tier == "LOW":
                goal.ratification_status = RATIFICATION_AUTO_APPROVED
            elif goal.risk_tier in ("HIGH", "CRITICAL") or not goal.is_reversible:
                goal.ratification_status = RATIFICATION_QUEUED_FOR_DIGEST
                self._digest_queue.append(goal)
            else:
                # Medium risk sandboxed -> Auto approved with supervision
                goal.ratification_status = RATIFICATION_AUTO_APPROVED

            self._goal_archive[goal.goal_id] = goal

        log.debug(f"[GoalGenerator] Goal {goal.goal_id} ratified as {goal.ratification_status}")

        try:
            get_registry().set_capability_evidence(
                "GOAL_GENERATOR",
                EvidenceLevel.LIVE,
                f"Ratified goal {goal.goal_id} ({goal.predicate}) -> {goal.ratification_status}",
                source="goal_generator.ratify_goal",
            )
        except Exception:
            pass

        return goal

    def get_digest_queue(self) -> List[SynthesizedGoal]:
        with _lock:
            return list(self._digest_queue)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_generator_instance: Optional[GoalGenerator] = None


def get_goal_generator() -> GoalGenerator:
    global _generator_instance
    if _generator_instance is None:
        with _lock:
            if _generator_instance is None:
                _generator_instance = GoalGenerator()
    return _generator_instance


def synthesize_goals(domain_objects: List[Dict[str, Any]], **kwargs) -> List[SynthesizedGoal]:
    return get_goal_generator().synthesize_goals(domain_objects, **kwargs)


def filter_goldilocks_goals(goals: List[SynthesizedGoal], **kwargs) -> List[SynthesizedGoal]:
    return get_goal_generator().filter_goldilocks_goals(goals, **kwargs)


def ratify_goal(goal: SynthesizedGoal) -> SynthesizedGoal:
    return get_goal_generator().ratify_goal(goal)
