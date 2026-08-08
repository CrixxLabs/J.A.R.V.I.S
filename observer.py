
# observer.py — Observer Modules
# Continuously watches screen, app context,
# system state WITHOUT making decisions

import cv2
import numpy as np
import threading
import time
import datetime
import psutil
import win32gui
from mss import mss
from memory import log_context_change, log_activity

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
    while True:
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
        except:
            pass
        time.sleep(3)


# ── System Observer Thread ──
def _system_observer():
    while True:
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
        except:
            pass
        time.sleep(5)


# ── Screen Change Observer Thread ──
def _screen_observer():
    last_hash = None
    while True:
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
        except:
            pass
        time.sleep(4)


# ── YouTube Time Tracker ──
def _youtube_tracker():
    while True:
        try:
            with _lock:
                app = state["active_app"]
            if app == "YouTube":
                with _lock:
                    state["youtube_time"] = state.get("youtube_time", 0) + 30
        except:
            pass
        time.sleep(30)


# ── Start all observers ──
def start_observers():
    threads = [
        threading.Thread(target=_context_observer, daemon=True),
        threading.Thread(target=_system_observer,  daemon=True),
        threading.Thread(target=_screen_observer,  daemon=True),
        threading.Thread(target=_youtube_tracker,  daemon=True),
    ]
    for t in threads:
        t.start()
    print("[Observer] All observers started.")
