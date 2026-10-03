"""Unit tests for Metacognitive Governor & Risk-Scaled Autonomy Engine (Module N)."""
from pathlib import Path
import pytest

from metacognitive_governor import (
    MetacognitiveGovernor,
    STAKE_LOW,
    STAKE_MEDIUM,
    STAKE_HIGH,
    STAKE_CRITICAL,
    TIER_FULL_AUTONOMOUS,
    TIER_CONFIRMATION_REQUIRED,
    TIER_ESCALATION_REQUIRED,
)


@pytest.fixture
def governor(tmp_path):
    ledger_file = tmp_path / "test_outcomes.json"
    return MetacognitiveGovernor(ledger_path=ledger_file)


def test_record_and_get_metrics(governor):
    r1 = governor.record_task_outcome("code_gen", "task_001", success=True, metadata={"tokens": 120})
    assert r1["recorded"] is True
    assert r1["domain_successes"] == 1
    assert r1["domain_total"] == 1

    r2 = governor.record_task_outcome("code_gen", "task_002", success=False, metadata={"tokens": 240})
    assert r2["domain_successes"] == 1
    assert r2["domain_failures"] == 1
    assert r2["domain_total"] == 2
    assert r2["empirical_success_rate"] == 0.5

    metrics = governor.get_domain_metrics("code_gen")
    assert metrics["successes"] == 1
    assert metrics["failures"] == 1


def test_predict_competence_cold_vs_warm(governor):
    # Cold start prediction
    cold = governor.predict_competence("robotics_navigation")
    assert cold.domain == "robotics_navigation"
    assert cold.sample_size == 0
    assert cold.calibrated_lower_bound < 0.6  # wide uncertainty interval on cold start

    # Train with 15 verified successes
    for i in range(15):
        governor.record_task_outcome("robotics_navigation", f"nav_{i}", success=True)

    warm = governor.predict_competence(
        "robotics_navigation",
        task_params={"retrieval_similarity": 0.95, "sample_agreement": 0.98},
    )
    assert warm.sample_size == 15
    assert warm.point_score > cold.point_score
    assert warm.calibrated_lower_bound > cold.calibrated_lower_bound
    assert warm.uncertainty_margin < cold.uncertainty_margin


def test_risk_scaled_autonomy_low_stake_accepted(governor):
    # For low stake, standard baseline should allow autonomous execution
    decision = governor.evaluate_autonomy_level(
        domain="general_chat",
        task_params={"retrieval_similarity": 0.85, "sample_agreement": 0.90},
        stake_level=STAKE_LOW,
    )
    assert decision.can_execute_autonomously is True
    assert decision.requires_confirmation is False
    assert decision.autonomy_tier == TIER_FULL_AUTONOMOUS
    assert decision.stake_level == STAKE_LOW


def test_risk_scaled_autonomy_critical_stake_escalated(governor):
    # With no past track record and critical stakes, it must escalate
    decision = governor.evaluate_autonomy_level(
        domain="database_migration",
        task_params={"complexity": 0.9, "prompt": "Execute destructive DROP TABLE on prod"},
        stake_level=STAKE_CRITICAL,
    )
    assert decision.can_execute_autonomously is False
    assert decision.requires_confirmation is True
    assert decision.autonomy_tier == TIER_ESCALATION_REQUIRED
    assert decision.stake_level == STAKE_CRITICAL
    assert "DROP TABLE" not in decision.source_attribution or "database_migration" in decision.source_attribution


def test_calibration_factors_penalties(governor):
    # High complexity & empty prompt vs normal
    score_normal = governor.predict_competence(
        "math",
        task_params={"prompt": "Calculate 2+2", "complexity": 0.1},
    )
    score_extreme = governor.predict_competence(
        "math",
        task_params={"prompt": "x" * 6000, "complexity": 0.95},
    )
    assert score_extreme.point_score < score_normal.point_score
    assert score_extreme.calibration_factors["prompt_length_penalty"] > 0
    assert score_extreme.calibration_factors["complexity_discount"] > score_normal.calibration_factors["complexity_discount"]
