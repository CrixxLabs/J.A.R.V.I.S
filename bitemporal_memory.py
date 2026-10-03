"""Bitemporal Belief Graph & Truth Maintenance System for J.A.R.V.I.S. — MARK VIII.

Module Q:
  1. Bitemporal Tracking:
     - SQLite-backed store (data/bitemporal_beliefs.db in WAL mode) tracking:
       * Valid Time (when the fact was true in the real world).
       * Transaction Time (when the knowledge was recorded in the system).
     - Full provenance metadata: source_channel, confidence, extractor_model, dependencies.
  2. Contradiction Handling & Truth Maintenance:
     - Detects overlapping valid-time assertions with contradictory object values.
     - Creates explicit ContradictionRecords with reliability/confidence scores instead of blind overwrite.
  3. Belief Retraction & Dependency Propagation:
     - Retraction closes transaction time and cascades 'DEPENDENCY_BROKEN' status across
       downstream derived beliefs.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.bitemporal_memory")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bitemporal_beliefs.db"

_lock = threading.RLock()


@dataclass
class BeliefRecord:
    belief_id: str
    subject: str
    predicate: str
    object_value: str
    valid_from: float
    valid_to: Optional[float]
    tx_from: float
    tx_to: Optional[float]
    source_channel: str
    confidence: float
    extractor_model: str
    status: str  # ACTIVE, RETRACTED, SUPERSEDED, DEPENDENCY_BROKEN
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BitemporalMemory:
    """Manages SQLite bitemporal belief state, contradiction resolution, and dependency cascading."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path).resolve() if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with _lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS beliefs (
                        belief_id TEXT PRIMARY KEY,
                        subject TEXT NOT NULL,
                        predicate TEXT NOT NULL,
                        object_value TEXT NOT NULL,
                        valid_from REAL NOT NULL,
                        valid_to REAL,
                        tx_from REAL NOT NULL,
                        tx_to REAL,
                        source_channel TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        extractor_model TEXT NOT NULL,
                        status TEXT NOT NULL,
                        dependencies TEXT NOT NULL,
                        metadata TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_beliefs_subj_pred
                    ON beliefs (subject, predicate, status);
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_beliefs_bitemporal
                    ON beliefs (valid_from, valid_to, tx_from, tx_to);
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS contradictions (
                        contradiction_id TEXT PRIMARY KEY,
                        subject TEXT NOT NULL,
                        predicate TEXT NOT NULL,
                        existing_belief_id TEXT NOT NULL,
                        new_belief_id TEXT NOT NULL,
                        existing_object TEXT NOT NULL,
                        new_object TEXT NOT NULL,
                        existing_confidence REAL NOT NULL,
                        new_confidence REAL NOT NULL,
                        detected_at REAL NOT NULL,
                        status TEXT NOT NULL,
                        resolution_details TEXT
                    );
                    """
                )
                conn.commit()

    def record_belief(
        self,
        subject: str,
        predicate: str,
        object_value: Any,
        valid_from: Optional[float] = None,
        valid_to: Optional[float] = None,
        source_channel: str = "direct_user",
        confidence: float = 1.0,
        extractor_model: str = "direct",
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record a new belief in bitemporal graph with automatic contradiction detection."""
        subj = str(subject).strip().lower()
        pred = str(predicate).strip().lower()
        obj_str = json.dumps(object_value) if not isinstance(object_value, str) else object_value
        now = time.time()
        vf = float(valid_from) if valid_from is not None else now
        vt = float(valid_to) if valid_to is not None else None
        conf = max(0.0, min(1.0, float(confidence)))
        deps = list(dependencies or [])
        meta = metadata or {}

        belief_id = f"bel_{uuid.uuid4().hex[:12]}"

        with _lock:
            # 1. Check for contradictions among active beliefs in overlapping valid_time
            existing_conflicts = self._find_contradictions(subj, pred, obj_str, vf, vt)
            contradiction_id = None

            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO beliefs (
                        belief_id, subject, predicate, object_value,
                        valid_from, valid_to, tx_from, tx_to,
                        source_channel, confidence, extractor_model,
                        status, dependencies, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        belief_id,
                        subj,
                        pred,
                        obj_str,
                        vf,
                        vt,
                        now,
                        None,
                        source_channel,
                        conf,
                        extractor_model,
                        "ACTIVE",
                        json.dumps(deps),
                        json.dumps(meta),
                    ),
                )

                if existing_conflicts:
                    for ex in existing_conflicts:
                        cid = f"contra_{uuid.uuid4().hex[:10]}"
                        contradiction_id = cid
                        conn.execute(
                            """
                            INSERT INTO contradictions (
                                contradiction_id, subject, predicate, existing_belief_id, new_belief_id,
                                existing_object, new_object, existing_confidence, new_confidence,
                                detected_at, status, resolution_details
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                cid,
                                subj,
                                pred,
                                ex["belief_id"],
                                belief_id,
                                ex["object_value"],
                                obj_str,
                                ex["confidence"],
                                conf,
                                now,
                                "UNRESOLVED",
                                json.dumps({"note": "Detected during record_belief"}),
                            ),
                        )
                        log.warning(
                            f"[BitemporalMemory] Contradiction detected on ({subj}, {pred}): "
                            f"existing '{ex['object_value']}' (conf={ex['confidence']}) vs new '{obj_str}' (conf={conf})"
                        )

                conn.commit()

            try:
                get_registry().set_capability_evidence(
                    "BITEMPORAL_MEMORY",
                    EvidenceLevel.LIVE,
                    f"Recorded belief '{subj} {pred} {obj_str[:20]}' (contradiction={bool(existing_conflicts)})",
                    source="bitemporal_memory.record_belief",
                )
            except Exception:
                pass

            return {
                "success": True,
                "belief_id": belief_id,
                "subject": subj,
                "predicate": pred,
                "object_value": obj_str,
                "valid_from": vf,
                "valid_to": vt,
                "tx_from": now,
                "confidence": conf,
                "contradiction_detected": bool(existing_conflicts),
                "contradiction_id": contradiction_id,
            }

    def _find_contradictions(
        self,
        subject: str,
        predicate: str,
        new_obj: str,
        new_vf: float,
        new_vt: Optional[float],
    ) -> List[Dict[str, Any]]:
        """Identify overlapping active beliefs with different object values."""
        conflicts = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM beliefs
                WHERE subject = ? AND predicate = ? AND status = 'ACTIVE' AND tx_to IS NULL;
                """,
                (subject, predicate),
            )
            for row in cursor.fetchall():
                ex_obj = row["object_value"]
                if ex_obj == new_obj:
                    continue  # Identical value is not a contradiction

                ex_vf = float(row["valid_from"])
                ex_vt = float(row["valid_to"]) if row["valid_to"] is not None else float("inf")
                cand_vt = new_vt if new_vt is not None else float("inf")

                # Overlap condition: max(start1, start2) < min(end1, end2)
                overlap = max(ex_vf, new_vf) < min(ex_vt, cand_vt)
                if overlap:
                    conflicts.append(dict(row))
        return conflicts

    def query_beliefs(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        as_of_valid_time: Optional[float] = None,
        as_of_tx_time: Optional[float] = None,
        status: str = "ACTIVE",
    ) -> List[Dict[str, Any]]:
        """Query belief graph across valid time and transaction time dimensions."""
        query = "SELECT * FROM beliefs WHERE 1=1"
        params: List[Any] = []

        if subject is not None:
            query += " AND subject = ?"
            params.append(str(subject).strip().lower())

        if predicate is not None:
            query += " AND predicate = ?"
            params.append(str(predicate).strip().lower())

        if status:
            query += " AND status = ?"
            params.append(str(status).strip().upper())

        # Transaction time constraint
        if as_of_tx_time is not None:
            query += " AND tx_from <= ? AND (tx_to IS NULL OR tx_to > ?)"
            params.extend([as_of_tx_time, as_of_tx_time])
        else:
            query += " AND tx_to IS NULL"

        # Valid time constraint
        if as_of_valid_time is not None:
            query += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([as_of_valid_time, as_of_valid_time])

        query += " ORDER BY valid_from DESC, confidence DESC;"

        with _lock:
            with self._get_connection() as conn:
                cursor = conn.execute(query, params)
                results = []
                for row in cursor.fetchall():
                    d = dict(row)
                    try:
                        d["dependencies"] = json.loads(d["dependencies"])
                    except Exception:
                        d["dependencies"] = []
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except Exception:
                        d["metadata"] = {}
                    results.append(d)
                return results

    def resolve_contradiction(
        self,
        contradiction_id: str,
        resolution_strategy: str = "prefer_higher_confidence",
        override_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a detected contradiction by superseding the loser belief."""
        now = time.time()
        with _lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM contradictions WHERE contradiction_id = ?",
                    (contradiction_id,),
                ).fetchone()

                if not row:
                    return {"success": False, "error": f"Contradiction '{contradiction_id}' not found"}

                c = dict(row)
                ex_id = c["existing_belief_id"]
                new_id = c["new_belief_id"]
                ex_conf = float(c["existing_confidence"])
                new_conf = float(c["new_confidence"])

                winner_id: str
                loser_id: str

                if override_choice == "existing":
                    winner_id, loser_id = ex_id, new_id
                    status_text = "RESOLVED_PREFER_EXISTING"
                elif override_choice == "new":
                    winner_id, loser_id = new_id, ex_id
                    status_text = "RESOLVED_PREFER_NEW"
                elif resolution_strategy == "prefer_higher_confidence":
                    if new_conf >= ex_conf:
                        winner_id, loser_id = new_id, ex_id
                        status_text = "RESOLVED_PREFER_NEW"
                    else:
                        winner_id, loser_id = ex_id, new_id
                        status_text = "RESOLVED_PREFER_EXISTING"
                else:
                    # Default to newer belief
                    winner_id, loser_id = new_id, ex_id
                    status_text = "RESOLVED_PREFER_NEW"

                # Mark loser as SUPERSEDED and close its tx_to
                conn.execute(
                    "UPDATE beliefs SET status = 'SUPERSEDED', tx_to = ? WHERE belief_id = ?;",
                    (now, loser_id),
                )
                conn.execute(
                    "UPDATE contradictions SET status = ?, resolution_details = ? WHERE contradiction_id = ?;",
                    (
                        status_text,
                        json.dumps({"winner": winner_id, "loser": loser_id, "resolved_at": now}),
                        contradiction_id,
                    ),
                )
                conn.commit()

                log.info(
                    f"[BitemporalMemory] Resolved contradiction {contradiction_id}: winner='{winner_id}', loser='{loser_id}'"
                )

                return {
                    "success": True,
                    "contradiction_id": contradiction_id,
                    "status": status_text,
                    "winner_belief_id": winner_id,
                    "loser_belief_id": loser_id,
                }

    def retract_belief(self, belief_id: str, reason: str = "user_correction") -> Dict[str, Any]:
        """Retract a belief and cascade dependency breaks to all downstream derived beliefs."""
        now = time.time()
        with _lock:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM beliefs WHERE belief_id = ? AND tx_to IS NULL;",
                    (belief_id,),
                ).fetchone()

                if not row:
                    return {"success": False, "error": f"Active belief '{belief_id}' not found"}

                # 1. Retract target belief
                conn.execute(
                    "UPDATE beliefs SET status = 'RETRACTED', tx_to = ? WHERE belief_id = ?;",
                    (now, belief_id),
                )

                # 2. Find and flag downstream dependent beliefs
                broken_cascade: List[str] = []
                queue = [belief_id]

                while queue:
                    curr_parent = queue.pop(0)
                    # Find any active belief whose dependencies JSON contains curr_parent
                    candidates = conn.execute(
                        "SELECT belief_id, dependencies FROM beliefs WHERE status = 'ACTIVE' AND tx_to IS NULL;"
                    ).fetchall()

                    for cand in candidates:
                        cid = cand["belief_id"]
                        try:
                            cdeps = json.loads(cand["dependencies"])
                        except Exception:
                            cdeps = []

                        if curr_parent in cdeps and cid not in broken_cascade:
                            conn.execute(
                                "UPDATE beliefs SET status = 'DEPENDENCY_BROKEN', tx_to = ? WHERE belief_id = ?;",
                                (now, cid),
                            )
                            broken_cascade.append(cid)
                            queue.append(cid)

                conn.commit()
                log.info(
                    f"[BitemporalMemory] Retracted '{belief_id}' (reason='{reason}'). "
                    f"Cascaded dependency break to {len(broken_cascade)} downstream beliefs."
                )

                return {
                    "success": True,
                    "belief_id": belief_id,
                    "reason": reason,
                    "broken_dependencies_count": len(broken_cascade),
                    "broken_dependencies": broken_cascade,
                }

    def get_contradictions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve recorded contradictions."""
        with _lock:
            with self._get_connection() as conn:
                if status:
                    cursor = conn.execute(
                        "SELECT * FROM contradictions WHERE status = ? ORDER BY detected_at DESC;",
                        (status.upper(),),
                    )
                else:
                    cursor = conn.execute("SELECT * FROM contradictions ORDER BY detected_at DESC;")
                return [dict(r) for r in cursor.fetchall()]


_memory_instance: Optional[BitemporalMemory] = None


def get_bitemporal_memory() -> BitemporalMemory:
    global _memory_instance
    if _memory_instance is None:
        with _lock:
            if _memory_instance is None:
                _memory_instance = BitemporalMemory()
    return _memory_instance


def record_belief(
    subject: str,
    predicate: str,
    object_value: Any,
    valid_from: Optional[float] = None,
    valid_to: Optional[float] = None,
    source_channel: str = "direct_user",
    confidence: float = 1.0,
    extractor_model: str = "direct",
    dependencies: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return get_bitemporal_memory().record_belief(
        subject, predicate, object_value, valid_from, valid_to,
        source_channel, confidence, extractor_model, dependencies, metadata
    )


def query_beliefs(
    subject: Optional[str] = None,
    predicate: Optional[str] = None,
    as_of_valid_time: Optional[float] = None,
    as_of_tx_time: Optional[float] = None,
    status: str = "ACTIVE",
) -> List[Dict[str, Any]]:
    return get_bitemporal_memory().query_beliefs(subject, predicate, as_of_valid_time, as_of_tx_time, status)


def resolve_contradiction(
    contradiction_id: str,
    resolution_strategy: str = "prefer_higher_confidence",
    override_choice: Optional[str] = None,
) -> Dict[str, Any]:
    return get_bitemporal_memory().resolve_contradiction(contradiction_id, resolution_strategy, override_choice)


def retract_belief(belief_id: str, reason: str = "user_correction") -> Dict[str, Any]:
    return get_bitemporal_memory().retract_belief(belief_id, reason)


def get_contradictions(status: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_bitemporal_memory().get_contradictions(status)
