"""Tests for Workstream 1: Emergent Dynamics Safeguards & Lifelong Compaction."""
import time
import pytest
from emergent_safeguards import (
    CorrelatedLearnersAuditor,
    CryingWolfTracker,
    GovernorConflictResolver,
    RolloutAndKillSwitchManager,
    SubstrateVersionTracker,
    SurprisePoisoningGuard,
    SycophancyDissentTracker,
    CapabilityProbingDetector,
)
from lifelong_compactor import (
    GoldenProbe,
    GoldenProbeRotator,
    LifelongGrowthCompactor,
)


class TestEmergentSafeguards:
    # 1. Governor Conflict Resolver
    def test_governor_conflict_resolver_cooldown_and_hysteresis(self):
        resolver = GovernorConflictResolver(cooldown_seconds=120.0, hysteresis_margin=1.5)
        t_base = 1000.0

        # Step 1: Demotion occurs at t_base (always allowed)
        ok, tier, _ = resolver.request_tier_change("ACT_THEN_REPORT", "SUGGEST", is_promotion=False, root_cause="CUSUM drift", now=t_base)
        assert ok is True
        assert tier == "SUGGEST"

        # Step 2: Immediate promotion request at t_base + 30s is blocked by 120s cooldown
        ok_p, tier_p, msg = resolver.request_tier_change("SUGGEST", "ACT_THEN_REPORT", is_promotion=True, root_cause="Trust recovery", now=t_base + 30.0)
        assert ok_p is False
        assert "cooldown" in msg.lower()
        assert tier_p == "SUGGEST"

        # Step 3: At t_base + 130s, simulate excessive self-initiated velocity
        resolver.record_activity(count=10, is_user_requested=False)  # self-initiated = 10
        resolver.record_activity(count=2, is_user_requested=True)    # user = 2 -> ratio = 5.0 > 1.5
        ok_h, tier_h, msg_h = resolver.request_tier_change("SUGGEST", "ACT_THEN_REPORT", is_promotion=True, root_cause="Trust recovery", now=t_base + 130.0)
        assert ok_h is False
        assert "hysteresis" in msg_h.lower()

    # 2. Correlated Learners Auditor
    def test_correlated_learners_auditor_decompilation_trigger(self):
        auditor = CorrelatedLearnersAuditor(safety_floor=0.80)

        # Held-out dataset where habit accuracy is 60% (fails safety floor)
        held_out_samples = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]  # 5/10 = 50%
        report = auditor.audit_habit_rule("habit_search_v1", held_out_samples)

        assert report.decompiled is True
        assert report.held_out_accuracy == 0.50
        assert report.effective_sample_size > 0
        assert auditor.get_decompilation_rate() == 1.0

        # High-performing habit: 95% accuracy
        good_samples = [1] * 19 + [0]
        report_good = auditor.audit_habit_rule("habit_compile_v2", good_samples)
        assert report_good.decompiled is False
        assert report_good.held_out_accuracy == 0.95

    # 3. Surprise Poisoning Guard
    def test_surprise_poisoning_guard_enforcements(self):
        guard = SurprisePoisoningGuard()

        # Hard cap on lambda
        assert guard.clamp_knn_lambda(0.50) == 0.25
        assert guard.clamp_knn_lambda(0.15) == 0.15
        assert guard.clamp_knn_lambda(-0.1) == 0.0

        # Taint tag rejection
        ok_taint, msg = guard.verify_exemplar_safety({"action": "save"}, taint_tags=["quarantined"])
        assert ok_taint is False
        assert "quarantined" in msg

        # Symbolic check failure
        ok_sym, msg_sym = guard.verify_exemplar_safety({"action": "save"}, symbolic_check_passed=False)
        assert ok_sym is False

        # Shell injection attempt
        ok_inj, msg_inj = guard.verify_exemplar_safety({"code": "__import__('os').system('rm -rf /')"})
        assert ok_inj is False
        assert "dangerous" in msg_inj.lower()

    # 4. Sycophancy & Dissent Floor Tracker
    def test_sycophancy_tracker_trips_on_severe_suppression(self):
        tracker = SycophancyDissentTracker(high_severity_threshold=0.70, required_dissent_floor=0.90)

        # Severe bug (severity = 0.85) but system did NOT dissent -> Sycophancy Alert!
        res_fail = tracker.evaluate_dissent(severity=0.85, dissented=False)
        assert res_fail.sycophancy_alert is True
        assert res_fail.is_safe is False

        # Severe bug where system properly dissents -> Safe
        res_ok = tracker.evaluate_dissent(severity=0.90, dissented=True)
        assert res_ok.severity == 0.90

    # 5. Crying Wolf Precision / Recall Tracker
    def test_crying_wolf_tracker_calibration(self):
        tracker = CryingWolfTracker(initial_dissent_threshold=0.40)

        # Record 5 false alarms (dissented, but was not actual defect)
        for _ in range(5):
            tracker.record_outcome(dissented=True, was_actual_defect=False)

        assert tracker.get_precision() == 0.0
        # Should have raised the threshold to reduce false alarms
        assert tracker.dissent_threshold > 0.40

    # 6. Capability Probing Detector
    def test_capability_probing_detector_rate_limits(self):
        detector = CapabilityProbingDetector(window_sec=300.0, max_denials=3)
        t_base = 1000.0

        # 1st denial
        is_rl, is_alert, _ = detector.record_denial("goal_1", now=t_base)
        assert is_rl is False
        assert is_alert is False

        # 2nd denial
        is_rl, is_alert, _ = detector.record_denial("goal_2", now=t_base + 10.0)
        assert is_rl is False

        # 3rd denial within 300s -> Trips security probe alert and rate-limits AN!
        is_rl, is_alert, msg = detector.record_denial("goal_3", now=t_base + 50.0)
        assert is_rl is True
        assert is_alert is True
        assert detector.is_rate_limited() is True
        assert "PROBE ALERT" in msg

    # 7. Substrate Version Tracker
    def test_substrate_version_tracker_purges_mismatched_artifacts(self):
        tracker = SubstrateVersionTracker(current_substrate_hash="claude-sonnet-5-5-base")
        tracker.register_artifact("lora_rank4_v1", "lora_adapter", substrate_hash="claude-sonnet-5-5-base")
        tracker.register_artifact("habit_fast_v1", "habit_rule", substrate_hash="claude-sonnet-5-5-base")
        tracker.register_artifact("legacy_v0", "habit_rule", substrate_hash="claude-old-model-v0")

        # Swap to Claude Opus 5.5
        purged = tracker.on_substrate_swap("claude-opus-5-5-base")
        assert "lora_rank4_v1" in purged
        assert "habit_fast_v1" in purged
        assert "legacy_v0" in purged
        assert len(tracker._artifact_registry) == 0

    # 8. Golden Probe Rotator
    def test_golden_probe_rotator_partition_and_rotation(self):
        probes = [GoldenProbe(f"p_{i}", f"prompt {i}", f"base {i}") for i in range(10)]
        rotator = GoldenProbeRotator(probes, active_ratio=0.60)

        active = rotator.get_active_probes()
        held_out = rotator.get_held_out_probes()
        assert len(active) == 6
        assert len(held_out) == 4

        # Rotate partitions
        rotator.rotate_partitions(rng_seed=42)
        assert len(rotator.get_active_probes()) == 6
        assert len(rotator.get_held_out_probes()) == 4

    # 9. Lifelong Compactor Accelerated Aging
    def test_lifelong_compactor_accelerated_aging(self):
        compactor = LifelongGrowthCompactor(retention_hours=720.0)  # 30 days
        # Simulate 6 months of operation
        report = compactor.simulate_accelerated_aging(total_months=6, events_per_day=50)

        assert report.total_simulated_days == 180
        assert report.total_events_generated == 180 * 50
        assert report.events_compacted_into_anchors > 0
        assert report.total_anchors_created > 0
        assert report.compression_ratio > 1.0

    # 10. Rollout & Kill Switches
    def test_rollout_and_kill_switches(self):
        manager = RolloutAndKillSwitchManager()
        assert manager.is_module_active("AQ") is True
        assert manager.is_observe_only("AQ") is False

        # Set observe-only
        manager.set_observe_only("AQ", enable=True)
        assert manager.is_observe_only("AQ") is True

        # Kill switch
        manager.toggle_kill_switch("AQ", killed=True)
        assert manager.is_module_active("AQ") is False

        # Release kill switch
        manager.toggle_kill_switch("AQ", killed=False)
        assert manager.is_module_active("AQ") is True
