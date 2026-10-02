"""Unit tests for Multi-Task Concurrency & Hardware Resource Arbiter (Module M)."""
import pytest

from concurrency_arbiter import (
    ConcurrencyArbiter,
    acquire_resource_lock,
    release_resource_lock,
    get_resource_allocation_state,
    PRIORITY_TIER_VOICE_UI,
    PRIORITY_TIER_DELIBERATION,
    PRIORITY_TIER_BACKGROUND,
)


@pytest.fixture
def arbiter():
    return ConcurrencyArbiter(max_vram_mb=5000.0, max_cpu_cores=4)


def test_normal_allocation_and_release(arbiter):
    r1 = arbiter.acquire_resource_lock(
        consumer_id="model_ministral",
        priority_tier=PRIORITY_TIER_DELIBERATION,
        requested_vram_mb=2500.0,
        requested_cpu_cores=2,
    )
    assert r1["granted"] is True
    assert r1["total_active_vram_mb"] == 2500.0

    state = arbiter.get_resource_allocation_state()
    assert state["allocated_vram_mb"] == 2500.0
    assert state["available_vram_mb"] == 2500.0

    rel = arbiter.release_resource_lock("model_ministral")
    assert rel["success"] is True
    assert rel["freed_vram_mb"] == 2500.0


def test_rejection_on_vram_exhaustion(arbiter):
    # Consume 4000 MB
    arbiter.acquire_resource_lock(
        consumer_id="heavy_background_job",
        priority_tier=PRIORITY_TIER_BACKGROUND,
        requested_vram_mb=4000.0,
    )

    # Request another 2000 MB (Total 6000MB > 5000MB limit) at same priority
    r2 = arbiter.acquire_resource_lock(
        consumer_id="another_background_job",
        priority_tier=PRIORITY_TIER_BACKGROUND,
        requested_vram_mb=2000.0,
    )
    assert r2["granted"] is False
    assert r2["reason"] == "resource_exhaustion"


def test_priority_preemption_by_tier1_voice(arbiter):
    # Background job occupies 4500 MB
    arbiter.acquire_resource_lock(
        consumer_id="background_distiller",
        priority_tier=PRIORITY_TIER_BACKGROUND,
        requested_vram_mb=4500.0,
    )

    # Tier 1 Voice requests 1500 MB -> Preempts background job
    voice_res = arbiter.acquire_resource_lock(
        consumer_id="realtime_voice_tts",
        priority_tier=PRIORITY_TIER_VOICE_UI,
        requested_vram_mb=1500.0,
    )
    assert voice_res["granted"] is True
    assert "background_distiller" in voice_res["preempted_consumers"]

    state = arbiter.get_resource_allocation_state()
    assert state["allocated_vram_mb"] == 1500.0
