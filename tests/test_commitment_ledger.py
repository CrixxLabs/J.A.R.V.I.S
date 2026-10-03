"""Unit tests for Executive Commitment Ledger & Simple Temporal Network (Module P)."""
import time
from pathlib import Path
import pytest

from commitment_ledger import (
    CommitmentLedger,
    record_commitment,
    update_commitment_status,
    get_active_commitments,
    calculate_temporal_slack,
    evaluate_stn_consistency,
)


@pytest.fixture
def ledger(tmp_path):
    db_file = tmp_path / "test_commitments.db"
    return CommitmentLedger(db_path=db_file)


def test_record_and_get_commitments(ledger):
    now = time.time()
    c1 = ledger.record_commitment(
        task_name="Deliver Q3 Financial Model",
        deadline_epoch=now + 86400,
        stake_level="CRITICAL",
        estimated_duration_hours=4.0,
        saga_compensation="notify_cfo_delay",
    )
    assert c1["task_name"] == "Deliver Q3 Financial Model"
    assert c1["status"] == "PENDING"
    assert c1["stake_level"] == "CRITICAL"

    active = ledger.get_active_commitments()
    assert len(active) == 1
    assert active[0]["commitment_id"] == c1["commitment_id"]


def test_update_status_lifecycle(ledger):
    now = time.time()
    c = ledger.record_commitment("Review Arc Blueprint", deadline_epoch=now + 3600)
    cid = c["commitment_id"]

    ok_running = ledger.update_commitment_status(cid, "RUNNING")
    assert ok_running is True
    assert ledger.get_active_commitments()[0]["status"] == "RUNNING"

    ok_completed = ledger.update_commitment_status(cid, "COMPLETED")
    assert ok_completed is True
    assert len(ledger.get_active_commitments()) == 0


def test_stn_slack_and_consistency(ledger):
    now = 1000000.0  # Synthetic fixed epoch

    # Existing task: due in 24 hours (1 day), requires 4 hours work
    ledger.record_commitment(
        task_name="Task A",
        deadline_epoch=now + 86400.0,
        estimated_duration_hours=4.0,
    )

    slack = ledger.calculate_temporal_slack(reference_time=now, daily_capacity_hours=8.0)
    assert slack["consistent"] is True
    assert slack["total_active_tasks"] == 1
    assert slack["slack_reports"][0]["slack_hours"] > 0

    # Feasible new task: due in 48 hours, requires 4 hours
    feasible = ledger.evaluate_stn_consistency(
        new_task_name="Task B",
        deadline_epoch=now + 172800.0,
        estimated_duration_hours=4.0,
        reference_time=now,
        daily_capacity_hours=8.0,
    )
    assert feasible["can_accept"] is True
    assert feasible["status"] == "feasible"

    # Infeasible new task: due in 2 hours, requires 10 hours work
    infeasible = ledger.evaluate_stn_consistency(
        new_task_name="Emergency Heavy Rewrite",
        deadline_epoch=now + 7200.0,
        estimated_duration_hours=10.0,
        reference_time=now,
        daily_capacity_hours=8.0,
    )
    assert infeasible["can_accept"] is False
    assert infeasible["renegotiation_needed"] is True
