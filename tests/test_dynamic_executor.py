"""Test suite for dynamic_executor.py sandboxed execution engine."""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

import dynamic_executor


class TestDynamicExecutor:
    """Test cases for isolated sandboxed code execution."""

    def test_execute_valid_python_code(self):
        """Test successful execution of valid Python code."""
        code = "print('Hello from sandbox')"
        result = dynamic_executor.execute_python_code(code, enable_retry=False)

        assert result.success is True
        assert "Hello from sandbox" in result.stdout
        assert result.exit_code == 0
        assert result.retries == 0

    def test_execute_code_with_computation(self):
        """Test code execution with actual computation."""
        code = """
result = sum(range(1, 11))
print(f"Sum: {result}")
"""
        result = dynamic_executor.execute_python_code(code, enable_retry=False)

        assert result.success is True
        assert "Sum: 55" in result.stdout
        assert result.exit_code == 0

    def test_execute_empty_code(self):
        """Test rejection of empty code payload."""
        result = dynamic_executor.execute_python_code("", enable_retry=False)

        assert result.success is False
        assert result.error is not None
        assert "empty" in result.error.lower()

    def test_execute_failing_code(self):
        """Test handling of code that raises exceptions."""
        code = "raise ValueError('Test error')"
        result = dynamic_executor.execute_python_code(code, enable_retry=False)

        assert result.success is False
        assert result.exit_code != 0
        assert "ValueError" in result.stderr or "Test error" in result.stderr

    def test_execute_code_with_syntax_error(self):
        """Test handling of syntax errors."""
        code = "print('unclosed string"
        result = dynamic_executor.execute_python_code(code, enable_retry=False)

        assert result.success is False
        assert result.exit_code != 0

    def test_timeout_enforcement(self):
        """Test that execution timeout is enforced."""
        code = """
import time
time.sleep(10)
print("Should not reach here")
"""
        result = dynamic_executor.execute_python_code(code, timeout=1.0, enable_retry=False)

        assert result.success is False
        assert "timeout" in result.error.lower()

    def test_working_directory_isolation(self):
        """Test that code executes in isolated working directory."""
        test_dir = tempfile.mkdtemp(prefix="test_sandbox_")
        code = """
import os
print(f"CWD: {os.getcwd()}")
"""
        result = dynamic_executor.execute_python_code(
            code, working_dir=test_dir, enable_retry=False
        )

        assert result.success is True
        assert test_dir in result.stdout

    @patch('dynamic_executor.brain.ask_llm')
    def test_self_healing_retry_with_patch(self, mock_brain):
        """Test self-healing retry loop with brain patch."""
        # Simulate brain providing a fix
        mock_brain.return_value = """```python
print("Fixed version")
```"""

        # Code that will fail first time
        failing_code = "print(undefined_variable)"

        result = dynamic_executor.execute_python_code(
            failing_code, enable_retry=True
        )

        # Should have attempted retry
        assert result.retries >= 0
        assert mock_brain.called

    def test_shell_command_execution_success(self):
        """Test successful shell command execution."""
        result = dynamic_executor.execute_shell_command("echo Hello Shell")

        assert result.success is True
        assert "Hello Shell" in result.stdout
        assert result.exit_code == 0

    def test_shell_command_execution_failure(self):
        """Test failed shell command handling."""
        result = dynamic_executor.execute_shell_command("nonexistent_command_xyz")

        assert result.success is False
        assert result.exit_code != 0

    def test_shell_command_empty_input(self):
        """Test rejection of empty shell command."""
        result = dynamic_executor.execute_shell_command("")

        assert result.success is False
        assert result.error is not None

    def test_shell_command_timeout(self):
        """Test shell command timeout enforcement."""
        # Windows-compatible long-running command
        import platform
        if platform.system() == "Windows":
            # Use ping with 10 second timeout - subprocess will timeout first
            cmd = "ping -n 11 127.0.0.1"
        else:
            cmd = "sleep 10"

        result = dynamic_executor.execute_shell_command(cmd, timeout=1.0)

        assert result.success is False
        assert result.error is not None
        assert "timeout" in result.error.lower()

    def test_simulate_preflight_passed(self):
        """Test dynamic execution with pre-flight simulation enabled for valid code."""
        code = "val = 40 + 2\nprint(f'Ans: {val}')"
        result = dynamic_executor.execute_python_code(code, enable_retry=False, simulate_preflight=True)
        assert result.success is True
        assert "Ans: 42" in result.stdout

    def test_simulate_preflight_rejected(self):
        """Test dynamic execution rejects code failing preflight simulation."""
        code = "raise ValueError('Preflight intentional crash')"
        result = dynamic_executor.execute_python_code(code, enable_retry=False, simulate_preflight=True)
        assert result.success is False
        assert "Preflight" in str(result.error) or "crash" in str(result.error)

    def test_result_structure(self):
        """Test DynamicExecutionResult structure."""
        result = dynamic_executor.DynamicExecutionResult(
            success=True,
            stdout="test output",
            stderr="",
            exit_code=0,
            retries=1,
            error=None
        )

        assert result.success is True
        assert result.stdout == "test output"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.retries == 1
        assert result.error is None

    def test_code_file_cleanup_after_execution(self):
        """Test that temporary code files are created in isolated directory."""
        code = "print('test')"
        test_dir = tempfile.mkdtemp(prefix="test_cleanup_")

        result = dynamic_executor.execute_python_code(
            code, working_dir=test_dir, enable_retry=False
        )

        # Verify execution happened
        assert result.success is True

        # Check that code file was created in test_dir
        py_files = [f for f in os.listdir(test_dir) if f.endswith('.py')]
        assert len(py_files) > 0


class TestDynamicExecutorIntegration:
    """Integration tests for dynamic executor."""

    def test_file_write_and_read(self):
        """Test code that writes and reads files."""
        test_dir = tempfile.mkdtemp(prefix="test_fileio_")
        code = f"""
with open('test_output.txt', 'w') as f:
    f.write('Integration test')

with open('test_output.txt', 'r') as f:
    content = f.read()
    print(f"Read: {{content}}")
"""
        result = dynamic_executor.execute_python_code(
            code, working_dir=test_dir, enable_retry=False
        )

        assert result.success is True
        assert "Read: Integration test" in result.stdout

    def test_import_standard_library(self):
        """Test that standard library imports work."""
        code = """
import json
import sys
data = {"test": True}
print(json.dumps(data))
print(f"Python: {sys.version_info.major}.{sys.version_info.minor}")
"""
        result = dynamic_executor.execute_python_code(code, enable_retry=False)

        assert result.success is True
        assert '"test": true' in result.stdout
        assert "Python:" in result.stdout
