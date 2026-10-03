"""Tests for Module AG: Research Synthesis & Empirical Discovery."""
import pytest
from research_engine import (
    ResearchEngine,
    evaluate_hypothesis,
    get_research_engine,
    preregister_hypothesis,
    record_claim,
)


class TestResearchEngine:
    def test_preregistration_hashing(self):
        engine = ResearchEngine()
        reg1 = engine.preregister_hypothesis(
            title="A/B Test Latency",
            predicted_effect="Latency reduces by 10ms",
            test_type="t-test",
        )
        assert reg1.registration_hash is not None
        assert reg1.is_executed is False

        # Identical re-registration at a different time must have different hash
        # (covered by created_at timestamp in payload)
        import time
        time.sleep(0.01)
        reg2 = engine.preregister_hypothesis(
            title="A/B Test Latency",
            predicted_effect="Latency reduces by 10ms",
            test_type="t-test",
        )
        assert reg1.registration_hash != reg2.registration_hash

    def test_alpha_investing_success_reward(self):
        engine = ResearchEngine(initial_alpha_wealth=0.05, alpha_gamma=0.5, success_reward=0.10)
        # initial wealth 0.05 -> allocated alpha = 0.5 * 0.05 = 0.025
        reg = engine.preregister_hypothesis("Test H1", "Positive effect")
        assert reg.allocated_alpha == pytest.approx(0.025, abs=1e-5)

        # evaluate (successful discovery, p < 0.025)
        res = engine.evaluate_hypothesis(reg.prereg_id, p_value=0.01, observed_effect_size=1.5)
        assert res.is_significant is True

        # wealth = 0.05 - 0.025 + 0.10 = 0.125
        assert engine.get_alpha_wealth() == pytest.approx(0.125, abs=1e-5)

    def test_alpha_investing_failure_penalty(self):
        engine = ResearchEngine(initial_alpha_wealth=0.04, alpha_gamma=0.5)
        # allocated = 0.02
        reg = engine.preregister_hypothesis("Test H2", "No effect")

        # evaluate (failed discovery, p > 0.02)
        res = engine.evaluate_hypothesis(reg.prereg_id, p_value=0.05, observed_effect_size=0.1)
        assert res.is_significant is False

        # wealth = 0.04 - 0.02 = 0.02
        assert engine.get_alpha_wealth() == pytest.approx(0.02, abs=1e-5)

    def test_doi_verification(self):
        engine = ResearchEngine()
        assert engine.verify_doi_format("10.1038/nphys1170") is True
        assert engine.verify_doi_format("https://doi.org/10.1109/5.771073") is True
        assert engine.verify_doi_format("doi:10.1016/j.jcp.2007.01.037") is True
        assert engine.verify_doi_format("not-a-doi") is False

    def test_record_claim(self):
        engine = ResearchEngine()
        with pytest.raises(ValueError, match="Invalid DOI format"):
            engine.record_claim("Invalid citation", "art_1", doi_citations=["bad-doi"])

        claim = engine.record_claim(
            "Graph throughput exceeds 1M TPS",
            artifact_id="benchmark_run_52",
            doi_citations=["10.1145/1234.5678"]
        )
        assert claim.is_verified is True
        assert claim.artifact_id == "benchmark_run_52"
