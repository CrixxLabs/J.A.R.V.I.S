"""Proactive Context-Aware Interruption Engine for J.A.R.V.I.S.

Non-blocking background telemetry daemon that monitors system health, obligations,
and user context to deliver high-priority spoken warnings without requiring wake word.

Monitors:
- Battery level (<15% and discharging)
- CPU/GPU thermal load (>82°C or sustained >90% utilization)
- Impending calendar obligations (within 60 minutes)

Interruption arbiter enforces 15-minute cooldown per condition to prevent spam.
"""
from __future__ import annotations

import datetime
import threading
import time
from typing import Callable, Dict, Optional

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    _PSUTIL_AVAILABLE = False

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

# Monitoring thresholds
BATTERY_THRESHOLD = 15.0  # Warn below 15%
THERMAL_THRESHOLD = 82.0  # °C
CPU_LOAD_THRESHOLD = 90.0  # Sustained % utilization
OBLIGATION_WINDOW_MINUTES = 60  # Warn for events within 60 minutes

# Cooldown configuration
CONDITION_COOLDOWN_SECONDS = 900  # 15 minutes per condition
POLL_INTERVAL_SECONDS = 30  # Background telemetry loop interval

_daemon_thread: Optional[threading.Thread] = None
_daemon_running = False
_daemon_lock = threading.Lock()

# Cooldown tracking: condition_key -> last_notification_timestamp
_cooldowns: Dict[str, float] = {}
_cooldown_lock = threading.Lock()

# Callback hooks for jarvis.py integration
_speak_callback: Optional[Callable[[str], None]] = None
_active_getter: Optional[Callable[[], bool]] = None


def init(speak_fn: Callable[[str], None], active_getter: Callable[[], bool]) -> None:
    """Initialize the proactive daemon with callback hooks.

    Args:
        speak_fn: Function to invoke for spoken warnings (e.g., jarvis_tts.stream_speech)
        active_getter: Function returning True if JARVIS is active/awake
    """
    global _speak_callback, _active_getter
    _speak_callback = speak_fn
    _active_getter = active_getter
    print("[ProactiveDaemon] Initialized with speak and active callbacks.")


def start_daemon() -> bool:
    """Start the background monitoring daemon thread.

    Returns:
        True if started successfully, False otherwise
    """
    global _daemon_thread, _daemon_running
    registry = get_registry()

    if _daemon_running:
        return True

    if not _speak_callback or not _active_getter:
        registry.set_capability_evidence(
            "PROACTIVE_DAEMON", EvidenceLevel.BROKEN,
            "Missing speak or active callbacks", source="proactive daemon"
        )
        return False

    with _daemon_lock:
        if _daemon_running:
            return True

        _daemon_running = True
        _daemon_thread = threading.Thread(target=_daemon_loop, daemon=True, name="ProactiveDaemon")
        _daemon_thread.start()

    registry.set_capability_evidence(
        "PROACTIVE_DAEMON", EvidenceLevel.LIVE,
        "Background monitoring thread started", source="proactive daemon"
    )
    print("[ProactiveDaemon] Background monitoring thread started.")
    return True


def stop_daemon() -> None:
    """Stop the background monitoring daemon."""
    global _daemon_running
    with _daemon_lock:
        _daemon_running = False
    print("[ProactiveDaemon] Daemon stop requested.")


def _can_notify(condition_key: str) -> bool:
    """Check if a condition can trigger notification (cooldown expired)."""
    with _cooldown_lock:
        last_time = _cooldowns.get(condition_key, 0.0)
        now = time.time()
        if (now - last_time) >= CONDITION_COOLDOWN_SECONDS:
            _cooldowns[condition_key] = now
            return True
        return False


def _speak_proactive(message: str) -> None:
    """Invoke proactive speech output without wake word."""
    if not _speak_callback:
        return
    try:
        _speak_callback(message)
    except Exception as exc:
        print(f"[ProactiveDaemon] Speak callback failed: {exc}")


def _check_battery() -> Optional[str]:
    """Check battery status and return warning message if critical."""
    if not _PSUTIL_AVAILABLE or psutil is None:
        return None

    try:
        if not hasattr(psutil, "sensors_battery"):
            return None
        battery = psutil.sensors_battery()
        if battery is None:
            return None

        percent = battery.percent
        plugged = battery.power_plugged

        if percent < BATTERY_THRESHOLD and not plugged:
            if _can_notify("battery_low"):
                return f"Sir, battery level is critically low at {int(percent)} percent and discharging. I recommend connecting to power immediately."
    except Exception as exc:
        print(f"[ProactiveDaemon] Battery check failed: {exc}")

    return None


def _check_thermals() -> Optional[str]:
    """Check CPU/GPU temperatures and return warning if overheating."""
    if not _PSUTIL_AVAILABLE or psutil is None:
        return None

    try:
        if not hasattr(psutil, "sensors_temperatures"):
            return None
        temps = psutil.sensors_temperatures()

        if not temps:
            return None

        max_temp = 0.0
        hottest_component = ""

        for sensor_name, entries in temps.items():
            for entry in entries:
                if entry.current > max_temp:
                    max_temp = entry.current
                    hottest_component = f"{sensor_name} {entry.label or ''}".strip()

        if max_temp > THERMAL_THRESHOLD:
            if _can_notify("thermal_high"):
                return f"Sir, thermal warning. {hottest_component} is at {int(max_temp)} degrees Celsius. System cooling may be insufficient."
    except Exception as exc:
        print(f"[ProactiveDaemon] Thermal check failed: {exc}")

    return None


def _check_cpu_load() -> Optional[str]:
    """Check sustained high CPU load and return warning."""
    if not _PSUTIL_AVAILABLE or psutil is None:
        return None

    try:
        # Sample CPU over 3 seconds to detect sustained load
        cpu_percent = psutil.cpu_percent(interval=0.1)

        if cpu_percent > CPU_LOAD_THRESHOLD:
            if _can_notify("cpu_load_high"):
                return f"Sir, CPU load is sustained at {int(cpu_percent)} percent. Background processes may be consuming significant resources."
    except Exception as exc:
        print(f"[ProactiveDaemon] CPU load check failed: {exc}")

    return None


def _check_obligations() -> Optional[str]:
    """Check for impending obligations within the time window."""
    try:
        import obligations
        obligations._refresh_overdue()  # Ensure overdue status is current

        now = datetime.datetime.now()
        upcoming_threshold = now + datetime.timedelta(minutes=OBLIGATION_WINDOW_MINUTES)

        pending_obligations = [
            ob for ob in obligations._obligations
            if ob.get("status") in ("pending", "in_progress")
        ]

        for ob in pending_obligations:
            due_str = ob.get("due_date", "")
            if not due_str:
                continue

            try:
                due_dt = datetime.datetime.fromisoformat(due_str)
                if now < due_dt <= upcoming_threshold:
                    minutes_remaining = int((due_dt - now).total_seconds() / 60)
                    ob_type = ob.get("type", "task")
                    ob_title = ob.get("title", "Unknown")

                    condition_key = f"obligation_{ob.get('id', ob_title)}"
                    if _can_notify(condition_key):
                        return f"Sir, reminder: {ob_type} '{ob_title}' is due in {minutes_remaining} minutes."
            except Exception:
                pass

    except Exception as exc:
        print(f"[ProactiveDaemon] Obligation check failed: {exc}")

    return None


def _daemon_loop() -> None:
    """Background monitoring loop executing on daemon thread."""
    registry = get_registry()
    print("[ProactiveDaemon] Monitoring loop started.")

    while _daemon_running:
        try:
            # Only notify if JARVIS is active/awake
            if not _active_getter or not _active_getter():
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Run all condition checks
            warnings = []

            battery_warning = _check_battery()
            if battery_warning:
                warnings.append(battery_warning)

            thermal_warning = _check_thermals()
            if thermal_warning:
                warnings.append(thermal_warning)

            cpu_warning = _check_cpu_load()
            if cpu_warning:
                warnings.append(cpu_warning)

            obligation_warning = _check_obligations()
            if obligation_warning:
                warnings.append(obligation_warning)

            # Deliver warnings (high priority first)
            for warning in warnings:
                _speak_proactive(warning)
                time.sleep(2)  # Brief pause between multiple warnings

            if warnings:
                registry.set_capability_evidence(
                    "PROACTIVE_DAEMON", EvidenceLevel.LIVE,
                    f"Delivered {len(warnings)} proactive warning(s)",
                    source="proactive daemon"
                )

        except Exception as exc:
            error_handler.log_and_demote(
                "PROACTIVE_DAEMON", exc,
                "Monitoring loop iteration",
                SubsystemState.DEGRADED
            )

        time.sleep(POLL_INTERVAL_SECONDS)

    print("[ProactiveDaemon] Monitoring loop exited.")


def get_cooldown_status() -> Dict[str, float]:
    """Return current cooldown status for all conditions (for diagnostics)."""
    with _cooldown_lock:
        now = time.time()
        return {
            key: max(0.0, CONDITION_COOLDOWN_SECONDS - (now - last_time))
            for key, last_time in _cooldowns.items()
        }


def force_check_all() -> Dict[str, Optional[str]]:
    """Force immediate check of all conditions, bypassing cooldowns (for testing).

    Returns:
        Dict mapping condition names to warning messages (or None)
    """
    return {
        "battery": _check_battery(),
        "thermals": _check_thermals(),
        "cpu_load": _check_cpu_load(),
        "obligations": _check_obligations(),
    }
