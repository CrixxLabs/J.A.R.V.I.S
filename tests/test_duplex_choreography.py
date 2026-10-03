"""Tests for Module AS: Zero-Latency Duplex Acoustic Choreography."""
import time
import pytest
from duplex_choreography import (
    AudioGateDecision,
    BargeInType,
    BargeInVerdict,
    DuplexChoreographer,
    TurnEndPrediction,
    classify_barge_in,
    evaluate_audio_gate,
    get_duplex_choreographer,
    predict_turn_end,
)


class TestDuplexChoreography:
    def test_turn_end_prediction_high_completion(self):
        choreographer = DuplexChoreographer()
        # High syntactic completion (1.0), falling pitch (-50 Hz/s -> -f0_fall=50, or normalized -0.8), high energy decay (0.9), long pause (800ms)
        pred = choreographer.predict_turn_end(
            syntactic_completeness=1.0,
            f0_fall=-0.8,
            energy_decay=0.9,
            pause_ms=800.0,
        )
        assert pred.p_end > 0.80
        assert pred.is_turn_end is True
        assert "syntactic_completeness" in pred.features

    def test_turn_end_prediction_mid_utterance(self):
        choreographer = DuplexChoreographer()
        # Low syntactic completion (0.2), rising pitch (f0_fall = +0.5), low energy decay (0.1), short pause (50ms)
        pred = choreographer.predict_turn_end(
            syntactic_completeness=0.2,
            f0_fall=0.5,
            energy_decay=0.1,
            pause_ms=50.0,
        )
        assert pred.p_end < 0.20
        assert pred.is_turn_end is False

    def test_audio_gate_decision_theory(self):
        choreographer = DuplexChoreographer(g_early=1.0, c_overlap=2.0)
        # When p_end = 0.80, expected_gain = 0.8 * 1.0 = 0.8, expected_cost = 0.2 * 2.0 = 0.4 -> should release
        decision_high = choreographer.evaluate_audio_gate(p_end=0.80)
        assert decision_high.should_release_audio is True
        assert decision_high.speculative_generation_triggered is True

        # When p_end = 0.40, expected_gain = 0.4, expected_cost = 0.6 * 2.0 = 1.2 -> should NOT release
        decision_low = choreographer.evaluate_audio_gate(p_end=0.40)
        assert decision_low.should_release_audio is False
        assert decision_low.speculative_generation_triggered is False

        # When p_end = 0.60, speculative generation triggers even if release doesn't (if threshold is 0.55)
        decision_mid = choreographer.evaluate_audio_gate(p_end=0.60)
        assert decision_mid.speculative_generation_triggered is True

    def test_ambient_backchannel_detection_and_rate_limiting(self):
        choreographer = DuplexChoreographer(backchannel_cooldown_sec=5.0)
        t_base = 1000.0

        # Non-terminal pause: 200ms pause, low p_end (0.25), non-falling pitch (f0_fall=0.1)
        phrase1 = choreographer.check_backchannel_opportunity(
            pause_ms=200.0,
            p_end=0.25,
            f0_fall=0.1,
            now=t_base,
        )
        assert phrase1 in ["right", "understood", "mm-hm", "I see", "go on"]

        # Immediate second opportunity at t_base + 1.0s should be rate-limited
        phrase2 = choreographer.check_backchannel_opportunity(
            pause_ms=220.0,
            p_end=0.20,
            f0_fall=0.2,
            now=t_base + 1.0,
        )
        assert phrase2 is None

        # After cooldown (t_base + 6.0s), should fire again
        phrase3 = choreographer.check_backchannel_opportunity(
            pause_ms=200.0,
            p_end=0.25,
            f0_fall=0.1,
            now=t_base + 6.0,
        )
        assert phrase3 is not None

    def test_barge_in_classification_user_backchannel(self):
        choreographer = DuplexChoreographer()
        # Short "yeah" should NOT interrupt playback
        verdict = choreographer.classify_barge_in(
            overlapping_audio_duration_ms=250.0,
            transcript="yeah",
            current_playback_timestamp=1.5,
        )
        assert verdict.barge_in_type == BargeInType.BACKCHANNEL
        assert verdict.should_yield_floor is False
        assert verdict.should_flush_audio is False

    def test_barge_in_classification_correction(self):
        choreographer = DuplexChoreographer()
        choreographer.set_active_utterance_context("We are deploying to production server A.")

        # User says "Wait, not production!" -> CORRECTION
        verdict = choreographer.classify_barge_in(
            overlapping_audio_duration_ms=800.0,
            transcript="Wait, not that!",
            current_playback_timestamp=2.8,
        )
        assert verdict.barge_in_type == BargeInType.CORRECTION
        assert verdict.should_yield_floor is True
        assert verdict.should_flush_audio is True
        assert verdict.truncate_timestamp == 2.8
        assert verdict.flagged_statement == "We are deploying to production server A."

    def test_barge_in_classification_standard_barge_in(self):
        choreographer = DuplexChoreographer()
        # Substantial utterance
        verdict = choreographer.classify_barge_in(
            overlapping_audio_duration_ms=1200.0,
            transcript="Check the database connection first",
            current_playback_timestamp=3.4,
        )
        assert verdict.barge_in_type == BargeInType.BARGE_IN
        assert verdict.should_yield_floor is True
        assert verdict.should_flush_audio is True
        assert verdict.truncate_timestamp == 3.4
