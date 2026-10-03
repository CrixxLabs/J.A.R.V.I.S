"""Tests for Module AV: Trust-Calibrated Mixed-Initiative Governor."""
import time
import pytest
from initiative_governor import (
    AutonomyTier,
    CapabilityTrustState,
    InitiativeAction,
    InitiativeGovernor,
    InitiativeVerdict,
    evaluate_intervention,
    get_initiative_governor,
    record_feedback,
)


class TestInitiativeGovernor:
    def test_expected_utility_selection_high_confidence(self):
        governor = InitiativeGovernor(promotion_lcb_threshold=0.75)
        # Set capability to ACT_WITH_PREVIEW (15 successes)
        for _ in range(15):
            governor.record_feedback("format_code", accepted=True)

        verdict = governor.evaluate_intervention(
            capability_name="format_code",
            p_success=0.95,
            u_benefit=2.0,
            c_wrong=1.0,
            c_undo=0.2,
            c_attention=0.1,
            modifies_codebase=True,
            proposed_worktree_path=".claude/worktrees/proactive_test",
        )

        assert verdict.selected_action == InitiativeAction.ACT
        assert verdict.utility_scores[InitiativeAction.ACT.value] > verdict.utility_scores[InitiativeAction.SUGGEST.value]
        assert verdict.worktree_isolated is True

    def test_worktree_isolation_blocks_direct_repo_modifications(self):
        governor = InitiativeGovernor()
        # Promote capability
        for _ in range(10):
            governor.record_feedback("refactor_engine", accepted=True)

        # Attempt to ACT directly in active workspace without worktree
        verdict = governor.evaluate_intervention(
            capability_name="refactor_engine",
            p_success=0.98,
            u_benefit=3.0,
            modifies_codebase=True,
            proposed_worktree_path="D:/J.A.R.V.I.S/main_repo",  # Not a worktree!
        )

        # Must be downgraded from ACT because worktree isolation failed
        assert verdict.selected_action in (InitiativeAction.SUGGEST, InitiativeAction.SILENT)
        assert verdict.worktree_isolated is False

    def test_autonomy_tier_escalation_and_demotion(self):
        governor = InitiativeGovernor(min_trials_for_promotion=5, promotion_lcb_threshold=0.75)
        cap = "test_linter"

        # Initially SUGGEST (1)
        state = governor._get_or_create_trust_state(cap)
        assert state.current_tier == AutonomyTier.SUGGEST

        # Record 15 consecutive successes
        for _ in range(15):
            state = governor.record_feedback(cap, accepted=True)

        # Should have been promoted to ACT_WITH_PREVIEW (2) or higher
        assert state.current_tier >= AutonomyTier.ACT_WITH_PREVIEW
        tier_before_demotion = state.current_tier

        # Single user rejection -> immediate demotion
        state = governor.record_feedback(cap, accepted=False)
        assert state.current_tier == tier_before_demotion - 1

    def test_hourly_interruption_budget_enforcement(self):
        governor = InitiativeGovernor(hourly_budget=3)
        t_base = 1000.0

        # Fire 3 suggestions
        for i in range(3):
            assert governor.check_interruption_budget(now=t_base + i * 10) is True
            governor._record_interruption(now=t_base + i * 10)

        # 4th suggestion within same hour should be rejected by budget
        assert governor.check_interruption_budget(now=t_base + 300) is False

        # But critical safety invariant warning bypasses the budget
        assert governor.check_interruption_budget(now=t_base + 300, is_critical_safety=True) is True

        # After 1 hour (t_base + 3601s), budget resets
        assert governor.check_interruption_budget(now=t_base + 3605) is True
