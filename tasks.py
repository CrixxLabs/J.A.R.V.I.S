# tasks.py — Task & Reminder System (Phase 2 Upgrade)
# Stores goals, evaluates conditions, triggers actions automatically
# Now supports natural language reminders + conversation context
# Module 2: entities dict accepted by add_reminder for exact time scheduling

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
    global _speak_cb, _action_cb
    _speak_cb  = speak_fn
    _action_cb = action_fn
    _load_tasks()
    threading.Thread(target=_task_loop, daemon=True).start()
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
        with open(TASKS_FILE, "w") as f:
            json.dump(_tasks, f, indent=2)
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
        return (datetime.datetime.now() - last_dt).seconds / 60 >= mins
    except:
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

def _task_loop():
    from observer import get_state_snapshot
    while True:
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

        time.sleep(30)

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


# ── Public API for Reminders ──────────────────────────────────────────────────

def add_reminder(message: str, seconds: int = 3600, entities: dict = None) -> str:
    """
    Add a timed reminder.

    Module 2 addition: accepts optional entities dict from NER.
    If entities contains datetime info and seconds wasn't already
    resolved by planner._resolve_reminder_seconds, this function
    leaves seconds as-is (planner handles resolution before calling here).
    The entities param is stored for future use / logging only.
    """
    task_id = str(uuid.uuid4())[:8]
    seconds = max(int(seconds), 1)   # safety floor

    task = {
        "id":               f"reminder_{task_id}",
        "type":             "scheduled",
        "scheduled_time":   None,
        "delay_seconds":    seconds,
        "action":           "speak",
        "message":          message,
        "active":           True,
        "last_triggered":   None,
        "cooldown_minutes": 9999,
        "repeat":           False,
        "label":            f"reminder: {message[:30]}",
        "entities":         entities or {},   # stored for logging/future use
    }

    def _delayed():
        time.sleep(seconds)
        if _speak_cb:
            _speak_cb(f"hey Arju — {message}")

    threading.Thread(target=_delayed, daemon=True).start()

    with _lock:
        _tasks.append(task)
    _save_tasks()

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