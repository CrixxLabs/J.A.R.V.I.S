# error_handler.py
"""
J.A.R.V.I.S — Structured Error Handler & Reliability Governance
================================================================
Intercepts errors, logs them to session/disk, and dynamically demotes
the health status of subsystems in status_registry.py.
"""

import sys
import traceback
from datetime import datetime, timezone
from typing import Optional, Type

from status_registry import SubsystemState, get_registry
from session_logger import log_event


def log_and_demote(
    subsystem: str,
    exception: Exception,
    context: str = "",
    demote_to: SubsystemState = SubsystemState.DEGRADED,
    tb_limit: int = 5
) -> str:
    """
    Log an exception with traceback details, update the session logger,
    and demote the status registry entry for the given subsystem.

    Parameters
    ----------
    subsystem : str
        The upper-case name of the subsystem (e.g. "VOICE_STT").
    exception : Exception
        The caught exception.
    context : str
        Additional debug context (e.g. "during PyAudio initialization").
    demote_to : SubsystemState
        The state to set (usually DEGRADED or OFFLINE).
    tb_limit : int
        Traceback recursion depth limit to prevent excessive logs.

    Returns
    -------
    str
        A clean, simplified error string suitable for passing to speak or UI.
    """
    subsystem = subsystem.strip().upper()
    exc_type, exc_value, exc_tb = sys.exc_info()
    
    # Format a compact traceback
    if exc_tb:
        tb_list = traceback.format_exception(exc_type, exc_value, exc_tb, limit=tb_limit)
        tb_str = "".join(tb_list)
    else:
        tb_str = "".join(traceback.format_exception_only(type(exception), exception))

    err_msg = str(exception) or type(exception).__name__
    detail_msg = f"{context}: {err_msg}" if context else err_msg

    print(f"\n[ERROR][{subsystem}] {detail_msg}")
    print(f"--- Traceback (limit={tb_limit}) ---")
    print(tb_str.strip())
    print("-----------------------------------\n")

    # 1. Update status registry
    registry = get_registry()
    registry.set_status(
        name=subsystem,
        state=demote_to,
        detail=detail_msg
    )

    # 2. Write to session logs (which persists session telemetry)
    log_event(
        event_type="subsystem_failure",
        data={
            "subsystem": subsystem,
            "error_type": type(exception).__name__,
            "message": err_msg,
            "context": context,
            "demoted_to": demote_to.value,
            "traceback": tb_str
        },
        severity="error" if demote_to == SubsystemState.OFFLINE else "warning",
        module=subsystem.lower(),
        tags=["failure", "reliability", subsystem.lower()]
    )

    return detail_msg


def safe_execute(subsystem: str, func, *args, **kwargs):
    """
    Convenience wrapper to run a function safely. 
    If it throws, demotes the subsystem to OFFLINE.
    """
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        log_and_demote(
            subsystem=subsystem,
            exception=exc,
            context=f"Executing {func.__name__}",
            demote_to=SubsystemState.OFFLINE
        )
        return None