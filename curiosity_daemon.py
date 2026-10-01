"""Autonomous Curiosity and Self-Directed Diagnostic Daemon for J.A.R.V.I.S. — MARK VIII.

Operates during quiescent periods (> 20 min idle, CPU < 25%) to:
  1. Audit capability gaps, degraded evidence states, and unverified action paths in status_registry.py.
  2. Formulate diagnostic hypotheses on root causes and requirements.
  3. Execute safe pre-flight/file diagnostics to verify subsystem dependencies.
  4. Synthesize and persist relational discoveries into cognitive_graph.py.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cognitive_graph
import error_handler
import psutil
from status_registry import EvidenceLevel, SubsystemState, get_registry

DEFAULT_IDLE_QUIESCENCE_SECONDS = 1200.0  # 20 minutes
DEFAULT_MAX_CPU_PERCENT = 25.0


class CuriosityDaemon:
    """Background autonomous daemon for idle gap analysis and knowledge graph synthesis."""

    def __init__(
        self,
        idle_threshold: float = DEFAULT_IDLE_QUIESCENCE_SECONDS,
        max_cpu_percent: float = DEFAULT_MAX_CPU_PERCENT,
        scan_interval: float = 60.0,
    ):
        self.idle_threshold = idle_threshold
        self.max_cpu_percent = max_cpu_percent
        self.scan_interval = scan_interval
        self._last_user_activity = time.time()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._learnings_count = 0

    def mark_user_activity(self) -> None:
        """Reset the quiescence timer whenever the user or primary agent interacts."""
        with self._lock:
            self._last_user_activity = time.time()

    def get_idle_duration(self) -> float:
        """Get seconds elapsed since last user interaction."""
        with self._lock:
            return time.time() - self._last_user_activity

    def is_quiescent(self) -> Tuple[bool, str]:
        """Check whether system is sufficiently idle and quiescent for curiosity sweeps."""
        idle_sec = self.get_idle_duration()
        if idle_sec < self.idle_threshold:
            return False, f"Active recently ({idle_sec:.1f}s < {self.idle_threshold:.1f}s)"

        try:
            cpu = psutil.cpu_percent(interval=None)
            if cpu > self.max_cpu_percent:
                return False, f"CPU load high ({cpu:.1f}% > {self.max_cpu_percent:.1f}%)"
        except Exception:
            pass

        return True, "Quiescent"

    def probe_capability_gaps(self) -> List[Dict[str, Any]]:
        """Audit status_registry for capabilities requiring diagnostic probing."""
        registry = get_registry()
        caps = registry.get_capabilities()
        gaps: List[Dict[str, Any]] = []

        # Target capabilities that are unverified, broken, degraded, or code-only
        target_evidences = {
            EvidenceLevel.UNKNOWN.value,
            EvidenceLevel.CODE.value,
            EvidenceLevel.BROKEN.value,
            EvidenceLevel.BLOCKED.value,
            EvidenceLevel.PROBED.value,
        }

        for info in caps:
            if not info:
                continue
            cap_id = info.get("id", "")
            ev = info.get("evidence", "UNKNOWN")
            if ev in target_evidences:
                gaps.append({
                    "capability_id": cap_id,
                    "name": info.get("name", cap_id),
                    "evidence": ev,
                    "reason": info.get("detail", "") or info.get("reason", ""),
                    "code_paths": info.get("code_paths", []),
                    "actions": info.get("actions", []),
                    "dependencies": info.get("dependencies", []),
                })

        return gaps

    def execute_diagnostic_probe(self, gap: Dict[str, Any]) -> Dict[str, Any]:
        """Execute safe, non-destructive file and dependency verification for a capability gap."""
        cap_id = gap["capability_id"]
        code_paths = gap.get("code_paths", [])
        dependencies = gap.get("dependencies", [])

        file_statuses = {}
        for p in code_paths:
            path_obj = Path(p)
            file_statuses[p] = {
                "exists": path_obj.exists(),
                "is_file": path_obj.is_file() if path_obj.exists() else False,
            }

        dep_statuses = {}
        registry = get_registry()
        for dep in dependencies:
            if dep.startswith("cap:"):
                d_id = dep.split("cap:")[1]
                dep_info = registry.get_capability(d_id)
                dep_statuses[dep] = dep_info.get("evidence", "UNKNOWN") if dep_info else "MISSING"
            elif dep.startswith("file:"):
                f_path = dep.split("file:")[1]
                dep_statuses[dep] = "PRESENT" if Path(f_path).exists() else "MISSING"
            elif dep.startswith("env:"):
                e_var = dep.split("env:")[1]
                dep_statuses[dep] = "CONFIGURED" if os.environ.get(e_var) else "UNSET"

        all_files_ok = all(f["exists"] for f in file_statuses.values()) if file_statuses else True
        all_deps_ok = all(s in ("LIVE", "PROBED", "CONFIGURED", "PRESENT") for s in dep_statuses.values()) if dep_statuses else True

        diagnosis = "VERIFIED_PASS" if (all_files_ok and all_deps_ok) else "GAPS_DETECTED"

        return {
            "capability_id": cap_id,
            "diagnosis": diagnosis,
            "file_statuses": file_statuses,
            "dep_statuses": dep_statuses,
            "timestamp": time.time(),
        }

    def commit_learning_to_graph(self, gap: Dict[str, Any], probe_res: Dict[str, Any]) -> bool:
        """Persist synthesized structural triples into cognitive_graph.py."""
        cap_id = gap["capability_id"]
        diagnosis = probe_res["diagnosis"]

        try:
            # 1. State triple
            cognitive_graph.add_triple(
                subject=f"Capability:{cap_id}",
                predicate="diagnostic_status",
                obj=diagnosis,
                confidence=0.95,
                source="curiosity_daemon",
            )

            # 2. Dependency triples
            for dep, status in probe_res.get("dep_statuses", {}).items():
                cognitive_graph.add_triple(
                    subject=f"Capability:{cap_id}",
                    predicate=f"dependency_{status.lower()}",
                    obj=dep,
                    confidence=0.90,
                    source="curiosity_daemon",
                )

            self._learnings_count += 1
            return True
        except Exception as exc:
            print(f"[CuriosityDaemon] Failed committing learning for {cap_id}: {exc}")
            return False

    def run_curiosity_cycle(self, force: bool = False) -> Dict[str, Any]:
        """Perform a single autonomous curiosity audit cycle."""
        if not force:
            quiescent, reason = self.is_quiescent()
            if not quiescent:
                return {"status": "skipped", "reason": reason, "learnings_committed": 0}

        registry = get_registry()
        gaps = self.probe_capability_gaps()
        diagnostics = []
        committed = 0

        for gap in gaps[:5]:  # Bound work per cycle to prevent latency spikes
            try:
                probe_res = self.execute_diagnostic_probe(gap)
                diagnostics.append(probe_res)
                if self.commit_learning_to_graph(gap, probe_res):
                    committed += 1
            except Exception as exc:
                error_handler.log_and_demote(
                    "CURIOSITY_DAEMON",
                    exc,
                    f"Curiosity probe for {gap.get('capability_id')}",
                    SubsystemState.DEGRADED,
                )

        registry.set_capability_evidence(
            "CURIOSITY_DAEMON",
            EvidenceLevel.LIVE,
            f"Curiosity cycle completed: {len(diagnostics)} probed, {committed} learnings stored",
            source="curiosity daemon",
        )

        return {
            "status": "completed",
            "probed_count": len(diagnostics),
            "learnings_committed": committed,
            "diagnostics": diagnostics,
        }

    def _daemon_loop(self) -> None:
        """Internal background thread worker."""
        while self._running:
            try:
                self.run_curiosity_cycle(force=False)
            except Exception as exc:
                print(f"[CuriosityDaemon] Loop exception: {exc}")
            time.sleep(self.scan_interval)

    def start(self) -> bool:
        """Start the curiosity daemon thread."""
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._thread = threading.Thread(
                target=self._daemon_loop,
                name="JarvisCuriosityDaemon",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        """Stop the curiosity daemon thread."""
        with self._lock:
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            self._thread = None

    def is_running(self) -> bool:
        """Return True if background daemon is active."""
        with self._lock:
            return self._running and (self._thread is not None and self._thread.is_alive())


_daemon_instance: Optional[CuriosityDaemon] = None
_daemon_lock = threading.Lock()


def get_daemon() -> CuriosityDaemon:
    """Retrieve or initialize the global singleton CuriosityDaemon."""
    global _daemon_instance
    with _daemon_lock:
        if _daemon_instance is None:
            _daemon_instance = CuriosityDaemon()
        return _daemon_instance


def start_curiosity_daemon() -> bool:
    """Start global curiosity daemon."""
    return get_daemon().start()


def stop_curiosity_daemon() -> None:
    """Stop global curiosity daemon."""
    get_daemon().stop()


def run_curiosity_cycle(force: bool = False) -> Dict[str, Any]:
    """Execute a single curiosity diagnostic sweep."""
    return get_daemon().run_curiosity_cycle(force=force)
