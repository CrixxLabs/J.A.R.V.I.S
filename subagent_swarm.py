"""Concurrent Sub-Agent Swarm & Async Worker Pool for J.A.R.V.I.S. — MARK VIII.

Enables non-blocking background autonomous execution:
  1. Thread pool executor for long-horizon parallel sub-agents.
  2. Subagent lifecycle tracking (QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED).
  3. Safe cancellation and result aggregation.
  4. Real-time telemetry reporting to Global Workspace and Status Registry.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

import global_workspace
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.subagent_swarm")

_lock = threading.RLock()


@dataclass
class SubagentTask:
    task_id: str
    objective: str
    params: Dict[str, Any]
    status: str  # "queued", "running", "completed", "failed", "cancelled"
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    progress: float = 0.0


class SubagentSwarmPool:
    """Manages thread-safe concurrent sub-agent worker execution."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="subagent")
        self._tasks: Dict[str, SubagentTask] = {}
        self._futures: Dict[str, concurrent.futures.Future] = {}
        self._cancel_flags: Dict[str, threading.Event] = {}

    def spawn_subagent(
        self,
        task_id: str,
        objective: str,
        params: Optional[Dict[str, Any]] = None,
        fn: Optional[Callable[[Dict[str, Any], threading.Event], Any]] = None,
    ) -> Dict[str, Any]:
        """Spawn a non-blocking background sub-agent worker."""
        p = params or {}
        cancel_event = threading.Event()

        task = SubagentTask(
            task_id=task_id,
            objective=objective,
            params=p,
            status="queued",
        )

        with _lock:
            self._tasks[task_id] = task
            self._cancel_flags[task_id] = cancel_event

            future = self._executor.submit(self._worker_wrapper, task_id, fn, p, cancel_event)
            self._futures[task_id] = future

        log.info(f"[SubagentSwarm] Spawned subagent '{task_id}': {objective}")

        try:
            get_registry().set_capability_evidence(
                "SUBAGENT_SWARM",
                EvidenceLevel.LIVE,
                f"Spawned subagent [{task_id}] objective: {objective[:40]}",
                source="subagent_swarm.spawn_subagent",
            )
        except Exception:
            pass

        return asdict(task)

    def _worker_wrapper(
        self,
        task_id: str,
        fn: Optional[Callable[[Dict[str, Any], threading.Event], Any]],
        params: Dict[str, Any],
        cancel_event: threading.Event,
    ) -> Any:
        with _lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = "running"
                self._tasks[task_id].started_at = time.time()

        try:
            if cancel_event.is_set():
                with _lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].status = "cancelled"
                        self._tasks[task_id].completed_at = time.time()
                return {"status": "cancelled"}

            # Execute payload
            if fn:
                res = fn(params, cancel_event)
            else:
                # Default mock simulation
                time.sleep(0.05)
                res = {"status": "success", "summary": f"Completed {task_id}"}

            with _lock:
                if task_id in self._tasks:
                    if cancel_event.is_set():
                        self._tasks[task_id].status = "cancelled"
                    else:
                        self._tasks[task_id].status = "completed"
                        self._tasks[task_id].result = res
                        self._tasks[task_id].progress = 1.0
                    self._tasks[task_id].completed_at = time.time()

            return res

        except Exception as exc:
            log.error(f"[SubagentSwarm] Subagent '{task_id}' crashed: {exc}")
            with _lock:
                if task_id in self._tasks:
                    self._tasks[task_id].status = "failed"
                    self._tasks[task_id].error = str(exc)
                    self._tasks[task_id].completed_at = time.time()
            return {"status": "failed", "error": str(exc)}

    def get_subagent_status(self, task_id: str) -> Dict[str, Any]:
        """Query state and output of specific subagent."""
        with _lock:
            if task_id not in self._tasks:
                return {"success": False, "status": "not_found", "message": f"Subagent {task_id} not found."}
            return {"success": True, **asdict(self._tasks[task_id])}

    def cancel_subagent(self, task_id: str) -> Dict[str, Any]:
        """Request immediate cancellation of a running subagent."""
        with _lock:
            if task_id not in self._tasks:
                return {"success": False, "status": "not_found"}

            if task_id in self._cancel_flags:
                self._cancel_flags[task_id].set()

            if task_id in self._futures:
                self._futures[task_id].cancel()

            self._tasks[task_id].status = "cancelled"
            self._tasks[task_id].completed_at = time.time()

        log.info(f"[SubagentSwarm] Cancelled subagent '{task_id}'")
        return {"success": True, "status": "cancelled", "task_id": task_id}

    def list_active_subagents(self) -> List[Dict[str, Any]]:
        """List all currently active (queued or running) subagents."""
        with _lock:
            return [
                asdict(t) for t in self._tasks.values()
                if t.status in ("queued", "running")
            ]

    def wait_for_subagent(self, task_id: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Wait for subagent completion up to timeout."""
        with _lock:
            fut = self._futures.get(task_id)

        if fut:
            try:
                fut.result(timeout=timeout)
            except Exception:
                pass

        return self.get_subagent_status(task_id)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)


_swarm_pool_instance: Optional[SubagentSwarmPool] = None


def get_subagent_swarm() -> SubagentSwarmPool:
    global _swarm_pool_instance
    if _swarm_pool_instance is None:
        with _lock:
            if _swarm_pool_instance is None:
                _swarm_pool_instance = SubagentSwarmPool()
    return _swarm_pool_instance


def spawn_subagent(
    task_id: str,
    objective: str,
    params: Optional[Dict[str, Any]] = None,
    fn: Optional[Callable[[Dict[str, Any], threading.Event], Any]] = None,
) -> Dict[str, Any]:
    return get_subagent_swarm().spawn_subagent(task_id, objective, params, fn)


def get_subagent_status(task_id: str) -> Dict[str, Any]:
    return get_subagent_swarm().get_subagent_status(task_id)


def cancel_subagent(task_id: str) -> Dict[str, Any]:
    return get_subagent_swarm().cancel_subagent(task_id)


def list_active_subagents() -> List[Dict[str, Any]]:
    return get_subagent_swarm().list_active_subagents()


def wait_for_subagent(task_id: str, timeout: float = 5.0) -> Dict[str, Any]:
    return get_subagent_swarm().wait_for_subagent(task_id, timeout)
