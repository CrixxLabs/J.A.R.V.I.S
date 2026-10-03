"""Tests for Module AN: Open-Ended Goal Formulation under Ratification."""
import pytest
from goal_generator import (
    GoalGenerator,
    RATIFICATION_AUTO_APPROVED,
    RATIFICATION_QUEUED_FOR_DIGEST,
    SynthesizedGoal,
    filter_goldilocks_goals,
    get_goal_generator,
    ratify_goal,
    synthesize_goals,
)


class TestGoalGenerator:
    def test_goal_synthesis(self):
        gen = GoalGenerator(seed=123)
        objects = [{"type": "key"}, {"type": "door"}]
        goals = gen.synthesize_goals(objects, count=6)

        assert len(goals) == 6
        for g in goals:
            assert isinstance(g, SynthesizedGoal)
            assert g.predicate is not None
            assert g.goal_id.startswith("goal_")

    def test_goldilocks_difficulty_filter(self):
        gen = GoalGenerator()
        g_trivial = SynthesizedGoal("g1", "p1", {}, estimated_difficulty_p=0.95, learning_progress_potential=0.5, risk_tier="LOW", is_sandboxed=True, is_reversible=True)
        g_impossible = SynthesizedGoal("g2", "p2", {}, estimated_difficulty_p=0.05, learning_progress_potential=0.5, risk_tier="LOW", is_sandboxed=True, is_reversible=True)
        g_just_right = SynthesizedGoal("g3", "p3", {}, estimated_difficulty_p=0.50, learning_progress_potential=0.8, risk_tier="LOW", is_sandboxed=True, is_reversible=True)

        filtered = gen.filter_goldilocks_goals([g_trivial, g_impossible, g_just_right], min_p=0.20, max_p=0.80)
        assert len(filtered) == 1
        assert filtered[0].goal_id == "g3"

    def test_ratification_pipeline(self):
        gen = GoalGenerator()

        # Safe low-risk sandboxed goal -> Auto approved
        g_safe = SynthesizedGoal("g_safe", "reach", {}, 0.5, 0.5, risk_tier="LOW", is_sandboxed=True, is_reversible=True)
        rat_safe = gen.ratify_goal(g_safe)
        assert rat_safe.ratification_status == RATIFICATION_AUTO_APPROVED

        # High-risk goal -> Queued for digest
        g_risk = SynthesizedGoal("g_risk", "delete_db", {}, 0.5, 0.5, risk_tier="HIGH", is_sandboxed=False, is_reversible=False)
        rat_risk = gen.ratify_goal(g_risk)
        assert rat_risk.ratification_status == RATIFICATION_QUEUED_FOR_DIGEST
        assert len(gen.get_digest_queue()) == 1
