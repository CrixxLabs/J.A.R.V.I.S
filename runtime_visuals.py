"""Thread-safe operational-state hub and localhost SSE bridge for the native UI.

Python remains authoritative. Publishers never wait for UI clients: each subscriber has
a small bounded queue and stale intermediate snapshots are discarded under backpressure.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import queue
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator


VALID_STATES = {"dormant", "idle", "listening", "thinking", "speaking", "executing", "alert"}
STATE_PRIORITY = {"dormant": 0, "idle": 10, "listening": 40, "thinking": 50,
                  "executing": 60, "speaking": 70, "alert": 100}


class VisualStateHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._base_state = "dormant"
        self._activities: dict[str, dict[str, Any]] = {}
        self._subscribers: set[queue.Queue[str]] = set()
        self._sequence = 0
        self._snapshot: dict[str, Any] = {
            "operational_state": "dormant",
            "affect": "neutral",
            "affect_tension": 0.0,
            "listening_amplitude": None,
            "speech_amplitude": None,
            "thinking_intensity": 1.0,
            "execution_intensity": 1.0,
            "alert_severity": 1.0,
            "task_count": 0,
            "memory_usage": None,
            "vision_status": None,
            "provider_model": None,
            "current_action": None,
            "current_user_transcript": None,
            "current_jarvis_response": None,
            "alert_information": None,
            "sequence": 0,
            "updated_at": time.time(),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def set_base_state(self, state: str, **fields: Any) -> None:
        state = _state(state)
        with self._lock:
            self._base_state = state
            self._apply_locked(fields)
            self._resolve_locked()

    def update(self, **fields: Any) -> None:
        with self._lock:
            if self._apply_locked(fields):
                self._emit_locked()

    def begin(self, state: str, **fields: Any) -> str:
        state = _state(state)
        token = uuid.uuid4().hex
        with self._lock:
            self._activities[token] = {"state": state, "started": time.monotonic(), "fields": fields}
            self._apply_locked(fields)
            self._resolve_locked()
        return token

    def end(self, token: str) -> None:
        with self._lock:
            activity = self._activities.pop(token, None)
            if activity is None:
                return
            state = activity["state"]
            cleanup = {}
            if state == "executing": cleanup["current_action"] = None
            if state == "alert": cleanup["alert_information"] = None
            self._apply_locked(cleanup)
            self._resolve_locked()

    @contextlib.contextmanager
    def activity(self, state: str, **fields: Any) -> Iterator[None]:
        token = self.begin(state, **fields)
        try:
            yield
        finally:
            self.end(token)

    def subscribe(self) -> queue.Queue[str]:
        channel: queue.Queue[str] = queue.Queue(maxsize=8)
        with self._lock:
            self._subscribers.add(channel)
            channel.put_nowait(self._encode_locked())
        return channel

    def unsubscribe(self, channel: queue.Queue[str]) -> None:
        with self._lock:
            self._subscribers.discard(channel)

    def _resolve_locked(self) -> None:
        state = self._base_state
        active = None
        if self._activities:
            active = max(self._activities.values(), key=lambda item: (STATE_PRIORITY[item["state"]], item["started"]))
            state = active["state"]
        executing = [item for item in self._activities.values() if item["state"] == "executing"]
        self._snapshot["task_count"] = len(executing)
        if active is not None and active["state"] == "executing":
            action = active["fields"].get("current_action")
            if isinstance(action, dict):
                action = action.get("action") or action.get("name") or "task"
            self._snapshot["current_action"] = action
        elif not executing:
            self._snapshot["current_action"] = None
        if self._snapshot["operational_state"] != state:
            self._snapshot["operational_state"] = state
        self._emit_locked()

    def _apply_locked(self, fields: dict[str, Any]) -> bool:
        changed = False
        for key, value in fields.items():
            if key not in self._snapshot or key in {"sequence", "updated_at", "operational_state"}:
                continue
            if key == "current_action" and isinstance(value, dict):
                value = value.get("action") or value.get("name") or "task"
            if self._snapshot[key] != value:
                self._snapshot[key] = value
                changed = True
        return changed

    def _emit_locked(self) -> None:
        self._sequence += 1
        self._snapshot["sequence"] = self._sequence
        self._snapshot["updated_at"] = time.time()
        payload = self._encode_locked()
        for channel in tuple(self._subscribers):
            try:
                channel.put_nowait(payload)
            except queue.Full:
                try:
                    channel.get_nowait()
                    channel.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    pass

    def _encode_locked(self) -> str:
        return json.dumps(self._snapshot, ensure_ascii=False, separators=(",", ":"), default=str)


hub = VisualStateHub()
class _VisualBridgeServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


_server: _VisualBridgeServer | None = None
_server_thread: threading.Thread | None = None


def set_base_state(state: str, **fields: Any) -> None:
    hub.set_base_state(state, **fields)


def update(**fields: Any) -> None:
    hub.update(**fields)


def activity(state: str, **fields: Any):
    return hub.activity(state, **fields)


def alert(information: str, severity: float = 1.0) -> str:
    return hub.begin("alert", alert_information=str(information), alert_severity=_level(severity))


def clear_alert(token: str) -> None:
    hub.end(token)


def visual_activity(state: str, field_from_argument: str | None = None):
    """Decorator for real synchronous runtime boundaries such as speak/plan/execute."""
    def decorate(function: Callable):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            fields = {}
            if field_from_argument and args:
                fields[field_from_argument] = args[0]
            with hub.activity(state, **fields):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def start_bridge(host: str | None = None, port: int | None = None) -> tuple[str, int]:
    global _server, _server_thread
    if _server is not None:
        return _server.server_address
    bind_host = host or "127.0.0.1"
    if bind_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The native visual bridge is restricted to localhost.")
    bind_port = int(port if port is not None else os.getenv("JARVIS_VISUAL_PORT", "8765"))
    try:
        _server = _VisualBridgeServer((bind_host, bind_port), _handler_type(hub))
    except OSError as exc:
        _server = None
        print(f"[VisualBridge] Unavailable on {bind_host}:{bind_port}: {exc}. JARVIS will continue without the native UI.")
        return bind_host, bind_port
    _server.daemon_threads = True
    _server_thread = threading.Thread(target=_server.serve_forever, name="jarvis-visual-bridge", daemon=True)
    _server_thread.start()
    print(f"[VisualBridge] Listening on http://{_server.server_address[0]}:{_server.server_address[1]}")
    return _server.server_address


def stop_bridge() -> None:
    global _server, _server_thread
    server, thread = _server, _server_thread
    _server = _server_thread = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2)


def _handler_type(state_hub: VisualStateHub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/v1/snapshot":
                body = json.dumps(state_hub.snapshot(), ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path != "/v1/events":
                self.send_error(404)
                return
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            channel = state_hub.subscribe()
            try:
                while True:
                    try:
                        payload = channel.get(timeout=12)
                        frame = f"event: snapshot\ndata: {payload}\n\n".encode("utf-8")
                    except queue.Empty:
                        frame = b": keepalive\n\n"
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                state_hub.unsubscribe(channel)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def _state(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in VALID_STATES:
        raise ValueError(f"Unsupported operational state: {value}")
    return normalized


def _level(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


if __name__ == "__main__":
    # Lightweight diagnostic bridge: JSON lines can drive snapshots without starting JARVIS.
    # Example: {"operational_state":"thinking","provider_model":"test"}
    start_bridge()
    print("[VisualBridge] Diagnostic mode ready; enter JSON snapshots, Ctrl+C to stop.")
    try:
        while True:
            line = input()
            data = json.loads(line)
            state = data.pop("operational_state", None)
            if state:
                set_base_state(state, **data)
            else:
                update(**data)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        stop_bridge()
