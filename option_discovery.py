"""Option Discovery Engine for J.A.R.V.I.S. — MARK VIII.

Module AC: Betweenness Bottleneck Options
  1. Graph-theoretic Option Discovery:
     - Constructs a state-transition graph from observed transitions.
     - Identifies bottleneck states via betweenness centrality — states through
       which many shortest paths pass.
  2. Option Initiation and Termination Sets:
     - High-betweenness states become initiation sets (enter-option trigger).
     - The bottleneck state itself is the termination condition.
  3. Option Policy Extraction:
     - Returns a simple shortest-path subpolicy to reach each bottleneck goal.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.option_discovery")

_lock = threading.RLock()


@dataclass
class Option:
    option_id: str
    bottleneck_state: str             # the termination state (high betweenness node)
    betweenness: float
    initiation_set: List[str]         # states from which this option can be started
    # policy: state -> action to reach bottleneck (BFS-derived)
    policy: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OptionDiscovery:
    """Discover temporal-abstraction options via betweenness-centrality bottlenecks."""

    def __init__(self):
        # Adjacency: state -> {(action, next_state)}
        self._graph: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        self._state_set: Set[str] = set()

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_transition(self, pre_state: str, action: str, post_state: str) -> None:
        """Register a directed state transition into the graph."""
        with _lock:
            self._graph[pre_state].add((action, post_state))
            self._state_set.add(pre_state)
            self._state_set.add(post_state)

    def build_from_transitions(self, transitions: List[Dict[str, Any]]) -> None:
        """Batch-insert transitions extracted from episode data.

        Each transition dict should have ``pre_state``, ``action``, and
        ``post_state`` keys, all strings (or JSON-serialisable dicts which
        will be stringified).
        """
        for t in transitions:
            pre = _state_key(t.get("pre_state", ""))
            act = str(t.get("action", ""))
            post = _state_key(t.get("post_state", ""))
            self.add_transition(pre, act, post)

    # ------------------------------------------------------------------
    # Betweenness centrality (Brandes, unweighted)
    # ------------------------------------------------------------------

    def compute_betweenness(self) -> Dict[str, float]:
        """Compute approximate (unweighted) betweenness centrality for all nodes."""
        states = list(self._state_set)
        betweenness: Dict[str, float] = {s: 0.0 for s in states}

        # Build plain adjacency (ignore action labels)
        adj: Dict[str, List[str]] = defaultdict(list)
        for src, edges in self._graph.items():
            for _, dst in edges:
                adj[src].append(dst)

        for source in states:
            # BFS from source
            stack: List[str] = []
            pred: Dict[str, List[str]] = {s: [] for s in states}
            sigma: Dict[str, float] = {s: 0.0 for s in states}
            dist: Dict[str, int] = {s: -1 for s in states}

            sigma[source] = 1.0
            dist[source] = 0
            queue: deque = deque([source])

            while queue:
                v = queue.popleft()
                stack.append(v)
                for w in adj.get(v, []):
                    if dist[w] < 0:
                        queue.append(w)
                        dist[w] = dist[v] + 1
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)

            # Accumulate dependencies
            delta: Dict[str, float] = {s: 0.0 for s in states}
            while stack:
                w = stack.pop()
                for v in pred[w]:
                    if sigma[w] > 0:
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != source:
                    betweenness[w] += delta[w]

        # Normalise
        n = len(states)
        if n > 2:
            norm = (n - 1) * (n - 2)
            betweenness = {s: round(b / norm, 6) for s, b in betweenness.items()}
        return betweenness

    # ------------------------------------------------------------------
    # Option extraction
    # ------------------------------------------------------------------

    def _bfs_policy(self, goal: str) -> Dict[str, str]:
        """BFS backward from goal to build a per-state one-step policy (state -> action)."""
        # Build reverse adjacency
        rev_adj: Dict[str, List[Tuple[str, str]]] = defaultdict(list)  # state -> [(action, prev)]
        for src, edges in self._graph.items():
            for act, dst in edges:
                rev_adj[dst].append((act, src))

        policy: Dict[str, str] = {}
        visited: Set[str] = {goal}
        queue: deque = deque([goal])

        while queue:
            node = queue.popleft()
            for act, prev in rev_adj.get(node, []):
                if prev not in visited:
                    visited.add(prev)
                    policy[prev] = act  # take `act` from `prev` to move toward goal
                    queue.append(prev)
        return policy

    def discover_options(
        self,
        top_k: int = 5,
        min_betweenness: float = 0.0,
    ) -> List[Option]:
        """Return the top-k bottleneck options ranked by betweenness centrality."""
        if not self._state_set:
            return []

        betweenness = self.compute_betweenness()
        ranked = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)

        options: List[Option] = []
        for state_key, bw in ranked[:top_k]:
            if bw < min_betweenness:
                continue
            policy = self._bfs_policy(state_key)
            initiation_set = list(policy.keys())

            opt = Option(
                option_id=f"opt_{state_key[:16]}",
                bottleneck_state=state_key,
                betweenness=bw,
                initiation_set=initiation_set,
                policy=policy,
            )
            options.append(opt)

        try:
            get_registry().set_capability_evidence(
                "OPTION_DISCOVERY",
                EvidenceLevel.LIVE,
                f"Discovered {len(options)} bottleneck options "
                f"(|V|={len(self._state_set)}, |E|={sum(len(e) for e in self._graph.values())})",
                source="option_discovery.discover_options",
            )
        except Exception:
            pass

        return options

    def get_option_for_state(self, state: str, options: List[Option]) -> Optional[Option]:
        """Return the highest-betweenness option that is applicable from `state`."""
        applicable = [o for o in options if state in o.initiation_set]
        if not applicable:
            return None
        return max(applicable, key=lambda o: o.betweenness)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_discovery_instance: Optional[OptionDiscovery] = None


def _state_key(raw: Any) -> str:
    """Normalise a state to a hashable string key."""
    if isinstance(raw, dict):
        import json
        return json.dumps(raw, sort_keys=True, default=str)
    return str(raw)


def get_option_discovery() -> OptionDiscovery:
    global _discovery_instance
    if _discovery_instance is None:
        with _lock:
            if _discovery_instance is None:
                _discovery_instance = OptionDiscovery()
    return _discovery_instance


def build_from_transitions(transitions: List[Dict[str, Any]]) -> None:
    get_option_discovery().build_from_transitions(transitions)


def discover_options(top_k: int = 5, min_betweenness: float = 0.0) -> List[Option]:
    return get_option_discovery().discover_options(top_k, min_betweenness)
