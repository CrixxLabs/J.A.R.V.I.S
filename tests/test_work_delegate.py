"""Unit tests for Delegated Task Delivery & HITL Workflow (Module D)."""
from pathlib import Path
import pytest

from work_delegate import (
    WorkDelegationCoordinator,
    create_delegated_task,
    run_task_pipeline,
    approve_delivery,
    get_task_status,
)


@pytest.fixture
def coordinator(tmp_path):
    tasks_file = tmp_path / "test_tasks.json"
    return WorkDelegationCoordinator(tasks_path=tasks_file)


def test_create_delegated_task(coordinator):
    res = coordinator.create_delegated_task(
        title="Refactor Logging Subsystem",
        spec="Convert prints to logging.getLogger and add ISO timestamping",
        target_files=["logger_utils.py"],
    )
    assert res["task_id"] is not None
    assert res["title"] == "Refactor Logging Subsystem"
    assert res["stage"] == "SPEC_PARSED"
    assert res["target_files"] == ["logger_utils.py"]


def test_run_task_pipeline_generates_diffs(coordinator):
    task = coordinator.create_delegated_task(
        title="Data Sanitizer",
        spec="Strip HTML tags and normalize whitespace",
        target_files=["sanitizer.py"],
    )
    task_id = task["task_id"]

    new_code = {"sanitizer.py": "import re\n\ndef clean(text):\n    return re.sub(r'<[^>]+>', '', text).strip()\n"}
    res = coordinator.run_task_pipeline(task_id, candidate_content=new_code)

    assert res["success"] is True
    assert res["stage"] == "DIFF_READY"
    assert "clean" in res["diff_preview"] or "sanitizer.py" in res["diff_preview"]

    status = coordinator.get_task_status(task_id)
    assert status["stage"] == "DIFF_READY"
    assert "sanitizer.py" in status["draft_outputs"]


def test_revise_task_draft(coordinator):
    task = coordinator.create_delegated_task(
        title="API Client",
        spec="Create REST client",
        target_files=["client.py"],
    )
    task_id = task["task_id"]
    coordinator.run_task_pipeline(task_id, {"client.py": "def get(): pass"})

    # Revise with feedback
    revised = coordinator.revise_task_draft(
        task_id=task_id,
        feedback="Add timeout parameter",
        revised_content={"client.py": "def get(timeout=10): pass"},
    )
    assert revised["success"] is True
    assert "timeout=10" in coordinator.get_task_status(task_id)["draft_outputs"]["client.py"]


def test_approve_delivery(coordinator):
    task = coordinator.create_delegated_task(
        title="Final Deliverable",
        spec="Complete spec",
        target_files=["final.txt"],
    )
    task_id = task["task_id"]
    coordinator.run_task_pipeline(task_id, {"final.txt": "Deliverable Content"})

    app_res = coordinator.approve_delivery(task_id)
    assert app_res["success"] is True
    assert app_res["status"] == "delivered"
    assert app_res["manifest"]["status"] == "delivered_and_verified"

    status = coordinator.get_task_status(task_id)
    assert status["approved"] is True
    assert status["stage"] == "DELIVERED"
