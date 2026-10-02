"""Unit tests for Proactive Academic & Life Sentinel (Module I)."""
import datetime
from pathlib import Path
import pytest

from academic_sentinel import (
    AcademicSentinel,
    add_deadline_item,
    solve_temporal_constraints,
    get_active_deadlines,
    get_academic_risk_report,
)


@pytest.fixture
def sentinel(tmp_path):
    store_file = tmp_path / "test_academic.json"
    return AcademicSentinel(store_path=store_file)


def test_add_and_retrieve_deadlines(sentinel):
    sentinel.add_deadline_item(
        item_id="cs-lab-04",
        course="CS501 Deep Learning",
        title="Transformer Self-Attention Lab",
        due_date_iso="2026-10-15",
        estimated_hours=6.0,
        item_type="assignment",
    )
    sentinel.add_deadline_item(
        item_id="math-midterm",
        course="MATH402 Topology",
        title="Midterm Exam",
        due_date_iso="2026-10-10",
        estimated_hours=12.0,
        item_type="exam",
    )

    items = sentinel.get_active_deadlines()
    assert len(items) == 2
    # Should be sorted chronologically
    assert items[0]["item_id"] == "math-midterm"
    assert items[1]["item_id"] == "cs-lab-04"


def test_mark_completed(sentinel):
    sentinel.add_deadline_item(
        item_id="task-1",
        course="EE301",
        title="Circuits Lab",
        due_date_iso="2026-10-05",
        estimated_hours=2.0,
    )
    assert len(sentinel.get_active_deadlines()) == 1

    success = sentinel.mark_completed("task-1")
    assert success is True
    assert len(sentinel.get_active_deadlines()) == 0


def test_temporal_constraint_solver_risk_flagging(sentinel):
    today = datetime.date(2026, 10, 2)

    # 1 day remaining, but needs 10 hours work (capacity = 4h/day -> CRITICAL_RISK)
    sentinel.add_deadline_item(
        item_id="urgent-proj",
        course="AI Capstone",
        title="Final Prototype Submission",
        due_date_iso="2026-10-03",
        estimated_hours=10.0,
    )

    # 10 days remaining, needs 5 hours work (capacity = 40h -> ON_TRACK)
    sentinel.add_deadline_item(
        item_id="relaxed-quiz",
        course="Ethics",
        title="Weekly Reflection Quiz",
        due_date_iso="2026-10-12",
        estimated_hours=2.0,
    )

    res = sentinel.solve_temporal_constraints(reference_date=today, available_daily_hours=4.0)
    assert res["success"] is True
    assert res["total_active_items"] == 2
    assert res["total_backlog_hours"] == 12.0

    evals = {r["item_id"]: r for r in res["risk_evaluations"]}
    assert evals["urgent-proj"]["risk_level"] in ("CRITICAL_RISK", "HIGH_RISK")
    assert evals["relaxed-quiz"]["risk_level"] == "ON_TRACK"
    assert len(res["focus_schedule"]) > 0


def test_academic_risk_report(sentinel):
    sentinel.add_deadline_item(
        item_id="hw-1",
        course="CS101",
        title="Recursion",
        due_date_iso="2026-10-03",
        estimated_hours=8.0,
    )
    report = sentinel.get_academic_risk_report()
    assert report["total_pending_items"] == 1
    assert report["total_hours_required"] == 8.0
