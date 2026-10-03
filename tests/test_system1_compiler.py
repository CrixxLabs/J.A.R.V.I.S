"""Tests for Module AO: Amortized Deliberation / System-2 to System-1 Compiler."""
import pytest
from system1_compiler import (
    CompiledRule,
    System1Compiler,
    get_system1_compiler,
    query_fast_path,
    record_trace,
    report_execution_result,
)


class TestSystem1Compiler:
    def test_beta_binomial_lower_bound(self):
        compiler = System1Compiler(prior_alpha=1.0, prior_beta=1.0)
        # 10 successes out of 10 trials
        lcb_perfect = compiler.compute_lower_credible_bound(10, 10, confidence=0.95)
        assert lcb_perfect > 0.70

        # 20 successes out of 20 trials
        lcb_high = compiler.compute_lower_credible_bound(20, 20, confidence=0.95)
        assert lcb_high > 0.85

        # 5 successes, 5 failures
        lcb_mediocre = compiler.compute_lower_credible_bound(5, 10, confidence=0.95)
        assert lcb_mediocre < 0.35

    def test_trace_compilation_and_fast_path_query(self):
        compiler = System1Compiler(reliability_threshold=0.80, min_trials=10)
        pattern = {"context": "search_codebase", "query_type": "symbol"}

        # Initially no fast path
        assert compiler.query_fast_path(pattern) is None

        # Record 15 consecutive successes
        compiled = None
        for _ in range(15):
            compiled = compiler.record_trace(pattern, "call_ripgrep", was_successful=True)

        assert compiled is not None
        assert compiled.is_active is True
        assert compiler.query_fast_path(pattern) == "call_ripgrep"

    def test_dynamic_decompilation_on_failure(self):
        compiler = System1Compiler(reliability_threshold=0.75, min_trials=10)
        pattern = {"context": "network_probe", "target": "internal_api"}

        # Train until compiled
        for _ in range(15):
            compiler.record_trace(pattern, "http_get", was_successful=True)

        assert compiler.query_fast_path(pattern) == "http_get"

        # Inject multiple runtime failures
        for _ in range(8):
            compiler.report_execution_result(pattern, "http_get", success=False)

        # Should be decompiled back to System-2 deliberation (None)
        assert compiler.query_fast_path(pattern) is None
