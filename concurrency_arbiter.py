"""Multi-Task Concurrency & Hardware Resource Arbiter for J.A.R.V.I.S. — MARK VIII.

Enforces execution priority and hardware constraints on RTX 3050 (6GB VRAM / CPU):
  - Hard limit: Peak concurrent VRAM consumption < 5000 MB (5.0 GB).
  - Tier 1 (Voice / UI): Highest priority, instant preemption of lower tiers.
  - Tier 2 (Deliberation / Reasoning): High priority.
  - Tier 3 (Background Swarm / Synthesis): Yields VRAM/CPU when higher tiers demand headroom.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.concurrency_arbiter")

_lock = threading.RLock()

# Hardware boundary constraints
MAX_CONCURRENT_VRAM_MB = 5000.0  # 5.0 GB strict ceiling for RTX 3050 (6GB)
MAX_CONCURRENT_CPU_CORES = 4

PRIORITY_TIER_VOICE_UI = 1       # Real-time Interactive
PRIORITY_TIER_DELIBERATION = 2   # Reasoning & Planning
PRIORITY_TIER_BACKGROUND = 3     # Swarms, Synthesis, Training


@dataclass
class ResourceAllocation:
    consumer_id: str
    priority_tier: int
    vram_mb: float
    cpu_cores: int
    allocated_at: float = field(default_factory=time.time)


class ConcurrencyArbiter:
    """Arbiter regulating resource access across cognitive subsystems."""

    def __init__(self, max_vram_mb: float = MAX_CONCURRENT_VRAM_MB, max_cpu_cores: int = MAX_CONCURRENT_CPU_CORES):
        self.max_vram_mb = max_vram_mb
        self.max_cpu_cores = max_cpu_cores
        self._allocations: Dict[str, ResourceAllocation] = {}

    def get_current_usage(self) -> Tuple[float, int]:
        """Compute current active allocated VRAM (MB) and CPU cores."""
        with _lock:
            total_vram = sum(a.vram_mb for a in self._allocations.values())
            total_cpu = sum(a.cpu_cores for a in self._allocations.values())
            return total_vram, total_cpu

    def acquire_resource_lock(
        self,
        consumer_id: str,
        priority_tier: int = PRIORITY_TIER_BACKGROUND,
        requested_vram_mb: float = 500.0,
        requested_cpu_cores: int = 1,
    ) -> Dict[str, Any]:
        """Attempt to acquire hardware resource allocation lock."""
        with _lock:
            current_vram, current_cpu = self.get_current_usage()

            # Check if within bounds
            if (
                current_vram + requested_vram_mb <= self.max_vram_mb
                and current_cpu + requested_cpu_cores <= self.max_cpu_cores
            ):
                alloc = ResourceAllocation(
                    consumer_id=consumer_id,
                    priority_tier=priority_tier,
                    vram_mb=requested_vram_mb,
                    cpu_cores=requested_cpu_cores,
                )
                self._allocations[consumer_id] = alloc
                log.info(f"[ConcurrencyArbiter] Allocated {requested_vram_mb}MB to '{consumer_id}' (tier={priority_tier})")
                return {
                    "granted": True,
                    "consumer_id": consumer_id,
                    "allocated_vram_mb": requested_vram_mb,
                    "total_active_vram_mb": current_vram + requested_vram_mb,
                    "preempted_consumers": [],
                }

            # If requesting higher priority (e.g. Tier 1 Voice), preempt lower tier background tasks
            if priority_tier < PRIORITY_TIER_BACKGROUND:
                preempted: List[str] = []
                # Sort existing allocations by lowest priority (highest tier number) first
                candidates = sorted(
                    [a for a in self._allocations.values() if a.priority_tier > priority_tier],
                    key=lambda x: x.priority_tier,
                    reverse=True,
                )

                freed_vram = 0.0
                freed_cpu = 0
                for c in candidates:
                    preempted.append(c.consumer_id)
                    freed_vram += c.vram_mb
                    freed_cpu += c.cpu_cores
                    del self._allocations[c.consumer_id]

                    if (
                        current_vram - freed_vram + requested_vram_mb <= self.max_vram_mb
                        and current_cpu - freed_cpu + requested_cpu_cores <= self.max_cpu_cores
                    ):
                        break

                if (
                    current_vram - freed_vram + requested_vram_mb <= self.max_vram_mb
                    and current_cpu - freed_cpu + requested_cpu_cores <= self.max_cpu_cores
                ):
                    alloc = ResourceAllocation(
                        consumer_id=consumer_id,
                        priority_tier=priority_tier,
                        vram_mb=requested_vram_mb,
                        cpu_cores=requested_cpu_cores,
                    )
                    self._allocations[consumer_id] = alloc
                    log.info(f"[ConcurrencyArbiter] Preempted {preempted} for high-priority '{consumer_id}'")

                    try:
                        get_registry().set_capability_evidence(
                            "CONCURRENCY_ARBITER",
                            EvidenceLevel.LIVE,
                            f"Allocated {requested_vram_mb}MB (preempted {len(preempted)})",
                            source="concurrency_arbiter.acquire_resource_lock",
                        )
                    except Exception:
                        pass

                    return {
                        "granted": True,
                        "consumer_id": consumer_id,
                        "allocated_vram_mb": requested_vram_mb,
                        "preempted_consumers": preempted,
                    }

            # Insufficient capacity and cannot preempt
            return {
                "granted": False,
                "consumer_id": consumer_id,
                "reason": "resource_exhaustion",
                "current_vram_mb": current_vram,
                "max_vram_mb": self.max_vram_mb,
                "message": f"Requested {requested_vram_mb}MB exceeds remaining budget ({self.max_vram_mb - current_vram}MB free)",
            }

    def release_resource_lock(self, consumer_id: str) -> Dict[str, Any]:
        """Release allocation lock and free VRAM/CPU headroom."""
        with _lock:
            if consumer_id in self._allocations:
                alloc = self._allocations.pop(consumer_id)
                log.info(f"[ConcurrencyArbiter] Released {alloc.vram_mb}MB from '{consumer_id}'")
                return {"success": True, "freed_vram_mb": alloc.vram_mb, "consumer_id": consumer_id}
            return {"success": False, "status": "not_found", "consumer_id": consumer_id}

    def get_resource_allocation_state(self) -> Dict[str, Any]:
        """Return snapshot of current hardware allocation."""
        with _lock:
            total_vram, total_cpu = self.get_current_usage()
            return {
                "max_vram_mb": self.max_vram_mb,
                "allocated_vram_mb": total_vram,
                "available_vram_mb": max(0.0, self.max_vram_mb - total_vram),
                "max_cpu_cores": self.max_cpu_cores,
                "allocated_cpu_cores": total_cpu,
                "active_allocations": [asdict(a) for a in self._allocations.values()],
            }


_arbiter_instance: Optional[ConcurrencyArbiter] = None


def get_concurrency_arbiter() -> ConcurrencyArbiter:
    global _arbiter_instance
    if _arbiter_instance is None:
        with _lock:
            if _arbiter_instance is None:
                _arbiter_instance = ConcurrencyArbiter()
    return _arbiter_instance


def acquire_resource_lock(
    consumer_id: str,
    priority_tier: int = PRIORITY_TIER_BACKGROUND,
    requested_vram_mb: float = 500.0,
    requested_cpu_cores: int = 1,
) -> Dict[str, Any]:
    return get_concurrency_arbiter().acquire_resource_lock(
        consumer_id, priority_tier, requested_vram_mb, requested_cpu_cores
    )


def release_resource_lock(consumer_id: str) -> Dict[str, Any]:
    return get_concurrency_arbiter().release_resource_lock(consumer_id)


def get_resource_allocation_state() -> Dict[str, Any]:
    return get_concurrency_arbiter().get_resource_allocation_state()
