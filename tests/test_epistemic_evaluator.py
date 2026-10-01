"""Test suite for epistemic_evaluator.py uncertainty calibration engine."""
import pytest
from unittest.mock import patch, MagicMock

import epistemic_evaluator


class TestEpistemicEvaluator:
    """Test cases for epistemic uncertainty evaluation and verbal calibration."""

    def test_calculate_semantic_entropy_identical(self):
        """Test entropy calculation with identical sample outputs."""
        samples = [
            "The system is running nominally on port 8000.",
            "The system is running nominally on port 8000.",
            "The system is running nominally on port 8000.",
        ]
        entropy, variance = epistemic_evaluator.calculate_semantic_entropy(samples)
        assert variance == 0.0
        assert entropy < 0.25

    def test_calculate_semantic_entropy_divergent(self):
        """Test entropy calculation with completely conflicting/divergent samples."""
        samples = [
            "Delete all user profiles and clean disk database immediately.",
            "Start web server on port 8080 with SSL encryption enabled.",
            "Play classical Beethoven piano symphony music through audio speaker.",
        ]
        entropy, variance = epistemic_evaluator.calculate_semantic_entropy(samples)
        assert variance >= 0.7
        assert entropy >= 0.45

    def test_evaluate_uncertainty_low_entropy(self):
        """Test uncertainty evaluation with consistent high confidence."""
        samples = [
            "Battery level is 85 percent and charging.",
            "Battery state is 85 percent charging.",
        ]
        eval_res = epistemic_evaluator.evaluate_uncertainty("Battery query", samples=samples, threshold=0.5)
        assert eval_res.is_uncertain is False
        assert eval_res.confidence > 0.5
        assert eval_res.hedge_phrase is None

    def test_evaluate_uncertainty_high_entropy_triggers_hedge(self):
        """Test that high entropy triggers verbal self-correction hedge."""
        samples = [
            "Option Alpha: Execute nuclear wipe on cache.",
            "Option Beta: Download cloud backup from remote mirror.",
            "Option Gamma: Ignore signal and resume background music.",
        ]
        eval_res = epistemic_evaluator.evaluate_uncertainty("Complex recovery", samples=samples, threshold=0.35)
        assert eval_res.is_uncertain is True
        assert eval_res.confidence < 0.65
        assert eval_res.hedge_phrase is not None
        assert "low certainty" in eval_res.hedge_phrase.lower() or "epistemic" in eval_res.hedge_phrase.lower()

    def test_calibrate_and_guard_uncertain(self):
        """Test calibrate_and_guard prefixes hedge phrase when uncertain."""
        samples = [
            "Execute procedure Alpha with root credentials.",
            "Abort mission and shutdown hardware immediately.",
        ]
        guarded, eval_res = epistemic_evaluator.calibrate_and_guard(
            "Execute procedure",
            "I will proceed with the routine.",
            threshold=0.3,
            samples=samples
        )
        assert eval_res.is_uncertain is True
        assert guarded.startswith("I have low certainty") or "epistemic" in guarded.lower()
        assert "I will proceed with the routine." in guarded

    def test_calibrate_and_guard_certain(self):
        """Test calibrate_and_guard leaves response unchanged when certain."""
        samples = [
            "The file exists in directory.",
            "The file exists in directory.",
        ]
        guarded, eval_res = epistemic_evaluator.calibrate_and_guard(
            "Check file",
            "The file is present on disk.",
            threshold=0.5,
            samples=samples
        )
        assert eval_res.is_uncertain is False
        assert guarded == "The file is present on disk."

    def test_evaluate_uncertainty_empty_prompt(self):
        """Test evaluation handles empty prompt cleanly."""
        eval_res = epistemic_evaluator.evaluate_uncertainty("")
        assert eval_res.is_uncertain is False
        assert eval_res.confidence == 1.0

    @patch('epistemic_evaluator.brain.ask_llm')
    def test_sample_llm_variations(self, mock_brain):
        """Test sampling variations using brain LLM."""
        mock_brain.side_effect = [
            "Response version 1",
            "Response version 2",
            "Response version 3",
        ]
        samples = epistemic_evaluator.sample_llm_variations("Test prompt", n_samples=3)
        assert len(samples) == 3
        assert samples[0] == "Response version 1"

    def test_uncertainty_evaluation_to_dict(self):
        """Test UncertaintyEvaluation dictionary serialization."""
        eval_res = epistemic_evaluator.UncertaintyEvaluation(
            prompt="Test intent",
            samples=["S1", "S2"],
            entropy=0.35,
            variance=0.30,
            is_uncertain=False,
            confidence=0.65,
            hedge_phrase=None,
            consensus_response="S1"
        )
        data = eval_res.to_dict()
        assert data["prompt"] == "Test intent"
        assert data["samples_count"] == 2
        assert data["entropy"] == 0.35
        assert data["confidence"] == 0.65
        assert data["is_uncertain"] is False
