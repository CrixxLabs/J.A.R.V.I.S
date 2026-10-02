"""Unit tests for Cognitive OS Scheduler & Context Virtualization (Module V)."""
from pathlib import Path
import pytest

from cognitive_os import (
    CognitiveOSScheduler,
    acquire_gpu_lease,
    release_gpu_lease,
    swap_out_context,
    swap_in_context,
    get_scheduler_state,
    TIER_0_VOICE_INTERRUPT,
    TIER_1_INTERACTIVE,
    TIER_2_BACKGROUND_SWARM,
    TIER_3_QUIESCENT,
    STATE_HOT,
    STATE_WARM,
)


@pytest.fixture
def scheduler(tmp_path):
    swap_dir = tmp_path / "test_swap"
    return CognitiveOSScheduler(swap_path=swap_dir)


def test_gpu_lease_acquisition_and_release(scheduler):
    r1 = scheduler.acquire_gpu_lease(
        task_id="chat_turn_1",
        priority_tier=TIER_1_INTERACTIVE,
        vram_mb=1200.0,
    )
    assert r1["granted"] is True
    assert r1["gpu_owner"] == "chat_turn_1"
    assert r1["context_state"] == STATE_HOT

    state = scheduler.get_scheduler_state()
    assert state["current_gpu_owner"] == "chat_turn_1"

    rel = scheduler.release_gpu_lease("chat_turn_1")
    assert rel["released"] is True
    assert scheduler.get_scheduler_state()["current_gpu_owner"] is None


def test_context_virtualization_swap_cycle(scheduler):
    scheduler.acquire_gpu_lease(
        task_id="complex_math_task",
        priority_tier=TIER_2_BACKGROUND_SWARM,
        payload={"step": 4, "intermediate_matrix": [1.0, 2.0]},
    )

    # Swap out to NVMe SSD
    swap_res = scheduler.swap_out_context("complex_math_task")
    assert swap_res["success"] is True
    assert Path(swap_res["swap_path"]).exists()

    state = scheduler.get_scheduler_state()
    assert state["tasks"]["complex_math_task"]["context_state"] == STATE_WARM

    # Swap in
    in_res = scheduler.swap_in_context("complex_math_task")
    assert in_res["success"] is True
    assert scheduler.get_scheduler_state()["tasks"]["complex_math_task"]["context_state"] == STATE_HOT


def test_priority_preemption_tier0_over_tier2(scheduler):
    # Tier 2 Background task acquires GPU
    scheduler.acquire_gpu_lease(
        task_id="qlora_training",
        priority_tier=TIER_2_BACKGROUND_SWARM,
        payload={"epoch": 1},
    )
    assert scheduler.get_scheduler_state()["current_gpu_owner"] == "qlora_training"

    # Tier 0 Voice interrupt preempts
    preempt_res = scheduler.acquire_gpu_lease(
        task_id="voice_barge_in",
        priority_tier=TIER_0_VOICE_INTERRUPT,
        payload={"audio_energy": 3200},
    )
    assert preempt_res["granted"] is True
    assert preempt_res["gpu_owner"] == "voice_barge_in"
    assert preempt_res["preempted_task"] == "qlora_training"

    # Verify qlora_training was swapped to NVMe
    state = scheduler.get_scheduler_state()
    assert state["tasks"]["qlora_training"]["context_state"] == STATE_WARM
    assert state["current_gpu_owner"] == "voice_barge_in"

    # Releasing voice promotes queued/preempted task back
    scheduler.release_gpu_lease("voice_barge_in")


def test_lower_priority_queued_when_gpu_busy(scheduler):
    scheduler.acquire_gpu_lease("user_query", priority_tier=TIER_1_INTERACTIVE)

    # Lower priority tier 3 tries to acquire -> Queued
    q_res = scheduler.acquire_gpu_lease("db_vacuum", priority_tier=TIER_3_QUIESCENT)
    assert q_res["granted"] is False
    assert q_res["queued"] is True
    assert q_res["queue_position"] == 1
