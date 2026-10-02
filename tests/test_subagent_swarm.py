"""Unit tests for Concurrent Sub-Agent Swarm (Module K)."""
import time
import threading
import pytest

from subagent_swarm import (
    SubagentSwarmPool,
    spawn_subagent,
    get_subagent_status,
    cancel_subagent,
    list_active_subagents,
    wait_for_subagent,
)


@pytest.fixture
def swarm_pool():
    pool = SubagentSwarmPool(max_workers=2)
    yield pool
    pool.shutdown(wait=False)


def test_spawn_and_complete_subagent(swarm_pool):
    def work_fn(params, cancel_event):
        return {"processed_items": 42, "target": params.get("target")}

    task = swarm_pool.spawn_subagent(
        task_id="worker-01",
        objective="Analyze telemetry logs",
        params={"target": "system.log"},
        fn=work_fn,
    )
    assert task["task_id"] == "worker-01"
    assert task["status"] in ("queued", "running", "completed")

    res = swarm_pool.wait_for_subagent("worker-01", timeout=2.0)
    assert res["success"] is True
    assert res["status"] == "completed"
    assert res["result"]["processed_items"] == 42


def test_cancel_subagent(swarm_pool):
    def slow_fn(params, cancel_event):
        for _ in range(50):
            if cancel_event.is_set():
                return
            time.sleep(0.05)

    swarm_pool.spawn_subagent(
        task_id="slow-worker",
        objective="Long background crawling",
        fn=slow_fn,
    )

    cancel_res = swarm_pool.cancel_subagent("slow-worker")
    assert cancel_res["success"] is True

    status = swarm_pool.get_subagent_status("slow-worker")
    assert status["status"] == "cancelled"


def test_subagent_failure_handling(swarm_pool):
    def failing_fn(params, cancel_event):
        raise ValueError("Simulated network drop")

    swarm_pool.spawn_subagent(
        task_id="failing-worker",
        objective="Risky task",
        fn=failing_fn,
    )

    res = swarm_pool.wait_for_subagent("failing-worker", timeout=2.0)
    assert res["status"] == "failed"
    assert "Simulated network drop" in res["error"]


def test_list_active_subagents(swarm_pool):
    def hang_fn(params, cancel_event):
        time.sleep(0.2)

    swarm_pool.spawn_subagent("act-1", "Task 1", fn=hang_fn)
    active = swarm_pool.list_active_subagents()
    assert len(active) >= 1
