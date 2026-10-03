"""Lifelong Growth, Probe Rotation & Compaction for J.A.R.V.I.S. — MARK VIII.

Workstream 1 (Continuation):
  1. Golden Probe Rotator (Goodharting Defense):
     - Maintains active and held-out probe partitions.
     - Periodically rotates partitions to prevent policy drift / over-tuning on static probes.
  2. Lifelong Growth & Compaction:
     - Enforces retention rules: compacts event logs older than configured hours (default 720h).
     - Rolls overflowing BLAKE2b audit chains into a signed COMPACTED_ANCHOR block.
  3. Accelerated-Aging Simulation:
     - Simulates months of operational events in seconds to verify compaction stability.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("jarvis.lifelong_compactor")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 1. Golden Probe Rotator (Goodharting Defense)
# ---------------------------------------------------------------------------

@dataclass
class GoldenProbe:
    probe_id: str
    prompt: str
    baseline_response: str
    is_active: bool = True
    eval_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GoldenProbeRotator:
    """Manages active vs. held-out partitions of behavioral golden probes with periodic rotation."""

    def __init__(self, initial_probes: Optional[List[GoldenProbe]] = None, active_ratio: float = 0.60):
        self.active_ratio = active_ratio
        self._probes: Dict[str, GoldenProbe] = {}
        if initial_probes:
            for p in initial_probes:
                self._probes[p.probe_id] = p
            self._partition_probes()

    def add_probe(self, probe_id: str, prompt: str, baseline_response: str) -> GoldenProbe:
        with _lock:
            probe = GoldenProbe(probe_id=probe_id, prompt=prompt, baseline_response=baseline_response)
            self._probes[probe_id] = probe
            self._partition_probes()
            return probe

    def _partition_probes(self) -> None:
        probe_list = list(self._probes.values())
        k_active = max(1, int(len(probe_list) * self.active_ratio))
        for idx, p in enumerate(probe_list):
            p.is_active = idx < k_active

    def rotate_partitions(self, rng_seed: Optional[int] = None) -> Tuple[int, int]:
        """Rotate probes between active and held-out partitions to avoid Goodharting."""
        with _lock:
            probe_list = list(self._probes.values())
            if len(probe_list) < 2:
                return len(probe_list), 0

            rng = random.Random(rng_seed)
            rng.shuffle(probe_list)
            k_active = max(1, int(len(probe_list) * self.active_ratio))

            for idx, p in enumerate(probe_list):
                p.is_active = idx < k_active

            active_cnt = sum(1 for p in self._probes.values() if p.is_active)
            held_out_cnt = len(self._probes) - active_cnt
            log.info(f"[GoldenProbeRotator] Probes rotated: {active_cnt} active, {held_out_cnt} held-out")
            return active_cnt, held_out_cnt

    def get_active_probes(self) -> List[GoldenProbe]:
        with _lock:
            return [p for p in self._probes.values() if p.is_active]

    def get_held_out_probes(self) -> List[GoldenProbe]:
        with _lock:
            return [p for p in self._probes.values() if not p.is_active]


# ---------------------------------------------------------------------------
# 2. Lifelong Growth & Audit Compactor
# ---------------------------------------------------------------------------

@dataclass
class CompactedAnchorBlock:
    anchor_id: str
    start_timestamp: float
    end_timestamp: float
    event_count: int
    summary_root_hash: str
    signature: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CompactionAgingReport:
    total_simulated_days: int
    total_events_generated: int
    events_retained_in_hot_log: int
    events_compacted_into_anchors: int
    total_anchors_created: int
    compression_ratio: float
    simulation_duration_sec: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LifelongGrowthCompactor:
    """Compacts historical logs and rolls hash chains into signed COMPACTED_ANCHOR blocks."""

    def __init__(self, retention_hours: float = 720.0, anchor_batch_size: int = 1000):
        self.retention_hours = retention_hours
        self.anchor_batch_size = anchor_batch_size
        self._anchors: List[CompactedAnchorBlock] = []
        self._hot_event_log: List[Dict[str, Any]] = []

    def ingest_event(self, event_type: str, payload: Dict[str, Any], timestamp: Optional[float] = None) -> None:
        t_now = time.time() if timestamp is None else timestamp
        with _lock:
            self._hot_event_log.append({
                "event_id": f"ev_{uuid.uuid4().hex[:8]}",
                "event_type": event_type,
                "payload": payload,
                "timestamp": t_now,
            })

    def run_compaction(self, current_time: Optional[float] = None) -> Optional[CompactedAnchorBlock]:
        """Roll logs older than retention_hours into a signed COMPACTED_ANCHOR block."""
        t_now = time.time() if current_time is None else current_time
        retention_threshold = t_now - (self.retention_hours * 3600.0)

        with _lock:
            # Partition hot vs cold events
            cold_events = [e for e in self._hot_event_log if e["timestamp"] < retention_threshold]
            if not cold_events:
                return None

            # Keep only hot events
            self._hot_event_log = [e for e in self._hot_event_log if e["timestamp"] >= retention_threshold]

            # Compute summary root hash over cold events
            hasher = hashlib.blake2b(digest_size=32)
            for ev in cold_events:
                ev_bytes = json.dumps(ev, sort_keys=True).encode("utf-8")
                hasher.update(ev_bytes)
            root_hash = hasher.hexdigest()

            # Sign with simulated anchor signature
            anchor_id = f"anchor_{uuid.uuid4().hex[:8]}"
            sig = hashlib.sha256(f"ED25519_ANCHOR_SIG::{anchor_id}::{root_hash}".encode("utf-8")).hexdigest()

            anchor = CompactedAnchorBlock(
                anchor_id=anchor_id,
                start_timestamp=cold_events[0]["timestamp"],
                end_timestamp=cold_events[-1]["timestamp"],
                event_count=len(cold_events),
                summary_root_hash=root_hash,
                signature=sig,
                created_at=t_now,
            )
            self._anchors.append(anchor)
            log.info(f"[LifelongCompactor] Created {anchor_id}: compacted {len(cold_events)} events into summary hash {root_hash[:12]}...")
            return anchor

    def simulate_accelerated_aging(
        self,
        total_months: int = 6,
        events_per_day: int = 100,
    ) -> CompactionAgingReport:
        """Simulate months of operations in seconds to verify stability under lifelong growth."""
        start_time = time.time()
        sim_start_time = 1700000000.0  # Synthetic base timestamp
        total_days = total_months * 30
        total_events = total_days * events_per_day

        with _lock:
            self._hot_event_log.clear()
            self._anchors.clear()

            # Generate events across simulated days
            for d in range(total_days):
                day_time = sim_start_time + (d * 86400.0)
                for i in range(events_per_day):
                    ev_time = day_time + (i * 864.0)
                    self.ingest_event("tool_call", {"day": d, "index": i}, timestamp=ev_time)

                # Run weekly compaction check
                if d % 7 == 0:
                    self.run_compaction(current_time=day_time + 86400.0)

            # Final compaction pass
            final_time = sim_start_time + (total_days * 86400.0)
            self.run_compaction(current_time=final_time)

            hot_count = len(self._hot_event_log)
            compacted_count = sum(a.event_count for a in self._anchors)
            total_compacted_anchors = len(self._anchors)

            compression_ratio = (
                float(total_events) / float(hot_count + total_compacted_anchors)
                if (hot_count + total_compacted_anchors) > 0
                else 1.0
            )

        duration = time.time() - start_time
        return CompactionAgingReport(
            total_simulated_days=total_days,
            total_events_generated=total_events,
            events_retained_in_hot_log=hot_count,
            events_compacted_into_anchors=compacted_count,
            total_anchors_created=total_compacted_anchors,
            compression_ratio=round(compression_ratio, 2),
            simulation_duration_sec=round(duration, 4),
        )


# ---------------------------------------------------------------------------
# Global Singleton
# ---------------------------------------------------------------------------

_golden_probe_rotator = GoldenProbeRotator()
_lifelong_compactor = LifelongGrowthCompactor()


def get_golden_probe_rotator() -> GoldenProbeRotator:
    return _golden_probe_rotator


def get_lifelong_compactor() -> LifelongGrowthCompactor:
    return _lifelong_compactor
