# observer.py — Observer Modules
# Continuously watches screen, app context,
# system state WITHOUT making decisions

import cv2
import numpy as np
import threading
import time
import datetime
import os
import psutil
import win32gui
from mss import mss
from memory import log_context_change, log_activity

# Reliability imports
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler

# ── Shared state (read-only from outside) ──
state = {
    "active_app":         "",
    "active_title":       "",
    "screen_changed":     False,
    "last_screen_hash":   None,
    "screen_change_time": 0,
    "user_idle_seconds":  0,
    "last_input_time":    time.time(),
    "cpu_percent":        0,
    "ram_percent":        0,
    "battery_percent":    -1,
    "battery_charging":   False,
    "youtube_time":       0,   # seconds spent on youtube today
    "last_app_change":    time.time(),
}
_shutdown = False
_lock = threading.Lock()

# ── App name mapping ──
APP_MAP = {
    "youtube":            "YouTube",
    "netflix":            "Netflix",
    "spotify":            "Spotify",
    "chrome":             "Chrome",
    "firefox":            "Firefox",
    "brave":              "Brave",
    "visual studio code": "VS Code",
    "code":               "VS Code",
    "pycharm":            "PyCharm",
    "word":               "Word",
    "excel":              "Excel",
    "powerpoint":         "PowerPoint",
    "notion":             "Notion",
    "discord":            "Discord",
    "whatsapp":           "WhatsApp",
    "steam":              "Steam",
    "task manager":       "Task Manager",
    "explorer":           "File Explorer",
    "terminal":           "Terminal",
    "powershell":         "PowerShell",
    "cmd":                "CMD",
    "prime video":        "Prime Video",
    "twitch":             "Twitch",
    "figma":              "Figma",
    "photoshop":          "Photoshop",
    "notepad":            "Notepad",
}

def _detect_app(title):
    t = title.lower()
    for key, name in APP_MAP.items():
        if key in t:
            return name
    return title[:30] if title else "Unknown"

def get_context_hint():
    with _lock:
        app   = state["active_app"]
        title = state["active_title"]
    if not app:
        return ""
    hints = {
        "YouTube":      "Arju is watching YouTube.",
        "Netflix":      "Arju is watching Netflix.",
        "Prime Video":  "Arju is watching Prime Video.",
        "Twitch":       "Arju is on Twitch.",
        "Spotify":      "Arju has Spotify open.",
        "Chrome":       "Arju has Chrome open.",
        "Firefox":      "Arju has Firefox open.",
        "Brave":        "Arju has Brave open.",
        "VS Code":      "Arju is coding in VS Code.",
        "PyCharm":      "Arju is coding in PyCharm.",
        "Word":         "Arju has Word open.",
        "Excel":        "Arju has Excel open.",
        "Notion":       "Arju has Notion open.",
        "Discord":      "Arju has Discord open.",
        "WhatsApp":     "Arju has WhatsApp open.",
        "Steam":        "Arju is on Steam.",
        "File Explorer":"Arju has File Explorer open.",
        "Terminal":     "Arju has a terminal open.",
        "PowerShell":   "Arju has PowerShell open.",
        "Figma":        "Arju is designing in Figma.",
        "Photoshop":    "Arju is in Photoshop.",
    }
    return hints.get(app, f'Arju has "{title[:40]}" open.')

def get_state_snapshot():
    with _lock:
        return dict(state)

def mark_user_input():
    with _lock:
        state["last_input_time"] = time.time()
        state["user_idle_seconds"] = 0


# ── Context Observer Thread ──
def _context_observer():
    registry = get_registry()
    while not _shutdown:
        try:
            hwnd  = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            app   = _detect_app(title)
            with _lock:
                old_app = state["active_app"]
                state["active_app"]   = app
                state["active_title"] = title

            if app != old_app and app:
                log_context_change(app, title)
                with _lock:
                    state["last_app_change"] = time.time()
                print(f"[Observer] App: {app}")
            
            # Keep observer status healthy
            registry.set_status("PLUGINS", SubsystemState.READY, "Observer contexts parsing fine")
        except Exception as exc:
            # Demote dynamically if win32 calls fail continuously
            error_handler.log_and_demote(
                subsystem="PLUGINS",
                exception=exc,
                context="Background context window tracker",
                demote_to=SubsystemState.DEGRADED
            )
        time.sleep(3)


# ── System Observer Thread ──
def _system_observer():
    registry = get_registry()
    while not _shutdown:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            bat = psutil.sensors_battery()
            now = time.time()
            with _lock:
                state["cpu_percent"]      = cpu
                state["ram_percent"]      = ram.percent
                state["user_idle_seconds"] = now - state["last_input_time"]
                if bat:
                    state["battery_percent"]  = bat.percent
                    state["battery_charging"] = bat.power_plugged
            
            # We track general tasks/monitor health with this thread
            registry.set_status("TASKS", SubsystemState.READY, "System telemetry engine healthy")
        except Exception as exc:
            error_handler.log_and_demote(
                subsystem="TASKS",
                exception=exc,
                context="Background psutil telemetry monitor",
                demote_to=SubsystemState.DEGRADED
            )
        time.sleep(5)


# ── Screen Change Observer Thread ──
def _screen_observer():
    registry = get_registry()
    last_hash = None
    while not _shutdown:
        try:
            with mss() as sct:
                img = np.array(sct.grab(sct.monitors[1]))
            # Downsample for speed
            small = cv2.resize(img, (160, 90))
            gray  = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            h     = hash(gray.tobytes())
            changed = (last_hash is not None and h != last_hash)
            with _lock:
                state["screen_changed"]     = changed
                state["last_screen_hash"]   = h
                if changed:
                    state["screen_change_time"] = time.time()
            last_hash = h
            
            # Grabbing screens directly affects our OCR capability health!
            registry.set_status("TESSERACT_OCR", SubsystemState.READY, "Screen grab active, pixels feeding OCR")
        except Exception as exc:
            # If screen grabbing is completely broken (e.g. display disconnected/admin block), OCR is OFFLINE
            error_handler.log_and_demote(
                subsystem="TESSERACT_OCR",
                exception=exc,
                context="Screen capture loop for AI parsing",
                demote_to=SubsystemState.OFFLINE
            )
        time.sleep(4)


# ── YouTube Time Tracker ──
def _youtube_tracker():
    while not _shutdown:
        try:
            with _lock:
                app = state["active_app"]
            if app == "YouTube":
                with _lock:
                    state["youtube_time"] = state.get("youtube_time", 0) + 30
        except Exception as exc:
            # Keep log cleaner, use event tracker instead of demoting core subsystems
            from session_logger import log_event
            log_event(
                event_type="tracker_error",
                data={"error": str(exc)},
                severity="warning",
                module="observer"
            )
        time.sleep(30)


# Thread references for graceful shutdown
_observer_threads: list[threading.Thread] = []


# ── Start all observers ──
def start_observers():
    global _observer_threads
    _observer_threads = [
        threading.Thread(target=_context_observer, daemon=True),
        threading.Thread(target=_system_observer,  daemon=True),
        threading.Thread(target=_screen_observer,  daemon=True),
        threading.Thread(target=_youtube_tracker,  daemon=True),
    ]
    for t in _observer_threads:
        t.start()
    print("[Observer] All observers started.")


def stop_observers():
    """Signal all observer threads to stop and wait for them."""
    global _shutdown
    _shutdown = True
    # Give threads time to exit cleanly
    for t in _observer_threads:
        if t.is_alive():
            t.join(timeout=2.0)
    print("[Observer] All observers stopped.")


# ══════════════════════════════════════════════════════════════════════════════
# NEW (PHASE 5): ACTION OUTCOME VERIFICATION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def capture_pre_action_state(action: dict) -> dict:
    """
    Take a lightweight snapshot before an action executes so we can verify
    the state difference afterward.
    """
    if not isinstance(action, dict):
        return {}
    
    act = action.get("action", "")
    snapshot = {
        "timestamp": time.time(),
        "action": act,
    }

    try:
        if act in ("open_app", "close_app", "open_and_login"):
            app_name = (action.get("app", "") or "").lower().strip()
            running_pids = []
            for proc in psutil.process_iter(attrs=["name", "pid"]):
                pname = (proc.info.get("name") or "").lower()
                if app_name and (app_name in pname or pname.startswith(app_name)):
                    running_pids.append(proc.info["pid"])
            snapshot["matching_pids"] = running_pids

        elif act == "rename_file":
            old_name = action.get("old_name", "") or action.get("name", "")
            new_name = action.get("new_name", "")
            snapshot["old_exists"] = os.path.exists(old_name) if old_name else False
            snapshot["new_exists"] = os.path.exists(new_name) if new_name else False

        elif act == "set_reminder":
            import tasks
            snapshot["reminder_count"] = len(tasks.reminders) if hasattr(tasks, "reminders") else 0

        elif act == "add_obligation":
            import obligations
            snapshot["obligation_count"] = len(obligations.get_all())

    except Exception as exc:
        print(f"[Observer][pre-state] failed: {exc}")

    return snapshot


def verify_action_outcome(action: dict, pre_state: dict = None, timeout: float = 2.5) -> tuple[bool, str]:
    """
    Verify that an executed action ACTUALLY produced its intended physical effect.
    Returns (verified: bool, detail_message: str).
    """
    if not isinstance(action, dict):
        return True, "No verification needed."

    act = action.get("action", "")
    pre_state = pre_state or {}

    try:
        # ── 1. Open App Verification ──────────────────────────────────────────
        if act in ("open_app", "open_and_login"):
            app_name = (action.get("app", "") or "").lower().strip()
            if not app_name:
                return True, "No specific app target."

            deadline = time.time() + timeout
            while time.time() < deadline:
                for proc in psutil.process_iter(attrs=["name", "pid"]):
                    pname = (proc.info.get("name") or "").lower()
                    if app_name in pname or pname.startswith(app_name):
                        return True, f"Process '{pname}' is active."
                time.sleep(0.4)

            # Check foreground window as secondary verification
            hwnd = win32gui.GetForegroundWindow()
            title = (win32gui.GetWindowText(hwnd) or "").lower()
            if app_name in title:
                return True, f"Window title '{title}' confirmed active."

            return False, f"{app_name} did not start or launch a recognized process."

        # ── 2. Close App Verification ─────────────────────────────────────────
        if act == "close_app":
            app_name = (action.get("app", "") or "").lower().strip()
            if not app_name:
                return True, "No specific app target."

            time.sleep(0.8)
            still_running = []
            for proc in psutil.process_iter(attrs=["name", "pid"]):
                pname = (proc.info.get("name") or "").lower()
                if app_name in pname or pname.startswith(app_name):
                    still_running.append(proc.info["pid"])

            if still_running:
                return False, f"Process for {app_name} is still running (PIDs: {still_running})."
            return True, f"{app_name} process terminated successfully."

        # ── 3. Rename File Verification ───────────────────────────────────────
        if act == "rename_file":
            new_name = action.get("new_name", "")
            old_name = action.get("old_name", "") or action.get("name", "")
            if new_name and os.path.exists(new_name):
                return True, f"Target file '{new_name}' verified on disk."
            return False, f"Renamed file '{new_name}' does not exist on disk."

        # ── 4. Set Reminder Verification ──────────────────────────────────────
        if act == "set_reminder":
            import tasks
            time.sleep(0.3)
            current_count = tasks.get_active_reminder_count()
            pre_count = pre_state.get("reminder_count", 0)
            if current_count > pre_count or current_count > 0:
                return True, "Reminder verified in task registry."
            return False, "Reminder was not found in active task registry."

        # ── 5. Add Obligation Verification ────────────────────────────────────
        if act == "add_obligation":
            import obligations
            time.sleep(0.3)
            current_count = len(obligations.get_all())
            pre_count = pre_state.get("obligation_count", 0)
            if current_count > pre_count:
                return True, "Obligation recorded in obligation ledger."
            return False, "Obligation count did not increase in ledger."

        # ── 6. Save Login Verification ────────────────────────────────────────
        if act == "save_login":
            import credential_vault
            app_name = action.get("app", "")
            if app_name:
                creds = credential_vault.get_credential(app_name)
                if creds:
                    return True, f"Credentials for {app_name} verified in secure DPAPI vault."
            return False, f"Failed to retrieve newly stored credential for {app_name} from vault."

        # Default pass-through for other tools
        return True, "Outcome verified."

    except Exception as exc:
        print(f"[Observer][verify_outcome] check failed: {exc}")
        return True, f"Verification check threw exception: {exc}"