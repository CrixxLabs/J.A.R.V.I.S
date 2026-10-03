"""Tests for Module AH: Social Cognition & Multi-Party Negotiation Simulator."""
import pytest
from negotiation_simulator import (
    ASSERTION_TYPE_FACT,
    ASSERTION_TYPE_OFFER,
    ASSERTION_TYPE_OPINION,
    TACTIC_BOULWARE,
    TACTIC_CONCEDER,
    NegotiationSimulator,
    compute_concession_level,
    compute_pareto_and_nash,
    get_negotiation_simulator,
    profile_counterpart,
    verify_outbound_assertion,
)


class TestNegotiationSimulator:
    def test_faratin_concession_trajectories(self):
        # Boulware (beta = 0.2): concession stays very low until near deadline
        mid_boulware = compute_concession_level(t=50, t_max=100, beta=0.2)
        assert mid_boulware < 0.10  # 0.5^5 = 0.03125

        end_boulware = compute_concession_level(t=100, t_max=100, beta=0.2)
        assert end_boulware == 1.0

        # Conceder (beta = 3.0): concession increases quickly early on
        mid_conceder = compute_concession_level(t=50, t_max=100, beta=3.0)
        assert mid_conceder > 0.70  # 0.5^(1/3) ~ 0.7937

    def test_counterpart_profiling(self):
        sim = NegotiationSimulator()
        observed_bids = [
            {"price": 0.9, "warranty": 0.2, "delivery": 0.1},
            {"price": 0.85, "warranty": 0.3, "delivery": 0.1},
        ]
        profile = sim.profile_counterpart("vendor_acme", observed_bids, ["price", "warranty", "delivery"])
        assert profile.inferred_weights["price"] > profile.inferred_weights["warranty"]
        assert profile.inferred_weights["warranty"] > profile.inferred_weights["delivery"]

    def test_pareto_and_nash_bargaining(self):
        sim = NegotiationSimulator()
        candidate_bids = [
            {"salary": 1.0, "remote_days": 1.0},  # all for employee
            {"salary": 0.0, "remote_days": 0.0},  # all for employer
            {"salary": 0.5, "remote_days": 0.5},  # balanced
            {"salary": 0.8, "remote_days": 0.2},  # trade-off
        ]
        self_weights = {"salary": 0.6, "remote_days": 0.4}
        cp_weights = {"salary": 0.7, "remote_days": 0.3}

        plan = sim.compute_pareto_and_nash(candidate_bids, self_weights, cp_weights, tactic=TACTIC_BOULWARE)
        assert len(plan.pareto_frontier_bids) > 0
        assert plan.nash_bargaining_solution is not None
        assert plan.self_nash_utility > 0.1
        assert plan.counterpart_nash_utility > 0.1

    def test_outbound_assertion_guard(self):
        sim = NegotiationSimulator()

        # Unverified factual assertion -> Rejected
        res_unverified = sim.verify_outbound_assertion(
            "Our server uptime last month was 99.999%",
            assertion_type=ASSERTION_TYPE_FACT,
            grounded_belief_id="belief_uptime_123",
            verified_beliefs_in_memory=["belief_other_456"],
        )
        assert res_unverified.is_authorized is False
        assert "not verified" in res_unverified.rejection_reason

        # Verified factual assertion -> Authorized
        res_verified = sim.verify_outbound_assertion(
            "Our server uptime last month was 99.999%",
            assertion_type=ASSERTION_TYPE_FACT,
            grounded_belief_id="belief_uptime_123",
            verified_beliefs_in_memory=["belief_uptime_123"],
        )
        assert res_verified.is_authorized is True

        # Subjective offer -> Authorized directly
        res_offer = sim.verify_outbound_assertion(
            "We propose a 15% discount for annual pre-payment",
            assertion_type=ASSERTION_TYPE_OFFER,
        )
        assert res_offer.is_authorized is True
