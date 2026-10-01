"""Test suite for preflight_simulator.py counterfactual execution sandbox."""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

import preflight_simulator


class TestPreflightSimulator:
    """Test cases for counterfactual pre-flight simulation engine."""

    def test_create_ephemeral_workspace(self):
        """Test ephemeral workspace creation and cleanup."""
        ws = preflight_simulator.create_ephemeral_workspace()
        try:
            assert os.path.exists(ws)
            assert os.path.isdir(ws)
            assert "jarvis_preflight_" in os.path.basename(ws)
        finally:
            if os.path.exists(ws):
                os.rmdir(ws)

    def test_detect_referenced_files(self):
        """Test detection of referenced files in source code."""
        # Create a temp file
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            code = f"""
with open(r"{path}", "r") as f:
    data = f.read()
"""
            detected = preflight_simulator.detect_referenced_files(code)
            assert any(os.path.normcase(path) == os.path.normcase(p) for p in detected)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_shadow_copy_targets(self):
        """Test shadow-copying files into ephemeral sandbox."""
        ws = preflight_simulator.create_ephemeral_workspace()
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write('{"test": true}')

        try:
            mapping = preflight_simulator.shadow_copy_targets(ws, [path])
            assert len(mapping) == 1
            shadow_path = list(mapping.values())[0]
            assert os.path.exists(shadow_path)
            with open(shadow_path, "r") as f:
                assert '{"test": true}' in f.read()
        finally:
            if os.path.exists(path):
                os.remove(path)
            if os.path.exists(ws):
                import shutil
                shutil.rmtree(ws, ignore_errors=True)

    def test_simulate_python_execution_success(self):
        """Test dry-run simulation of benign Python code."""
        code = """
x = 10 * 5
print(f"Result: {x}")
"""
        result = preflight_simulator.simulate_python_execution(code)
        assert result.success is True
        assert result.exit_code == 0
        assert "Result: 50" in result.stdout
        assert result.duration > 0.0

    def test_simulate_python_execution_failure(self):
        """Test dry-run simulation captures failure without corrupting system."""
        code = "raise RuntimeError('Simulated crash')"
        result = preflight_simulator.simulate_python_execution(code)
        assert result.success is False
        assert result.exit_code != 0
        assert "RuntimeError" in result.stderr or "Simulated crash" in result.stderr

    def test_simulate_python_state_transitions(self):
        """Test dry-run simulation detects file creation in sandbox."""
        code = """
with open("test_output.txt", "w") as f:
    f.write("simulation data")
"""
        result = preflight_simulator.simulate_python_execution(code)
        assert result.success is True
        assert any("test_output.txt" in f for f in result.simulated_changes["files_created"])

    def test_simulate_python_empty_payload(self):
        """Test rejection of empty simulation payload."""
        result = preflight_simulator.simulate_python_execution("")
        assert result.success is False
        assert result.exit_code == -1
        assert "empty" in result.error.lower()

    def test_simulate_shell_execution(self):
        """Test dry-run simulation of shell command."""
        cmd = "echo 'Dry-run test'"
        result = preflight_simulator.simulate_shell_execution(cmd)
        assert result.success is True
        assert result.exit_code == 0
        assert "Dry-run test" in result.stdout

    def test_simulate_shell_empty_payload(self):
        """Test shell simulation with empty command."""
        result = preflight_simulator.simulate_shell_execution("")
        assert result.success is False
        assert "empty" in result.error.lower()

    def test_preflight_check_gatekeeper(self):
        """Test preflight_check gatekeeper function."""
        passed, res = preflight_simulator.preflight_check("print('Preflight passed')")
        assert passed is True
        assert res.success is True

        failed, res_fail = preflight_simulator.preflight_check("import non_existent_pkg_12345")
        assert failed is False
        assert res_fail.success is False

    def test_simulation_result_serialization(self):
        """Test SimulationResult dict serialization."""
        res = preflight_simulator.SimulationResult(
            success=True,
            exit_code=0,
            stdout="output",
            stderr="",
            simulated_changes={"files_created": ["out.txt"], "files_modified": [], "files_deleted": []},
            duration=0.1234,
            error=None
        )
        d = res.to_dict()
        assert d["success"] is True
        assert d["exit_code"] == 0
        assert d["duration"] == 0.1234
        assert d["simulated_changes"]["files_created"] == ["out.txt"]
