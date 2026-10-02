"""Global Workspace Theory (GWT) Blackboard for J.A.R.V.I.S. — MARK VIII.

Implements a unified, thread-safe cognitive publish/subscribe blackboard architecture
that replaces siloed sensory polling with real-time perceptual state synchronization
and priority-driven attention competition.

Topics:
  - VISION_FOCUS: Active window, screen changes, focused UI elements, OCR grounding.
  - AUDIO_ENERGY: Microphone energy levels, speech detection, user voice interrupts.
  - SYSTEM_TELEMETRY: CPU, RAM, battery, thermal status, active processes.
  - ACTIVE_INTENT: Current user query, recognized intent, active goal/plan.
  - ERROR_SIGNAL: Runtime exceptions, failed API routes, diagnostic alerts.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.global_workspace")

# ── TOPIC CONSTANTS ─────────────────────────────────────────────────────────
TOPIC_VISION_FOCUS = "VISION_FOCUS"
TOPIC_AUDIO_ENERGY = "AUDIO_ENERGY"
TOPIC_SYSTEM_TELEMETRY = "SYSTEM_TELEMETRY"
TOPIC_ACTIVE_INTENT = "ACTIVE_INTENT"
TOPIC_ERROR_SIGNAL = "ERROR_SIGNAL"

KNOWN_TOPICS: Set[str] = {
    TOPIC_VISION_FOCUS,
    TOPIC_AUDIO_ENERGY,
    TOPIC_SYSTEM_TELEMETRY,
    TOPIC_ACTIVE_INTENT,
    TOPIC_ERROR_SIGNAL,
}

# ── PRIORITY LEVELS ─────────────────────────────────────────────────────────
PRIORITY_LOW = 1
PRIORITY_NORMAL = 5
PRIORITY_HIGH = 8
PRIORITY_CRITICAL = 10

# Preemption thresholds
CPU_PREEMPTION_THRESHOLD = 95.0
AUDIO_ENERGY_PREEMPTION_THRESHOLD = 2500.0


class GlobalWorkspace:
    """Thread-safe Global Workspace Blackboard."""

    def __init__(self, max_history: int = 500):
        self._max_history = max_history
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Tuple[str, Callable[[dict], None]]]] = {}
        self._all_subscribers: List[Tuple[str, Callable[[dict], None]]] = []
        self._preemption_handlers: List[Callable[[dict], None]] = []
        self._history: collections.deque = collections.deque(maxlen=max_history)
        self._sub_counter = 0

        # Synchronized perceptual state cache
        self._state: Dict[str, Any] = {
            "vision": {},
            "audio": {},
            "telemetry": {},
            "intent": {},
            "errors": [],
            "last_broadcast": None,
            "preempted": False,
            "preemption_reason": None,
        }

    def publish(
        self,
        topic: str,
        payload: dict,
        source: str = "unknown",
        priority: int = PRIORITY_NORMAL,
    ) -> dict:
        """Publish a message to the blackboard bus."""
        now = time.time()
        topic_str = str(topic).strip().upper()
        prio = max(1, min(10, int(priority)))

        message = {
            "source": str(source),
            "timestamp": now,
            "topic": topic_str,
            "payload": payload if isinstance(payload, dict) else {"data": payload},
            "priority": prio,
        }

        # Check for preemption trigger
        is_preemptive, reason = self._check_preemption(message)

        with self._lock:
            self._history.append(message)
            self._update_state_cache(message)
            if is_preemptive:
                self._state["preempted"] = True
                self._state["preemption_reason"] = reason

            # Gather subscribers
            topic_subs = list(self._subscribers.get(topic_str, []))
            all_subs = list(self._all_subscribers)
            preempt_handlers = list(self._preemption_handlers) if is_preemptive else []

        # Dispatch callbacks outside lock
        for _, cb in topic_subs:
            try:
                cb(message)
            except Exception as exc:
                log.error(f"[GWT] Subscriber error on topic {topic_str}: {exc}")

        for _, cb in all_subs:
            try:
                cb(message)
            except Exception as exc:
                log.error(f"[GWT] Global subscriber error: {exc}")

        if is_preemptive:
            for handler in preempt_handlers:
                try:
                    handler(message)
                except Exception as exc:
                    log.error(f"[GWT] Preemption handler error: {exc}")

        # Update status registry evidence
        try:
            get_registry().set_capability_evidence(
                "GLOBAL_WORKSPACE",
                EvidenceLevel.LIVE,
                f"Broadcast {topic_str} from {source} (prio={prio})",
                source="global_workspace",
            )
        except Exception:
            pass

        return message

    def _check_preemption(self, message: dict) -> Tuple[bool, Optional[str]]:
        """Evaluate whether message warrants attention preemption."""
        topic = message["topic"]
        payload = message.get("payload", {})
        priority = message.get("priority", PRIORITY_NORMAL)

        if priority >= PRIORITY_CRITICAL:
            return True, f"Critical priority message on {topic}"

        if topic == TOPIC_SYSTEM_TELEMETRY:
            cpu = float(payload.get("cpu_percent", 0.0))
            if cpu >= CPU_PREEMPTION_THRESHOLD:
                return True, f"High CPU spike ({cpu:.1f}% >= {CPU_PREEMPTION_THRESHOLD}%)"

        elif topic == TOPIC_AUDIO_ENERGY:
            speech = payload.get("speech_detected", False)
            energy = float(payload.get("energy", 0.0))
            if speech or energy >= AUDIO_ENERGY_PREEMPTION_THRESHOLD:
                return True, f"Microphone speech burst (energy={energy:.0f})"

        elif topic == TOPIC_ERROR_SIGNAL:
            severity = str(payload.get("severity", "error")).lower()
            if severity in ("critical", "fatal", "panic"):
                return True, f"Critical error signal: {payload.get('message', 'unknown')}"

        return False, None

    def _update_state_cache(self, message: dict) -> None:
        """Update the synchronized perceptual state snapshot."""
        topic = message["topic"]
        payload = message["payload"]

        if topic == TOPIC_VISION_FOCUS:
            self._state["vision"] = dict(payload)
        elif topic == TOPIC_AUDIO_ENERGY:
            self._state["audio"] = dict(payload)
        elif topic == TOPIC_SYSTEM_TELEMETRY:
            self._state["telemetry"] = dict(payload)
        elif topic == TOPIC_ACTIVE_INTENT:
            self._state["intent"] = dict(payload)
        elif topic == TOPIC_ERROR_SIGNAL:
            errors = list(self._state["errors"])
            errors.append(payload)
            self._state["errors"] = errors[-20:]  # keep last 20

        self._state["last_broadcast"] = {
            "topic": topic,
            "source": message["source"],
            "timestamp": message["timestamp"],
            "priority": message["priority"],
        }

    def subscribe(self, topic: Optional[str], callback: Callable[[dict], None]) -> str:
        """Subscribe a callback to a specific topic or '*' for all topics."""
        with self._lock:
            self._sub_counter += 1
            sub_id = f"sub_{self._sub_counter}"
            if not topic or topic == "*":
                self._all_subscribers.append((sub_id, callback))
            else:
                topic_str = str(topic).strip().upper()
                if topic_str not in self._subscribers:
                    self._subscribers[topic_str] = []
                self._subscribers[topic_str].append((sub_id, callback))
            return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Unsubscribe by subscription ID."""
        with self._lock:
            removed = False
            for topic, subs in list(self._subscribers.items()):
                new_subs = [s for s in subs if s[0] != sub_id]
                if len(new_subs) != len(subs):
                    self._subscribers[topic] = new_subs
                    removed = True
            new_all = [s for s in self._all_subscribers if s[0] != sub_id]
            if len(new_all) != len(self._all_subscribers):
                self._all_subscribers = new_all
                removed = True
            return removed

    def register_preemption_handler(self, handler: Callable[[dict], None]) -> None:
        """Register a handler called immediately when preemption occurs."""
        with self._lock:
            self._preemption_handlers.append(handler)

    def is_preempted(self) -> bool:
        """Check whether preemption is currently active."""
        with self._lock:
            return bool(self._state.get("preempted", False))

    def clear_preemption(self) -> None:
        """Clear the preemption flag."""
        with self._lock:
            self._state["preempted"] = False
            self._state["preemption_reason"] = None

    def get_current_workspace_state(self) -> dict:
        """Return a synchronized perceptual snapshot for planner and brain."""
        with self._lock:
            return {
                "vision": dict(self._state["vision"]),
                "audio": dict(self._state["audio"]),
                "telemetry": dict(self._state["telemetry"]),
                "intent": dict(self._state["intent"]),
                "errors": list(self._state["errors"]),
                "last_broadcast": dict(self._state["last_broadcast"]) if self._state["last_broadcast"] else None,
                "preempted": bool(self._state["preempted"]),
                "preemption_reason": self._state["preemption_reason"],
            }

    def get_history(self, topic: Optional[str] = None, limit: int = 50) -> List[dict]:
        """Return recent messages, optionally filtered by topic."""
        with self._lock:
            messages = list(self._history)
        if topic and topic != "*":
            t_upper = str(topic).strip().upper()
            messages = [m for m in messages if m["topic"] == t_upper]
        return messages[-limit:]

    def clear(self) -> None:
        """Reset workspace state and history."""
        with self._lock:
            self._history.clear()
            self._state = {
                "vision": {},
                "audio": {},
                "telemetry": {},
                "intent": {},
                "errors": [],
                "last_broadcast": None,
                "preempted": False,
                "preemption_reason": None,
            }


# ── SINGLETON INSTANCE & CONVENIENCE FUNCTIONS ──────────────────────────────
_workspace = GlobalWorkspace()


def get_workspace() -> GlobalWorkspace:
    """Get the global workspace singleton."""
    return _workspace


def publish(topic: str, payload: dict, source: str = "unknown", priority: int = PRIORITY_NORMAL) -> dict:
    """Publish a message to the global workspace."""
    return _workspace.publish(topic, payload, source=source, priority=priority)


def subscribe(topic: Optional[str], callback: Callable[[dict], None]) -> str:
    """Subscribe to topic messages in the global workspace."""
    return _workspace.subscribe(topic, callback)


def unsubscribe(sub_id: str) -> bool:
    """Unsubscribe from the global workspace."""
    return _workspace.unsubscribe(sub_id)


def get_current_workspace_state() -> dict:
    """Get the synchronized workspace state snapshot."""
    return _workspace.get_current_workspace_state()


def is_preempted() -> bool:
    """Check if preemption is active."""
    return _workspace.is_preempted()


def clear_preemption() -> None:
    """Clear active preemption."""
    _workspace.clear_preemption()


def register_preemption_handler(handler: Callable[[dict], None]) -> None:
    """Register preemption notification callback."""
    _workspace.register_preemption_handler(handler)


# ── CONVENIENCE HOOKS FOR SENSORY DAEMONS ───────────────────────────────────

def hook_vision(active_app: str, active_title: str, screen_changed: bool = False, details: Optional[dict] = None) -> dict:
    """Helper for observer/vision to publish active visual focus."""
    payload = {
        "active_app": str(active_app),
        "active_title": str(active_title),
        "screen_changed": bool(screen_changed),
    }
    if details and isinstance(details, dict):
        payload.update(details)
    return publish(TOPIC_VISION_FOCUS, payload, source="observer.vision", priority=PRIORITY_NORMAL)


def hook_audio(energy: float, speech_detected: bool = False, details: Optional[dict] = None) -> dict:
    """Helper for listener to publish audio energy and speech status."""
    prio = PRIORITY_HIGH if speech_detected or energy >= AUDIO_ENERGY_PREEMPTION_THRESHOLD else PRIORITY_NORMAL
    payload = {
        "energy": float(energy),
        "speech_detected": bool(speech_detected),
    }
    if details and isinstance(details, dict):
        payload.update(details)
    return publish(TOPIC_AUDIO_ENERGY, payload, source="listener.audio", priority=prio)


def hook_telemetry(cpu_percent: float, ram_percent: float, battery_percent: float = -1, details: Optional[dict] = None) -> dict:
    """Helper for system monitor / observer to publish hardware telemetry."""
    prio = PRIORITY_CRITICAL if cpu_percent >= CPU_PREEMPTION_THRESHOLD else PRIORITY_NORMAL
    payload = {
        "cpu_percent": float(cpu_percent),
        "ram_percent": float(ram_percent),
        "battery_percent": float(battery_percent),
    }
    if details and isinstance(details, dict):
        payload.update(details)
    return publish(TOPIC_SYSTEM_TELEMETRY, payload, source="observer.telemetry", priority=prio)


def hook_intent(query: str, intent: Optional[str] = None, plan: Optional[dict] = None) -> dict:
    """Helper for planner / core to publish current active intent."""
    payload = {
        "query": str(query),
        "intent": str(intent or "unknown"),
        "plan": plan or {},
    }
    return publish(TOPIC_ACTIVE_INTENT, payload, source="planner.intent", priority=PRIORITY_HIGH)


def hook_error(subsystem: str, error_message: str, severity: str = "error", details: Optional[dict] = None) -> dict:
    """Helper for error handler to publish error signals."""
    prio = PRIORITY_CRITICAL if severity.lower() in ("critical", "fatal") else PRIORITY_HIGH
    payload = {
        "subsystem": str(subsystem),
        "message": str(error_message),
        "severity": str(severity),
    }
    if details and isinstance(details, dict):
        payload.update(details)
    return publish(TOPIC_ERROR_SIGNAL, payload, source=f"error_handler.{subsystem}", priority=prio)
