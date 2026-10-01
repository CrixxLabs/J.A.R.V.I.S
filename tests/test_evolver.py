"""Test suite for evolver.py and canary self-healing architecture."""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

import evolver


class TestEvolverLegacy:
    """Test cases for legacy analysis and proposal generation."""

    def test_analyze_empty_memory(self):
        """Test analysis with empty memory."""
        with patch('evolver._load_memory', return_value={}):
            findings = evolver.analyze()
            assert isinstance(findings, list)

    def test_analyze_high_failure_rate(self):
        """Test detection of high failure actions."""
        mock_memory = {
            "failure_log": {
                "broken_action": {"count": 4, "detail": "API error"}
            },
            "usage_freq": {
                "broken_action": {"count": 1}
            }
        }
        with patch('evolver._load_memory', return_value=mock_memory):
            findings = evolver.analyze()
            high_fails = [f for f in findings if f["type"] == "high_failure"]
            assert len(high_fails) == 1
            assert high_fails[0]["action"] == "broken_action"

    def test_generate_proposal_structure(self):
        """Test structure of generated proposal."""
        finding = {
            "type": "high_failure",
            "action": "test_action",
            "suggestion": "Fix the action"
        }
        proposal = evolver.generate_proposal(finding)

        assert proposal["finding"] == finding
        assert proposal["status"] == "pending_review"
        assert proposal["auto_apply"] is False
        assert "files_affected" in proposal

    def test_sandbox_test_valid_proposal(self):
        """Test sandbox validation for valid proposal."""
        proposal = {
            "generated_at": "2026-10-01",
            "finding": {"type": "high_failure"},
            "status": "pending_review",
            "auto_apply": False
        }
        assert evolver.sandbox_test(proposal) is True

    def test_sandbox_test_reject_auto_apply(self):
        """Test that sandbox rejects proposals with auto_apply=True."""
        proposal = {
            "generated_at": "2026-10-01",
            "finding": {"type": "high_failure"},
            "status": "pending_review",
            "auto_apply": True
        }
        assert evolver.sandbox_test(proposal) is False

    def test_backup_and_rollback(self):
        """Test file backup and rollback."""
        # Create a temp file
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("original_code = True\n")

        try:
            # Backup
            backup_path = evolver._backup_file(path)
            assert backup_path is not None
            assert os.path.exists(backup_path)

            # Modify file
            with open(path, "w") as f:
                f.write("modified_code = True\n")

            # Rollback
            restored = evolver.rollback_last_patch(path)
            assert restored is True

            # Verify original content restored
            with open(path, "r") as f:
                content = f.read()
            assert "original_code = True" in content

        finally:
            if os.path.exists(path):
                os.remove(path)


class TestCanarySelfHealing:
    """Test cases for Phase 4 Canary Self-Healing Pipeline."""

    @patch('evolver.brain.ask_llm')
    def test_generate_ai_patch_markdown(self, mock_brain):
        """Test AI patch extraction from markdown code block."""
        mock_brain.return_value = """Here is the fix:
```python
def fixed_function():
    return 42
```"""

        patch_code = evolver.generate_ai_patch(
            "def broken_function(): pass",
            "NameError",
            "Traceback line 1"
        )

        assert patch_code is not None
        assert "def fixed_function():" in patch_code

    @patch('evolver.brain.ask_llm')
    def test_generate_ai_patch_raw_code(self, mock_brain):
        """Test AI patch extraction with raw code (no markdown)."""
        mock_brain.return_value = "def fixed_function():\n    return 42\n"

        patch_code = evolver.generate_ai_patch(
            "def broken_function(): pass",
            "NameError"
        )

        assert patch_code is not None
        assert "def fixed_function():" in patch_code

    @patch('evolver.brain.ask_llm')
    def test_generate_ai_patch_empty_response(self, mock_brain):
        """Test handling of empty LLM response."""
        mock_brain.return_value = ""

        patch_code = evolver.generate_ai_patch("code", "error")
        assert patch_code is None

    @patch('subprocess.run')
    def test_run_canary_tests_success(self, mock_run):
        """Test canary test run when all tests pass."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "297 passed"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        passed, output = evolver.run_canary_tests()
        assert passed is True
        assert "297 passed" in output

    @patch('subprocess.run')
    def test_run_canary_tests_failure(self, mock_run):
        """Test canary test run when tests fail."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "1 failed, 296 passed"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        passed, output = evolver.run_canary_tests()
        assert passed is False
        assert "1 failed" in output

    @patch('evolver.run_canary_tests')
    @patch('evolver.generate_ai_patch')
    @patch('subprocess.run')
    def test_canary_self_heal_success_promotion(
        self, mock_subproc, mock_patch, mock_test
    ):
        """Test canary self-heal pipeline when tests pass (promotes and merges)."""
        # Mock git branch query
        branch_mock = MagicMock()
        branch_mock.stdout = "feature/test\n"
        mock_subproc.return_value = branch_mock

        # Mock AI patch
        mock_patch.return_value = "def healed_code(): return True\n"

        # Mock test success
        mock_test.return_value = (True, "All 297 tests passed")

        # Create a temp file to heal
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("def broken(): pass\n")

        try:
            result = evolver.canary_self_heal(path, "Test error", "Traceback...")

            assert result["success"] is True
            assert result["stage"] == "promoted"
            assert "canary_branch" in result

        finally:
            if os.path.exists(path):
                os.remove(path)

    @patch('evolver.run_canary_tests')
    @patch('evolver.generate_ai_patch')
    @patch('subprocess.run')
    def test_canary_self_heal_failure_rollback(
        self, mock_subproc, mock_patch, mock_test
    ):
        """Test canary self-heal pipeline when tests fail (aborts and rolls back)."""
        # Mock git branch query
        branch_mock = MagicMock()
        branch_mock.stdout = "feature/test\n"
        mock_subproc.return_value = branch_mock

        # Mock AI patch
        mock_patch.return_value = "def bad_patch(): raise Exception\n"

        # Mock test failure
        mock_test.return_value = (False, "1 failed on canary")

        # Create a temp file to heal
        fd, path = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("def original(): pass\n")

        try:
            result = evolver.canary_self_heal(path, "Test error", "Traceback...")

            assert result["success"] is False
            assert result["stage"] == "rejected_rollback"
            assert "Test verification failed" in result["error"]

        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_canary_self_heal_nonexistent_file(self):
        """Test canary self-heal rejects nonexistent target files."""
        result = evolver.canary_self_heal("nonexistent_fake_file.py", "Error")
        assert result["success"] is False
        assert result["stage"] == "file_lookup"
