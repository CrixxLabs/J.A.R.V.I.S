"""Unit tests for Autonomous Curriculum Loop (Voyager Phase 2)."""
import time
from unittest.mock import MagicMock, patch
import pytest

import cognitive_graph
import curriculum_engine
from curriculum_engine import CurriculumEngine, BUILTIN_CURRICULUM_TASKS


@pytest.fixture
def engine():
    """Create a fresh CurriculumEngine for testing."""
    eng = CurriculumEngine(idle_threshold=10.0, max_cpu_percent=80.0)
    # Start as idle
    eng._last_user_activity = time.time() - 20.0
    return eng


def test_is_curriculum_eligible(engine):
    # Should be eligible since last user activity was 20s ago (> 10s threshold)
    eligible, reason = engine.is_curriculum_eligible()
    assert eligible is True

    # When user activity occurs, it should become ineligible
    engine.mark_user_activity()
    eligible, reason = engine.is_curriculum_eligible()
    assert eligible is False
    assert "Recent user activity" in reason


def test_guardrails_safety_filter(engine):
    # Safe proposals
    safe1, msg1 = engine.is_safe_proposal("Extract text from PDF file", {"path": "test.pdf"})
    assert safe1 is True
    assert msg1 == "Safe"

    safe2, msg2 = engine.is_safe_proposal("Count lines in source files", {"dir": "skills"})
    assert safe2 is True

    # Destructive proposals
    bad_commands = [
        "format C: and restart",
        "run rmdir /s /q C:\\Windows",
        "rm -rf /var/log",
        "del /f /s /q C:\\system32\\drivers",
        "run bootrec /fixmbr",
        "bcdedit /deletevalue",
        "reg delete HKLM\\Software",
        "diskpart clean",
        "shutdown /s /t 0",
        "reboot now",
    ]

    for bad in bad_commands:
        safe, reason = engine.is_safe_proposal(bad)
        assert safe is False, f"Expected '{bad}' to be flagged as unsafe"
        assert "Destructive pattern rejected" in reason


def test_probe_learning_gaps_and_propose(engine):
    gaps = engine.probe_learning_gaps()
    assert len(gaps) > 0

    proposal = engine.propose_curriculum_task()
    assert proposal is not None
    assert "name" in proposal
    assert "description" in proposal
    assert "sample_params" in proposal
    assert proposal["safety_score"] > 0.8


def test_single_experiment_per_idle_window_cap(engine):
    mock_synth = {
        "success": True,
        "status": "mounted",
        "message": "Skill synthesized successfully",
        "file_path": "skills/custom_test.py",
    }

    with patch("skill_synthesizer.synthesize_skill", return_value=mock_synth):
        with patch("cognitive_graph.add_triple") as mock_graph:
            # First experiment should run
            res1 = engine.run_curriculum_experiment()
            assert res1["success"] is True
            assert mock_graph.called

            # Second experiment without user activity should be ineligible
            res2 = engine.run_curriculum_experiment()
            assert res2["success"] is False
            assert res2["status"] == "ineligible"
            assert "Experiment cap reached" in res2["reason"]

            # User interacts -> resets window -> eligible again
            engine.mark_user_activity()
            engine._last_user_activity = time.time() - 20.0  # simulate becoming idle again
            res3 = engine.run_curriculum_experiment()
            assert res3["success"] is True


def test_safety_rejection_during_experiment_run(engine):
    unsafe_proposal = {
        "name": "wipe_system",
        "description": "rm -rf / --no-preserve-root",
        "sample_params": {},
    }

    res = engine.run_curriculum_experiment(proposal=unsafe_proposal)
    assert res["success"] is False
    assert res["status"] == "safety_violation"
    assert "Destructive pattern" in res["message"]
