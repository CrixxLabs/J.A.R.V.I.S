"""Provider-specific health and cooldown tracking for the JARVIS brain router."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class HealthState(str, Enum):
    CONFIGURED = "CONFIGURED"
    LIVE = "LIVE"
    OFFLINE = "OFFLINE"
    AUTH_ERROR = "AUTH_ERROR"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    SERVER_ERROR = "SERVER_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    COOLDOWN = "COOLDOWN"


_DEFAULT_COOLDOWNS = {
    HealthState.OFFLINE: 15.0,
    HealthState.AUTH_ERROR: 3600.0,
    HealthState.MODEL_UNAVAILABLE: 3600.0,
    HealthState.RATE_LIMITED: 60.0,
    HealthState.TIMEOUT: 20.0,
    HealthState.SERVER_ERROR: 30.0,
    HealthState.BAD_REQUEST: 1800.0,
}


@dataclass
class HealthRecord:
    state: HealthState = HealthState.CONFIGURED
    detail: str = "Configured; not yet live-probed"
    failures: int = 0
    last_attempt: float = 0.0
    last_success: float = 0.0
    retry_at: float = 0.0


class ProviderHealth:
    """Thread-safe, in-memory circuit breaker keyed by provider and model."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._records: dict[tuple[str, str], HealthRecord] = {}

    @staticmethod
    def _key(provider: str, model: Optional[str]) -> tuple[str, str]:
        return provider.strip().upper(), (model or "*").strip()

    def configure(self, provider: str, model: Optional[str] = None) -> None:
        with self._lock:
            self._records.setdefault(self._key(provider, model), HealthRecord())

    def can_attempt(self, provider: str, model: Optional[str] = None) -> bool:
        now = self._clock()
        with self._lock:
            record = self._records.setdefault(self._key(provider, model), HealthRecord())
            if record.retry_at and now < record.retry_at:
                return False
            return True

    def record_success(self, provider: str, model: Optional[str] = None, detail: str = "") -> None:
        now = self._clock()
        with self._lock:
            record = self._records.setdefault(self._key(provider, model), HealthRecord())
            record.state = HealthState.LIVE
            record.detail = detail or "Live generation succeeded"
            record.failures = 0
            record.last_attempt = now
            record.last_success = now
            record.retry_at = 0.0

    def record_failure(
        self,
        provider: str,
        model: Optional[str],
        state: HealthState,
        detail: str = "",
        retry_after: Optional[float] = None,
    ) -> None:
        now = self._clock()
        with self._lock:
            record = self._records.setdefault(self._key(provider, model), HealthRecord())
            record.failures += 1
            base = _DEFAULT_COOLDOWNS.get(state, 15.0)
            delay = retry_after if retry_after is not None else min(base * (2 ** (record.failures - 1)), 3600.0)
            record.state = state
            record.detail = detail or state.value
            record.last_attempt = now
            record.retry_at = now + max(0.0, delay)

    def get(self, provider: str, model: Optional[str] = None) -> dict:
        now = self._clock()
        with self._lock:
            record = self._records.setdefault(self._key(provider, model), HealthRecord())
            cooling = bool(record.retry_at and now < record.retry_at)
            return {
                "state": HealthState.COOLDOWN.value if cooling else record.state.value,
                "cause": record.state.value,
                "detail": record.detail,
                "failures": record.failures,
                "last_attempt": record.last_attempt,
                "last_success": record.last_success,
                "retry_in": max(0.0, record.retry_at - now),
            }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


def classify_http(status_code: int, body: str = "") -> HealthState:
    lowered = (body or "").lower()
    if status_code in (401, 403):
        return HealthState.AUTH_ERROR
    if status_code == 404 and any(token in lowered for token in ("model", "not found", "not_found")):
        return HealthState.MODEL_UNAVAILABLE
    if status_code == 429:
        return HealthState.RATE_LIMITED
    if status_code == 408:
        return HealthState.TIMEOUT
    if status_code >= 500:
        return HealthState.SERVER_ERROR
    if status_code == 400:
        return HealthState.BAD_REQUEST
    if status_code == 404:
        return HealthState.MODEL_UNAVAILABLE
    return HealthState.SERVER_ERROR


health = ProviderHealth()
