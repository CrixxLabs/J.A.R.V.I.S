"""Dynamic sandboxed REPL for tasks outside the 50 hardcoded executor actions.

Provides isolated execution with strict timeout controls, working directory isolation,
and self-healing retry loops with brain.py feedback integration.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import brain
import error_handler
import preflight_simulator
from status_registry import EvidenceLevel, SubsystemState, get_registry


DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 2


class DynamicExecutionResult:
    """Structured result container for dynamic execution attempts."""

    def __init__(self, success: bool, stdout: str = "", stderr: str = "",
                 exit_code: int = 0, retries: int = 0, error: Optional[str] = None):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.retries = retries
        self.error = error


def execute_python_code(code: str, timeout: float = DEFAULT_TIMEOUT,
                       working_dir: Optional[str] = None,
                       enable_retry: bool = True,
                       simulate_preflight: bool = False) -> DynamicExecutionResult:
    """Execute arbitrary Python code in an isolated subprocess sandbox.

    Args:
        code: Python source code to execute
        timeout: Maximum execution time in seconds (default 30s)
        working_dir: Isolated working directory (defaults to temp directory)
        enable_retry: Enable self-healing retry loop (default True)
        simulate_preflight: Perform counterfactual pre-flight simulation before committing

    Returns:
        DynamicExecutionResult with execution outcome and diagnostics
    """
    registry = get_registry()

    if not code or not code.strip():
        registry.set_capability_evidence(
            "DYNAMIC_EXECUTOR", EvidenceLevel.BROKEN,
            "Empty code payload", source="dynamic execution"
        )
        return DynamicExecutionResult(
            success=False, error="Code payload is empty or whitespace-only"
        )

    # Optional Pre-Flight Counterfactual Simulation gate
    if simulate_preflight:
        sim_passed, sim_res = preflight_simulator.preflight_check(code, is_python=True, timeout=min(timeout, 15.0))
        if not sim_passed:
            print(f"[DynamicExecutor] Pre-flight simulation gate rejected execution: {sim_res.error or sim_res.stderr}")
            if not enable_retry:
                return DynamicExecutionResult(
                    success=False,
                    stdout=sim_res.stdout,
                    stderr=sim_res.stderr or sim_res.error or "Simulation failed",
                    exit_code=sim_res.exit_code,
                    error=f"Preflight simulation check failed: {sim_res.error or sim_res.stderr}"
                )

    # Create isolated temporary working directory
    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="jarvis_sandbox_")
    else:
        working_dir = str(Path(working_dir).resolve())
        os.makedirs(working_dir, exist_ok=True)

    attempt = 0
    last_error = None
    current_code = code

    while attempt <= MAX_RETRIES:
        try:
            # Write code to temporary file
            code_file = os.path.join(working_dir, f"exec_{int(time.time())}_{attempt}.py")
            with open(code_file, "w", encoding="utf-8") as f:
                f.write(current_code)

            # Execute in isolated subprocess
            result = subprocess.run(
                ["python", code_file],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ.copy()
            )

            # Successful execution
            if result.returncode == 0:
                registry.set_capability_evidence(
                    "DYNAMIC_EXECUTOR", EvidenceLevel.LIVE,
                    f"Sandbox execution succeeded (attempt {attempt + 1})",
                    source="dynamic execution"
                )
                return DynamicExecutionResult(
                    success=True,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.returncode,
                    retries=attempt
                )

            # Non-zero exit code with stderr
            last_error = result.stderr or f"Exit code {result.returncode}"

            # Self-healing: ask brain.py for a patch
            if enable_retry and attempt < MAX_RETRIES:
                print(f"[DynamicExecutor] Execution failed (attempt {attempt + 1}), requesting patch...")
                patch_prompt = f"""The following Python code failed with exit code {result.returncode}:

```python
{current_code}
```

Error output:
{result.stderr[:500]}

Please provide a corrected version of the code that fixes this error. Return only the corrected Python code, no explanations."""

                try:
                    patched_code = brain.ask_llm(
                        patch_prompt,
                        model_type="chat",
                        allow_actions=False
                    )
                    if patched_code and patched_code.strip():
                        # Extract code from markdown blocks if present
                        if "```python" in patched_code:
                            patched_code = patched_code.split("```python")[1].split("```")[0].strip()
                        elif "```" in patched_code:
                            patched_code = patched_code.split("```")[1].split("```")[0].strip()

                        current_code = patched_code
                        attempt += 1
                        continue
                except Exception as brain_exc:
                    print(f"[DynamicExecutor] Brain patch request failed: {brain_exc}")
                    break

            break

        except subprocess.TimeoutExpired:
            last_error = f"Execution exceeded {timeout}s timeout"
            registry.set_capability_evidence(
                "DYNAMIC_EXECUTOR", EvidenceLevel.BLOCKED,
                last_error, source="dynamic execution"
            )
            break

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            error_handler.log_and_demote(
                "DYNAMIC_EXECUTOR", exc,
                "Sandboxed code execution",
                SubsystemState.DEGRADED
            )
            break

    # All attempts exhausted
    registry.set_capability_evidence(
        "DYNAMIC_EXECUTOR", EvidenceLevel.BROKEN,
        f"Execution failed after {attempt + 1} attempts: {last_error[:120]}",
        source="dynamic execution"
    )
    return DynamicExecutionResult(
        success=False,
        stdout="",
        stderr=last_error or "Unknown error",
        exit_code=-1,
        retries=attempt,
        error=last_error
    )


def execute_shell_command(command: str, timeout: float = DEFAULT_TIMEOUT,
                         working_dir: Optional[str] = None) -> DynamicExecutionResult:
    """Execute arbitrary shell command in isolated subprocess.

    Args:
        command: Shell command to execute
        timeout: Maximum execution time in seconds
        working_dir: Isolated working directory (defaults to temp directory)

    Returns:
        DynamicExecutionResult with execution outcome
    """
    registry = get_registry()

    if not command or not command.strip():
        return DynamicExecutionResult(
            success=False, error="Command is empty"
        )

    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="jarvis_sandbox_")
    else:
        working_dir = str(Path(working_dir).resolve())
        os.makedirs(working_dir, exist_ok=True)

    try:
        result = subprocess.run(
            command,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
            env=os.environ.copy()
        )

        success = result.returncode == 0
        if success:
            registry.set_capability_evidence(
                "DYNAMIC_EXECUTOR", EvidenceLevel.LIVE,
                "Shell command execution succeeded",
                source="dynamic execution"
            )
        else:
            registry.set_capability_evidence(
                "DYNAMIC_EXECUTOR", EvidenceLevel.BROKEN,
                f"Shell command failed with exit code {result.returncode}",
                source="dynamic execution"
            )

        return DynamicExecutionResult(
            success=success,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            retries=0
        )

    except subprocess.TimeoutExpired:
        error = f"Command exceeded {timeout}s timeout"
        registry.set_capability_evidence(
            "DYNAMIC_EXECUTOR", EvidenceLevel.BLOCKED,
            error, source="dynamic execution"
        )
        return DynamicExecutionResult(
            success=False, error=error, stderr=error, exit_code=-1
        )

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        error_handler.log_and_demote(
            "DYNAMIC_EXECUTOR", exc,
            "Shell command execution",
            SubsystemState.DEGRADED
        )
        return DynamicExecutionResult(
            success=False, error=error, exit_code=-1
        )
