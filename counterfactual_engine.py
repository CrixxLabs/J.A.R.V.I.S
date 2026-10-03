"""Programmatic Structural Causal Models & Counterfactual Engine for J.A.R.V.I.S. — MARK VIII.

Module AM:
  1. Programmatic Structural Causal Model (SCM):
     - Structural equations: X_i = f_i(Pa_i, U_i) with executable Python functions.
     - Directed Acyclic Graph (DAG) topological evaluation.
  2. Pearl's Abduction-Action-Prediction Counterfactual Algorithm:
     - Step 1 (Abduction): Inverts observed evidence E = e to infer exogenous noise U*.
     - Step 2 (Action): Applies do(X = x') interventions (graph surgery / node replacement).
     - Step 3 (Prediction): Forward simulates counterfactual state Y_{do(x')}(u*).
  3. Causal Attribution & Counterfactual Regret Analysis:
     - Evaluates whether counterfactual decisions would have averted observed task failures.
"""
from __future__ import annotations

import collections
import copy
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.counterfactual_engine")

_lock = threading.RLock()


@dataclass
class SCMVariable:
    name: str
    parents: List[str]
    # equation: Callable[[Dict[str, Any], Any], Any] -> f(parent_values, exogenous_u)
    equation_fn: Callable[[Dict[str, Any], Any], Any]
    exogenous_sampler: Callable[[], Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "parents": self.parents}


@dataclass
class CounterfactualResult:
    factual_outcome: Dict[str, Any]
    intervention: Dict[str, Any]
    counterfactual_outcome: Dict[str, Any]
    abduced_exogenous: Dict[str, Any]
    regret_score: float             # Change in target utility / success
    prevented_failure: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StructuralCausalModel:
    """Programmatic Structural Causal Model executing Abduction-Action-Prediction counterfactuals."""

    def __init__(self):
        self._variables: Dict[str, SCMVariable] = {}
        # Invert functions for deterministic abduction if available: u = inv(x, pa)
        self._inverters: Dict[str, Callable[[Any, Dict[str, Any]], Any]] = {}

    def add_variable(
        self,
        name: str,
        parents: List[str],
        equation_fn: Callable[[Dict[str, Any], Any], Any],
        exogenous_sampler: Optional[Callable[[], Any]] = None,
        inverter_fn: Optional[Callable[[Any, Dict[str, Any]], Any]] = None,
    ) -> None:
        """Register a causal variable X_i = f_i(Pa_i, U_i)."""
        sampler = exogenous_sampler or (lambda: 0.0)
        with _lock:
            self._variables[name] = SCMVariable(name, parents, equation_fn, sampler)
            if inverter_fn:
                self._inverters[name] = inverter_fn

    def _topological_sort(self) -> List[str]:
        """Return variable names in causal topological order."""
        in_degree = {k: 0 for k in self._variables}
        adj = collections.defaultdict(list)

        for name, var in self._variables.items():
            for p in var.parents:
                if p in self._variables:
                    adj[p].append(name)
                    in_degree[name] += 1

        queue = [k for k, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in adj[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self._variables):
            raise ValueError("Causal graph contains cycles! Must be a DAG.")

        return order

    def sample_factual(
        self,
        interventions: Optional[Dict[str, Any]] = None,
        fixed_exogenous: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Forward simulate the SCM under optional interventions and fixed/sampled exogenous noise."""
        interventions = interventions or {}
        fixed_exogenous = fixed_exogenous or {}

        order = self._topological_sort()
        values: Dict[str, Any] = {}
        exogenous_used: Dict[str, Any] = {}

        for var_name in order:
            if var_name in interventions:
                values[var_name] = interventions[var_name]
                exogenous_used[var_name] = fixed_exogenous.get(var_name, None)
            else:
                var = self._variables[var_name]
                parent_vals = {p: values[p] for p in var.parents}
                u = fixed_exogenous.get(var_name, var.exogenous_sampler())
                exogenous_used[var_name] = u
                values[var_name] = var.equation_fn(parent_vals, u)

        return values, exogenous_used

    def evaluate_counterfactual(
        self,
        factual_observation: Dict[str, Any],
        intervention_do: Dict[str, Any],
        target_success_key: Optional[str] = "success",
    ) -> CounterfactualResult:
        """Execute 3-step Pearlian Counterfactual (Abduction -> Action -> Prediction)."""
        with _lock:
            # Step 1: Abduction (infer U* from observed factual data)
            abduced_u: Dict[str, Any] = {}
            for name, var in self._variables.items():
                obs_x = factual_observation.get(name)
                parent_vals = {p: factual_observation.get(p) for p in var.parents}

                if name in self._inverters and obs_x is not None:
                    # Invert u = f^-1(x, pa)
                    try:
                        abduced_u[name] = self._inverters[name](obs_x, parent_vals)
                    except Exception:
                        abduced_u[name] = var.exogenous_sampler()
                else:
                    abduced_u[name] = var.exogenous_sampler()

            # Step 2: Action & Step 3: Prediction (evaluate with do(X=x') and U=abduced_u)
            cf_values, _ = self.sample_factual(interventions=intervention_do, fixed_exogenous=abduced_u)

            # Regret & Failure analysis
            fact_success = factual_observation.get(target_success_key, False)
            cf_success = cf_values.get(target_success_key, False)

            # Did the counterfactual action fix the failure?
            prevented_failure = (not fact_success) and bool(cf_success)

            fact_score = 1.0 if fact_success else 0.0
            cf_score = 1.0 if cf_success else 0.0
            regret_score = round(cf_score - fact_score, 4)

            res = CounterfactualResult(
                factual_outcome=factual_observation,
                intervention=intervention_do,
                counterfactual_outcome=cf_values,
                abduced_exogenous=abduced_u,
                regret_score=regret_score,
                prevented_failure=prevented_failure,
            )

            try:
                get_registry().set_capability_evidence(
                    "COUNTERFACTUAL_ENGINE",
                    EvidenceLevel.LIVE,
                    f"Evaluated counterfactual do({intervention_do}): prevented_failure={prevented_failure}",
                    source="counterfactual_engine.evaluate_counterfactual",
                )
            except Exception:
                pass

            return res


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scm_instance: Optional[StructuralCausalModel] = None


def get_counterfactual_engine() -> StructuralCausalModel:
    global _scm_instance
    if _scm_instance is None:
        with _lock:
            if _scm_instance is None:
                _scm_instance = StructuralCausalModel()
    return _scm_instance


def add_variable(name: str, parents: List[str], equation_fn: Callable, **kwargs) -> None:
    get_counterfactual_engine().add_variable(name, parents, equation_fn, **kwargs)


def evaluate_counterfactual(factual_observation: Dict[str, Any], intervention_do: Dict[str, Any], **kwargs) -> CounterfactualResult:
    return get_counterfactual_engine().evaluate_counterfactual(factual_observation, intervention_do, **kwargs)
