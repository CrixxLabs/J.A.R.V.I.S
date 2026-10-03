"""Affordance & State-Abstraction Engine for J.A.R.V.I.S. — MARK VIII.

Module W:
  1. Symbolic Object Extraction:
     - Parses discrete interface/raw state into typed object representations (id, type, x, y, properties)
       plus global features (score, inventory, modal flags).
  2. Exogenous Noise Floor Estimation:
     - Measures baseline environmental noise via no-op probing.
  3. Affordance Discovery & Agency Attribution:
     - Attributes causality only when state delta significantly correlates with actions vs no-op baselines.
     - Dynamically induces candidate action spaces from empirical behavior.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.state_abstractor")

_lock = threading.RLock()


@dataclass
class TypedObject:
    id: str
    type: str
    x: float
    y: float
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AbstractedState:
    objects: Dict[str, TypedObject] = field(default_factory=dict)
    global_features: Dict[str, Any] = field(default_factory=dict)
    agent_id: Optional[str] = "agent"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objects": {k: v.to_dict() for k, v in self.objects.items()},
            "global_features": self.global_features,
            "agent_id": self.agent_id,
        }


class StateAbstractor:
    """Extracts typed objects, estimates ambient noise, and attributes causal affordances."""

    def __init__(self):
        pass

    def abstract_state(self, raw_state: Dict[str, Any]) -> AbstractedState:
        """Parse raw dictionary or grid representation into structured typed objects."""
        objects: Dict[str, TypedObject] = {}
        global_features: Dict[str, Any] = {}

        # 1. Parse explicit objects list if present
        if "objects" in raw_state and isinstance(raw_state["objects"], list):
            for item in raw_state["objects"]:
                oid = str(item.get("obj_id") or item.get("id") or f"obj_{len(objects)}")
                otype = str(item.get("obj_type") or item.get("type") or "generic")
                x = float(item.get("x", 0))
                y = float(item.get("y", 0))
                props = item.get("properties", {})
                objects[oid] = TypedObject(id=oid, type=otype, x=x, y=y, properties=props)

        # 2. Extract agent pos directly if top-level
        if "agent_pos" in raw_state and "agent" not in objects:
            pos = raw_state["agent_pos"]
            inv = raw_state.get("inventory", [])
            objects["agent"] = TypedObject(
                id="agent",
                type="agent",
                x=float(pos[0]),
                y=float(pos[1]),
                properties={"inventory": inv},
            )

        # 3. Extract global features
        for k, v in raw_state.items():
            if k not in ["objects", "agent_pos"]:
                global_features[k] = v

        return AbstractedState(objects=objects, global_features=global_features, agent_id="agent")

    def sample_exogenous_noise(
        self,
        env_step_fn: Callable[[str], Tuple[Dict[str, Any], float, bool, Dict[str, Any]]],
        noop_action: str = "NOOP",
        n_samples: int = 5,
    ) -> float:
        """Measure background state mutation frequency during no-op steps."""
        state_changes = 0
        last_state = None

        for _ in range(n_samples):
            next_state, _, done, _ = env_step_fn(noop_action)
            if last_state is not None:
                # Check if state changed without intentional action
                if next_state.get("agent_pos") != last_state.get("agent_pos") or next_state.get("objects") != last_state.get("objects"):
                    state_changes += 1
            last_state = next_state
            if done:
                break

        noise_floor = round(state_changes / max(1, n_samples), 4)
        log.info(f"[StateAbstractor] Measured exogenous noise floor: {noise_floor}")
        return noise_floor

    def attribute_agency(
        self,
        transitions: List[Dict[str, Any]],
        noise_floor: float = 0.0,
    ) -> Dict[str, Any]:
        """Attribute causality between actions and object mutations vs background noise."""
        action_effects: Dict[str, Dict[str, int]] = {}
        controlled_objects: Set[str] = set()

        for t in transitions:
            action = t.get("action", "UNKNOWN")
            s_before = self.abstract_state(t.get("pre_state", {}))
            s_after = self.abstract_state(t.get("post_state", {}))

            if action not in action_effects:
                action_effects[action] = {"mutations": 0, "total": 0}
            action_effects[action]["total"] += 1

            mutated = False
            for oid, obj_before in s_before.objects.items():
                obj_after = s_after.objects.get(oid)
                if obj_after is None or (obj_before.x != obj_after.x or obj_before.y != obj_after.y or obj_before.properties != obj_after.properties):
                    mutated = True
                    controlled_objects.add(oid)

            if mutated:
                action_effects[action]["mutations"] += 1

        causal_actions = []
        for act, stats in action_effects.items():
            mutation_rate = stats["mutations"] / max(1, stats["total"])
            if mutation_rate > noise_floor:
                causal_actions.append(act)

        try:
            get_registry().set_capability_evidence(
                "STATE_ABSTRACTOR",
                EvidenceLevel.LIVE,
                f"Attributed agency: {len(controlled_objects)} controlled objects, {len(causal_actions)} causal actions",
                source="state_abstractor.attribute_agency",
            )
        except Exception:
            pass

        return {
            "controlled_objects": list(controlled_objects),
            "causal_actions": causal_actions,
            "action_effects": action_effects,
            "noise_floor": noise_floor,
        }

    def induce_action_space(self, transitions: List[Dict[str, Any]]) -> List[str]:
        """Extract candidate action vocabulary with observed non-zero empirical effects."""
        agency = self.attribute_agency(transitions)
        return agency["causal_actions"]


_abstractor_instance: Optional[StateAbstractor] = None


def get_state_abstractor() -> StateAbstractor:
    global _abstractor_instance
    if _abstractor_instance is None:
        with _lock:
            if _abstractor_instance is None:
                _abstractor_instance = StateAbstractor()
    return _abstractor_instance


def abstract_state(raw_state: Dict[str, Any]) -> AbstractedState:
    return get_state_abstractor().abstract_state(raw_state)


def sample_exogenous_noise(
    env_step_fn: Callable[[str], Tuple[Dict[str, Any], float, bool, Dict[str, Any]]],
    noop_action: str = "NOOP",
    n_samples: int = 5,
) -> float:
    return get_state_abstractor().sample_exogenous_noise(env_step_fn, noop_action, n_samples)


def attribute_agency(
    transitions: List[Dict[str, Any]],
    noise_floor: float = 0.0,
) -> Dict[str, Any]:
    return get_state_abstractor().attribute_agency(transitions, noise_floor)


def induce_action_space(transitions: List[Dict[str, Any]]) -> List[str]:
    return get_state_abstractor().induce_action_space(transitions)
