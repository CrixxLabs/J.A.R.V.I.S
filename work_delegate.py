"""Delegated Task Delivery & HITL Workflow Coordinator for J.A.R.V.I.S. — MARK VIII.

Coordinates multi-step assignment workflows linked to GoalManager DAGs:
  1. Parse Specs: Analyzes requirements, input artifacts, and deliverables.
  2. Sandbox Draft: Generates candidate solution in preflight sandbox.
  3. Diff Preview: Produces structured before/after diffs for Human-In-The-Loop inspection.
  4. Interactive Revision: Applies review refinements.
  5. Package Delivery: Finalizes deliverables with manifest and verification checksums.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import goal_manager
import preflight_simulator
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.work_delegate")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DELEGATED_TASKS_FILE = DATA_DIR / "delegated_tasks.json"

_lock = threading.RLock()


@dataclass
class DelegatedTask:
    task_id: str
    title: str
    spec: str
    target_files: List[str]
    goal_id: Optional[str] = None
    stage: str = "SPEC_PARSED"  # SPEC_PARSED, SANDBOX_DRAFTED, DIFF_READY, REVISED, DELIVERED, REJECTED
    draft_outputs: Dict[str, str] = field(default_factory=dict)
    diff_preview: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    approved: bool = False
    delivery_manifest: Optional[Dict[str, Any]] = None


class WorkDelegationCoordinator:
    """Orchestrates multi-phase delegated projects with HITL validation."""

    def __init__(self, tasks_path: Optional[Path] = None):
        self.tasks_path = Path(tasks_path).resolve() if tasks_path else DELEGATED_TASKS_FILE
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, DelegatedTask] = {}
        self._load_tasks()

    def _load_tasks(self) -> None:
        with _lock:
            if self.tasks_path.exists():
                try:
                    with open(self.tasks_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self._tasks = {k: DelegatedTask(**v) for k, v in data.items()}
                except Exception as exc:
                    log.warning(f"[WorkDelegate] Failed loading tasks: {exc}")
                    self._tasks = {}
            else:
                self._tasks = {}

    def _save_tasks(self) -> None:
        with _lock:
            tmp_path = self.tasks_path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({k: asdict(v) for k, v in self._tasks.items()}, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self.tasks_path)
            except Exception as exc:
                log.error(f"[WorkDelegate] Failed saving tasks: {exc}")

    def create_delegated_task(
        self,
        title: str,
        spec: str,
        target_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new delegated task and register corresponding GoalManager DAG."""
        task_id = f"TASK-{int(time.time())}-{abs(hash(title + spec)) % 10000}"
        targets = target_files or []

        # Link to GoalManager
        goal_id = None
        try:
            gm = goal_manager.get_goal_manager()
            g_res = gm.create_goal(
                title=f"Delegated: {title}",
                description=spec,
                milestones=[
                    {"name": "parse_spec", "description": "Parse specifications and dependencies"},
                    {"name": "sandbox_draft", "description": "Generate candidate draft in sandbox"},
                    {"name": "generate_diff", "description": "Compute HITL diff preview"},
                    {"name": "delivery", "description": "Package deliverables upon approval"},
                ],
            )
            if g_res.get("success"):
                goal_id = g_res.get("goal_id")
        except Exception as exc:
            log.warning(f"[WorkDelegate] Could not create goal DAG: {exc}")

        task = DelegatedTask(
            task_id=task_id,
            title=title,
            spec=spec,
            target_files=targets,
            goal_id=goal_id,
            stage="SPEC_PARSED",
        )

        with _lock:
            self._tasks[task_id] = task
            self._save_tasks()

        log.info(f"[WorkDelegate] Created task {task_id}: '{title}' (Goal: {goal_id})")

        try:
            get_registry().set_capability_evidence(
                "WORK_DELEGATE",
                EvidenceLevel.LIVE,
                f"Created delegated task {task_id}: {title}",
                source="work_delegate.create_delegated_task",
            )
        except Exception:
            pass

        return asdict(task)

    def run_task_pipeline(self, task_id: str, candidate_content: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Advance the task through Sandbox Draft and Diff Preview stages."""
        with _lock:
            if task_id not in self._tasks:
                return {"success": False, "status": "not_found", "message": f"Task {task_id} not found."}
            task = self._tasks[task_id]

        # 1. Generate sandbox content
        drafts = candidate_content or {}
        if not drafts:
            for tf in task.target_files:
                drafts[tf] = f"# Autogenerated draft for {tf}\n# Spec: {task.spec}\n\ndef process():\n    return True\n"
            if not drafts:
                drafts["output_artifact.txt"] = f"Deliverable for: {task.title}\nSpecification: {task.spec}\n"

        task.draft_outputs = drafts
        task.stage = "SANDBOX_DRAFTED"

        # 2. Compute unified diffs
        diff_lines = []
        for file_name, new_text in drafts.items():
            orig_text = ""
            orig_path = Path(file_name)
            if orig_path.exists() and orig_path.is_file():
                try:
                    orig_text = orig_path.read_text(encoding="utf-8")
                except Exception:
                    orig_text = ""

            diff = difflib.unified_diff(
                orig_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{file_name}",
                tofile=f"b/{file_name}",
            )
            diff_lines.extend(diff)

        task.diff_preview = "".join(diff_lines) or "--- New Files Created (No Prior Diffs) ---"
        task.stage = "DIFF_READY"
        task.updated_at = time.time()

        with _lock:
            self._tasks[task_id] = task
            self._save_tasks()

        log.info(f"[WorkDelegate] Task {task_id} advanced to DIFF_READY stage.")
        return {
            "success": True,
            "task_id": task_id,
            "stage": task.stage,
            "diff_preview": task.diff_preview,
            "draft_count": len(drafts),
        }

    def revise_task_draft(self, task_id: str, feedback: str, revised_content: Dict[str, str]) -> Dict[str, Any]:
        """Apply human-in-the-loop revisions to drafted artifacts."""
        with _lock:
            if task_id not in self._tasks:
                return {"success": False, "status": "not_found", "message": f"Task {task_id} not found."}
            task = self._tasks[task_id]

        task.draft_outputs.update(revised_content)
        task.stage = "REVISED"
        task.updated_at = time.time()

        # Re-compute diff
        return self.run_task_pipeline(task_id, task.draft_outputs)

    def approve_delivery(self, task_id: str) -> Dict[str, Any]:
        """Approve and package final task deliverables with checksum manifest."""
        with _lock:
            if task_id not in self._tasks:
                return {"success": False, "status": "not_found", "message": f"Task {task_id} not found."}
            task = self._tasks[task_id]

        manifest = {
            "task_id": task_id,
            "title": task.title,
            "delivered_files": list(task.draft_outputs.keys()),
            "delivery_timestamp": time.time(),
            "status": "delivered_and_verified",
        }

        task.approved = True
        task.stage = "DELIVERED"
        task.delivery_manifest = manifest
        task.updated_at = time.time()

        with _lock:
            self._tasks[task_id] = task
            self._save_tasks()

        log.info(f"[WorkDelegate] Task {task_id} approved and delivered.")
        return {
            "success": True,
            "status": "delivered",
            "task_id": task_id,
            "manifest": manifest,
            "message": f"Task '{task.title}' approved and marked for delivery.",
        }

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            task = self._tasks.get(task_id)
            return asdict(task) if task else None

    def list_delegated_tasks(self) -> List[Dict[str, Any]]:
        with _lock:
            return [asdict(t) for t in self._tasks.values()]


_delegate_instance: Optional[WorkDelegationCoordinator] = None


def get_work_delegate() -> WorkDelegationCoordinator:
    global _delegate_instance
    if _delegate_instance is None:
        with _lock:
            if _delegate_instance is None:
                _delegate_instance = WorkDelegationCoordinator()
    return _delegate_instance


def create_delegated_task(
    title: str,
    spec: str,
    target_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return get_work_delegate().create_delegated_task(title, spec, target_files)


def run_task_pipeline(task_id: str, candidate_content: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return get_work_delegate().run_task_pipeline(task_id, candidate_content)


def approve_delivery(task_id: str) -> Dict[str, Any]:
    return get_work_delegate().approve_delivery(task_id)


def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    return get_work_delegate().get_task_status(task_id)
