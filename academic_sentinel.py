"""Proactive Academic & Life Sentinel for J.A.R.V.I.S. — MARK VIII.

Orchestrates academic obligation tracking, temporal constraint solving, and focus scheduling:
  1. Ingestion of assignments, lab deliverables, exams, and milestones.
  2. Temporal constraint satisfaction solver: maps needed workload hours against remaining time windows.
  3. Risk Assessment: tags items as CRITICAL_RISK, HIGH_RISK, or ON_TRACK.
  4. Autonomous Focus Block recommendations.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.academic_sentinel")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
ACADEMIC_STORE = DATA_DIR / "academic_schedule.json"

_lock = threading.RLock()


@dataclass
class AcademicItem:
    item_id: str
    course: str
    title: str
    item_type: str  # "assignment", "exam", "lab_report", "project"
    due_date_iso: str  # "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS"
    estimated_hours: float
    completed: bool = False
    priority: int = 5  # 1-10
    notes: str = ""


@dataclass
class FocusBlock:
    date_iso: str
    allocated_hours: float
    item_id: str
    item_title: str
    course: str


class AcademicSentinel:
    """Tracks academic deadlines and computes temporal focus allocations."""

    def __init__(self, store_path: Optional[Path] = None):
        self.store_path = Path(store_path).resolve() if store_path else ACADEMIC_STORE
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._items: Dict[str, AcademicItem] = {}
        self._load_store()

    def _load_store(self) -> None:
        with _lock:
            if self.store_path.exists():
                try:
                    with open(self.store_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            raw_items = data.get("items", {})
                            self._items = {k: AcademicItem(**v) for k, v in raw_items.items()}
                except Exception as exc:
                    log.warning(f"[AcademicSentinel] Failed loading store: {exc}")
                    self._items = {}

    def _save_store(self) -> None:
        with _lock:
            tmp = self.store_path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({
                        "items": {k: asdict(v) for k, v in self._items.items()},
                        "updated_at": time.time(),
                    }, f, indent=2, ensure_ascii=False)
                os.replace(tmp, self.store_path)
            except Exception as exc:
                log.error(f"[AcademicSentinel] Failed saving store: {exc}")

    def add_deadline_item(
        self,
        item_id: str,
        course: str,
        title: str,
        due_date_iso: str,
        estimated_hours: float,
        item_type: str = "assignment",
        priority: int = 5,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Add or update an academic deliverable or exam milestone."""
        item = AcademicItem(
            item_id=item_id,
            course=course,
            title=title,
            item_type=item_type,
            due_date_iso=due_date_iso,
            estimated_hours=float(estimated_hours),
            priority=int(priority),
            notes=notes,
        )

        with _lock:
            self._items[item_id] = item
            self._save_store()

        try:
            get_registry().set_capability_evidence(
                "ACADEMIC_SENTINEL",
                EvidenceLevel.LIVE,
                f"Tracked deadline '{title}' ({course}) due {due_date_iso}",
                source="academic_sentinel.add_deadline_item",
            )
        except Exception:
            pass

        return asdict(item)

    def mark_completed(self, item_id: str) -> bool:
        """Mark an academic item as completed."""
        with _lock:
            if item_id in self._items:
                self._items[item_id].completed = True
                self._save_store()
                return True
        return False

    def get_active_deadlines(self) -> List[Dict[str, Any]]:
        """Return all uncompleted deadlines sorted by due date."""
        with _lock:
            active = [asdict(i) for i in self._items.values() if not i.completed]

        def _sort_key(x):
            return x.get("due_date_iso", "9999-99-99")

        return sorted(active, key=_sort_key)

    def solve_temporal_constraints(
        self,
        reference_date: Optional[datetime.date] = None,
        available_daily_hours: float = 4.0,
    ) -> Dict[str, Any]:
        """Evaluate temporal schedule, compute risks, and allocate focus study blocks."""
        ref = reference_date or datetime.date.today()
        deadlines = self.get_active_deadlines()

        risk_evaluations: List[Dict[str, Any]] = []
        schedule_blocks: List[Dict[str, Any]] = []
        total_backlog_hours = 0.0

        for d in deadlines:
            due_str = d["due_date_iso"]
            try:
                if "T" in due_str:
                    due_date = datetime.datetime.fromisoformat(due_str).date()
                else:
                    due_date = datetime.date.fromisoformat(due_str)
            except Exception:
                due_date = ref + datetime.timedelta(days=7)

            days_remaining = max(0, (due_date - ref).days)
            est_hours = float(d.get("estimated_hours", 2.0))
            total_backlog_hours += est_hours

            max_possible_study = days_remaining * available_daily_hours

            if days_remaining <= 1 and est_hours > available_daily_hours:
                risk_level = "CRITICAL_RISK"
            elif est_hours > max_possible_study:
                risk_level = "HIGH_RISK"
            elif days_remaining <= 3:
                risk_level = "MODERATE_RISK"
            else:
                risk_level = "ON_TRACK"

            # Allocate focus blocks
            hours_per_day = min(available_daily_hours, round(est_hours / max(1, days_remaining), 1))
            for day_offset in range(max(1, days_remaining)):
                alloc_date = (ref + datetime.timedelta(days=day_offset)).isoformat()
                schedule_blocks.append({
                    "date_iso": alloc_date,
                    "allocated_hours": hours_per_day,
                    "item_id": d["item_id"],
                    "item_title": d["title"],
                    "course": d["course"],
                })

            risk_evaluations.append({
                "item_id": d["item_id"],
                "title": d["title"],
                "course": d["course"],
                "days_remaining": days_remaining,
                "estimated_hours": est_hours,
                "risk_level": risk_level,
                "due_date": due_str,
            })

        return {
            "success": True,
            "reference_date": ref.isoformat(),
            "total_active_items": len(deadlines),
            "total_backlog_hours": round(total_backlog_hours, 1),
            "risk_evaluations": risk_evaluations,
            "focus_schedule": schedule_blocks,
        }

    def get_academic_risk_report(self) -> Dict[str, Any]:
        """Compile an academic risk assessment artifact."""
        solution = self.solve_temporal_constraints()
        critical_count = sum(1 for r in solution["risk_evaluations"] if r["risk_level"] in ("CRITICAL_RISK", "HIGH_RISK"))
        return {
            "critical_risk_count": critical_count,
            "total_pending_items": solution["total_active_items"],
            "total_hours_required": solution["total_backlog_hours"],
            "details": solution["risk_evaluations"],
        }


_sentinel_instance: Optional[AcademicSentinel] = None


def get_academic_sentinel() -> AcademicSentinel:
    global _sentinel_instance
    if _sentinel_instance is None:
        with _lock:
            if _sentinel_instance is None:
                _sentinel_instance = AcademicSentinel()
    return _sentinel_instance


def add_deadline_item(
    item_id: str,
    course: str,
    title: str,
    due_date_iso: str,
    estimated_hours: float,
    item_type: str = "assignment",
    priority: int = 5,
) -> Dict[str, Any]:
    return get_academic_sentinel().add_deadline_item(
        item_id, course, title, due_date_iso, estimated_hours, item_type, priority
    )


def solve_temporal_constraints(available_daily_hours: float = 4.0) -> Dict[str, Any]:
    return get_academic_sentinel().solve_temporal_constraints(available_daily_hours=available_daily_hours)


def get_active_deadlines() -> List[Dict[str, Any]]:
    return get_academic_sentinel().get_active_deadlines()


def get_academic_risk_report() -> Dict[str, Any]:
    return get_academic_sentinel().get_academic_risk_report()
