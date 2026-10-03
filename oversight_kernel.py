"""Oversight, Provenance & Reversibility Kernel for J.A.R.V.I.S. — MARK VIII.

Module AJ:
  1. Cryptographic BLAKE2b Audit Chain:
     - h_t = BLAKE2b(h_{t-1} || event_bytes) stored in SQLite.
     - Tamper-evident ledger with verify_chain_integrity() verification.
  2. CUSUM / EWMA Statistical Process Control:
     - Monitors mutation velocities (tool calls, external messages, file mutations).
     - Auto-demotes autonomy tier upon drift or out-of-control threshold breach.
  3. Dead-Man Switch:
     - Downgrades autonomy tier to READ_ONLY safe mode if user check-in lapses
       beyond the configured threshold.
  4. Rubber-Stamp Approval Defense:
     - Analyzes human confirmation latency against cognitive reading limits
       (words per minute vs approval duration) for high-stakes actions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.oversight_kernel")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
AUDIT_DB_PATH = DATA_DIR / "oversight_audit_chain.db"

_lock = threading.RLock()

# Autonomy Tiers
TIER_FULL_AUTONOMY = "FULL_AUTONOMY"
TIER_SUPERVISED = "SUPERVISED"
TIER_HUMAN_IN_THE_LOOP = "HUMAN_IN_THE_LOOP"
TIER_READ_ONLY = "READ_ONLY"

GENESIS_HASH = "0" * 64


@dataclass
class AuditEvent:
    event_id: int
    timestamp: float
    event_type: str
    payload: Dict[str, Any]
    prev_hash: str
    current_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SPCMetrics:
    metric_name: str
    mean: float
    std_dev: float
    cusum_pos: float
    cusum_neg: float
    ewma: float
    threshold_k: float
    threshold_h: float
    out_of_control: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OversightKernel:
    """Oversight, cryptographic provenance, and statistical process control kernel."""

    def __init__(self, db_path: Optional[str | Path] = None, deadman_timeout_sec: float = 86400.0):
        self.db_path = Path(db_path) if db_path else AUDIT_DB_PATH
        self.deadman_timeout_sec = deadman_timeout_sec
        self.current_autonomy_tier = TIER_FULL_AUTONOMY
        self.last_user_checkin_ts = time.time()

        # SPC Tracking structures
        # metric_name -> { "values": [...], "ewma": float, "cusum_pos": 0.0, "cusum_neg": 0.0 }
        self._spc_state: Dict[str, Dict[str, Any]] = {}
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with _lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS audit_chain (
                            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp REAL NOT NULL,
                            event_type TEXT NOT NULL,
                            payload_json TEXT NOT NULL,
                            prev_hash TEXT NOT NULL,
                            current_hash TEXT NOT NULL
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_audit_chain_ts ON audit_chain(timestamp)
                    """)
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Cryptographic BLAKE2b Audit Chain
    # ------------------------------------------------------------------

    def _hash_event(self, prev_hash: str, timestamp: float, event_type: str, payload_json: str) -> str:
        h = hashlib.blake2b(digest_size=32)
        h.update(prev_hash.encode("utf-8"))
        h.update(f"{timestamp:.6f}".encode("utf-8"))
        h.update(event_type.encode("utf-8"))
        h.update(payload_json.encode("utf-8"))
        return h.hexdigest()

    def get_latest_hash(self) -> str:
        with _lock:
            conn = self._get_connection()
            try:
                row = conn.execute(
                    "SELECT current_hash FROM audit_chain ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                if row:
                    return str(row["current_hash"])
                return GENESIS_HASH
            finally:
                conn.close()

    def record_event(self, event_type: str, payload: Dict[str, Any]) -> AuditEvent:
        with _lock:
            now = time.time()
            prev_hash = self.get_latest_hash()
            payload_json = json.dumps(payload, sort_keys=True)
            curr_hash = self._hash_event(prev_hash, now, event_type, payload_json)

            conn = self._get_connection()
            try:
                with conn:
                    cur = conn.execute(
                        """
                        INSERT INTO audit_chain (timestamp, event_type, payload_json, prev_hash, current_hash)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (now, event_type, payload_json, prev_hash, curr_hash),
                    )
                    event_id = cur.lastrowid
                event = AuditEvent(
                    event_id=event_id,
                    timestamp=now,
                    event_type=event_type,
                    payload=payload,
                    prev_hash=prev_hash,
                    current_hash=curr_hash,
                )
            finally:
                conn.close()

            # Record capability evidence
            try:
                get_registry().set_capability_evidence(
                    "OVERSIGHT_KERNEL",
                    EvidenceLevel.LIVE,
                    f"Recorded audit event #{event_id} ({event_type}) with BLAKE2b verification",
                    source="oversight_kernel.record_event",
                )
            except Exception:
                pass

            return event

    def verify_chain_integrity(self) -> Tuple[bool, Optional[str]]:
        """Verify hash links across all blocks from genesis to the head."""
        with _lock:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT event_id, timestamp, event_type, payload_json, prev_hash, current_hash "
                    "FROM audit_chain ORDER BY event_id ASC"
                ).fetchall()

                if not rows:
                    return True, None

                expected_prev = GENESIS_HASH
                for row in rows:
                    eid = row["event_id"]
                    ts = row["timestamp"]
                    etype = row["event_type"]
                    pjson = row["payload_json"]
                    prev_h = row["prev_hash"]
                    curr_h = row["current_hash"]

                    if prev_h != expected_prev:
                        return False, f"Broken chain link at event #{eid}: prev_hash {prev_h} != expected {expected_prev}"

                    recomputed = self._hash_event(prev_h, ts, etype, pjson)
                    if curr_h != recomputed:
                        return False, f"Corrupted hash at event #{eid}: {curr_h} != recomputed {recomputed}"

                    expected_prev = curr_h

                return True, None
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # CUSUM / EWMA Statistical Process Control
    # ------------------------------------------------------------------

    def update_spc_metric(
        self,
        metric_name: str,
        value: float,
        target_mean: Optional[float] = None,
        target_std: Optional[float] = None,
        k: float = 0.5,
        h: float = 4.0,
        ewma_lambda: float = 0.2,
    ) -> SPCMetrics:
        """Update CUSUM and EWMA for a monitored rate (e.g., tool mutations/hr).

        If out of control (CUSUM exceeds decision interval h), demote autonomy tier.
        """
        with _lock:
            if metric_name not in self._spc_state:
                self._spc_state[metric_name] = {
                    "values": [],
                    "ewma": value,
                    "cusum_pos": 0.0,
                    "cusum_neg": 0.0,
                    "mean": target_mean if target_mean is not None else value,
                    "std": target_std if target_std is not None else 1.0,
                }

            state = self._spc_state[metric_name]
            values = state["values"]
            values.append(value)
            if len(values) > 1000:
                values.pop(0)

            # Update sample statistics if targets are not pre-set
            if target_mean is None:
                state["mean"] = sum(values) / len(values)
            if target_std is None and len(values) > 1:
                variance = sum((x - state["mean"]) ** 2 for x in values) / (len(values) - 1)
                state["std"] = max(math.sqrt(variance), 1e-6)

            mu = state["mean"]
            sigma = max(state["std"], 1e-6)

            # Standardized observation
            z = (value - mu) / sigma

            # CUSUM updates
            cp = max(0.0, state["cusum_pos"] + z - k)
            cn = max(0.0, state["cusum_neg"] - z - k)
            state["cusum_pos"] = cp
            state["cusum_neg"] = cn

            # EWMA update
            state["ewma"] = ewma_lambda * value + (1.0 - ewma_lambda) * state["ewma"]

            out_of_control = (cp > h) or (cn > h)

            if out_of_control:
                self._demote_autonomy(f"SPC drift breach on metric '{metric_name}': CUSUM+={cp:.2f}, CUSUM-={cn:.2f} > h={h}")

            return SPCMetrics(
                metric_name=metric_name,
                mean=round(mu, 4),
                std_dev=round(sigma, 4),
                cusum_pos=round(cp, 4),
                cusum_neg=round(cn, 4),
                ewma=round(state["ewma"], 4),
                threshold_k=k,
                threshold_h=h,
                out_of_control=out_of_control,
            )

    def _demote_autonomy(self, reason: str) -> str:
        old_tier = self.current_autonomy_tier
        if self.current_autonomy_tier == TIER_FULL_AUTONOMY:
            self.current_autonomy_tier = TIER_SUPERVISED
        elif self.current_autonomy_tier == TIER_SUPERVISED:
            self.current_autonomy_tier = TIER_HUMAN_IN_THE_LOOP
        elif self.current_autonomy_tier == TIER_HUMAN_IN_THE_LOOP:
            self.current_autonomy_tier = TIER_READ_ONLY
        elif self.current_autonomy_tier == TIER_READ_ONLY:
            return TIER_READ_ONLY

        log.warning(f"[OversightKernel] Autonomy demoted {old_tier} -> {self.current_autonomy_tier} ({reason})")
        self.record_event("AUTONOMY_DEMOTION", {
            "old_tier": old_tier,
            "new_tier": self.current_autonomy_tier,
            "reason": reason,
        })
        return self.current_autonomy_tier

    # ------------------------------------------------------------------
    # Dead-Man Switch
    # ------------------------------------------------------------------

    def record_user_checkin(self) -> float:
        with _lock:
            self.last_user_checkin_ts = time.time()
            return self.last_user_checkin_ts

    def check_deadman_switch(self, current_ts: Optional[float] = None) -> Tuple[bool, str]:
        """Check if user check-in has lapsed; if so, downgrade to READ_ONLY safe mode."""
        with _lock:
            now = current_ts if current_ts is not None else time.time()
            elapsed = now - self.last_user_checkin_ts
            if elapsed > self.deadman_timeout_sec:
                if self.current_autonomy_tier != TIER_READ_ONLY:
                    self._demote_autonomy(f"Dead-man switch triggered: {elapsed:.1f}s > {self.deadman_timeout_sec:.1f}s")
                    self.current_autonomy_tier = TIER_READ_ONLY
                return True, TIER_READ_ONLY
            return False, self.current_autonomy_tier

    # ------------------------------------------------------------------
    # Rubber-Stamp Approval Defense
    # ------------------------------------------------------------------

    def detect_rubber_stamp(
        self,
        action_text: str,
        review_duration_sec: float,
        reading_speed_wpm: float = 300.0,
        min_cognitive_overhead_sec: float = 0.5,
    ) -> Dict[str, Any]:
        """Detect suspiciously fast approval on high-stakes actions based on reading speeds."""
        word_count = len(action_text.split())
        # Expected minimum reading time in seconds = (words / wpm) * 60 + overhead
        expected_min_sec = (word_count / max(10.0, reading_speed_wpm)) * 60.0 + min_cognitive_overhead_sec
        is_rubber_stamped = review_duration_sec < expected_min_sec

        result = {
            "word_count": word_count,
            "review_duration_sec": review_duration_sec,
            "expected_min_sec": round(expected_min_sec, 3),
            "is_rubber_stamped": is_rubber_stamped,
            "speed_ratio": round(review_duration_sec / max(expected_min_sec, 1e-4), 3),
        }

        if is_rubber_stamped:
            log.warning(f"[OversightKernel] Potential rubber-stamp approval detected: {result}")
            self.record_event("RUBBER_STAMP_FLAGGED", result)

        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_kernel_instance: Optional[OversightKernel] = None


def get_oversight_kernel(db_path: Optional[str | Path] = None, deadman_timeout_sec: float = 86400.0) -> OversightKernel:
    global _kernel_instance
    if _kernel_instance is None:
        with _lock:
            if _kernel_instance is None:
                _kernel_instance = OversightKernel(db_path=db_path, deadman_timeout_sec=deadman_timeout_sec)
    return _kernel_instance


def record_audit_event(event_type: str, payload: Dict[str, Any]) -> AuditEvent:
    return get_oversight_kernel().record_event(event_type, payload)


def verify_chain_integrity() -> Tuple[bool, Optional[str]]:
    return get_oversight_kernel().verify_chain_integrity()


def update_spc_metric(metric_name: str, value: float, **kwargs) -> SPCMetrics:
    return get_oversight_kernel().update_spc_metric(metric_name, value, **kwargs)


def check_deadman_switch(current_ts: Optional[float] = None) -> Tuple[bool, str]:
    return get_oversight_kernel().check_deadman_switch(current_ts)


def detect_rubber_stamp(action_text: str, review_duration_sec: float, **kwargs) -> Dict[str, Any]:
    return get_oversight_kernel().detect_rubber_stamp(action_text, review_duration_sec, **kwargs)
