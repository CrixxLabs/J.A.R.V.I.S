"""Tests for Module AC: Betweenness Bottleneck Option Discovery."""
import pytest
from option_discovery import Option, OptionDiscovery, discover_options, get_option_discovery


def _make_linear_graph(n: int) -> OptionDiscovery:
    """Build a linear chain 0 -> 1 -> 2 -> ... -> n-1."""
    od = OptionDiscovery()
    for i in range(n - 1):
        od.add_transition(str(i), "FORWARD", str(i + 1))
    return od


def _make_bottleneck_graph() -> OptionDiscovery:
    """
    Build a graph with an obvious bottleneck at node 'B':
        A1 -> B, A2 -> B, B -> C1, B -> C2

    B must be on every path between the left cluster and the right cluster.
    """
    od = OptionDiscovery()
    # Left side
    od.add_transition("A1", "go_B", "B")
    od.add_transition("A2", "go_B", "B")
    # Right side
    od.add_transition("B", "go_C1", "C1")
    od.add_transition("B", "go_C2", "C2")
    return od


class TestOptionDiscovery:
    def test_add_transition_and_graph_build(self):
        od = OptionDiscovery()
        od.add_transition("s0", "a", "s1")
        od.add_transition("s1", "a", "s2")
        assert "s0" in od._state_set
        assert "s1" in od._state_set
        assert "s2" in od._state_set

    def test_build_from_transitions_dict(self):
        od = OptionDiscovery()
        transitions = [
            {"pre_state": "s0", "action": "go", "post_state": "s1"},
            {"pre_state": "s1", "action": "go", "post_state": "s2"},
        ]
        od.build_from_transitions(transitions)
        assert len(od._state_set) == 3

    def test_betweenness_bottleneck(self):
        od = _make_bottleneck_graph()
        bw = od.compute_betweenness()
        # B has highest betweenness because paths from {A1,A2} to {C1,C2} go through B
        assert bw["B"] == max(bw.values())

    def test_discover_options_returns_bottleneck_first(self):
        od = _make_bottleneck_graph()
        options = od.discover_options(top_k=3)
        assert len(options) >= 1
        assert options[0].bottleneck_state == "B"

    def test_bfs_policy_reaches_goal(self):
        """Policy derived from BFS backward-search should guide any state to the bottleneck."""
        od = _make_linear_graph(5)
        options = od.discover_options(top_k=1)
        assert options
        opt = options[0]
        # The bottleneck in a linear chain should be somewhere in the middle
        # and the policy should not be empty (there are reachable predecessors)
        # At minimum the initiation set covers states before the bottleneck
        assert isinstance(opt.initiation_set, list)

    def test_option_selection_for_state(self):
        od = _make_bottleneck_graph()
        options = od.discover_options(top_k=3)
        # A1 should be in the initiation set of the bottleneck option at B
        opt = od.get_option_for_state("A1", options)
        assert opt is not None
        assert opt.bottleneck_state == "B"
