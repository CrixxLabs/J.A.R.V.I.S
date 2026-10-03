"""Continuous Ambient Co-Presence & Deixis Resolution for J.A.R.V.I.S. — MARK VIII.

Module AQ:
  1. Multimodal Event Fusion:
     - Aggregates window focus (Win32), editor LSP diagnostics, cursor selection,
       and speech stream with millisecond-aligned monotonic timestamps.
  2. Referent Registry:
     - Tracks active entities (files, AST functions, UI controls, 3D elements)
       with ACT-R base-level recency/frequency activation.
  3. Ellipsis & Pronoun Disambiguation:
     - Resolves deictic terms ("that", "it", "the error", "that function") by combining
       ACT-R salience, type compatibility constraints, and temporal dwell intervals.
  4. Parametric Action Replay:
     - Interprets comparative shorthand ("make it thinner", "do that again", "run it again with -v")
       by modifying numeric attributes in the structured action log.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.ambient_copresence")

_lock = threading.RLock()


class EventType(str, Enum):
    WINDOW_FOCUS = "WINDOW_FOCUS"
    EDITOR_DIAGNOSTIC = "EDITOR_DIAGNOSTIC"
    CURSOR_SELECTION = "CURSOR_SELECTION"
    SPEECH_STREAM = "SPEECH_STREAM"
    ACTION_EXECUTION = "ACTION_EXECUTION"


@dataclass
class MultimodalEvent:
    event_id: str
    event_type: EventType
    timestamp_mono_ms: float
    payload: Dict[str, Any]
    source: str = "ambient_fusion"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


@dataclass
class ReferentEntity:
    entity_id: str
    entity_type: str  # "file", "ast_function", "ui_control", "cad_feature", "diagnostic_error", "terminal_command"
    name: str
    path_or_identifier: str
    last_seen_timestamp: float
    dwell_time_ms: float = 0.0
    access_history: List[float] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeixisResolutionResult:
    resolved_entity: Optional[ReferentEntity]
    salience_score: float
    confidence: float
    cue_text: str
    matched_type: Optional[str]
    rationale: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.resolved_entity:
            d["resolved_entity"] = self.resolved_entity.to_dict()
        return d


@dataclass
class ParametricReplayResult:
    action_verb: str
    target_entity: Optional[str]
    modified_params: Dict[str, Any]
    original_params: Dict[str, Any]
    replay_summary: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AmbientCopresenceTracker:
    """Continuous Ambient Co-Presence and Deictic Referent Resolver."""

    def __init__(self, decay_d: float = 0.5):
        self.decay_d = decay_d
        self._referents: Dict[str, ReferentEntity] = {}
        self._action_log: List[Dict[str, Any]] = []
        self._event_stream: List[MultimodalEvent] = []

    # ------------------------------------------------------------------
    # 1. Multimodal Event Fusion & Referent Registration
    # ------------------------------------------------------------------

    def ingest_event(
        self,
        event_type: EventType,
        payload: Dict[str, Any],
        timestamp_mono_ms: Optional[float] = None,
        source: str = "ambient_fusion",
    ) -> MultimodalEvent:
        """Ingest synchronized multimodal event and update referent entity activation."""
        t_ms = time.monotonic() * 1000.0 if timestamp_mono_ms is None else timestamp_mono_ms
        eid = f"ev_{uuid.uuid4().hex[:8]}"

        event = MultimodalEvent(
            event_id=eid,
            event_type=event_type,
            timestamp_mono_ms=t_ms,
            payload=payload,
            source=source,
        )

        with _lock:
            self._event_stream.append(event)
            self._process_event_payload(event_type, payload, t_ms)

        try:
            get_registry().set_capability_evidence(
                "AMBIENT_COPRESENCE",
                EvidenceLevel.LIVE,
                f"Ingested {event_type.value}, referents active: {len(self._referents)}",
                source="ambient_copresence.ingest_event",
            )
        except Exception:
            pass

        return event

    def _process_event_payload(self, event_type: EventType, payload: Dict[str, Any], t_ms: float) -> None:
        t_sec = t_ms / 1000.0
        if event_type == EventType.WINDOW_FOCUS:
            app_name = payload.get("app_name", "unknown_app")
            title = payload.get("window_title", app_name)
            self._register_referent("ui_control", title, app_name, t_sec, dwell_delta_ms=payload.get("dwell_ms", 100.0))

        elif event_type == EventType.EDITOR_DIAGNOSTIC:
            file_path = payload.get("file_path", "")
            err_msg = payload.get("message", "error")
            line = payload.get("line", 1)
            ident = f"{file_path}:{line}"
            self._register_referent("diagnostic_error", f"Error at {ident}: {err_msg}", ident, t_sec, metadata=payload)

        elif event_type == EventType.CURSOR_SELECTION:
            file_path = payload.get("file_path", "")
            symbol = payload.get("symbol", "")
            selected_text = payload.get("selected_text", "")
            if symbol:
                self._register_referent("ast_function", symbol, f"{file_path}#{symbol}", t_sec, dwell_delta_ms=payload.get("dwell_ms", 500.0))
            if file_path:
                self._register_referent("file", file_path.split("/")[-1], file_path, t_sec)

        elif event_type == EventType.ACTION_EXECUTION:
            verb = payload.get("verb", "execute")
            target = payload.get("target", "")
            params = payload.get("params", {})
            self._action_log.append({
                "action_id": f"act_{len(self._action_log)+1}",
                "verb": verb,
                "target": target,
                "params": params,
                "timestamp": t_sec,
            })
            if target:
                self._register_referent("cad_feature" if "cad" in verb else "terminal_command", target, target, t_sec)

    def _register_referent(
        self,
        entity_type: str,
        name: str,
        path_or_identifier: str,
        t_sec: float,
        dwell_delta_ms: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReferentEntity:
        key = f"{entity_type}::{path_or_identifier}"
        if key in self._referents:
            ref = self._referents[key]
            ref.last_seen_timestamp = t_sec
            ref.dwell_time_ms += dwell_delta_ms
            ref.access_history.append(t_sec)
            if metadata:
                ref.metadata.update(metadata)
        else:
            ref = ReferentEntity(
                entity_id=f"ref_{uuid.uuid4().hex[:8]}",
                entity_type=entity_type,
                name=name,
                path_or_identifier=path_or_identifier,
                last_seen_timestamp=t_sec,
                dwell_time_ms=dwell_delta_ms,
                access_history=[t_sec],
                metadata=metadata or {},
            )
            self._referents[key] = ref
        return ref

    # ------------------------------------------------------------------
    # 2. Deictic Referent & Pronoun Disambiguation
    # ------------------------------------------------------------------

    def compute_salience(self, entity: ReferentEntity, now_sec: float, cue_text: str) -> float:
        """Compute salience combining ACT-R base-level activation, dwell duration, and type compatibility."""
        # Base-level activation
        sum_decay = 0.0
        for t_k in entity.access_history:
            delta = max(0.001, now_sec - t_k)
            sum_decay += math.pow(delta, -self.decay_d)
        b_i = math.log(max(1e-6, sum_decay))

        # Dwell bonus (log scale of dwell ms)
        dwell_bonus = math.log1p(entity.dwell_time_ms / 100.0) * 0.5

        # Type cue compatibility bonus
        type_bonus = 0.0
        clean_cue = cue_text.lower()
        if "error" in clean_cue or "bug" in clean_cue or "warning" in clean_cue:
            if entity.entity_type == "diagnostic_error":
                type_bonus += 3.0
        elif "function" in clean_cue or "method" in clean_cue or "def" in clean_cue:
            if entity.entity_type == "ast_function":
                type_bonus += 3.0
        elif "file" in clean_cue or "module" in clean_cue:
            if entity.entity_type == "file":
                type_bonus += 3.0
        elif "button" in clean_cue or "window" in clean_cue or "ui" in clean_cue:
            if entity.entity_type == "ui_control":
                type_bonus += 3.0
        elif "cad" in clean_cue or "part" in clean_cue or "sketch" in clean_cue:
            if entity.entity_type == "cad_feature":
                type_bonus += 3.0

        return b_i + dwell_bonus + type_bonus

    def resolve_deixis(self, cue_text: str, now_sec: Optional[float] = None) -> DeixisResolutionResult:
        """Resolve ambiguous or deictic expressions ('that', 'it', 'the error', 'that function')."""
        t_now = (time.monotonic() if now_sec is None else now_sec)
        with _lock:
            if not self._referents:
                return DeixisResolutionResult(
                    resolved_entity=None,
                    salience_score=0.0,
                    confidence=0.0,
                    cue_text=cue_text,
                    matched_type=None,
                    rationale="Referent registry is empty",
                )

            scored = []
            for ref in self._referents.values():
                s = self.compute_salience(ref, t_now, cue_text)
                scored.append((s, ref))

            scored.sort(key=lambda x: x[0], reverse=True)
            top_score, top_ref = scored[0]

            # Confidence based on score gap to second candidate
            if len(scored) > 1:
                gap = top_score - scored[1][0]
                confidence = 1.0 / (1.0 + math.exp(-gap))
            else:
                confidence = 0.95

            rationale = f"Resolved '{cue_text}' to {top_ref.entity_type} '{top_ref.name}' (salience={top_score:.2f}, conf={confidence:.2f})"
            log.info(f"[AmbientCopresence] {rationale}")

            return DeixisResolutionResult(
                resolved_entity=top_ref,
                salience_score=round(top_score, 4),
                confidence=round(confidence, 4),
                cue_text=cue_text,
                matched_type=top_ref.entity_type,
                rationale=rationale,
            )

    # ------------------------------------------------------------------
    # 3. Parametric Action Replay
    # ------------------------------------------------------------------

    def replay_parametric_action(self, modifier_text: str) -> Optional[ParametricReplayResult]:
        """Interpret comparative shorthand on last action (e.g., 'make it thinner', 'do that again')."""
        with _lock:
            if not self._action_log:
                return None
            last_action = self._action_log[-1]

        verb = last_action["verb"]
        target = last_action["target"]
        orig_params = dict(last_action["params"])
        new_params = dict(orig_params)
        clean = modifier_text.lower().strip()

        # Shorthand modifications
        if "thinner" in clean or "smaller" in clean or "decrease" in clean:
            for k in ["thickness", "depth", "width", "radius", "scale", "height"]:
                if k in new_params and isinstance(new_params[k], (int, float)):
                    new_params[k] = round(float(new_params[k]) * 0.5, 3)
            summary = f"Reduced dimensions for '{verb}' on '{target}'"

        elif "thicker" in clean or "larger" in clean or "bigger" in clean or "increase" in clean:
            for k in ["thickness", "depth", "width", "radius", "scale", "height"]:
                if k in new_params and isinstance(new_params[k], (int, float)):
                    new_params[k] = round(float(new_params[k]) * 1.5, 3)
            summary = f"Expanded dimensions for '{verb}' on '{target}'"

        elif "again" in clean or "re-run" in clean or "repeat" in clean:
            # Check for numeric overrides, e.g., "with depth 25"
            match = re.search(r"(\w+)\s*=\s*(\d+(?:\.\d+)?)", clean) or re.search(r"(\w+)\s+(\d+(?:\.\d+)?)", clean)
            if match:
                pname, pval = match.group(1), float(match.group(2))
                if pname in new_params or pname in ["depth", "width", "height", "thickness"]:
                    new_params[pname] = pval
                    summary = f"Replayed '{verb}' on '{target}' with {pname}={pval}"
                else:
                    summary = f"Replayed '{verb}' on '{target}'"
            else:
                summary = f"Exact replay of '{verb}' on '{target}'"
        else:
            summary = f"Replayed '{verb}' on '{target}'"

        result = ParametricReplayResult(
            action_verb=verb,
            target_entity=target,
            modified_params=new_params,
            original_params=orig_params,
            replay_summary=summary,
        )
        log.info(f"[AmbientCopresence] Parametric Replay: {summary}")
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_ambient_instance: Optional[AmbientCopresenceTracker] = None


def get_ambient_tracker() -> AmbientCopresenceTracker:
    global _ambient_instance
    if _ambient_instance is None:
        with _lock:
            if _ambient_instance is None:
                _ambient_instance = AmbientCopresenceTracker()
    return _ambient_instance


def ingest_event(event_type: EventType, payload: Dict[str, Any], **kwargs) -> MultimodalEvent:
    return get_ambient_tracker().ingest_event(event_type, payload, **kwargs)


def resolve_deixis(cue_text: str, **kwargs) -> DeixisResolutionResult:
    return get_ambient_tracker().resolve_deixis(cue_text, **kwargs)


def replay_parametric_action(modifier_text: str) -> Optional[ParametricReplayResult]:
    return get_ambient_tracker().replay_parametric_action(modifier_text)
