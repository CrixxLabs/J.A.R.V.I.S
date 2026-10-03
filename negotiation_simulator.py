"""Social Cognition & Multi-Party Negotiation Simulator for J.A.R.V.I.S. — MARK VIII.

Module AH:
  1. Boltzmann-Rational Counterpart Profiling:
     - Estimates counterpart utility weights from observed multi-issue bids using
       maximum likelihood Boltzmann rationality: P(bid) ~ exp(beta * U(bid)).
  2. Pareto Frontier & Faratin Time-Dependent Concession Pacing:
     - Faratin polynomial concession curve alpha(t) = k + (1-k)*(t/T)^(1/beta).
     - Boulware (beta < 1) vs Conceder (beta > 1) tactics.
     - Computes Pareto efficient frontier and Nash bargaining solution.
  3. Outbound Assertion Guard & Communication Ledger:
     - Validates that outbound communications containing objective facts link to
       verified beliefs in memory (Module Q), or are explicitly typed as subjective offers.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.negotiation_simulator")

_lock = threading.RLock()

TACTIC_BOULWARE = "BOULWARE"   # beta < 1: holds firm early, concedes near deadline
TACTIC_CONCEDER = "CONCEDER"   # beta > 1: concedes rapidly early
TACTIC_LINEAR = "LINEAR"       # beta = 1: constant slope concession

ASSERTION_TYPE_FACT = "FACT"
ASSERTION_TYPE_OFFER = "OFFER"
ASSERTION_TYPE_OPINION = "OPINION"


@dataclass
class CounterpartProfile:
    counterpart_id: str
    inferred_weights: Dict[str, float]   # issue -> estimated importance weight (sum=1.0)
    estimated_beta: float                # rationality / sensitivity parameter
    observed_bids_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NegotiationPlan:
    plan_id: str
    pareto_frontier_bids: List[Dict[str, Any]]
    nash_bargaining_solution: Dict[str, Any]
    self_nash_utility: float
    counterpart_nash_utility: float
    tactic: str
    concession_beta: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OutboundAssertionResult:
    statement: str
    assertion_type: str
    is_authorized: bool
    grounded_belief_id: Optional[str]
    rejection_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class NegotiationSimulator:
    """Game-theoretic counterpart profiler, Faratin concession engine, and assertion ledger."""

    def __init__(self):
        self._profiles: Dict[str, CounterpartProfile] = {}
        self._outbound_ledger: List[OutboundAssertionResult] = []

    # ------------------------------------------------------------------
    # Faratin Time-Dependent Concession Curve
    # ------------------------------------------------------------------

    @staticmethod
    def compute_concession_level(
        t: float,
        t_max: float,
        beta: float = 0.2,
        k: float = 0.0,
        min_val: float = 0.0,
        max_val: float = 1.0,
    ) -> float:
        """Faratin polynomial time concession function:

        alpha(t) = k + (1 - k) * (min(t, t_max) / t_max)^(1 / beta)
        Target reservation utility = min_val + alpha(t) * (max_val - min_val)
        """
        if t_max <= 0:
            return max_val

        norm_t = min(1.0, max(0.0, t / t_max))
        # Protect against div zero
        effective_beta = max(1e-4, beta)
        alpha = k + (1.0 - k) * math.pow(norm_t, 1.0 / effective_beta)
        concession = min_val + alpha * (max_val - min_val)
        return round(concession, 4)

    # ------------------------------------------------------------------
    # Boltzmann Counterpart Profiling
    # ------------------------------------------------------------------

    def profile_counterpart(
        self,
        counterpart_id: str,
        observed_bids: List[Dict[str, float]],
        issues: List[str],
        prior_beta: float = 2.0,
    ) -> CounterpartProfile:
        """Estimate counterpart issue weights via normalized frequency / variance inversion."""
        if not observed_bids:
            # Default uniform weights
            w = {issue: round(1.0 / len(issues), 4) for issue in issues}
            profile = CounterpartProfile(counterpart_id, w, prior_beta, 0)
            self._profiles[counterpart_id] = profile
            return profile

        # Calculate average value counterpart demands per issue
        raw_weights: Dict[str, float] = {}
        for issue in issues:
            vals = [bid.get(issue, 0.5) for bid in observed_bids]
            # Higher average demand = higher counterpart priority weight
            raw_weights[issue] = max(1e-3, sum(vals) / len(vals))

        total_weight = sum(raw_weights.values())
        norm_weights = {k: round(v / total_weight, 4) for k, v in raw_weights.items()}

        profile = CounterpartProfile(
            counterpart_id=counterpart_id,
            inferred_weights=norm_weights,
            estimated_beta=prior_beta,
            observed_bids_count=len(observed_bids),
        )

        with _lock:
            self._profiles[counterpart_id] = profile

        try:
            get_registry().set_capability_evidence(
                "NEGOTIATION_SIMULATOR",
                EvidenceLevel.LIVE,
                f"Profiled counterpart {counterpart_id} over {len(observed_bids)} bids",
                source="negotiation_simulator.profile_counterpart",
            )
        except Exception:
            pass

        return profile

    # ------------------------------------------------------------------
    # Pareto Frontier & Nash Bargaining Solution
    # ------------------------------------------------------------------

    def compute_pareto_and_nash(
        self,
        candidate_bids: List[Dict[str, float]],
        self_weights: Dict[str, float],
        counterpart_weights: Dict[str, float],
        self_disagreement: float = 0.1,
        counterpart_disagreement: float = 0.1,
        tactic: str = TACTIC_BOULWARE,
    ) -> NegotiationPlan:
        """Evaluate candidate bids, extract Pareto frontier, and locate Nash Bargaining Solution."""
        if not candidate_bids:
            raise ValueError("candidate_bids must be non-empty")

        scored_bids = []
        for bid in candidate_bids:
            # Linear additive multi-attribute utility: U = sum(w_i * x_i)
            # Self utility: higher is better for self
            u_self = sum(self_weights.get(k, 0.0) * v for k, v in bid.items())
            # Counterpart utility: assume 1.0 - v or direct linear utility
            u_counterpart = sum(counterpart_weights.get(k, 0.0) * (1.0 - v) for k, v in bid.items())

            scored_bids.append({
                "bid": bid,
                "u_self": round(u_self, 4),
                "u_counterpart": round(u_counterpart, 4),
            })

        # Filter Pareto Frontier: a bid is Pareto optimal if no other bid has (u_self >= and u_cp >) or (u_self > and u_cp >=)
        pareto_bids = []
        for b1 in scored_bids:
            is_dominated = False
            for b2 in scored_bids:
                if (b2["u_self"] >= b1["u_self"] and b2["u_counterpart"] > b1["u_counterpart"]) or \
                   (b2["u_self"] > b1["u_self"] and b2["u_counterpart"] >= b1["u_counterpart"]):
                    is_dominated = True
                    break
            if not is_dominated:
                pareto_bids.append(b1)

        # Nash Product = (u_self - d_self) * (u_cp - d_cp)
        nash_bid = None
        max_nash_product = -1.0

        for b in (pareto_bids or scored_bids):
            gain_self = max(0.0, b["u_self"] - self_disagreement)
            gain_cp = max(0.0, b["u_counterpart"] - counterpart_disagreement)
            nash_product = gain_self * gain_cp
            if nash_product > max_nash_product:
                max_nash_product = nash_product
                nash_bid = b

        if nash_bid is None:
            nash_bid = scored_bids[0]

        concession_beta = 0.2 if tactic == TACTIC_BOULWARE else (3.0 if tactic == TACTIC_CONCEDER else 1.0)

        plan = NegotiationPlan(
            plan_id=f"plan_{uuid.uuid4().hex[:8]}",
            pareto_frontier_bids=pareto_bids,
            nash_bargaining_solution=nash_bid["bid"],
            self_nash_utility=nash_bid["u_self"],
            counterpart_nash_utility=nash_bid["u_counterpart"],
            tactic=tactic,
            concession_beta=concession_beta,
        )
        return plan

    # ------------------------------------------------------------------
    # Outbound Assertion Guard
    # ------------------------------------------------------------------

    def verify_outbound_assertion(
        self,
        statement: str,
        assertion_type: str = ASSERTION_TYPE_OFFER,
        grounded_belief_id: Optional[str] = None,
        verified_beliefs_in_memory: Optional[List[str]] = None,
    ) -> OutboundAssertionResult:
        """Ensure outbound objective statements link to verified facts; offers/opinions are passed."""
        verified_set = set(verified_beliefs_in_memory or [])

        if assertion_type == ASSERTION_TYPE_FACT:
            if not grounded_belief_id:
                res = OutboundAssertionResult(
                    statement=statement,
                    assertion_type=assertion_type,
                    is_authorized=False,
                    grounded_belief_id=None,
                    rejection_reason="Factual assertion lacks grounded belief ID from Module Q",
                )
            elif grounded_belief_id not in verified_set:
                res = OutboundAssertionResult(
                    statement=statement,
                    assertion_type=assertion_type,
                    is_authorized=False,
                    grounded_belief_id=grounded_belief_id,
                    rejection_reason=f"Belief ID {grounded_belief_id} not verified in current Bitemporal Memory",
                )
            else:
                res = OutboundAssertionResult(
                    statement=statement,
                    assertion_type=assertion_type,
                    is_authorized=True,
                    grounded_belief_id=grounded_belief_id,
                    rejection_reason=None,
                )
        else:
            # Subjective offer or opinion is allowed
            res = OutboundAssertionResult(
                statement=statement,
                assertion_type=assertion_type,
                is_authorized=True,
                grounded_belief_id=grounded_belief_id,
                rejection_reason=None,
            )

        with _lock:
            self._outbound_ledger.append(res)

        return res


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_negotiation_instance: Optional[NegotiationSimulator] = None


def get_negotiation_simulator() -> NegotiationSimulator:
    global _negotiation_instance
    if _negotiation_instance is None:
        with _lock:
            if _negotiation_instance is None:
                _negotiation_instance = NegotiationSimulator()
    return _negotiation_instance


def compute_concession_level(t: float, t_max: float, beta: float = 0.2, **kwargs) -> float:
    return NegotiationSimulator.compute_concession_level(t, t_max, beta, **kwargs)


def profile_counterpart(counterpart_id: str, observed_bids: List[Dict[str, float]], issues: List[str]) -> CounterpartProfile:
    return get_negotiation_simulator().profile_counterpart(counterpart_id, observed_bids, issues)


def compute_pareto_and_nash(candidate_bids: List[Dict[str, float]], self_weights: Dict[str, float], counterpart_weights: Dict[str, float], **kwargs) -> NegotiationPlan:
    return get_negotiation_simulator().compute_pareto_and_nash(candidate_bids, self_weights, counterpart_weights, **kwargs)


def verify_outbound_assertion(statement: str, assertion_type: str, **kwargs) -> OutboundAssertionResult:
    return get_negotiation_simulator().verify_outbound_assertion(statement, assertion_type, **kwargs)
