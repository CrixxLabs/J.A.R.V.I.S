"""Unit tests for Gated Self-Adaptation Engine (Module U)."""
from pathlib import Path
import pytest

from gated_adaptation import (
    GatedAdaptationEngine,
    TYPE_PROMPT_MUTATION,
    TYPE_SYNTHESIZED_SKILL,
    STATUS_QUALIFIED,
    STATUS_REJECTED,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_ROLLED_BACK,
)


@pytest.fixture
def engine(tmp_path):
    ledger_file = tmp_path / "test_adaptation_ledger.json"
    return GatedAdaptationEngine(ledger_path=ledger_file)


def test_filter_trajectories_provenance_and_ast_safety(engine):
    trajectories = [
        {
            "trajectory_id": "traj_01_safe_verified",
            "symbolic_verified": True,
            "user_confirmed": False,
            "code": "def compute_tax(income):\n    return income * 0.2\n",
        },
        {
            "trajectory_id": "traj_02_unverified",
            "symbolic_verified": False,
            "user_confirmed": False,
            "code": "def process():\n    pass\n",
        },
        {
            "trajectory_id": "traj_03_malicious_verified",
            "symbolic_verified": True,
            "user_confirmed": True,
            "code": "import ctypes\ndef crash():\n    pass\n",
        },
    ]

    res = engine.filter_trajectories(trajectories)
    assert res["total_processed"] == 3
    assert res["accepted_count"] == 1
    assert res["rejected_count"] == 2
    assert res["accepted_trajectories"][0]["trajectory_id"] == "traj_01_safe_verified"


def test_evaluate_candidate_regression_gate(engine):
    # Benchmark tests
    benchmark = [
        {
            "name": "case_1",
            "test_fn": lambda art, inp, exp: art.get("multiplier", 1) * 2 == 4,
        },
        {
            "name": "case_2",
            "test_fn": lambda art, inp, exp: "hello" in art.get("template", ""),
        },
    ]

    # Passing Candidate
    good_cand = engine.evaluate_candidate_adaptation(
        candidate_id="skill_v1",
        candidate_type=TYPE_SYNTHESIZED_SKILL,
        target_subsystem="financial_calc",
        artifact={"multiplier": 2, "template": "hello user"},
        regression_benchmark=benchmark,
    )
    assert good_cand["status"] == STATUS_QUALIFIED
    assert good_cand["evaluation_report"]["regression_detected"] is False

    # Failing Candidate
    bad_cand = engine.evaluate_candidate_adaptation(
        candidate_id="skill_v2_broken",
        candidate_type=TYPE_SYNTHESIZED_SKILL,
        target_subsystem="financial_calc",
        artifact={"multiplier": 99, "template": "wrong template"},
        regression_benchmark=benchmark,
    )
    assert bad_cand["status"] == STATUS_REJECTED
    assert bad_cand["evaluation_report"]["regression_detected"] is True


def test_promotion_and_atomic_rollback(engine):
    # 1. Qualify Candidate V1
    engine.evaluate_candidate_adaptation(
        candidate_id="prompt_v1",
        candidate_type=TYPE_PROMPT_MUTATION,
        target_subsystem="planner",
        artifact={"prompt": "Strict structured output"},
    )
    p1 = engine.promote_adaptation("prompt_v1", promotion_mode="ACTIVE")
    assert p1["success"] is True
    assert p1["status"] == STATUS_ACTIVE

    # 2. Qualify Candidate V2
    engine.evaluate_candidate_adaptation(
        candidate_id="prompt_v2",
        candidate_type=TYPE_PROMPT_MUTATION,
        target_subsystem="planner",
        artifact={"prompt": "Over-concise output"},
    )
    p2 = engine.promote_adaptation("prompt_v2", promotion_mode="ACTIVE")
    assert p2["success"] is True
    assert p2["status"] == STATUS_ACTIVE

    # Verify v1 became ARCHIVED
    history = {r["candidate_id"]: r["status"] for r in engine.get_adaptation_history()}
    assert history["prompt_v1"] == STATUS_ARCHIVED
    assert history["prompt_v2"] == STATUS_ACTIVE

    # 3. Rollback V2 -> Should restore V1
    rb = engine.rollback_adaptation("prompt_v2", reason="Produced ambiguous plans")
    assert rb["success"] is True
    assert rb["rolled_back_id"] == "prompt_v2"
    assert rb["restored_baseline_id"] == "prompt_v1"

    active = engine.get_active_adaptations()
    assert len(active) == 1
    assert active[0]["candidate_id"] == "prompt_v1"
    assert active[0]["status"] == STATUS_ACTIVE
