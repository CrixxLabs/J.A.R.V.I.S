"""Hierarchical Goal Management Engine for J.A.R.V.I.S. — MARK VIII.

Implements a long-horizon meta-controller:
  1. Goal DAG State Machine: High-level goals decomposed into directed sub-goal milestones
     (PENDING, IN_PROGRESS, VERIFIED, FAILED, SKIPPED).
  2. Persistent Storage: Atomic JSON persistence in data/persistent_goals.json with state recovery.
  3. Credit Assignment & Localized Replanning: Resets or splices failed subgraphs while preserving
     upstream verified artifacts.
"""
from __future__ import annotations

import datetime
import enum
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
GOALS_FILE = DATA_DIR / "persistent_goals.json"

_lock = threading.RLock()


class MilestoneStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class GoalStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


def _load_goals_unlocked(file_path: Path = GOALS_FILE) -> Dict[str, Dict[str, Any]]:
    """Load goals from JSON file."""
    if not file_path.exists():
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"[GoalManager] Failed loading {file_path}: {exc}")
        return {}


def _save_goals_unlocked(goals: Dict[str, Dict[str, Any]], file_path: Path = GOALS_FILE) -> bool:
    """Save goals atomically to JSON file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(goals, f, indent=2, ensure_ascii=False)
        temp_path.replace(file_path)
        return True
    except Exception as exc:
        print(f"[GoalManager] Failed saving {file_path}: {exc}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return False


def create_goal(
    title: str,
    description: str = "",
    milestones: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    goals_file: Path = GOALS_FILE,
) -> Dict[str, Any]:
    """Create a new long-horizon hierarchical goal."""
    with _lock:
        goals = _load_goals_unlocked(goals_file)
        goal_id = f"goal_{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        formatted_milestones = []
        for i, m in enumerate(milestones or []):
            m_id = m.get("id") or f"m_{goal_id}_{i+1}"
            formatted_milestones.append({
                "id": m_id,
                "title": m.get("title", f"Milestone {i+1}"),
                "description": m.get("description", ""),
                "status": m.get("status", MilestoneStatus.PENDING.value),
                "dependencies": m.get("dependencies", []),
                "action_spec": m.get("action_spec", {}),
                "artifact": m.get("artifact"),
                "error": m.get("error"),
                "retries": m.get("retries", 0),
            })

        goal_data = {
            "id": goal_id,
            "title": title.strip(),
            "description": description.strip(),
            "status": GoalStatus.PENDING.value if formatted_milestones else GoalStatus.IN_PROGRESS.value,
            "created_at": now,
            "updated_at": now,
            "milestones": formatted_milestones,
            "metadata": metadata or {},
        }

        goals[goal_id] = goal_data
        _save_goals_unlocked(goals, goals_file)

        get_registry().set_capability_evidence(
            "GOAL_MANAGER",
            EvidenceLevel.LIVE,
            f"Created goal: '{title}' ({len(formatted_milestones)} milestones)",
            source="goal manager",
        )
        return goal_data


def decompose_goal(
    goal_id: str,
    milestones: List[Dict[str, Any]],
    goals_file: Path = GOALS_FILE,
) -> Optional[Dict[str, Any]]:
    """Decompose or update DAG milestones for an existing goal."""
    with _lock:
        goals = _load_goals_unlocked(goals_file)
        if goal_id not in goals:
            return None

        formatted_milestones = []
        for i, m in enumerate(milestones):
            m_id = m.get("id") or f"m_{goal_id}_{i+1}"
            formatted_milestones.append({
                "id": m_id,
                "title": m.get("title", f"Milestone {i+1}"),
                "description": m.get("description", ""),
                "status": m.get("status", MilestoneStatus.PENDING.value),
                "dependencies": m.get("dependencies", []),
                "action_spec": m.get("action_spec", {}),
                "artifact": m.get("artifact"),
                "error": m.get("error"),
                "retries": m.get("retries", 0),
            })

        goals[goal_id]["milestones"] = formatted_milestones
        goals[goal_id]["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save_goals_unlocked(goals, goals_file)
        return goals[goal_id]


def get_goal(goal_id: str, goals_file: Path = GOALS_FILE) -> Optional[Dict[str, Any]]:
    """Retrieve a goal by ID."""
    with _lock:
        goals = _load_goals_unlocked(goals_file)
        return goals.get(goal_id)


def list_goals(status: Optional[str] = None, goals_file: Path = GOALS_FILE) -> List[Dict[str, Any]]:
    """List goals, optionally filtered by status."""
    with _lock:
        goals = _load_goals_unlocked(goals_file)
        res = list(goals.values())
        if status:
            res = [g for g in res if g.get("status") == status]
        return res


def get_ready_milestones(goal_id: str, goals_file: Path = GOALS_FILE) -> List[Dict[str, Any]]:
    """Get all milestones ready for execution (all dependencies VERIFIED, status PENDING)."""
    with _lock:
        goal = get_goal(goal_id, goals_file)
        if not goal:
            return []

        verified_ids = {
            m["id"] for m in goal.get("milestones", [])
            if m.get("status") == MilestoneStatus.VERIFIED.value
        }

        ready = []
        for m in goal.get("milestones", []):
            if m.get("status") == MilestoneStatus.PENDING.value:
                deps = m.get("dependencies", [])
                if all(dep in verified_ids for dep in deps):
                    ready.append(m)
        return ready


def update_milestone_status(
    goal_id: str,
    milestone_id: str,
    status: str,
    artifact: Optional[Any] = None,
    error: Optional[str] = None,
    goals_file: Path = GOALS_FILE,
) -> Optional[Dict[str, Any]]:
    """Update status of a milestone and recompute overall goal status."""
    with _lock:
        goals = _load_goals_unlocked(goals_file)
        if goal_id not in goals:
            return None

        goal = goals[goal_id]
        target_milestone = None
        for m in goal.get("milestones", []):
            if m.get("id") == milestone_id:
                target_milestone = m
                break

        if not target_milestone:
            return None

        target_milestone["status"] = status
        if artifact is not None:
            target_milestone["artifact"] = artifact
        if error is not None:
            target_milestone["error"] = error
        if status == MilestoneStatus.FAILED.value:
            target_milestone["retries"] = target_milestone.get("retries", 0) + 1

        # Re-evaluate goal status
        milestones = goal.get("milestones", [])
        if milestones:
            if all(m["status"] == MilestoneStatus.VERIFIED.value for m in milestones):
                goal["status"] = GoalStatus.COMPLETED.value
            elif any(m["status"] == MilestoneStatus.FAILED.value for m in milestones):
                goal["status"] = GoalStatus.FAILED.value
            elif any(m["status"] in (MilestoneStatus.IN_PROGRESS.value, MilestoneStatus.VERIFIED.value) for m in milestones):
                goal["status"] = GoalStatus.IN_PROGRESS.value

        goal["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save_goals_unlocked(goals, goals_file)
        return goal


def replan_goal(
    goal_id: str,
    failed_milestone_id: str,
    replacement_milestones: List[Dict[str, Any]],
    goals_file: Path = GOALS_FILE,
) -> Optional[Dict[str, Any]]:
    """Perform localized DAG replanning for a failed milestone while keeping verified milestones intact.

    Removes the failed milestone and all downstream dependent milestones, then appends the replacement milestones.
    """
    with _lock:
        goals = _load_goals_unlocked(goals_file)
        if goal_id not in goals:
            return None

        goal = goals[goal_id]
        current_milestones = goal.get("milestones", [])

        # Find downstream dependent milestone IDs recursively
        invalidated: Set[str] = {failed_milestone_id}
        changed = True
        while changed:
            changed = False
            for m in current_milestones:
                if m["id"] not in invalidated:
                    if any(dep in invalidated for dep in m.get("dependencies", [])):
                        invalidated.add(m["id"])
                        changed = True

        # Keep untouched milestones (e.g. verified upstream milestones)
        kept_milestones = [m for m in current_milestones if m["id"] not in invalidated]

        # Add replacement milestones
        formatted_replacements = []
        for i, m in enumerate(replacement_milestones):
            m_id = m.get("id") or f"m_{goal_id}_replan_{i+1}"
            formatted_replacements.append({
                "id": m_id,
                "title": m.get("title", f"Replacement Milestone {i+1}"),
                "description": m.get("description", ""),
                "status": m.get("status", MilestoneStatus.PENDING.value),
                "dependencies": m.get("dependencies", []),
                "action_spec": m.get("action_spec", {}),
                "artifact": m.get("artifact"),
                "error": None,
                "retries": 0,
            })

        goal["milestones"] = kept_milestones + formatted_replacements
        goal["status"] = GoalStatus.IN_PROGRESS.value
        goal["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save_goals_unlocked(goals, goals_file)

        get_registry().set_capability_evidence(
            "GOAL_MANAGER",
            EvidenceLevel.LIVE,
            f"Replanned goal {goal_id} ({len(invalidated)} invalidated, {len(formatted_replacements)} added)",
            source="goal manager",
        )
        return goal
