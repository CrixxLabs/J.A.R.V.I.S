"""Tests for Module AT: Autonomous Teleological Intention Projection."""
import pytest
from intention_projector import (
    ConstructiveDissent,
    GoalPosterior,
    IntentionProjector,
    PlanAction,
    PreMortemResult,
    UserGoal,
    evaluate_constructive_dissent,
    get_intention_projector,
    infer_goal_posteriors,
    run_premortem_simulation,
)


class TestIntentionProjector:
    def test_inverse_plan_recognition_goal_posterior(self):
        projector = IntentionProjector(beta=2.0)

        # Goal 1: Fix authentication bug
        g1 = UserGoal(
            goal_id="fix_auth",
            description="Fix JWT Token Validation in auth.py",
            prior_probability=0.50,
            canonical_plan=[
                PlanAction("edit_file", "src/auth.py"),
                PlanAction("add_import", "jwt"),
                PlanAction("run_test", "tests/test_auth.py"),
            ],
        )

        # Goal 2: Add Database Migration
        g2 = UserGoal(
            goal_id="add_migration",
            description="Add DB Migration for User Schema",
            prior_probability=0.50,
            canonical_plan=[
                PlanAction("edit_file", "db/migrations.py"),
                PlanAction("run_test", "tests/test_db.py"),
            ],
        )

        # User performs: edit_file src/auth.py, add_import jwt
        observed = [
            PlanAction("edit_file", "src/auth.py"),
            PlanAction("add_import", "jwt"),
        ]

        posteriors = projector.infer_goal_posteriors(observed, custom_goals=[g1, g2])
        assert len(posteriors) == 2
        # Goal 1 should dominate
        top = posteriors[0]
        assert top.goal_id == "fix_auth"
        assert top.posterior_probability > 0.85

    def test_premortem_simulation_catches_syntax_error(self):
        projector = IntentionProjector()
        broken_code = "def authenticate(user):\n    if user is None\n        return False"

        res = projector.run_premortem_simulation(broken_code)
        assert res.predicted_failure is True
        assert res.syntax_valid is False
        assert res.p_fail >= 0.99
        assert len(res.detected_defects) >= 1

    def test_premortem_simulation_catches_zero_division(self):
        projector = IntentionProjector()
        bad_code = "def compute_ratio(a, b):\n    return a / 0"

        res = projector.run_premortem_simulation(bad_code)
        assert res.predicted_failure is True
        assert res.syntax_valid is True
        assert any("ZeroDivisionError" in d for d in res.detected_defects)

    def test_constructive_dissent_trigger(self):
        projector = IntentionProjector(dissent_threshold=0.35)
        premortem = PreMortemResult(
            predicted_failure=True,
            p_fail=0.90,
            severity=0.80,
            syntax_valid=False,
            detected_defects=["SyntaxError on line 2"],
            failing_scenario="Code fails to compile",
            simulation_duration_sec=0.01,
        )

        dissent = projector.evaluate_constructive_dissent(
            premortem=premortem,
            user_intent_goal="Add validation to auth helper",
            dominating_patch="def validate(token):\n    if not token:\n        return False\n    return True",
            failing_test_code="def test_validate(): assert validate(None) is False",
        )

        assert dissent.should_dissent is True
        assert dissent.risk_score >= 0.70
        assert dissent.dominating_patch is not None
        assert dissent.failing_test_code is not None
