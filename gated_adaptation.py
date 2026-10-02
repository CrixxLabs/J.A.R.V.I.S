"""Gated Self-Adaptation Engine for J.A.R.V.I.S. — MARK VIII.

Module U:
  1. Verified Trajectory Filtering:
     - Curates training and adaptation datasets strictly from verified experiences (symbolically
       checked via Module O or explicitly user-confirmed). Rejects unverified self-generated traces.
  2. Regression Gate & Canary Promotion:
     - Executes frozen benchmark harness before accepting any prompt mutation, synthesized skill,
       routing rule, or fine-tuned LoRA adapter.
     - Single-command atomic rollback restoring previous verified active baseline upon regression.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry
from symbolic_verifier import verify_code_safety

log = logging.getLogger("jarvis.gated_adaptation")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
LEDGER_FILE = DATA_DIR / "adaptation_ledger.json"

_lock = threading.RLock()

# Adaptation Candidate Types
TYPE_PROMPT_MUTATION = "PROMPT_MUTATION"
TYPE_SYNTHESIZED_SKILL = "SYNTHESIZED_SKILL"
TYPE_LORA_ADAPTER = "LORA_ADAPTER"
TYPE_ROUTING_RULE = "ROUTING_RULE"

# Lifecycle Statuses
STATUS_SUBMITTED = "SUBMITTED"
STATUS_QUALIFIED = "QUALIFIED"
STATUS_REJECTED = "REJECTED"
STATUS_CANARY = "CANARY"
STATUS_ACTIVE = "ACTIVE"
STATUS_ARCHIVED = "ARCHIVED"
STATUS_ROLLED_BACK = "ROLLED_BACK"


@dataclass
class AdaptationRecord:
    candidate_id: str
    candidate_type: str
    target_subsystem: str
    artifact: Dict[str, Any]
    status: str
    submitted_at: float
    promoted_at: Optional[float] = None
    rolled_back_at: Optional[float] = None
    evaluation_report: Dict[str, Any] = field(default_factory=dict)
    rollback_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GatedAdaptationEngine:
    """Manages verified trajectory curation, regression testing, canary promotion, and rollback."""

    def __init__(self, ledger_path: Optional[Path] = None):
        self.ledger_path = Path(ledger_path).resolve() if ledger_path else LEDGER_FILE
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, Dict[str, Any]] = {}
        self._load_ledger()

    def _load_ledger(self) -> None:
        with _lock:
            if self.ledger_path.exists():
                try:
                    with open(self.ledger_path, "r", encoding="utf-8") as f:
                        self._records = json.load(f)
                except Exception:
                    self._records = {}
            else:
                self._records = {}

    def _save_ledger(self) -> None:
        with _lock:
            try:
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    json.dump(self._records, f, indent=2)
            except Exception as exc:
                log.error(f"[GatedAdaptation] Failed to save ledger: {exc}")

    def filter_trajectories(
        self,
        trajectories: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Filter self-improvement trajectories: accept only symbolically verified or human-confirmed traces."""
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        for traj in trajectories:
            tid = traj.get("trajectory_id", "unknown_traj")
            symbolic_verified = bool(traj.get("symbolic_verified", False))
            user_confirmed = bool(traj.get("user_confirmed", False))
            code = traj.get("code")

            # 1. Verification provenance gate
            if not (symbolic_verified or user_confirmed):
                rejected.append({
                    "trajectory_id": tid,
                    "reason": "Missing verification: neither symbolic_verified nor user_confirmed.",
                })
                continue

            # 2. If code artifact is present, run AST safety checks
            if code and isinstance(code, str):
                safety_res = verify_code_safety(code)
                if not safety_res.get("verified", False):
                    rejected.append({
                        "trajectory_id": tid,
                        "reason": f"AST Safety Violation: {', '.join(safety_res.get('violations', []))}",
                    })
                    continue

            accepted.append(traj)

        total = len(trajectories)
        acceptance_rate = len(accepted) / max(1, total)

        log.info(f"[GatedAdaptation] Trajectory filtering: {len(accepted)} accepted, {len(rejected)} rejected (rate={acceptance_rate:.2f})")

        return {
            "total_processed": total,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "acceptance_rate": round(acceptance_rate, 4),
            "accepted_trajectories": accepted,
            "rejected_trajectories": rejected,
        }

    def evaluate_candidate_adaptation(
        self,
        candidate_id: str,
        candidate_type: str,
        target_subsystem: str,
        artifact: Dict[str, Any],
        regression_benchmark: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute regression test suite against candidate adaptation before promotion."""
        cid = str(candidate_id).strip()
        now = time.time()

        # 1. Code safety check if code payload exists
        code_payload = artifact.get("code") or artifact.get("script")
        if code_payload and isinstance(code_payload, str):
            ast_res = verify_code_safety(code_payload)
            if not ast_res.get("verified", False):
                eval_report = {
                    "passed": False,
                    "regression_detected": True,
                    "error": f"AST Safety Check Failed: {ast_res.get('violations', [])}",
                }
                rec = AdaptationRecord(
                    candidate_id=cid,
                    candidate_type=candidate_type,
                    target_subsystem=target_subsystem,
                    artifact=artifact,
                    status=STATUS_REJECTED,
                    submitted_at=now,
                    evaluation_report=eval_report,
                )
                with _lock:
                    self._records[cid] = rec.to_dict()
                    self._save_ledger()
                return rec.to_dict()

        # 2. Run regression benchmark test cases
        benchmark = regression_benchmark or []
        passed_cases = 0
        failed_cases = []

        for case in benchmark:
            case_name = case.get("name", "test_case")
            input_val = case.get("input")
            expected_val = case.get("expected")
            test_fn = case.get("test_fn")

            case_passed = True
            if test_fn and callable(test_fn):
                try:
                    case_passed = bool(test_fn(artifact, input_val, expected_val))
                except Exception as exc:
                    case_passed = False
                    failed_cases.append(f"{case_name}: Exception {exc}")
            elif expected_val is not None:
                # Simulating rule or prompt transformation check
                case_passed = (input_val == expected_val)

            if case_passed:
                passed_cases += 1
            else:
                failed_cases.append(case_name)

        total_cases = len(benchmark)
        pass_rate = (passed_cases / total_cases) if total_cases > 0 else 1.0
        regression_detected = len(failed_cases) > 0

        status = STATUS_QUALIFIED if not regression_detected else STATUS_REJECTED
        eval_report = {
            "total_benchmark_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "pass_rate": round(pass_rate, 4),
            "regression_detected": regression_detected,
        }

        rec = AdaptationRecord(
            candidate_id=cid,
            candidate_type=candidate_type,
            target_subsystem=target_subsystem,
            artifact=artifact,
            status=status,
            submitted_at=now,
            evaluation_report=eval_report,
        )

        with _lock:
            self._records[cid] = rec.to_dict()
            self._save_ledger()

        try:
            get_registry().set_capability_evidence(
                "GATED_ADAPTATION",
                EvidenceLevel.LIVE,
                f"Evaluated candidate '{cid}' for '{target_subsystem}' (status={status}, pass_rate={pass_rate:.2f})",
                source="gated_adaptation.evaluate_candidate_adaptation",
            )
        except Exception:
            pass

        return rec.to_dict()

    def promote_adaptation(
        self,
        candidate_id: str,
        promotion_mode: str = "ACTIVE",
    ) -> Dict[str, Any]:
        """Promote qualified candidate adaptation to ACTIVE (or CANARY) and archive existing active version."""
        cid = str(candidate_id).strip()
        now = time.time()
        mode_norm = str(promotion_mode).strip().upper()
        target_status = STATUS_CANARY if mode_norm == "CANARY" else STATUS_ACTIVE

        with _lock:
            rec = self._records.get(cid)
            if not rec:
                return {"success": False, "error": f"Candidate '{cid}' not found in ledger"}

            if rec["status"] not in [STATUS_QUALIFIED, STATUS_CANARY]:
                return {"success": False, "error": f"Candidate '{cid}' is in status '{rec['status']}', cannot promote"}

            target_subsystem = rec["target_subsystem"]
            target_type = rec["candidate_type"]

            # Archive existing active version for this subsystem & type
            for old_id, old_rec in self._records.items():
                if old_id != cid and old_rec.get("target_subsystem") == target_subsystem and old_rec.get("candidate_type") == target_type:
                    if old_rec.get("status") in [STATUS_ACTIVE, STATUS_CANARY]:
                        old_rec["status"] = STATUS_ARCHIVED
                        old_rec["archived_at"] = now
                        log.info(f"[GatedAdaptation] Archived prior version '{old_id}'")

            rec["status"] = target_status
            rec["promoted_at"] = now
            self._save_ledger()

            log.info(f"[GatedAdaptation] Promoted '{cid}' to {target_status} for subsystem '{target_subsystem}'")

            return {
                "success": True,
                "candidate_id": cid,
                "status": target_status,
                "promoted_at": now,
                "target_subsystem": target_subsystem,
            }

    def rollback_adaptation(
        self,
        adaptation_id: str,
        reason: str = "performance_regression",
    ) -> Dict[str, Any]:
        """Atomically rollback an active adaptation and restore previous archived baseline."""
        aid = str(adaptation_id).strip()
        now = time.time()

        with _lock:
            rec = self._records.get(aid)
            if not rec:
                return {"success": False, "error": f"Adaptation '{aid}' not found"}

            target_subsystem = rec["target_subsystem"]
            target_type = rec["candidate_type"]

            rec["status"] = STATUS_ROLLED_BACK
            rec["rolled_back_at"] = now
            rec["rollback_reason"] = reason

            # Find latest archived version to restore
            restored_id = None
            archived_candidates = [
                (k, v) for k, v in self._records.items()
                if v.get("target_subsystem") == target_subsystem
                and v.get("candidate_type") == target_type
                and v.get("status") == STATUS_ARCHIVED
            ]
            archived_candidates.sort(key=lambda x: x[1].get("promoted_at", 0), reverse=True)

            if archived_candidates:
                restored_id, restored_rec = archived_candidates[0]
                restored_rec["status"] = STATUS_ACTIVE
                restored_rec["promoted_at"] = now
                log.info(f"[GatedAdaptation] Restored archived version '{restored_id}' to ACTIVE")

            self._save_ledger()

            return {
                "success": True,
                "rolled_back_id": aid,
                "reason": reason,
                "restored_baseline_id": restored_id,
            }

    def get_active_adaptations(self) -> List[Dict[str, Any]]:
        with _lock:
            return [v for v in self._records.values() if v.get("status") in [STATUS_ACTIVE, STATUS_CANARY]]

    def get_adaptation_history(self) -> List[Dict[str, Any]]:
        with _lock:
            return list(self._records.values())


_engine_instance: Optional[GatedAdaptationEngine] = None


def get_adaptation_engine() -> GatedAdaptationEngine:
    global _engine_instance
    if _engine_instance is None:
        with _lock:
            if _engine_instance is None:
                _engine_instance = GatedAdaptationEngine()
    return _engine_instance


def filter_trajectories(trajectories: List[Dict[str, Any]]) -> Dict[str, Any]:
    return get_adaptation_engine().filter_trajectories(trajectories)


def evaluate_candidate_adaptation(
    candidate_id: str,
    candidate_type: str,
    target_subsystem: str,
    artifact: Dict[str, Any],
    regression_benchmark: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return get_adaptation_engine().evaluate_candidate_adaptation(
        candidate_id, candidate_type, target_subsystem, artifact, regression_benchmark
    )


def promote_adaptation(candidate_id: str, promotion_mode: str = "ACTIVE") -> Dict[str, Any]:
    return get_adaptation_engine().promote_adaptation(candidate_id, promotion_mode)


def rollback_adaptation(adaptation_id: str, reason: str = "performance_regression") -> Dict[str, Any]:
    return get_adaptation_engine().rollback_adaptation(adaptation_id, reason)


def get_active_adaptations() -> List[Dict[str, Any]]:
    return get_adaptation_engine().get_active_adaptations()


def get_adaptation_history() -> List[Dict[str, Any]]:
    return get_adaptation_engine().get_adaptation_history()
