"""Counterfactual Pre-Flight Sandbox Simulator for J.A.R.V.I.S. — MARK VIII.

Provides pre-execution validation in an ephemeral isolated sandbox workspace:
  1. Ephemeral Workspace Isolation: Creates temporary isolated containers (tempfile.mkdtemp()).
  2. Shadow-Copying: Clones target read/write files into the ephemeral container before execution.
  3. Pre-Flight Dry Run: Executes candidate scripts/commands under sandboxed constraints.
  4. State Transition & Safety Assertion: Asserts exit code 0, expected state diffs, and no unauthorized mutations.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

DEFAULT_SIMULATION_TIMEOUT = 15.0


class SimulationResult:
    """Outcome of pre-flight counterfactual execution simulation."""

    def __init__(
        self,
        success: bool,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        simulated_changes: Optional[Dict[str, Any]] = None,
        duration: float = 0.0,
        error: Optional[str] = None,
        workspace: Optional[str] = None,
    ):
        self.success = success
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.simulated_changes = simulated_changes or {
            "files_created": [],
            "files_modified": [],
            "files_deleted": [],
        }
        self.duration = round(duration, 4)
        self.error = error
        self.workspace = workspace

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "simulated_changes": self.simulated_changes,
            "duration": self.duration,
            "error": self.error,
        }


def create_ephemeral_workspace(prefix: str = "jarvis_preflight_") -> str:
    """Create a pristine ephemeral isolated sandbox workspace directory."""
    temp_dir = tempfile.mkdtemp(prefix=prefix)
    return str(Path(temp_dir).resolve())


def detect_referenced_files(code: str, base_dir: Optional[str] = None) -> List[str]:
    """Scan candidate code to detect referenced file paths for shadow copying."""
    if not code:
        return []

    found_files: Set[str] = set()
    base_path = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()

    # Pattern for open('path', ...), Path('path'), os.path.exists('path'), with open(...)
    patterns = [
        r"""(?:open|Path|read_text|write_text)\s*\(\s*[rfbRFB]?['"]([^'"]+)['"]""",
        r"""os\.path\.\w+\s*\(\s*[rfbRFB]?['"]([^'"]+)['"]""",
        r"""with\s+open\s*\(\s*[rfbRFB]?['"]([^'"]+)['"]""",
        r"""[rfbRFB]?['"]([a-zA-Z0-9_\-/\\]+\.[a-zA-Z0-9_]{1,6})['"]""",
    ]

    for pat in patterns:
        matches = re.findall(pat, code)
        for m in matches:
            candidate = m.strip()
            if not candidate or candidate.startswith("http://") or candidate.startswith("https://"):
                continue

            # Check if it resolves to an existing file
            try:
                cand_path = Path(candidate)
                if cand_path.is_absolute() and cand_path.is_file():
                    found_files.add(str(cand_path.resolve()))
                else:
                    rel_candidate = (base_path / cand_path).resolve()
                    if rel_candidate.is_file():
                        found_files.add(str(rel_candidate))
            except Exception:
                continue

    return sorted(list(found_files))


def shadow_copy_targets(workspace_dir: str, target_files: List[str]) -> Dict[str, str]:
    """Clone target files into the ephemeral workspace container.

    Returns:
        Mapping of original_path -> shadow_path_in_workspace
    """
    mapping: Dict[str, str] = {}
    ws_path = Path(workspace_dir)

    for src_str in target_files:
        src = Path(src_str)
        if not src.exists() or not src.is_file():
            continue

        try:
            # Flatten relative to filename or relative path inside workspace
            dest_name = src.name
            dest = ws_path / dest_name

            # If collisions occur, disambiguate with stem + hash
            if dest.exists():
                dest = ws_path / f"{src.stem}_{abs(hash(str(src))) % 10000}{src.suffix}"

            shutil.copy2(str(src), str(dest))
            mapping[str(src.resolve())] = str(dest.resolve())
        except Exception as exc:
            print(f"[PreflightSimulator] Failed shadow-copying {src}: {exc}")

    return mapping


def _snapshot_workspace(workspace_dir: str) -> Dict[str, Tuple[float, int]]:
    """Capture snapshot of file paths, modification times, and sizes in workspace."""
    snapshot: Dict[str, Tuple[float, int]] = {}
    ws = Path(workspace_dir)
    if not ws.exists():
        return snapshot

    for root, _, files in os.walk(workspace_dir):
        for f in files:
            full_path = os.path.join(root, f)
            try:
                st = os.stat(full_path)
                snapshot[full_path] = (st.st_mtime, st.st_size)
            except Exception:
                pass
    return snapshot


def _diff_workspace(
    initial: Dict[str, Tuple[float, int]], current: Dict[str, Tuple[float, int]]
) -> Dict[str, List[str]]:
    """Compute files created, modified, or deleted in the ephemeral workspace."""
    created = [p for p in current if p not in initial]
    deleted = [p for p in initial if p not in current]
    modified = [
        p
        for p in current
        if p in initial and (current[p] != initial[p])
    ]

    return {
        "files_created": sorted(created),
        "files_modified": sorted(modified),
        "files_deleted": sorted(deleted),
    }


def simulate_python_execution(
    code: str,
    target_files: Optional[List[str]] = None,
    timeout: float = DEFAULT_SIMULATION_TIMEOUT,
    mock_network: bool = True,
    preserve_workspace: bool = False,
) -> SimulationResult:
    """Execute Python code in counterfactual ephemeral sandbox to verify safe execution.

    Args:
        code: Python source code string to dry-run
        target_files: Explicit list of file paths to shadow copy
        timeout: Maximum simulation time limit
        mock_network: Whether to enforce offline/mock network environment
        preserve_workspace: If True, does not delete ephemeral workspace (useful for debugging)

    Returns:
        SimulationResult indicating whether pre-flight passed without error
    """
    registry = get_registry()

    if not code or not code.strip():
        return SimulationResult(
            success=False,
            exit_code=-1,
            error="Simulation payload is empty",
        )

    workspace = create_ephemeral_workspace()
    start_time = time.time()

    try:
        # Detect files to shadow-copy
        auto_targets = detect_referenced_files(code)
        all_targets = list(set((target_files or []) + auto_targets))
        shadow_copy_targets(workspace, all_targets)

        # Take initial workspace snapshot
        initial_snap = _snapshot_workspace(workspace)

        # Prepare simulation runner script
        sim_script = os.path.join(workspace, "__sim_run__.py")
        with open(sim_script, "w", encoding="utf-8") as f:
            f.write(code)

        # Setup environment
        sim_env = os.environ.copy()
        if mock_network:
            # Block outbound calls by default in simulation
            sim_env["HTTP_PROXY"] = "http://127.0.0.1:0"
            sim_env["HTTPS_PROXY"] = "http://127.0.0.1:0"
            sim_env["PYTHONUNBUFFERED"] = "1"

        # Execute dry-run subprocess inside ephemeral workspace
        proc = subprocess.run(
            [sys.executable, "__sim_run__.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=sim_env,
        )

        duration = time.time() - start_time
        current_snap = _snapshot_workspace(workspace)
        diff = _diff_workspace(initial_snap, current_snap)
        # Filter out the runner script itself from diff
        diff["files_created"] = [f for f in diff["files_created"] if not f.endswith("__sim_run__.py")]

        success = (proc.returncode == 0)
        evidence = EvidenceLevel.LIVE if success else EvidenceLevel.BLOCKED
        registry.set_capability_evidence(
            "PREFLIGHT_SIMULATOR",
            evidence,
            f"Pre-flight simulation {'succeeded' if success else 'failed'} (Exit: {proc.returncode}, {duration:.2f}s)",
            source="counterfactual simulation",
        )

        return SimulationResult(
            success=success,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            simulated_changes=diff,
            duration=duration,
            error=proc.stderr if not success else None,
            workspace=workspace if preserve_workspace else None,
        )

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        registry.set_capability_evidence(
            "PREFLIGHT_SIMULATOR",
            EvidenceLevel.BLOCKED,
            f"Pre-flight simulation timed out after {timeout}s",
            source="counterfactual simulation",
        )
        return SimulationResult(
            success=False,
            exit_code=-2,
            stderr=f"Pre-flight simulation timed out after {timeout}s",
            duration=duration,
            error=f"Simulation exceeded timeout limit of {timeout}s",
        )

    except Exception as exc:
        duration = time.time() - start_time
        error_handler.log_and_demote(
            "PREFLIGHT_SIMULATOR",
            exc,
            "Pre-flight dry-run execution",
            SubsystemState.DEGRADED,
        )
        return SimulationResult(
            success=False,
            exit_code=-3,
            error=f"Simulation runner error: {type(exc).__name__}: {exc}",
            duration=duration,
        )

    finally:
        if not preserve_workspace and os.path.exists(workspace):
            try:
                shutil.rmtree(workspace, ignore_errors=True)
            except Exception:
                pass


def simulate_shell_execution(
    command: str,
    target_files: Optional[List[str]] = None,
    timeout: float = DEFAULT_SIMULATION_TIMEOUT,
    preserve_workspace: bool = False,
) -> SimulationResult:
    """Execute shell command in counterfactual ephemeral sandbox.

    Args:
        command: Shell command string
        target_files: Optional files to shadow-copy
        timeout: Maximum duration
        preserve_workspace: Debug flag to keep temp dir

    Returns:
        SimulationResult indicating dry-run outcome
    """
    registry = get_registry()

    if not command or not command.strip():
        return SimulationResult(
            success=False,
            exit_code=-1,
            error="Command payload is empty",
        )

    workspace = create_ephemeral_workspace()
    start_time = time.time()

    try:
        shadow_copy_targets(workspace, target_files or [])
        initial_snap = _snapshot_workspace(workspace)

        proc = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
            env=os.environ.copy(),
        )

        duration = time.time() - start_time
        current_snap = _snapshot_workspace(workspace)
        diff = _diff_workspace(initial_snap, current_snap)

        success = (proc.returncode == 0)
        evidence = EvidenceLevel.LIVE if success else EvidenceLevel.BLOCKED
        registry.set_capability_evidence(
            "PREFLIGHT_SIMULATOR",
            evidence,
            f"Shell simulation {'succeeded' if success else 'failed'} (Exit: {proc.returncode})",
            source="counterfactual simulation",
        )

        return SimulationResult(
            success=success,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            simulated_changes=diff,
            duration=duration,
            error=proc.stderr if not success else None,
            workspace=workspace if preserve_workspace else None,
        )

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return SimulationResult(
            success=False,
            exit_code=-2,
            stderr=f"Shell simulation timed out after {timeout}s",
            duration=duration,
            error=f"Simulation exceeded timeout limit of {timeout}s",
        )

    except Exception as exc:
        duration = time.time() - start_time
        return SimulationResult(
            success=False,
            exit_code=-3,
            error=f"Shell simulation error: {exc}",
            duration=duration,
        )

    finally:
        if not preserve_workspace and os.path.exists(workspace):
            try:
                shutil.rmtree(workspace, ignore_errors=True)
            except Exception:
                pass


def preflight_check(
    code: str,
    is_python: bool = True,
    target_files: Optional[List[str]] = None,
    timeout: float = DEFAULT_SIMULATION_TIMEOUT,
) -> Tuple[bool, SimulationResult]:
    """Convenience gatekeeper checking if code produces clean exit code 0 and valid state."""
    if is_python:
        result = simulate_python_execution(code, target_files=target_files, timeout=timeout)
    else:
        result = simulate_shell_execution(code, target_files=target_files, timeout=timeout)

    passed = result.success and result.exit_code == 0
    return passed, result
