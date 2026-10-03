"""Amortized Deliberation / System-2 to System-1 Compiler for J.A.R.V.I.S. — MARK VIII.

Module AO:
  1. Trace Compilation (System-2 -> System-1):
     - Distills repetitive deliberation traces into instant CPU fast-path rules and heuristics.
  2. Beta-Binomial Lower Credible Bound Gate:
     - Gated compilation requiring the 95% lower credible bound of the posterior
       Beta(alpha + successes, beta + failures) distribution to exceed reliability threshold theta.
  3. Dynamic Decompilation & Invalidation:
     - Automatically de-optimizes and retires fast-path heuristics if runtime verification
       fails or environment distribution drifts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from scipy.stats import beta as beta_dist

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.system1_compiler")

_lock = threading.RLock()


@dataclass
class CompiledRule:
    rule_id: str
    input_signature: str
    fast_action: str
    total_trials: int
    success_count: int
    lower_credible_bound: float
    is_active: bool
    compiled_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class System1Compiler:
    """Compiles repetitive System-2 deliberative traces into Bayesian-verified System-1 fast paths."""

    def __init__(
        self,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
        reliability_threshold: float = 0.85,
        min_trials: int = 5,
    ):
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self.reliability_threshold = reliability_threshold
        self.min_trials = min_trials

        # input_sig -> { action -> {"success": int, "fail": int} }
        self._trace_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._compiled_rules: Dict[str, CompiledRule] = {}

    def _hash_signature(self, input_pattern: Dict[str, Any]) -> str:
        s = json.dumps(input_pattern, sort_keys=True)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Beta-Binomial Credible Bound
    # ------------------------------------------------------------------

    def compute_lower_credible_bound(
        self,
        successes: int,
        total_trials: int,
        confidence: float = 0.95,
    ) -> float:
        """Compute the (1 - confidence) quantile of the posterior Beta distribution."""
        failures = total_trials - successes
        a = self.prior_alpha + successes
        b = self.prior_beta + failures
        # 95% lower bound = ppf(0.05)
        lower_bound = float(beta_dist.ppf(1.0 - confidence, a, b))
        return round(max(0.0, lower_bound), 4)

    # ------------------------------------------------------------------
    # Trace Recording & Compilation
    # ------------------------------------------------------------------

    def record_trace(
        self,
        input_pattern: Dict[str, Any],
        deliberated_action: str,
        was_successful: bool,
    ) -> Optional[CompiledRule]:
        """Record deliberative execution trace and trigger fast-path compilation if threshold met."""
        sig = self._hash_signature(input_pattern)

        with _lock:
            if sig not in self._trace_stats:
                self._trace_stats[sig] = {}
            if deliberated_action not in self._trace_stats[sig]:
                self._trace_stats[sig][deliberated_action] = {"success": 0, "fail": 0}

            if was_successful:
                self._trace_stats[sig][deliberated_action]["success"] += 1
            else:
                self._trace_stats[sig][deliberated_action]["fail"] += 1

            stats = self._trace_stats[sig][deliberated_action]
            k = stats["success"]
            n = k + stats["fail"]

            lcb = self.compute_lower_credible_bound(k, n)

            # Check if compiles to fast-path rule
            if n >= self.min_trials and lcb >= self.reliability_threshold:
                rid = f"rule_{sig[:8]}"
                rule = CompiledRule(
                    rule_id=rid,
                    input_signature=sig,
                    fast_action=deliberated_action,
                    total_trials=n,
                    success_count=k,
                    lower_credible_bound=lcb,
                    is_active=True,
                    compiled_at=time.time(),
                )
                self._compiled_rules[sig] = rule
                log.info(f"[System1Compiler] Compiled fast-path {rid}: '{deliberated_action}' (LCB={lcb:.3f})")

                try:
                    get_registry().set_capability_evidence(
                        "SYSTEM1_COMPILER",
                        EvidenceLevel.LIVE,
                        f"Compiled fast-path rule {rid} (action='{deliberated_action}', LCB={lcb:.3f})",
                        source="system1_compiler.record_trace",
                    )
                except Exception:
                    pass

                return rule

            return None

    def query_fast_path(self, input_pattern: Dict[str, Any]) -> Optional[str]:
        """Return fast-path action if active rule exists, else None (requires System-2 deliberation)."""
        sig = self._hash_signature(input_pattern)
        with _lock:
            rule = self._compiled_rules.get(sig)
            if rule and rule.is_active:
                return rule.fast_action
            return None

    # ------------------------------------------------------------------
    # Dynamic Decompilation
    # ------------------------------------------------------------------

    def report_execution_result(self, input_pattern: Dict[str, Any], action: str, success: bool) -> bool:
        """Report runtime execution result for a fast-path. Invalidate if reliability drops."""
        sig = self._hash_signature(input_pattern)
        with _lock:
            rule = self._compiled_rules.get(sig)
            if not rule:
                return False

            rule.total_trials += 1
            if success:
                rule.success_count += 1
            else:
                log.warning(f"[System1Compiler] Fast-path failure detected on rule {rule.rule_id}")

            lcb = self.compute_lower_credible_bound(rule.success_count, rule.total_trials)
            rule.lower_credible_bound = lcb

            if lcb < self.reliability_threshold:
                self.decompile_rule(sig, reason=f"LCB {lcb:.3f} dropped below threshold {self.reliability_threshold}")
                return True

            return False

    def decompile_rule(self, input_sig_or_rule_id: str, reason: str) -> None:
        """De-optimize and retire compiled fast-path rule back to System-2 deliberation."""
        with _lock:
            for sig, rule in list(self._compiled_rules.items()):
                if sig == input_sig_or_rule_id or rule.rule_id == input_sig_or_rule_id:
                    rule.is_active = False
                    log.warning(f"[System1Compiler] Decompiled rule {rule.rule_id} ({reason})")
                    self._compiled_rules.pop(sig, None)
                    break


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_compiler_instance: Optional[System1Compiler] = None


def get_system1_compiler() -> System1Compiler:
    global _compiler_instance
    if _compiler_instance is None:
        with _lock:
            if _compiler_instance is None:
                _compiler_instance = System1Compiler()
    return _compiler_instance


def record_trace(input_pattern: Dict[str, Any], deliberated_action: str, was_successful: bool) -> Optional[CompiledRule]:
    return get_system1_compiler().record_trace(input_pattern, deliberated_action, was_successful)


def query_fast_path(input_pattern: Dict[str, Any]) -> Optional[str]:
    return get_system1_compiler().query_fast_path(input_pattern)


def report_execution_result(input_pattern: Dict[str, Any], action: str, success: bool) -> bool:
    return get_system1_compiler().report_execution_result(input_pattern, action, success)
