"""Executive Commitment Ledger & Simple Temporal Network (STN) for J.A.R.V.I.S. — MARK VIII.

Tracks all contractual commitments, promises, and deliverables with temporal slack verification:
  1. Durable SQLite WAL-mode event-sourced commitment storage (data/commitments.db).
  2. Stake Classification (LOW, MEDIUM, CRITICAL).
  3. Simple Temporal Network (STN) consistency solver: evaluates whether new tasks breach deadlines.
  4. Saga compensation action linkage.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.commitment_ledger")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "commitments.db"

_lock = threading.RLock()


@dataclass
class CommitmentRecord:
    commitment_id: str
    task_name: str
    deadline_epoch: float
    stake_level: str  # "LOW", "MEDIUM", "CRITICAL"
    status: str  # "PENDING", "RUNNING", "COMPLETED", "CANCELLED"
    estimated_duration_hours: float
    saga_compensation: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


class CommitmentLedger:
    """Event-sourced SQLite commitment store backed by an STN consistency checker."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path).resolve() if db_path else DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with _lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS commitments (
                    commitment_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    deadline_epoch REAL NOT NULL,
                    stake_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    estimated_duration_hours REAL NOT NULL,
                    saga_compensation TEXT,
                    created_at REAL NOT NULL,
                    details_json TEXT NOT NULL
                );
            """)
            conn.commit()

    def record_commitment(
        self,
        task_name: str,
        deadline_epoch: float,
        stake_level: str = "MEDIUM",
        estimated_duration_hours: float = 1.0,
        saga_compensation: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record a binding commitment into the durable ledger."""
        cid = f"COM-{int(time.time() * 1000)}"
        det = details or {}
        now = time.time()

        record = CommitmentRecord(
            commitment_id=cid,
            task_name=task_name,
            deadline_epoch=float(deadline_epoch),
            stake_level=stake_level.upper(),
            status="PENDING",
            estimated_duration_hours=float(estimated_duration_hours),
            saga_compensation=saga_compensation,
            created_at=now,
            details=det,
        )

        with _lock, self._get_connection() as conn:
            conn.execute("""
                INSERT INTO commitments (
                    commitment_id, task_name, deadline_epoch, stake_level,
                    status, estimated_duration_hours, saga_compensation, created_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                record.commitment_id,
                record.task_name,
                record.deadline_epoch,
                record.stake_level,
                record.status,
                record.estimated_duration_hours,
                record.saga_compensation,
                record.created_at,
                json.dumps(record.details),
            ))
            conn.commit()

        log.info(f"[CommitmentLedger] Recorded commitment '{task_name}' [{cid}] (stake={stake_level})")

        try:
            get_registry().set_capability_evidence(
                "COMMITMENT_LEDGER",
                EvidenceLevel.LIVE,
                f"Recorded commitment '{task_name}' ({stake_level})",
                source="commitment_ledger.record_commitment",
            )
        except Exception:
            pass

        return asdict(record)

    def update_commitment_status(self, commitment_id: str, status: str) -> bool:
        """Update status (PENDING, RUNNING, COMPLETED, CANCELLED)."""
        valid_status = str(status).upper()
        with _lock, self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE commitments SET status = ? WHERE commitment_id = ?;",
                (valid_status, commitment_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_active_commitments(self) -> List[Dict[str, Any]]:
        """Retrieve all active (PENDING or RUNNING) commitments sorted by deadline."""
        with _lock, self._get_connection() as conn:
            cur = conn.execute("""
                SELECT commitment_id, task_name, deadline_epoch, stake_level,
                       status, estimated_duration_hours, saga_compensation, created_at, details_json
                FROM commitments
                WHERE status IN ('PENDING', 'RUNNING')
                ORDER BY deadline_epoch ASC;
            """)
            rows = cur.fetchall()

        results = []
        for r in rows:
            results.append({
                "commitment_id": r[0],
                "task_name": r[1],
                "deadline_epoch": r[2],
                "stake_level": r[3],
                "status": r[4],
                "estimated_duration_hours": r[5],
                "saga_compensation": r[6],
                "created_at": r[7],
                "details": json.loads(r[8]),
            })
        return results

    def calculate_temporal_slack(
        self,
        reference_time: Optional[float] = None,
        daily_capacity_hours: float = 8.0,
    ) -> Dict[str, Any]:
        """Simple Temporal Network (STN) solver computing slack across all active obligations."""
        now = reference_time if reference_time is not None else time.time()
        active = self.get_active_commitments()

        cumulative_work_hours = 0.0
        slack_reports: List[Dict[str, Any]] = []
        has_negative_slack = False

        for c in active:
            deadline = c["deadline_epoch"]
            time_to_deadline_hours = max(0.0, (deadline - now) / 3600.0)
            work_needed = c["estimated_duration_hours"]
            cumulative_work_hours += work_needed

            # Available capacity within the window
            days_until_deadline = max(0.1, time_to_deadline_hours / 24.0)
            max_capacity_hours = days_until_deadline * daily_capacity_hours

            slack_hours = max_capacity_hours - cumulative_work_hours
            is_tight = slack_hours < 2.0
            is_breached = slack_hours < 0.0

            if is_breached:
                has_negative_slack = True

            slack_reports.append({
                "commitment_id": c["commitment_id"],
                "task_name": c["task_name"],
                "time_to_deadline_hours": round(time_to_deadline_hours, 1),
                "slack_hours": round(slack_hours, 1),
                "is_breached": is_breached,
                "is_tight": is_tight,
                "stake_level": c["stake_level"],
            })

        return {
            "consistent": not has_negative_slack,
            "total_active_tasks": len(active),
            "cumulative_work_hours": round(cumulative_work_hours, 1),
            "slack_reports": slack_reports,
        }

    def evaluate_stn_consistency(
        self,
        new_task_name: str,
        deadline_epoch: float,
        estimated_duration_hours: float,
        stake_level: str = "MEDIUM",
        reference_time: Optional[float] = None,
        daily_capacity_hours: float = 8.0,
    ) -> Dict[str, Any]:
        """Pre-flight check: evaluate whether accepting a new commitment causes an STN breach."""
        now = reference_time if reference_time is not None else time.time()
        active = self.get_active_commitments()

        # Build hypothetical candidate list
        candidate = {
            "commitment_id": "CANDIDATE",
            "task_name": new_task_name,
            "deadline_epoch": float(deadline_epoch),
            "stake_level": stake_level,
            "status": "PENDING",
            "estimated_duration_hours": float(estimated_duration_hours),
        }
        all_tasks = active + [candidate]
        all_tasks.sort(key=lambda x: x["deadline_epoch"])

        cumulative = 0.0
        breached_tasks: List[str] = []

        for t in all_tasks:
            deadline = t["deadline_epoch"]
            time_to_deadline_hours = max(0.0, (deadline - now) / 3600.0)
            work_needed = t["estimated_duration_hours"]
            cumulative += work_needed

            days = max(0.1, time_to_deadline_hours / 24.0)
            max_capacity = days * daily_capacity_hours
            if cumulative > max_capacity:
                breached_tasks.append(t["task_name"])

        can_accept = len(breached_tasks) == 0

        return {
            "can_accept": can_accept,
            "status": "feasible" if can_accept else "temporal_breach_detected",
            "breached_tasks": breached_tasks,
            "renegotiation_needed": not can_accept,
            "new_task": new_task_name,
        }


_ledger_instance: Optional[CommitmentLedger] = None


def get_commitment_ledger() -> CommitmentLedger:
    global _ledger_instance
    if _ledger_instance is None:
        with _lock:
            if _ledger_instance is None:
                _ledger_instance = CommitmentLedger()
    return _ledger_instance


def record_commitment(
    task_name: str,
    deadline_epoch: float,
    stake_level: str = "MEDIUM",
    estimated_duration_hours: float = 1.0,
    saga_compensation: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return get_commitment_ledger().record_commitment(
        task_name, deadline_epoch, stake_level, estimated_duration_hours, saga_compensation, details
    )


def update_commitment_status(commitment_id: str, status: str) -> bool:
    return get_commitment_ledger().update_commitment_status(commitment_id, status)


def get_active_commitments() -> List[Dict[str, Any]]:
    return get_commitment_ledger().get_active_commitments()


def calculate_temporal_slack(daily_capacity_hours: float = 8.0) -> Dict[str, Any]:
    return get_commitment_ledger().calculate_temporal_slack(daily_capacity_hours=daily_capacity_hours)


def evaluate_stn_consistency(
    new_task_name: str,
    deadline_epoch: float,
    estimated_duration_hours: float,
    stake_level: str = "MEDIUM",
) -> Dict[str, Any]:
    return get_commitment_ledger().evaluate_stn_consistency(
        new_task_name, deadline_epoch, estimated_duration_hours, stake_level
    )
