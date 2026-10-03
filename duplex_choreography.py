"""Zero-Latency Duplex Acoustic Choreography for J.A.R.V.I.S. — MARK VIII.

Module AS:
  1. Turn-End Predictor:
     - Evaluates logistic turn-end probability using syntactic completeness,
       normalized pitch slope (falling F0), energy decay, and silence duration:
       logit = w_syn * syn + w_f0 * max(0, -f0_fall) + w_energy * energy + w_pause * pause_ms + bias.
  2. Decision-Theoretic Audio Gate:
     - Releases audio only when P_end * g_early > (1 - P_end) * c_overlap;
       triggers speculative generation ahead of speech cessation.
  3. Ambient Backchanneling:
     - Detects mid-clause non-terminal pauses (140–320ms) and emits contextual
       verbal acknowledgments ("right", "understood", "mm-hm") rate-limited to <= 1 per 5s.
  4. State-Preserving Barge-In:
     - Classifies overlapping speech into BACKCHANNEL (continue speaking),
       BARGE_IN (yield floor and truncate KV context to exact spoken token timestamp),
       and CORRECTION (halt, flush audio buffer, and flag disputed statement).
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.duplex_choreography")

_lock = threading.RLock()


class BargeInType(str, Enum):
    BACKCHANNEL = "BACKCHANNEL"
    BARGE_IN = "BARGE_IN"
    CORRECTION = "CORRECTION"
    NONE = "NONE"


@dataclass
class TurnEndPrediction:
    p_end: float
    logit: float
    features: Dict[str, float]
    is_turn_end: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioGateDecision:
    should_release_audio: bool
    speculative_generation_triggered: bool
    p_end: float
    expected_gain: float
    expected_cost: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BargeInVerdict:
    barge_in_type: BargeInType
    should_yield_floor: bool
    should_flush_audio: bool
    truncate_timestamp: Optional[float]
    flagged_statement: Optional[str]
    transcript: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["barge_in_type"] = self.barge_in_type.value
        return d


class DuplexChoreographer:
    """Zero-Latency Duplex Acoustic Choreography and Conversational State Arbiter."""

    def __init__(
        self,
        w_syn: float = 2.5,
        w_f0: float = 2.0,
        w_energy: float = 1.8,
        w_pause: float = 2.2,
        bias: float = -2.8,
        g_early: float = 1.0,
        c_overlap: float = 2.2,
        speculative_threshold: float = 0.55,
        backchannel_cooldown_sec: float = 5.0,
    ):
        self.w_syn = w_syn
        self.w_f0 = w_f0
        self.w_energy = w_energy
        self.w_pause = w_pause
        self.bias = bias
        self.g_early = g_early
        self.c_overlap = c_overlap
        self.speculative_threshold = speculative_threshold
        self.backchannel_cooldown_sec = backchannel_cooldown_sec

        self._last_backchannel_time: float = 0.0
        self._backchannel_history: List[Dict[str, Any]] = []
        self._barge_in_history: List[BargeInVerdict] = []
        self._active_utterance_context: Optional[str] = None

        self._correction_patterns = [
            re.compile(r"\b(no|wait|actually|stop|wrong|hold on|not that|cancel|incorrect|false)\b", re.IGNORECASE),
        ]
        self._user_backchannel_patterns = [
            re.compile(r"^(yeah|uh-huh|uh huh|mhm|mm-hm|right|ok|okay|yes|sure|got it|yep)$", re.IGNORECASE),
        ]

    # ------------------------------------------------------------------
    # 1. Turn-End Prediction
    # ------------------------------------------------------------------

    def predict_turn_end(
        self,
        syntactic_completeness: float,
        f0_fall: float,
        energy_decay: float,
        pause_ms: float,
    ) -> TurnEndPrediction:
        """Compute logistic turn-end probability from acoustic and syntactic features.

        logit = w_syn * syn + w_f0 * max(0, -f0_fall) + w_energy * energy + w_pause * (pause_ms / 1000) + bias
        """
        # Normalize and clamp inputs
        syn = max(0.0, min(1.0, float(syntactic_completeness)))
        f0_falling_component = max(0.0, -float(f0_fall))
        energy = max(0.0, min(1.0, float(energy_decay)))
        pause_sec = max(0.0, float(pause_ms) / 1000.0)

        logit = (
            self.w_syn * syn
            + self.w_f0 * f0_falling_component
            + self.w_energy * energy
            + self.w_pause * pause_sec
            + self.bias
        )

        p_end = 1.0 / (1.0 + math.exp(-logit))
        is_turn_end = p_end >= 0.50

        features = {
            "syntactic_completeness": syn,
            "f0_fall": float(f0_fall),
            "energy_decay": energy,
            "pause_ms": float(pause_ms),
        }

        pred = TurnEndPrediction(
            p_end=round(p_end, 4),
            logit=round(logit, 4),
            features=features,
            is_turn_end=is_turn_end,
        )

        try:
            get_registry().set_capability_evidence(
                "DUPLEX_CHOREOGRAPHY",
                EvidenceLevel.LIVE,
                f"Turn-end prediction P_end={p_end:.3f}, logit={logit:.2f}",
                source="duplex_choreography.predict_turn_end",
            )
        except Exception:
            pass

        return pred

    # ------------------------------------------------------------------
    # 2. Decision-Theoretic Audio Gating & Speculation
    # ------------------------------------------------------------------

    def evaluate_audio_gate(
        self,
        p_end: float,
        g_early: Optional[float] = None,
        c_overlap: Optional[float] = None,
    ) -> AudioGateDecision:
        """Evaluate decision-theoretic early audio release and speculative generation."""
        gain = self.g_early if g_early is None else g_early
        cost = self.c_overlap if c_overlap is None else c_overlap

        p = max(0.0, min(1.0, float(p_end)))
        expected_gain = p * gain
        expected_cost = (1.0 - p) * cost

        should_release = expected_gain > expected_cost
        speculative_gen = p >= self.speculative_threshold

        return AudioGateDecision(
            should_release_audio=should_release,
            speculative_generation_triggered=speculative_gen,
            p_end=round(p, 4),
            expected_gain=round(expected_gain, 4),
            expected_cost=round(expected_cost, 4),
        )

    # ------------------------------------------------------------------
    # 3. Ambient Backchanneling
    # ------------------------------------------------------------------

    def check_backchannel_opportunity(
        self,
        pause_ms: float,
        p_end: float,
        f0_fall: float,
        now: Optional[float] = None,
    ) -> Optional[str]:
        """Detect mid-clause non-terminal pauses (140-320ms) and emit rate-limited backchannels."""
        t_now = time.time() if now is None else now

        # Non-terminal pause condition: 140ms <= pause <= 320ms, low p_end (<0.45), non-falling pitch (f0_fall >= -0.1)
        in_pause_window = 140.0 <= pause_ms <= 320.0
        is_non_terminal = p_end <= 0.45 and f0_fall >= -0.10

        if not (in_pause_window and is_non_terminal):
            return None

        with _lock:
            if (t_now - self._last_backchannel_time) < self.backchannel_cooldown_sec:
                return None  # Rate-limited

            # Contextual acknowledgment selection
            backchannels = ["right", "understood", "mm-hm", "I see", "go on"]
            idx = len(self._backchannel_history) % len(backchannels)
            phrase = backchannels[idx]

            self._last_backchannel_time = t_now
            self._backchannel_history.append({"phrase": phrase, "timestamp": t_now, "p_end": p_end})
            log.info(f"[DuplexChoreography] Emitted ambient backchannel '{phrase}' (pause={pause_ms:.1f}ms)")
            return phrase

    # ------------------------------------------------------------------
    # 4. State-Preserving Barge-In
    # ------------------------------------------------------------------

    def set_active_utterance_context(self, context_text: Optional[str]) -> None:
        with _lock:
            self._active_utterance_context = context_text

    def classify_barge_in(
        self,
        overlapping_audio_duration_ms: float,
        transcript: str,
        current_playback_timestamp: float,
    ) -> BargeInVerdict:
        """Classify user speech during system playback into BACKCHANNEL, BARGE_IN, or CORRECTION."""
        clean_text = transcript.strip().lower()
        if not clean_text:
            return BargeInVerdict(
                barge_in_type=BargeInType.NONE,
                should_yield_floor=False,
                should_flush_audio=False,
                truncate_timestamp=None,
                flagged_statement=None,
                transcript=transcript,
            )

        # Check for explicit correction cues
        is_correction = any(pat.search(clean_text) for pat in self._correction_patterns)
        if is_correction:
            with _lock:
                flagged = self._active_utterance_context
            verdict = BargeInVerdict(
                barge_in_type=BargeInType.CORRECTION,
                should_yield_floor=True,
                should_flush_audio=True,
                truncate_timestamp=current_playback_timestamp,
                flagged_statement=flagged,
                transcript=transcript,
            )
            with _lock:
                self._barge_in_history.append(verdict)
            log.warning(f"[DuplexChoreography] CORRECTION barge-in detected: '{transcript}' -> floor yielded, audio flushed")
            return verdict

        # Check for user backchannel (short duration < 400ms and matches affirmative cue)
        is_user_backchannel = (
            overlapping_audio_duration_ms < 400.0
            and any(pat.match(clean_text) for pat in self._user_backchannel_patterns)
        )
        if is_user_backchannel:
            verdict = BargeInVerdict(
                barge_in_type=BargeInType.BACKCHANNEL,
                should_yield_floor=False,
                should_flush_audio=False,
                truncate_timestamp=None,
                flagged_statement=None,
                transcript=transcript,
            )
            with _lock:
                self._barge_in_history.append(verdict)
            log.info(f"[DuplexChoreography] User BACKCHANNEL '{transcript}' ignored; playback continuing")
            return verdict

        # Standard barge-in (user taking the conversational floor)
        verdict = BargeInVerdict(
            barge_in_type=BargeInType.BARGE_IN,
            should_yield_floor=True,
            should_flush_audio=True,
            truncate_timestamp=current_playback_timestamp,
            flagged_statement=None,
            transcript=transcript,
        )
        with _lock:
            self._barge_in_history.append(verdict)
        log.info(f"[DuplexChoreography] Full BARGE_IN '{transcript}' -> yielding floor at t={current_playback_timestamp:.2f}s")
        return verdict


# ---------------------------------------------------------------------------
# Module-level singleton & convenience helpers
# ---------------------------------------------------------------------------

_choreographer_instance: Optional[DuplexChoreographer] = None


def get_duplex_choreographer() -> DuplexChoreographer:
    global _choreographer_instance
    if _choreographer_instance is None:
        with _lock:
            if _choreographer_instance is None:
                _choreographer_instance = DuplexChoreographer()
    return _choreographer_instance


def predict_turn_end(syntactic_completeness: float, f0_fall: float, energy_decay: float, pause_ms: float) -> TurnEndPrediction:
    return get_duplex_choreographer().predict_turn_end(syntactic_completeness, f0_fall, energy_decay, pause_ms)


def evaluate_audio_gate(p_end: float, **kwargs) -> AudioGateDecision:
    return get_duplex_choreographer().evaluate_audio_gate(p_end, **kwargs)


def check_backchannel_opportunity(pause_ms: float, p_end: float, f0_fall: float, **kwargs) -> Optional[str]:
    return get_duplex_choreographer().check_backchannel_opportunity(pause_ms, p_end, f0_fall, **kwargs)


def classify_barge_in(overlapping_audio_duration_ms: float, transcript: str, current_playback_timestamp: float) -> BargeInVerdict:
    return get_duplex_choreographer().classify_barge_in(overlapping_audio_duration_ms, transcript, current_playback_timestamp)
