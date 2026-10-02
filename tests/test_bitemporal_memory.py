"""Unit tests for Bitemporal Belief Graph & Truth Maintenance (Module Q)."""
import time
from pathlib import Path
import pytest

from bitemporal_memory import (
    BitemporalMemory,
    record_belief,
    query_beliefs,
    resolve_contradiction,
    retract_belief,
)


@pytest.fixture
def mem(tmp_path):
    db_file = tmp_path / "test_beliefs.db"
    return BitemporalMemory(db_path=db_file)


def test_record_and_query_bitemporal(mem):
    t0 = 1000000.0
    r1 = mem.record_belief(
        subject="Sirius",
        predicate="status",
        object_value="online",
        valid_from=t0,
        valid_to=t0 + 3600.0,
        source_channel="telemetry",
        confidence=0.95,
    )
    assert r1["success"] is True
    assert r1["contradiction_detected"] is False

    # Query during valid window
    active = mem.query_beliefs(subject="Sirius", as_of_valid_time=t0 + 1800.0)
    assert len(active) == 1
    assert active[0]["object_value"] == "online"

    # Query outside valid window
    expired = mem.query_beliefs(subject="Sirius", as_of_valid_time=t0 + 7200.0)
    assert len(expired) == 0


def test_contradiction_detection_and_resolution(mem):
    t_now = time.time()
    # Fact 1: Default editor is VSCode (conf 0.7)
    f1 = mem.record_belief(
        subject="user",
        predicate="editor",
        object_value="vscode",
        valid_from=t_now,
        confidence=0.7,
    )
    assert f1["contradiction_detected"] is False

    # Fact 2: Conflicting assertion - Default editor is Neovim (conf 0.95)
    f2 = mem.record_belief(
        subject="user",
        predicate="editor",
        object_value="neovim",
        valid_from=t_now,
        confidence=0.95,
    )
    assert f2["contradiction_detected"] is True
    cid = f2["contradiction_id"]
    assert cid is not None

    contradictions = mem.get_contradictions(status="UNRESOLVED")
    assert len(contradictions) == 1

    # Resolve using higher confidence
    res = mem.resolve_contradiction(cid, resolution_strategy="prefer_higher_confidence")
    assert res["success"] is True
    assert res["winner_belief_id"] == f2["belief_id"]
    assert res["loser_belief_id"] == f1["belief_id"]

    # Verify f1 is superseded
    active = mem.query_beliefs(subject="user", predicate="editor", status="ACTIVE")
    assert len(active) == 1
    assert active[0]["object_value"] == "neovim"


def test_belief_retraction_and_dependency_cascade(mem):
    # Premise 1: User is traveling to London
    p1 = mem.record_belief(subject="user", predicate="location", object_value="London")
    b1_id = p1["belief_id"]

    # Derived Fact 2: User timezone is GMT (depends on p1)
    p2 = mem.record_belief(
        subject="user",
        predicate="timezone",
        object_value="GMT",
        dependencies=[b1_id],
    )
    b2_id = p2["belief_id"]

    # Derived Fact 3: Meeting reminder scheduled in GMT (depends on p2)
    p3 = mem.record_belief(
        subject="calendar",
        predicate="event_tz",
        object_value="GMT",
        dependencies=[b2_id],
    )
    b3_id = p3["belief_id"]

    assert len(mem.query_beliefs(status="ACTIVE")) == 3

    # Retract Premise 1
    ret_res = mem.retract_belief(b1_id, reason="Trip was cancelled")
    assert ret_res["success"] is True
    assert ret_res["broken_dependencies_count"] == 2
    assert b2_id in ret_res["broken_dependencies"]
    assert b3_id in ret_res["broken_dependencies"]

    # Active beliefs should now be 0
    active_now = mem.query_beliefs(status="ACTIVE")
    assert len(active_now) == 0


def test_as_of_valid_time_time_travel(mem):
    t_past = 500000.0
    t_future = 900000.0

    mem.record_belief("project", "status", "alpha", valid_from=t_past, valid_to=t_past + 1000.0)
    mem.record_belief("project", "status", "beta", valid_from=t_past + 1000.0, valid_to=t_future)

    res_past = mem.query_beliefs("project", "status", as_of_valid_time=t_past + 500.0)
    assert len(res_past) == 1
    assert res_past[0]["object_value"] == "alpha"

    res_beta = mem.query_beliefs("project", "status", as_of_valid_time=t_past + 1500.0)
    assert len(res_beta) == 1
    assert res_beta[0]["object_value"] == "beta"
