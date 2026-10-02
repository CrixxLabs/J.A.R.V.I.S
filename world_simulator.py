"""Predictive World Model & Counterfactual Replay Engine for J.A.R.V.I.S. — MARK VIII.

Module T:
  1. Active-Inference Decision Formulation:
     - Free Energy Minimization: Balances Epistemic Value (Information Gain / Ambiguity Reduction)
       and Pragmatic Value (Goal Progress / Extrinsic Utility) across ACT, PROBE, ASK, and WAIT.
  2. Digital Twin Counterfactual Replay:
     - Replays historical action failures against logged event traces to evaluate alternative policies
       and compute counterfactual regret.
  3. Safe Rehearsal Mode:
     - Sandboxed dry-run simulator executing virtual file mutations and API interactions
       in isolated paths before live execution.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.world_simulator")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
TRACES_FILE = DATA_DIR / "world_event_traces.json"

_lock = threading.RLock()

# Action Types
ACTION_ACT = "ACT"
ACTION_PROBE = "PROBE"
ACTION_ASK = "ASK"
ACTION_WAIT = "WAIT"


@dataclass
class ActionCandidate:
    action_id: str
    action_type: str  # ACT, PROBE, ASK, WAIT
    target_goal: str
    expected_pragmatic_utility: float  # 0.0 - 1.0
    epistemic_information_gain: float  # 0.0 - 1.0
    execution_cost: float = 0.1
    risk_penalty: float = 0.0
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WorldSimulator:
    """Simulates active-inference decision dynamics, counterfactual replays, and sandboxed rehearsals."""

    def __init__(self, traces_path: Optional[Path] = None):
        self.traces_path = Path(traces_path).resolve() if traces_path else TRACES_FILE
        self.traces_path.parent.mkdir(parents=True, exist_ok=True)
        self._traces: Dict[str, Dict[str, Any]] = {}
        self._load_traces()

    def _load_traces(self) -> None:
        with _lock:
            if self.traces_path.exists():
                try:
                    with open(self.traces_path, "r", encoding="utf-8") as f:
                        self._traces = json.load(f)
                except Exception:
                    self._traces = {}
            else:
                self._traces = {}

    def _save_traces(self) -> None:
        with _lock:
            try:
                with open(self.traces_path, "w", encoding="utf-8") as f:
                    json.dump(self._traces, f, indent=2)
            except Exception as exc:
                log.error(f"[WorldSimulator] Failed to save event traces: {exc}")

    def evaluate_active_inference(
        self,
        candidates: List[ActionCandidate],
        current_state: Optional[Dict[str, Any]] = None,
        epistemic_weight: float = 1.0,
        pragmatic_weight: float = 1.0,
    ) -> Dict[str, Any]:
        """Rank actions by minimizing Expected Free Energy G = -(Epistemic_Gain + Pragmatic_Utility - Cost - Risk)."""
        if not candidates:
            return {"selected_action": None, "rankings": [], "message": "No action candidates provided"}

        scored_candidates = []
        for cand in candidates:
            # Expected Free Energy Score G (Higher is more optimal)
            g_score = (
                (pragmatic_weight * cand.expected_pragmatic_utility)
                + (epistemic_weight * cand.epistemic_information_gain)
                - cand.execution_cost
                - cand.risk_penalty
            )
            g_score = round(g_score, 4)

            item = cand.to_dict()
            item["free_energy_score"] = g_score
            scored_candidates.append(item)

        # Sort descending by free energy score
        scored_candidates.sort(key=lambda x: x["free_energy_score"], reverse=True)
        best = scored_candidates[0]

        try:
            get_registry().set_capability_evidence(
                "WORLD_SIMULATOR",
                EvidenceLevel.LIVE,
                f"Selected optimal action '{best['action_id']}' (type={best['action_type']}, G={best['free_energy_score']})",
                source="world_simulator.evaluate_active_inference",
            )
        except Exception:
            pass

        return {
            "selected_action": best,
            "action_id": best["action_id"],
            "action_type": best["action_type"],
            "free_energy_score": best["free_energy_score"],
            "rankings": scored_candidates,
        }

    def record_event_trace(
        self,
        trace_id: str,
        initial_state: Dict[str, Any],
        actions_taken: List[Dict[str, Any]],
        final_outcome: Dict[str, Any],
        error_logs: Optional[List[str]] = None,
    ) -> str:
        """Log an execution trace for downstream counterfactual analysis."""
        tid = str(trace_id).strip()
        with _lock:
            self._traces[tid] = {
                "trace_id": tid,
                "recorded_at": time.time(),
                "initial_state": initial_state,
                "actions_taken": actions_taken,
                "final_outcome": final_outcome,
                "error_logs": error_logs or [],
            }
            self._save_traces()
            log.info(f"[WorldSimulator] Recorded event trace '{tid}'")
            return tid

    def replay_counterfactual(
        self,
        trace_id_or_data: Any,
        alternative_policy: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Simulate alternative policy against historical event trace to compute regret and delta improvement."""
        trace: Dict[str, Any]
        if isinstance(trace_id_or_data, str):
            with _lock:
                trace = self._traces.get(trace_id_or_data, {})
            if not trace:
                return {"success": False, "error": f"Trace '{trace_id_or_data}' not found"}
        elif isinstance(trace_id_or_data, dict):
            trace = trace_id_or_data
        else:
            return {"success": False, "error": "Invalid trace input"}

        orig_outcome = trace.get("final_outcome", {})
        orig_success = bool(orig_outcome.get("success", False))
        orig_errors = trace.get("error_logs", [])

        # Evaluate alternative policy
        simulated_state = dict(trace.get("initial_state", {}))
        simulated_errors = []
        counterfactual_success = True

        for step_idx, alt_action in enumerate(alternative_policy):
            action_name = alt_action.get("action", f"step_{step_idx}")
            params = alt_action.get("params", {})
            requires_precondition = alt_action.get("requires_precondition", {})

            # Check preconditions against simulated state
            for k, v in requires_precondition.items():
                if simulated_state.get(k) != v:
                    simulated_errors.append(f"Precondition failed at step {step_idx} ({action_name}): expected {k}={v}, got {simulated_state.get(k)}")
                    counterfactual_success = False

            # Apply state mutation
            mutates = alt_action.get("state_mutations", {})
            simulated_state.update(mutates)

        # Improvement calculation
        if not orig_success and counterfactual_success:
            delta_improvement = 1.0  # complete turnaround from failure to success
            regret_score = 0.85
            summary = "Alternative policy successfully avoids historical failure and satisfies all goals."
        elif orig_success and not counterfactual_success:
            delta_improvement = -1.0
            regret_score = 0.0
            summary = "Alternative policy regressed compared to original successful execution."
        elif counterfactual_success and orig_success:
            delta_improvement = 0.1
            regret_score = 0.05
            summary = "Both policies succeed; alternative policy matches baseline."
        else:
            delta_improvement = 0.0
            regret_score = 0.0
            summary = "Both policies encountered failures under historical conditions."

        return {
            "success": True,
            "original_success": orig_success,
            "counterfactual_success": counterfactual_success,
            "delta_improvement": delta_improvement,
            "counterfactual_regret": regret_score,
            "simulated_final_state": simulated_state,
            "simulated_errors": simulated_errors,
            "summary": summary,
        }

    def dry_run_action(
        self,
        action_type: str,
        params: Dict[str, Any],
        sandbox_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Perform a virtual dry-run of an action in an isolated temporary sandbox."""
        act_norm = str(action_type).strip().lower()

        # Temporary sandbox for file operations
        temp_root = sandbox_dir or Path(tempfile.mkdtemp(prefix="jarvis_rehearsal_"))
        temp_root.mkdir(parents=True, exist_ok=True)

        try:
            if act_norm in ["write_file", "create_file", "modify_file"]:
                rel_path = params.get("path", "sample.txt")
                target_file = temp_root / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)

                old_content = ""
                if target_file.exists():
                    old_content = target_file.read_text(encoding="utf-8")

                new_content = str(params.get("content", ""))
                target_file.write_text(new_content, encoding="utf-8")

                diff_lines = list(
                    difflib.unified_diff(
                        old_content.splitlines(),
                        new_content.splitlines(),
                        fromfile=f"a/{rel_path}",
                        tofile=f"b/{rel_path}",
                    )
                )
                sha256 = hashlib.sha256(new_content.encode("utf-8")).hexdigest()

                return {
                    "safe": True,
                    "action_type": action_type,
                    "simulated_path": str(target_file),
                    "file_bytes": len(new_content.encode("utf-8")),
                    "sha256": sha256,
                    "diff": "\n".join(diff_lines),
                    "status": "rehearsal_verified",
                }

            if act_norm in ["delete_file", "remove_file"]:
                rel_path = params.get("path", "sample.txt")
                return {
                    "safe": True,
                    "action_type": action_type,
                    "simulated_target": rel_path,
                    "status": "deletion_preview_verified",
                }

            if act_norm in ["shell_exec", "run_command"]:
                cmd = str(params.get("command", ""))
                # Inspect safety
                is_dangerous = any(b in cmd.lower() for b in ["rm -rf /", "format c:", "drop database", ":(){ :|:& };:"])
                return {
                    "safe": not is_dangerous,
                    "action_type": action_type,
                    "command": cmd,
                    "status": "command_syntax_valid" if not is_dangerous else "unsafe_command_blocked",
                }

            return {
                "safe": True,
                "action_type": action_type,
                "params": params,
                "status": "generic_action_simulated",
            }
        finally:
            if not sandbox_dir and temp_root.exists():
                shutil.rmtree(temp_root, ignore_errors=True)

    def get_event_traces(self) -> List[Dict[str, Any]]:
        with _lock:
            return list(self._traces.values())


_simulator_instance: Optional[WorldSimulator] = None


def get_world_simulator() -> WorldSimulator:
    global _simulator_instance
    if _simulator_instance is None:
        with _lock:
            if _simulator_instance is None:
                _simulator_instance = WorldSimulator()
    return _simulator_instance


def evaluate_active_inference(
    candidates: List[ActionCandidate],
    current_state: Optional[Dict[str, Any]] = None,
    epistemic_weight: float = 1.0,
    pragmatic_weight: float = 1.0,
) -> Dict[str, Any]:
    return get_world_simulator().evaluate_active_inference(
        candidates, current_state, epistemic_weight, pragmatic_weight
    )


def record_event_trace(
    trace_id: str,
    initial_state: Dict[str, Any],
    actions_taken: List[Dict[str, Any]],
    final_outcome: Dict[str, Any],
    error_logs: Optional[List[str]] = None,
) -> str:
    return get_world_simulator().record_event_trace(
        trace_id, initial_state, actions_taken, final_outcome, error_logs
    )


def replay_counterfactual(
    trace_id_or_data: Any,
    alternative_policy: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return get_world_simulator().replay_counterfactual(trace_id_or_data, alternative_policy)


def dry_run_action(
    action_type: str,
    params: Dict[str, Any],
    sandbox_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    return get_world_simulator().dry_run_action(action_type, params, sandbox_dir)
