# session_logger.py — Detailed Session Logger
# Phase 1 upgrade: structured logs, severity levels, module tagging, auto-save

import json
import datetime
import os
import threading

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LOG_DIR   = os.path.join(BASE_DIR, "logs")
_lock     = threading.Lock()

# Session file named by start time
_session_start = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE       = os.path.join(LOG_DIR, f"session_{_session_start}.json")

# In-memory log for this session
session_data = []

# Valid severity levels
SEVERITY_LEVELS = ["debug", "info", "warning", "error", "critical"]


def log_event(event_type, data, severity="info", module="core", tags=None):
    """
    Log a structured event.
    event_type : string label e.g. "user_input", "action_executed", "error"
    data       : string or dict
    severity   : debug | info | warning | error | critical
    module     : which module fired this e.g. "planner", "executor", "memory"
    tags       : optional list of strings for filtering
    """
    if severity not in SEVERITY_LEVELS:
        severity = "info"

    entry = {
        "time":     datetime.datetime.now().isoformat(),
        "type":     event_type,
        "severity": severity,
        "module":   module,
        "tags":     tags or [],
        "data":     data
    }

    with _lock:
        session_data.append(entry)

    # Auto-save every 10 events
    if len(session_data) % 10 == 0:
        save_session()


def log_user_input(text):
    log_event("user_input", text, severity="info", module="core", tags=["input"])


def log_action(action_name, result, success=True):
    severity = "info" if success else "warning"
    log_event(
        "action_executed",
        {"action": action_name, "result": result, "success": success},
        severity=severity,
        module="executor",
        tags=["action"]
    )


def log_error(message, module="core", exception=None):
    data = {"message": message}
    if exception:
        data["exception"] = str(exception)
    log_event("error", data, severity="error", module=module, tags=["error"])


def log_speak(text):
    log_event("speak", text, severity="debug", module="core", tags=["output"])


def log_startup():
    log_event(
        "startup",
        {"status": "Jarvis online", "session": _session_start},
        severity="info",
        module="core",
        tags=["lifecycle"]
    )


def log_shutdown():
    log_event(
        "shutdown",
        {"status": "Jarvis offline", "total_events": len(session_data)},
        severity="info",
        module="core",
        tags=["lifecycle"]
    )
    save_session()


def get_session_summary():
    """Returns a quick summary of this session's events."""
    total    = len(session_data)
    errors   = sum(1 for e in session_data if e.get("severity") == "error")
    actions  = sum(1 for e in session_data if e.get("type") == "action_executed")
    inputs   = sum(1 for e in session_data if e.get("type") == "user_input")
    return {
        "total_events": total,
        "errors":       errors,
        "actions":      actions,
        "user_inputs":  inputs,
        "session_id":   _session_start
    }


def filter_logs(severity=None, module=None, tag=None, event_type=None):
    """
    Filter in-memory logs by any combination of:
    severity, module, tag, event_type
    """
    results = session_data
    if severity:
        results = [e for e in results if e.get("severity") == severity]
    if module:
        results = [e for e in results if e.get("module") == module]
    if tag:
        results = [e for e in results if tag in e.get("tags", [])]
    if event_type:
        results = [e for e in results if e.get("type") == event_type]
    return results


def save_session():
    """Save current session log to disk."""
    os.makedirs(LOG_DIR, exist_ok=True)
    with _lock:
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[session_logger save error] {e}")