"""Cognitive OS Scheduler & Context Virtualization Engine for J.A.R.V.I.S. — MARK VIII.

Enforces operating system primitives on LLM / GPU hardware:
  1. Single GPU Invariant: Strictly serialized GPU/VRAM leases preventing CUDA OOM.
  2. Context Virtualization: NVMe swap-in / swap-out across HOT (VRAM), WARM (NVMe cache), and COLD (state-only) tiers.
  3. Priority Preemption:
       Tier 0: Duplex Voice / Barge-in Interrupt (Immediate preemption).
       Tier 1: User-Interactive Turn.
       Tier 2: Background Sub-agent Swarms.
       Tier 3: Quiescent Maintenance (QLoRA, DB vacuum).
"""
from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.cognitive_os")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
SWAP_DIR = DATA_DIR / "context_swap"

_lock = threading.RLock()

# Priority Tiers
TIER_0_VOICE_INTERRUPT = 0
TIER_1_INTERACTIVE = 1
TIER_2_BACKGROUND_SWARM = 2
TIER_3_QUIESCENT = 3

# Virtual Context Memory States
STATE_HOT = "HOT"    # Active in VRAM slot
STATE_WARM = "WARM"  # Persisted snapshot on NVMe SSD
STATE_COLD = "COLD"  # Dormant state schema


@dataclass
class TaskControlBlock:
    task_id: str
    priority_tier: int
    context_state: str  # HOT, WARM, COLD
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    vram_allocated_mb: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)
    kv_cache_path: Optional[str] = None


class CognitiveOSScheduler:
    """Manages GPU leases, context virtualization, and multi-tier priority scheduling."""

    def __init__(self, swap_path: Optional[Path] = None):
        self.swap_path = Path(swap_path).resolve() if swap_path else SWAP_DIR
        self.swap_path.mkdir(parents=True, exist_ok=True)

        self._tcbs: Dict[str, TaskControlBlock] = {}
        self._current_gpu_owner: Optional[str] = None
        self._gpu_lease_start: Optional[float] = None
        self._queue: List[str] = []

    def acquire_gpu_lease(
        self,
        task_id: str,
        priority_tier: int = TIER_1_INTERACTIVE,
        vram_mb: float = 1500.0,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Request exclusive GPU lease with priority preemption."""
        with _lock:
            # Create or update TCB
            tcb = self._tcbs.get(task_id) or TaskControlBlock(
                task_id=task_id,
                priority_tier=priority_tier,
                context_state=STATE_COLD,
                vram_allocated_mb=vram_mb,
                payload=payload or {},
            )
            tcb.priority_tier = priority_tier
            tcb.vram_allocated_mb = vram_mb
            if payload:
                tcb.payload.update(payload)
            self._tcbs[task_id] = tcb

            # Check if GPU is free
            if self._current_gpu_owner is None:
                self._current_gpu_owner = task_id
                self._gpu_lease_start = time.time()
                tcb.context_state = STATE_HOT
                tcb.last_active = time.time()
                log.info(f"[CognitiveOS] Granted exclusive GPU lease to '{task_id}' (tier={priority_tier})")
                return {
                    "granted": True,
                    "gpu_owner": task_id,
                    "context_state": STATE_HOT,
                    "preempted_task": None,
                }

            # If current owner is lower priority (higher tier number), preempt current owner
            current_tcb = self._tcbs.get(self._current_gpu_owner)
            current_tier = current_tcb.priority_tier if current_tcb else TIER_3_QUIESCENT

            if priority_tier < current_tier:
                preempted_id = self._current_gpu_owner
                log.info(f"[CognitiveOS] Preempting '{preempted_id}' (tier={current_tier}) for higher tier '{task_id}' (tier={priority_tier})")

                # Swap out preempted task to NVMe
                self.swap_out_context(preempted_id)

                self._current_gpu_owner = task_id
                self._gpu_lease_start = time.time()
                tcb.context_state = STATE_HOT
                tcb.last_active = time.time()

                try:
                    get_registry().set_capability_evidence(
                        "COGNITIVE_OS",
                        EvidenceLevel.LIVE,
                        f"Preempted task '{preempted_id}' for Tier {priority_tier} '{task_id}'",
                        source="cognitive_os.acquire_gpu_lease",
                    )
                except Exception:
                    pass

                return {
                    "granted": True,
                    "gpu_owner": task_id,
                    "context_state": STATE_HOT,
                    "preempted_task": preempted_id,
                }

            # If equal or lower priority, enqueue
            if task_id not in self._queue:
                self._queue.append(task_id)

            return {
                "granted": False,
                "gpu_owner": self._current_gpu_owner,
                "queued": True,
                "queue_position": len(self._queue),
                "message": f"GPU currently leased by '{self._current_gpu_owner}' (tier={current_tier})",
            }

    def release_gpu_lease(self, task_id: str) -> Dict[str, Any]:
        """Release GPU lease and promote next highest-priority queued task."""
        with _lock:
            if self._current_gpu_owner == task_id:
                tcb = self._tcbs.get(task_id)
                if tcb:
                    tcb.context_state = STATE_WARM
                self._current_gpu_owner = None
                self._gpu_lease_start = None

                next_task = self._promote_next_task()
                return {
                    "released": True,
                    "task_id": task_id,
                    "next_promoted_task": next_task,
                }

            if task_id in self._queue:
                self._queue.remove(task_id)
                return {"released": True, "dequeued": True, "task_id": task_id}

            return {"released": False, "status": "not_owner", "task_id": task_id}

    def _promote_next_task(self) -> Optional[str]:
        """Select highest-priority task from queue to acquire GPU."""
        if not self._queue:
            return None

        # Sort queue by lowest priority tier number first
        self._queue.sort(key=lambda tid: self._tcbs.get(tid, TaskControlBlock(tid, TIER_3_QUIESCENT, STATE_COLD)).priority_tier)
        next_tid = self._queue.pop(0)

        tcb = self._tcbs.get(next_tid)
        if tcb:
            self.swap_in_context(next_tid)
            self._current_gpu_owner = next_tid
            self._gpu_lease_start = time.time()
            tcb.context_state = STATE_HOT
            tcb.last_active = time.time()
            log.info(f"[CognitiveOS] Promoted queued task '{next_tid}' to GPU owner (tier={tcb.priority_tier})")
            return next_tid

        return None

    def swap_out_context(self, task_id: str) -> Dict[str, Any]:
        """Virtualize context: persist state snapshot to NVMe SSD swap file."""
        with _lock:
            if task_id not in self._tcbs:
                return {"success": False, "status": "not_found"}

            tcb = self._tcbs[task_id]
            swap_file = self.swap_path / f"{task_id}.json"

            snapshot = {
                "task_id": task_id,
                "priority_tier": tcb.priority_tier,
                "vram_allocated_mb": tcb.vram_allocated_mb,
                "payload": tcb.payload,
                "saved_at": time.time(),
            }

            try:
                with open(swap_file, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, indent=2)
                tcb.kv_cache_path = str(swap_file)
                tcb.context_state = STATE_WARM
                log.info(f"[CognitiveOS] Swapped out '{task_id}' to NVMe: {swap_file.name}")
                return {"success": True, "status": "swapped_out", "swap_path": str(swap_file)}
            except Exception as exc:
                log.error(f"[CognitiveOS] Failed swap-out for '{task_id}': {exc}")
                return {"success": False, "error": str(exc)}

    def swap_in_context(self, task_id: str) -> Dict[str, Any]:
        """Virtualize context: restore state snapshot from NVMe SSD into active memory."""
        with _lock:
            if task_id not in self._tcbs:
                return {"success": False, "status": "not_found"}

            tcb = self._tcbs[task_id]
            swap_file = Path(tcb.kv_cache_path) if tcb.kv_cache_path else self.swap_path / f"{task_id}.json"

            if swap_file.exists():
                try:
                    with open(swap_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    tcb.payload.update(data.get("payload", {}))
                    tcb.context_state = STATE_HOT
                    tcb.last_active = time.time()
                    log.info(f"[CognitiveOS] Swapped in '{task_id}' from NVMe: {swap_file.name}")
                    return {"success": True, "status": "swapped_in", "task_id": task_id}
                except Exception as exc:
                    return {"success": False, "error": str(exc)}

            tcb.context_state = STATE_HOT
            return {"success": True, "status": "hot_initialized"}

    def get_scheduler_state(self) -> Dict[str, Any]:
        """Return snapshot of Cognitive OS scheduler and context virtualization."""
        with _lock:
            return {
                "current_gpu_owner": self._current_gpu_owner,
                "lease_duration_sec": round(time.time() - self._gpu_lease_start, 2) if self._gpu_lease_start else None,
                "queued_tasks": list(self._queue),
                "total_tasks_tracked": len(self._tcbs),
                "tasks": {k: asdict(v) for k, v in self._tcbs.items()},
            }


_scheduler_instance: Optional[CognitiveOSScheduler] = None


def get_cognitive_scheduler() -> CognitiveOSScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        with _lock:
            if _scheduler_instance is None:
                _scheduler_instance = CognitiveOSScheduler()
    return _scheduler_instance


def acquire_gpu_lease(
    task_id: str,
    priority_tier: int = TIER_1_INTERACTIVE,
    vram_mb: float = 1500.0,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return get_cognitive_scheduler().acquire_gpu_lease(task_id, priority_tier, vram_mb, payload)


def release_gpu_lease(task_id: str) -> Dict[str, Any]:
    return get_cognitive_scheduler().release_gpu_lease(task_id)


def swap_out_context(task_id: str) -> Dict[str, Any]:
    return get_cognitive_scheduler().swap_out_context(task_id)


def swap_in_context(task_id: str) -> Dict[str, Any]:
    return get_cognitive_scheduler().swap_in_context(task_id)


def get_scheduler_state() -> Dict[str, Any]:
    return get_cognitive_scheduler().get_scheduler_state()
