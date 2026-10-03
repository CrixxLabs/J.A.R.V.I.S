"""Deadline Watchdog & Fail-Soft Degradation Ladder for J.A.R.V.I.S. — MARK VIII.

Workstream 2:
  1. Monitored Real-Time Pipeline Stages:
     - VAD / VAP (<= 45ms)
     - Turn Decision (<= 80ms)
     - First-Chunk TTS (<= 90ms)
     - Audio Callback (<= 5ms)
  2. Fail-Soft Degradation Ladder:
     - Step 0: NORMAL
     - Step 1: DROP_VAP (fallback to energy VAD)
     - Step 2: SHORTEN_TTS (restrict chunks to <= 4 words)
     - Step 3: MUTE_BACKCHANNELS (silence conversational filler)
     - Step 4: SUSPEND_BACKGROUND (throttle/pause P2/P3 background tasks)
  3. Escalates after K consecutive deadline misses, de-escalates on recovery.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.degradation_watchdog")

_lock = threading.RLock()


class DegradationLevel(IntEnum):
    NORMAL = 0
    DROP_VAP = 1
    SHORTEN_TTS = 2
    MUTE_BACKCHANNELS = 3
    SUSPEND_BACKGROUND = 4


@dataclass
class StageDeadlineConfig:
    stage_name: str
    deadline_ms: float


DEFAULT_STAGE_DEADLINES: Dict[str, float] = {
    "VAD_VAP": 45.0,
    "TURN_DECISION": 80.0,
    "FIRST_CHUNK_TTS": 90.0,
    "AUDIO_CALLBACK": 5.0,
}


@dataclass
class DegradationStatus:
    current_level: DegradationLevel
    consecutive_misses: int
    consecutive_on_time: int
    last_stage_latency_ms: float
    last_stage_name: str
    deadline_missed: bool
    active_remediations: List[str]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["current_level"] = self.current_level.name
        return d


class DegradationWatchdog:
    """Real-Time Latency Watchdog and Fail-Soft Degradation Ladder."""

    def __init__(
        self,
        deadlines: Optional[Dict[str, float]] = None,
        k_misses_to_escalate: int = 3,
        m_ontime_to_recover: int = 5,
    ):
        self.deadlines = deadlines or dict(DEFAULT_STAGE_DEADLINES)
        self.k_misses_to_escalate = k_misses_to_escalate
        self.m_ontime_to_recover = m_ontime_to_recover

        self._current_level: DegradationLevel = DegradationLevel.NORMAL
        self._consecutive_misses: int = 0
        self._consecutive_on_time: int = 0
        self._history: List[DegradationStatus] = []

    @property
    def current_level(self) -> DegradationLevel:
        with _lock:
            return self._current_level

    def record_stage_latency(self, stage_name: str, latency_ms: float) -> DegradationStatus:
        """Record elapsed time for a pipeline stage and update degradation ladder status."""
        with _lock:
            deadline = self.deadlines.get(stage_name, 50.0)
            missed = latency_ms > deadline

            if missed:
                self._consecutive_misses += 1
                self._consecutive_on_time = 0
                if self._consecutive_misses >= self.k_misses_to_escalate:
                    if self._current_level < DegradationLevel.SUSPEND_BACKGROUND:
                        old_lvl = self._current_level
                        self._current_level = DegradationLevel(self._current_level + 1)
                        log.warning(
                            f"[DegradationWatchdog] Escalated degradation ladder: {old_lvl.name} -> {self._current_level.name} "
                            f"(Stage {stage_name} exceeded deadline {deadline:.1f}ms with {latency_ms:.1f}ms, misses={self._consecutive_misses})"
                        )
                        self._consecutive_misses = 0
            else:
                self._consecutive_on_time += 1
                self._consecutive_misses = 0
                if self._consecutive_on_time >= self.m_ontime_to_recover:
                    if self._current_level > DegradationLevel.NORMAL:
                        old_lvl = self._current_level
                        self._current_level = DegradationLevel(self._current_level - 1)
                        log.info(
                            f"[DegradationWatchdog] De-escalated degradation ladder: {old_lvl.name} -> {self._current_level.name} "
                            f"(On-time recovery streak={self._consecutive_on_time})"
                        )
                    self._consecutive_on_time = 0

            # List active remediations based on level
            remediations = []
            if self._current_level >= DegradationLevel.DROP_VAP:
                remediations.append("DROP_VAP: Fallback to fast energy VAD")
            if self._current_level >= DegradationLevel.SHORTEN_TTS:
                remediations.append("SHORTEN_TTS: Streaming audio chunk size <= 4 words")
            if self._current_level >= DegradationLevel.MUTE_BACKCHANNELS:
                remediations.append("MUTE_BACKCHANNELS: Conversational filler silenced")
            if self._current_level >= DegradationLevel.SUSPEND_BACKGROUND:
                remediations.append("SUSPEND_BACKGROUND: P2/P3 maintenance background tasks paused")

            status = DegradationStatus(
                current_level=self._current_level,
                consecutive_misses=self._consecutive_misses,
                consecutive_on_time=self._consecutive_on_time,
                last_stage_latency_ms=round(latency_ms, 2),
                last_stage_name=stage_name,
                deadline_missed=missed,
                active_remediations=remediations,
            )
            self._history.append(status)

            try:
                get_registry().set_capability_evidence(
                    "DUPLEX_CHOREOGRAPHY",
                    EvidenceLevel.LIVE,
                    f"Watchdog Level: {self._current_level.name}, Stage {stage_name}: {latency_ms:.1f}ms",
                    source="degradation_watchdog.record_stage_latency",
                )
            except Exception:
                pass

            return status

    def reset(self) -> None:
        with _lock:
            self._current_level = DegradationLevel.NORMAL
            self._consecutive_misses = 0
            self._consecutive_on_time = 0


_watchdog_instance: Optional[DegradationWatchdog] = None


def get_degradation_watchdog() -> DegradationWatchdog:
    global _watchdog_instance
    if _watchdog_instance is None:
        _watchdog_instance = DegradationWatchdog()
    return _watchdog_instance


def record_stage_latency(stage_name: str, latency_ms: float) -> DegradationStatus:
    return get_degradation_watchdog().record_stage_latency(stage_name, latency_ms)
