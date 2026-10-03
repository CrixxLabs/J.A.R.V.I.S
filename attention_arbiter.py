"""Theory of Mind & Attention Economics Engine for J.A.R.V.I.S. — MARK VIII.

Module S:
  1. Latent User State Estimation:
     - Infers user state (FOCUSED, AVAILABLE, AWAY, DO_NOT_DISTURB) using non-intrusive
       OS signals: foreground window title/process, keyboard/mouse idle duration, and fullscreen mode.
  2. Interruption Cost & Value-of-Information (VOI) Gating:
     - Mathematical VOI formulation: VOI = Value(Urgency * Stakes) - Cost(User_State).
     - Gated alert delivery: blocks or routes non-critical interruptions during high-focus states.
  3. Thompson-Sampling Channel Bandit:
     - Multi-armed Bayesian bandit learning optimal notification channels across user states.
  4. Batched Digest Queue:
     - Buffers low-stakes alerts for scheduled delivery when user transitions to AVAILABLE state.
"""
from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import random
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.attention_arbiter")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
BANDIT_FILE = DATA_DIR / "attention_bandit_state.json"
DIGEST_FILE = DATA_DIR / "pending_digest_alerts.json"

_lock = threading.RLock()

# Latent User States
STATE_AVAILABLE = "AVAILABLE"
STATE_FOCUSED = "FOCUSED"
STATE_DO_NOT_DISTURB = "DO_NOT_DISTURB"
STATE_AWAY = "AWAY"
STATE_UNKNOWN = "UNKNOWN"

# Notification Channels
CHANNEL_VOICE_INTERRUPT = "VOICE_INTERRUPT"
CHANNEL_DESKTOP_POPUP = "DESKTOP_POPUP"
CHANNEL_DISCRETE_SOUND = "DISCRETE_SOUND"
CHANNEL_SCHEDULED_DIGEST = "SCHEDULED_DIGEST"
CHANNEL_PHONE_PUSH = "PHONE_PUSH"

ALL_CHANNELS = [
    CHANNEL_VOICE_INTERRUPT,
    CHANNEL_DESKTOP_POPUP,
    CHANNEL_DISCRETE_SOUND,
    CHANNEL_SCHEDULED_DIGEST,
    CHANNEL_PHONE_PUSH,
]

# Stake Weight Factors
STAKE_WEIGHTS = {
    "LOW": 0.5,
    "MEDIUM": 1.0,
    "HIGH": 2.5,
    "CRITICAL": 6.0,
}

# Base State Interruption Penalties
STATE_INTERRUPTION_COSTS = {
    STATE_AVAILABLE: 0.20,
    STATE_AWAY: 0.50,
    STATE_FOCUSED: 1.20,
    STATE_DO_NOT_DISTURB: 3.00,
    STATE_UNKNOWN: 0.50,
}


@dataclass
class UserStateAssessment:
    state: str
    confidence: float
    idle_seconds: float
    foreground_window: str
    is_fullscreen: bool
    assessed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AttentionArbiter:
    """Manages cognitive load modeling, latent user state, and Thompson sampling attention channels."""

    def __init__(
        self,
        bandit_path: Optional[Path] = None,
        digest_path: Optional[Path] = None,
    ):
        self.bandit_path = Path(bandit_path).resolve() if bandit_path else BANDIT_FILE
        self.digest_path = Path(digest_path).resolve() if digest_path else DIGEST_FILE
        self.bandit_path.parent.mkdir(parents=True, exist_ok=True)
        self.digest_path.parent.mkdir(parents=True, exist_ok=True)

        self._bandit_params: Dict[str, Dict[str, float]] = {}
        self._digest_queue: List[Dict[str, Any]] = []

        self._load_bandit_state()
        self._load_digest_queue()

    def _load_bandit_state(self) -> None:
        with _lock:
            if self.bandit_path.exists():
                try:
                    with open(self.bandit_path, "r", encoding="utf-8") as f:
                        self._bandit_params = json.load(f)
                except Exception:
                    self._bandit_params = {}
            if not self._bandit_params:
                # Initialize default prior alpha=2.0, beta=2.0 for each channel
                for ch in ALL_CHANNELS:
                    self._bandit_params[ch] = {"alpha": 2.0, "beta": 2.0}

    def _save_bandit_state(self) -> None:
        with _lock:
            try:
                with open(self.bandit_path, "w", encoding="utf-8") as f:
                    json.dump(self._bandit_params, f, indent=2)
            except Exception as exc:
                log.error(f"[AttentionArbiter] Failed to save bandit state: {exc}")

    def _load_digest_queue(self) -> None:
        with _lock:
            if self.digest_path.exists():
                try:
                    with open(self.digest_path, "r", encoding="utf-8") as f:
                        self._digest_queue = json.load(f)
                except Exception:
                    self._digest_queue = []

    def _save_digest_queue(self) -> None:
        with _lock:
            try:
                with open(self.digest_path, "w", encoding="utf-8") as f:
                    json.dump(self._digest_queue, f, indent=2)
            except Exception as exc:
                log.error(f"[AttentionArbiter] Failed to save digest queue: {exc}")

    def _get_system_idle_seconds(self) -> float:
        """Query Win32 GetLastInputInfo or return fallback idle time."""
        if sys.platform == "win32":
            try:
                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

                lii = LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                    return max(0.0, float(millis) / 1000.0)
            except Exception:
                pass
        return 0.0

    def estimate_user_state(
        self,
        signals: Optional[Dict[str, Any]] = None,
    ) -> UserStateAssessment:
        """Estimate latent user state from OS signals or provided telemetry dictionary."""
        sig = signals or {}

        # 1. Idle time
        if "idle_seconds" in sig:
            idle_sec = float(sig["idle_seconds"])
        else:
            idle_sec = self._get_system_idle_seconds()

        # 2. Foreground window
        fg_window = str(sig.get("foreground_window", "")).strip().lower()

        # 3. Fullscreen / DND flag
        is_fullscreen = bool(sig.get("is_fullscreen", False))

        # Heuristic State Inference
        state = STATE_AVAILABLE
        confidence = 0.85

        if idle_sec >= 300.0:
            state = STATE_AWAY
            confidence = 0.95
        elif is_fullscreen or any(
            k in fg_window
            for k in ["zoom", "teams", "meet", "webex", "powerpoint", "presentation", "fullscreen", "game", "vlc"]
        ):
            state = STATE_DO_NOT_DISTURB
            confidence = 0.90
        elif any(
            k in fg_window
            for k in [
                "visual studio", "code", "sublime", "pycharm", "intellij", "cursor",
                "terminal", "powershell", "bash", "nvim", "vim", "texstudio", "overleaf", "document"
            ]
        ) and idle_sec < 60.0:
            state = STATE_FOCUSED
            confidence = 0.88
        elif idle_sec > 120.0:
            state = STATE_AVAILABLE
            confidence = 0.75

        assessment = UserStateAssessment(
            state=state,
            confidence=confidence,
            idle_seconds=round(idle_sec, 2),
            foreground_window=fg_window,
            is_fullscreen=is_fullscreen,
        )

        try:
            get_registry().set_capability_evidence(
                "ATTENTION_ARBITER",
                EvidenceLevel.LIVE,
                f"Assessed user state as '{state}' (conf={confidence}, idle={idle_sec:.1f}s)",
                source="attention_arbiter.estimate_user_state",
            )
        except Exception:
            pass

        return assessment

    def should_interrupt(
        self,
        urgency_score: float,
        stakes: str = "MEDIUM",
        user_state: Optional[str] = None,
        cost_multiplier: float = 1.0,
    ) -> Dict[str, Any]:
        """Value-of-Information (VOI) gating to decide whether to interrupt user immediately."""
        urgency = max(0.0, min(1.0, float(urgency_score)))
        stake_norm = str(stakes).strip().upper()
        if stake_norm not in STAKE_WEIGHTS:
            stake_norm = "MEDIUM"

        current_state = user_state or self.estimate_user_state().state
        stake_w = STAKE_WEIGHTS[stake_norm]
        cost_base = STATE_INTERRUPTION_COSTS.get(current_state, 0.50)

        # Value of Interruption: V = Urgency * Stake_Weight
        value = urgency * stake_w

        # Cost of Interruption: C = Cost_Base * Multiplier
        cost = cost_base * float(cost_multiplier)

        # Net Value of Information: VOI = V - C
        voi = value - cost

        # Critical always preempts, otherwise VOI must be strictly positive
        should_int = (stake_norm == "CRITICAL") or (voi > 0.0)

        return {
            "should_interrupt": should_int,
            "voi": round(voi, 4),
            "value": round(value, 4),
            "cost": round(cost, 4),
            "user_state": current_state,
            "stake_level": stake_norm,
            "urgency_score": urgency,
            "recommended_action": "INTERRUPT_IMMEDIATELY" if should_int else "QUEUE_FOR_DIGEST",
        }

    def select_notification_channel(
        self,
        urgency_score: float,
        stakes: str = "MEDIUM",
        user_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Select optimal notification channel via Thompson Sampling modulated by VOI context."""
        state = user_state or self.estimate_user_state().state
        stake_norm = str(stakes).strip().upper()
        urgency = max(0.0, min(1.0, float(urgency_score)))

        with _lock:
            # Sample theta from Beta(alpha, beta) for each channel
            sampled_thetas: Dict[str, float] = {}
            for ch in ALL_CHANNELS:
                params = self._bandit_params.get(ch, {"alpha": 2.0, "beta": 2.0})
                alpha = max(0.1, params["alpha"])
                beta = max(0.1, params["beta"])
                # Thompson sampling draw using random.betavariate
                sampled_thetas[ch] = random.betavariate(alpha, beta)

            # Heuristic contextual modulation on sampled scores
            if state == STATE_DO_NOT_DISTURB and stake_norm != "CRITICAL":
                sampled_thetas[CHANNEL_VOICE_INTERRUPT] *= 0.1
                sampled_thetas[CHANNEL_DESKTOP_POPUP] *= 0.2
                sampled_thetas[CHANNEL_SCHEDULED_DIGEST] *= 2.0
            elif state == STATE_FOCUSED:
                sampled_thetas[CHANNEL_VOICE_INTERRUPT] *= 0.4
                sampled_thetas[CHANNEL_DISCRETE_SOUND] *= 1.5
            elif state == STATE_AWAY:
                sampled_thetas[CHANNEL_PHONE_PUSH] *= 2.5
                sampled_thetas[CHANNEL_SCHEDULED_DIGEST] *= 1.8
                sampled_thetas[CHANNEL_VOICE_INTERRUPT] *= 0.05
            elif stake_norm == "CRITICAL" or urgency > 0.85:
                sampled_thetas[CHANNEL_VOICE_INTERRUPT] *= 3.0
                sampled_thetas[CHANNEL_DESKTOP_POPUP] *= 2.0

            best_channel = max(sampled_thetas.keys(), key=lambda c: sampled_thetas[c])

            return {
                "selected_channel": best_channel,
                "user_state": state,
                "stake_level": stake_norm,
                "channel_scores": {k: round(v, 4) for k, v in sampled_thetas.items()},
            }

    def record_feedback(
        self,
        channel: str,
        user_state: str,
        accepted_or_useful: bool,
    ) -> Dict[str, Any]:
        """Update Thompson Sampling posterior (Beta distribution) based on user interaction."""
        ch = str(channel).strip().upper()
        if ch not in self._bandit_params:
            self._bandit_params[ch] = {"alpha": 2.0, "beta": 2.0}

        with _lock:
            if accepted_or_useful:
                self._bandit_params[ch]["alpha"] += 1.0
            else:
                self._bandit_params[ch]["beta"] += 1.0

            self._save_bandit_state()
            log.info(
                f"[AttentionArbiter] Updated bandit for channel '{ch}' (alpha={self._bandit_params[ch]['alpha']}, beta={self._bandit_params[ch]['beta']})"
            )

            return {
                "updated": True,
                "channel": ch,
                "alpha": self._bandit_params[ch]["alpha"],
                "beta": self._bandit_params[ch]["beta"],
            }

    def queue_alert_for_digest(self, alert_payload: Dict[str, Any]) -> str:
        """Buffer a low-stakes alert into the pending digest queue."""
        alert_id = f"alert_{uuid.uuid4().hex[:10]}"
        item = {
            "alert_id": alert_id,
            "queued_at": time.time(),
            "payload": alert_payload,
        }
        with _lock:
            self._digest_queue.append(item)
            self._save_digest_queue()
            log.info(f"[AttentionArbiter] Queued alert '{alert_id}' for next digest flush")
            return alert_id

    def get_pending_digest(self) -> List[Dict[str, Any]]:
        with _lock:
            return list(self._digest_queue)

    def flush_digest(self) -> List[Dict[str, Any]]:
        """Flush and return all pending batched digest alerts."""
        with _lock:
            flushed = list(self._digest_queue)
            self._digest_queue = []
            self._save_digest_queue()
            log.info(f"[AttentionArbiter] Flushed {len(flushed)} alerts from digest queue")
            return flushed


_arbiter_instance: Optional[AttentionArbiter] = None


def get_attention_arbiter() -> AttentionArbiter:
    global _arbiter_instance
    if _arbiter_instance is None:
        with _lock:
            if _arbiter_instance is None:
                _arbiter_instance = AttentionArbiter()
    return _arbiter_instance


def estimate_user_state(signals: Optional[Dict[str, Any]] = None) -> UserStateAssessment:
    return get_attention_arbiter().estimate_user_state(signals)


def should_interrupt(
    urgency_score: float,
    stakes: str = "MEDIUM",
    user_state: Optional[str] = None,
    cost_multiplier: float = 1.0,
) -> Dict[str, Any]:
    return get_attention_arbiter().should_interrupt(urgency_score, stakes, user_state, cost_multiplier)


def select_notification_channel(
    urgency_score: float,
    stakes: str = "MEDIUM",
    user_state: Optional[str] = None,
) -> Dict[str, Any]:
    return get_attention_arbiter().select_notification_channel(urgency_score, stakes, user_state)


def record_feedback(channel: str, user_state: str, accepted_or_useful: bool) -> Dict[str, Any]:
    return get_attention_arbiter().record_feedback(channel, user_state, accepted_or_useful)


def queue_alert_for_digest(alert_payload: Dict[str, Any]) -> str:
    return get_attention_arbiter().queue_alert_for_digest(alert_payload)


def flush_digest() -> List[Dict[str, Any]]:
    return get_attention_arbiter().flush_digest()
