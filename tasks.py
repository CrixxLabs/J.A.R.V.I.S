# tasks.py — Task & Reminder System (Phase 2 Upgrade + Phase 6 Persistence Fix)
# Stores goals, evaluates conditions, triggers actions automatically
# Now supports natural language reminders + conversation context
# Module 2: entities dict accepted by add_reminder for exact time scheduling
# Phase 6: Absolute fire_at timestamps + boot-time re-hydration of reminders

import json
import os
import time
import datetime
import threading
import uuid
from memory import log_activity, remember
import conversation_manager

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
TASKS_FILE = os.path.join(BASE_DIR, "tasks.json")

_tasks  = []
_lock   = threading.Lock()
_speak_cb  = None
_action_cb = None

def init(speak_fn, action_fn):
    global _speak_cb, _action_cb, _task_thread
    if _task_thread and _task_thread.is_alive():
        return
    _speak_cb  = speak_fn
    _action_cb = action_fn
    _tasks_stop.clear()
    _reminder_stop.clear()
    _load_tasks()
    _rehydrate_reminders()
    _task_thread = threading.Thread(target=_task_loop, daemon=True, name="jarvis-task-worker")
    _task_thread.start()
    print("[Tasks] Task engine started with reminder support.")

def _load_tasks():
    global _tasks
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r") as f:
                _tasks = json.load(f)
        except:
            _tasks = []
    else:
        _tasks = _default_tasks()
        _save_tasks()

def _save_tasks():
    try:
        tmp = TASKS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_tasks, f, indent=2)
        os.replace(tmp, TASKS_FILE)
    except Exception as e:
        print(f"[Tasks] save error: {e}")

def _default_tasks():
    return [
        {
            "id": "morning_briefing",
            "type": "scheduled",
            "scheduled_time": "08:00",
            "action": "morning_briefing",
            "message": None,
            "active": True,
            "last_triggered": None,
            "cooldown_minutes": 720,
            "repeat": True,
            "label": "morning briefing"
        },
        {
            "id": "battery_warn",
            "type": "condition",
            "condition": "battery_low",
            "action": "speak",
            "message": "heads up Arju, battery's getting low. time to plug in.",
            "active": True,
            "last_triggered": None,
            "cooldown_minutes": 30,
            "repeat": True,
            "label": "battery warning"
        },
        {
            "id": "youtube_break",
            "type": "condition",
            "condition": "youtube_long",
            "action": "speak",
            "message": "yo Arju, you've been on YouTube for a while. maybe take a 5 minute break?",
            "active": True,
            "last_triggered": None,
            "cooldown_minutes": 60,
            "repeat": True,
            "label": "YouTube break reminder"
        }
    ]

def _cooldown_ok(task):
    last = task.get("last_triggered")
    if not last:
        return True
    try:
        last_dt = datetime.datetime.fromisoformat(last)
        mins    = task.get("cooldown_minutes", 60)
        return (datetime.datetime.now() - last_dt).total_seconds() / 60 >= mins
    except (TypeError, ValueError):
        return True

def _mark_triggered(task):
    task["last_triggered"] = datetime.datetime.now().isoformat()
    if not task.get("repeat", True):
        task["active"] = False
    _save_tasks()

def _evaluate_condition(condition, snap):
    if condition == "battery_low":
        bat = snap.get("battery_percent", -1)
        return 0 < bat <= 15 and not snap.get("battery_charging", False)
    if condition == "youtube_long":
        return snap.get("active_app") == "YouTube" and snap.get("youtube_time", 0) >= 2700
    if condition == "user_idle":
        return snap.get("user_idle_seconds", 0) > 600
    if condition == "high_cpu":
        return snap.get("cpu_percent", 0) > 90
    return False

_task_thread: threading.Thread | None = None
_tasks_stop = threading.Event()
_reminder_stop = threading.Event()
_reminder_threads: dict[str, threading.Thread] = {}


def _task_loop():
    from observer import get_state_snapshot
    while not _tasks_stop.is_set():
        try:
            now  = datetime.datetime.now()
            snap = get_state_snapshot()
            with _lock:
                tasks_copy = list(_tasks)

            for task in tasks_copy:
                if not task.get("active", True):
                    continue
                if not _cooldown_ok(task):
                    continue

                triggered = False

                if task["type"] == "scheduled":
                    sched = task.get("scheduled_time", "")
                    if sched and now.strftime("%H:%M") == sched:
                        triggered = True

                elif task["type"] == "condition":
                    if _evaluate_condition(task.get("condition", ""), snap):
                        triggered = True

                if triggered:
                    _trigger_task(task)
                    _mark_triggered(task)

        except Exception as e:
            print(f"[Tasks] loop error: {e}")

        _tasks_stop.wait(30)

def _trigger_task(task):
    action  = task.get("action", "speak")
    message = task.get("message", "")
    label   = task.get("label", "task")

    print(f"[Tasks] Triggering: {label}")
    log_activity("task_triggered", label)

    if action == "speak" and _speak_cb and message:
        _speak_cb(message)
    elif action == "morning_briefing" and _action_cb:
        _action_cb({"action": "morning_briefing"})
    elif action == "reminder" and _speak_cb and message:
        _speak_cb(f"reminder — {message}")


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 6: REMINDER RE-HYDRATION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _spawn_reminder_thread(task, delay_seconds):
    """Schedule one cancellable reminder worker for this process."""
    message = task.get("message", "something")
    task_id = task.get("id")
    previous = _reminder_threads.get(task_id)
    if previous and previous.is_alive():
        return

    def _delayed():
        if _reminder_stop.wait(delay_seconds):
            return
        should_fire = False
        with _lock:
            for t in _tasks:
                if t.get("id") == task_id and t.get("active", True) and not _reminder_stop.is_set():
                    t["active"] = False
                    _save_tasks()
                    should_fire = True
                    break
        if not should_fire:
            return
        if _speak_cb:
            _speak_cb(f"hey Arju — {message}")
        log_activity("reminder_fired", message[:40])

    worker = threading.Thread(target=_delayed, daemon=True, name=f"jarvis-reminder-{task_id}")
    _reminder_threads[task_id] = worker
    worker.start()


def _rehydrate_reminders():
    """
    Called once at boot after _load_tasks().
    Finds all active reminders with a fire_at timestamp and:
      - If fire_at is in the future → spawn a fresh countdown thread
      - If fire_at already passed   → fire immediately (missed reminder)
    """
    now = datetime.datetime.now()
    rehydrated = 0
    missed = 0

    with _lock:
        for task in _tasks:
            if not task.get("active", True):
                continue
            if "reminder" not in task.get("id", ""):
                continue

            fire_at_str = task.get("fire_at", "")
            if not fire_at_str:
                # Legacy reminder without fire_at — skip, it's dead
                continue

            try:
                fire_at = datetime.datetime.fromisoformat(fire_at_str)
            except Exception:
                continue

            remaining = (fire_at - now).total_seconds()

            if remaining > 0:
                # Future reminder — re-spawn the countdown
                _spawn_reminder_thread(task, remaining)
                rehydrated += 1
                print(f"[Tasks] Re-hydrated reminder '{task.get('label', '')}' — fires in {remaining:.0f}s")
            else:
                # Missed reminder — fire immediately
                task["active"] = False
                missed += 1
                message = task.get("message", "something")
                print(f"[Tasks] Missed reminder detected: '{message}' — firing now")
                if _speak_cb:
                    _speak_cb(f"hey Arju — you had a reminder that expired while I was off: {message}")
                log_activity("reminder_missed_fired", message[:40])

        if missed > 0 or rehydrated > 0:
            _save_tasks()

    if rehydrated or missed:
        print(f"[Tasks] Re-hydration complete: {rehydrated} future, {missed} missed")


# ── Public API for Reminders ──────────────────────────────────────────────────

def add_reminder(message: str, seconds: int = 3600, entities: dict = None) -> str:
    """
    Add a timed reminder.

    Module 2 addition: accepts optional entities dict from NER.
    Phase 6 fix: stores absolute fire_at timestamp so reminders survive reboots.
    """
    task_id = str(uuid.uuid4())[:8]
    seconds = max(int(seconds), 1)   # safety floor

    now = datetime.datetime.now()
    fire_at = now + datetime.timedelta(seconds=seconds)

    task = {
        "id":               f"reminder_{task_id}",
        "type":             "scheduled",
        "scheduled_time":   None,
        "delay_seconds":    seconds,
        "fire_at":          fire_at.isoformat(),
        "action":           "speak",
        "message":          message,
        "active":           True,
        "last_triggered":   None,
        "cooldown_minutes": 9999,
        "repeat":           False,
        "label":            f"reminder: {message[:30]}",
        "entities":         entities or {},
    }

    with _lock:
        _tasks.append(task)
    _save_tasks()
    _spawn_reminder_thread(task, seconds)

    mins = seconds // 60
    if mins == 0:
        return f"Reminder set for {seconds} second{'s' if seconds != 1 else ''}."
    return f"Reminder set for {mins} minute{'s' if mins != 1 else ''}."


def list_reminders():
    with _lock:
        active = [t["label"] for t in _tasks if t.get("active", True) and "reminder" in t.get("id", "")]
    if not active:
        return "No active reminders."
    return "Active reminders: " + ", ".join(active)


def get_active_reminder_count() -> int:
    """Return count of active reminders for verification."""
    with _lock:
        return sum(1 for t in _tasks if t.get("active", True) and "reminder" in t.get("id", ""))


def get_active_reminders() -> list:
    """Return list of active reminder tasks for inspection."""
    with _lock:
        return [dict(t) for t in _tasks if t.get("active", True) and "reminder" in t.get("id", "")]


def cancel_reminder(keyword):
    with _lock:
        for t in _tasks:
            if "reminder" in t.get("id", "") and keyword.lower() in t.get("message", "").lower():
                t["active"] = False
                _save_tasks()
                return f"Cancelled reminder: {t.get('message')}"
    return "No matching reminder found."


def set_morning_briefing_time(time_str):
    with _lock:
        for t in _tasks:
            if t["id"] == "morning_briefing":
                t["scheduled_time"] = time_str
                break
    _save_tasks()
    return f"morning briefing set for {time_str}."


def disable_task(task_id):
    with _lock:
        for t in _tasks:
            if t["id"] == task_id:
                t["active"] = False
    _save_tasks()


def stop_tasks():
    """Signal the task loop to stop."""
    global _task_thread
    _tasks_stop.set()
    _reminder_stop.set()
    if _task_thread and _task_thread.is_alive():
        _task_thread.join(timeout=3.0)
    for worker in list(_reminder_threads.values()):
        if worker.is_alive():
            worker.join(timeout=3.0)
    _reminder_threads.clear()
    _task_thread = None
    print("[Tasks] Task engine stopped.")
