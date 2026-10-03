"""Tests for Module AP: Substrate Succession & Cold-Boot Reconstruction."""
import pytest
from substrate_manager import (
    ColdBootRecoveryReport,
    SubstrateManager,
    SubstrateMigrationVerdict,
    evaluate_candidate_substrate,
    export_cognitive_state,
    get_substrate_manager,
    reconstruct_from_cold_boot,
)


class TestSubstrateManager:
    def test_export_cognitive_state(self):
        manager = SubstrateManager(current_substrate_id="claude-sonnet-5-5")
        const = {"version": "1.0", "invariants": ["SAFE"]}
        beliefs = [{"subject": "user", "predicate": "name", "object": "Sonu"}]
        skills = [{"skill_id": "file_reader"}]
        goals = [{"goal_id": "explore"}]

        state = manager.export_cognitive_state(const, beliefs, skills, goals)
        assert state["schema_version"] == "8.0.0"
        assert state["substrate_id"] == "claude-sonnet-5-5"
        assert len(state["beliefs"]) == 1
        assert len(state["skills"]) == 1

    def test_evaluate_candidate_substrate_approval(self):
        manager = SubstrateManager(current_substrate_id="claude-sonnet-5-5")

        verdict = manager.evaluate_candidate_substrate(
            candidate_id="claude-opus-5-5",
            gym_win_rate=0.92,
            probe_max_jsd=0.08,
            safety_audit_passed=True,
        )

        assert verdict.is_approved is True
        assert len(verdict.safety_violations) == 0
        assert manager.current_substrate_id == "claude-opus-5-5"

    def test_evaluate_candidate_substrate_rejection(self):
        manager = SubstrateManager(current_substrate_id="claude-sonnet-5-5")

        verdict = manager.evaluate_candidate_substrate(
            candidate_id="untested-experimental-v1",
            gym_win_rate=0.50,  # Below 0.80
            probe_max_jsd=0.25,  # Above 0.15
            safety_audit_passed=False,
        )

        assert verdict.is_approved is False
        assert len(verdict.safety_violations) == 3
        # Should NOT promote
        assert manager.current_substrate_id == "claude-sonnet-5-5"

    def test_cold_boot_reconstruction(self):
        manager = SubstrateManager()
        const_doc = {"version": "1.0", "invariants": ["SAFETY_INVARIANT"], "signature_hex": "abcd"}
        autobiography = [{"commit": "init", "timestamp": 123456}]
        beliefs = [{"s": "a", "p": "b", "o": "c"}]
        skills = [{"name": "compile"}]

        report = manager.reconstruct_from_cold_boot(
            constitution_doc=const_doc,
            autobiography_records=autobiography,
            belief_triples=beliefs,
            skill_library=skills,
        )

        assert report.success is True
        assert report.constitution_verified is True
        assert report.beliefs_restored_count == 1
        assert report.skills_restored_count == 1
        assert report.autobiography_entries == 1
