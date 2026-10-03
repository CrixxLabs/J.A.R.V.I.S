# jarvis.py — Main Entry Point & Core Event Loop
# =======================================================================
# Initializes hardware, starts observers, handles vocal transactions,
# and coordinates planner dispatches and experience verification logging.

import asyncio
import datetime
import os
import random
import re
import tempfile
import threading
import time

_runtime_instance_guard = None
if __name__ == "__main__":
    from runtime_instance import DuplicateRuntimeError, acquire_runtime_guard

    try:
        _runtime_instance_guard = acquire_runtime_guard()
    except DuplicateRuntimeError as exc:
        print(f"[Startup] Refusing duplicate runtime: {exc}")
        raise SystemExit(2)

try:
    import edge_tts
except Exception as _edge_tts_err:
    edge_tts = None
    print(f"[jarvis] edge_tts unavailable: {_edge_tts_err}")
try:
    import pygame
except Exception as _pygame_err:
    pygame = None
    print(f"[jarvis] pygame unavailable: {_pygame_err}")
try:
    import pyautogui
except Exception as _pyautogui_err:
    pyautogui = None
    print(f"[jarvis] pyautogui unavailable: {_pyautogui_err}")
import speech_recognition as sr
try:
    import win32com.client as _win32com_client
except Exception as _win32com_err:
    _win32com_client = None
    print(f"[jarvis] SAPI unavailable: {_win32com_err}")
from dotenv import load_dotenv

import core
import brain
import executor
import memory
import observer
import planner
import tasks
import conversation_manager
import proactive_scheduler
import listener
import runtime_visuals
from lifecycle import LifecycleManager
from memory import log_failure, log_usage
from session_logger import log_event, save_session
from speech_cleaner import clean_speech_text

# ── Task Queue (MARK VII Phase 1) ─────────────────────────────────────────────
try:
    from task_queue import init_task_queue, shutdown_task_queue, get_task_queue
    _TASK_QUEUE_AVAILABLE = True
except Exception as _tq_err:
    _TASK_QUEUE_AVAILABLE = False
    print(f"[jarvis] Task queue not available: {_tq_err}")

# ── Reliability imports (Phase 2-5) ───────────────────────────────────────────
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler
import self_model

# ── Optional: Pocket-TTS persistent voice module ───────────────────────────────
try:
    import jarvis_tts
    _JARVIS_TTS_AVAILABLE = True
except Exception as _tts_err:
    _JARVIS_TTS_AVAILABLE = False
    print(f"[jarvis] Pocket-TTS module not available: {_tts_err}")

# ── self-awareness module ────────────────────────────────────────────────
try:
    import self_awareness
    _SELF_AWARENESS_READY = True
except Exception as _sa_err:
    _SELF_AWARENESS_READY = False
    print(f"[jarvis] self_awareness not available: {_sa_err}")

load_dotenv()

VOICE = "en-US-GuyNeural"
USE_POCKET_TTS = os.getenv("USE_POCKET_TTS", "true").lower() == "true"
# Compatibility alias retained for older tests/config while F5 is retired.
USE_F5_TTS = USE_POCKET_TTS


def _ui_press(key: str) -> bool:
    if pyautogui is None:
        return False
    pyautogui.press(key)
    return True

def _ui_scroll(amount: int) -> bool:
    if pyautogui is None:
        return False
    pyautogui.scroll(amount)
    return True

stop_speaking = False
is_speaking = False
_startup_done = False
_proactive_stop = threading.Event()
_proactive_thread = None
_lifecycle = None
_startup_lock = threading.RLock()
ACTIVE = False
last_active = time.time()

_pending_save_login = None


def _ensure_mixer(frequency: int = 44100):
    """Ensure pygame mixer is ready at the requested sample rate."""
    if pygame is None:
        return False
    try:
        init_state = pygame.mixer.get_init()
        if not init_state:
            pygame.mixer.init(frequency=frequency)
            return True
        return True
    except Exception as exc:
        print(f"[DEBUG][audio] mixer init failed: {exc}")
        return False


_STRIP_PHRASES = [
    "certainly!", "certainly,",
    "of course!", "of course,",
    "absolutely!", "absolutely,",
    "great question",
    "i'd be happy to", "i'd be glad to",
    "i'm happy to help",
    "as an ai", "as a language model",
    "i think", "i believe",
    "i'm sorry", "i apologize", "sorry about that",
    "please note that", "it's worth noting",
    "i hope this helps",
    "let me know if", "feel free to ask",
    "would you like me to", "do you want me to",
    "should i",
]

GREETINGS = [
    "Yeah, I'm here.",
    "Ready. What do you need?",
    "Listening.",
    "What's the move?",
]

BYES = [
    "Going quiet.",
    "Alright. Catch you later.",
    "Standing by.",
]

EXIT_KEYWORDS = [
    "exit", "bye", "goodbye", "shut down", "shutdown",
    "sleep", "turn off", "quit", "close jarvis",
]

INTERRUPT_WORDS = {
    "stop", "shut up", "quiet", "silence", "wait", "hold on",
    "jarvis stop", "stop jarvis", "shut it", "cancel",
}

FOLLOW_UP_YES = {"yes", "yeah", "yep", "join", "do it", "go ahead", "sure"}
FOLLOW_UP_NO = {"no", "nope", "don't", "dont", "cancel", "stop"}

_face_rec_available = False
_face_module = None


def jarvisify_response(text):
    if not text:
        return text
    result = text
    result = re.sub(r"```(?:json)?", "", result, flags=re.IGNORECASE)
    result = result.replace("```", "")
    for phrase in _STRIP_PHRASES:
        result = re.sub(re.escape(phrase), "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s{2,}", " ", result).strip()
    result = re.sub(r"\s+([.,!?])", r"\1", result)
    result = result.lstrip(".,! ")
    if result:
        result = result[0].upper() + result[1:]
    return result or text


# ══════════════════════════════════════════════════════════════════════════════
# SMART ERROR HINTS
# ══════════════════════════════════════════════════════════════════════════════

_ERROR_HINTS = {
    "open_app": {
        "not found":     "That app isn't installed. Say 'install [app name]' and I'll get it for you.",
        "cannot find":   "That app isn't installed. Say 'install [app name]' and I'll get it for you.",
        "winerror 2":    "That app isn't installed. Say 'install [app name]' and I'll get it for you.",
        "access denied": "Windows blocked me from opening that. You might need admin rights.",
        "permission":    "Windows blocked me from opening that. You might need admin rights.",
        "default":       "Couldn't open that. Check if it's installed correctly.",
    },
    "install_app": {
        "not found":     "Couldn't find that app in Windows' catalog. Try a different name.",
        "network":       "Couldn't download — check your internet connection.",
        "administrator": "That install needs admin rights. Run Jarvis as administrator.",
        "default":       "Install failed. Check the console for details.",
    },
    "close_app": {
        "not running": "That app isn't running right now.",
        "access denied": "Windows blocked me from closing that.",
        "default":     "Couldn't close that. It may have already exited.",
    },
    "web_search": {
        "connection":  "No internet connection. Want me to retry when you're back online?",
        "timeout":     "The search took too long. Might be a slow network.",
        "default":     "Search failed. Try rephrasing your query.",
    },
    "send_email": {
        "auth":        "Email login failed. Check your Gmail credentials in the .env file.",
        "connection":  "Couldn't reach Gmail. Check your internet connection.",
        "default":     "Email couldn't be sent. Check your Gmail setup.",
    },
    "set_reminder": {
        "default":     "Couldn't set that reminder. Try being more specific about the time.",
    },
    "join_meeting": {
        "no link":     "I couldn't find the meeting link. Try opening WhatsApp first.",
        "default":     "Couldn't join the meeting. The link might be invalid or expired.",
    },
    "weather": {
        "connection":  "Can't reach the weather service. Check your internet.",
        "default":     "Couldn't fetch the weather right now.",
    },
    "default": {
        "connection":  "No internet connection available.",
        "timeout":     "That took too long. Want me to try again?",
        "permission":  "Windows blocked that action. You might need admin rights.",
        "not found":   "Couldn't find what you're looking for.",
        "default":     "Something went wrong. Check the console for details.",
    },
}


def _get_smart_error_message(action_name: str, raw_error: str) -> str:
    if not raw_error:
        return "Something went wrong."

    err_lower = raw_error.lower()
    action_hints = _ERROR_HINTS.get(action_name, {})

    for keyword, hint in action_hints.items():
        if keyword == "default":
            continue
        if keyword in err_lower:
            return hint

    generic = _ERROR_HINTS["default"]
    for keyword, hint in generic.items():
        if keyword == "default":
            continue
        if keyword in err_lower:
            return hint

    if "default" in action_hints:
        return action_hints["default"]

    return generic["default"]


# ══════════════════════════════════════════════════════════════════════════════
# SPEAK WITH INTERRUPT SUPPORT
# ══════════════════════════════════════════════════════════════════════════════

@runtime_visuals.visual_activity("speaking", "current_jarvis_response")
def speak(text):
    global stop_speaking, is_speaking

    final_text = clean_speech_text(text)
    if not final_text:
        return

    runtime_visuals.update(current_jarvis_response=final_text)
    print(f"Jarvis: {final_text}")
    log_event("jarvis_response", final_text)
    memory.log_activity("speak", final_text[:60])
    stop_speaking = False
    is_speaking = True

    # Do not arm interruption while TTS audio is still being generated.
    # The watcher is started only when playback actually begins, otherwise
    # ambient noise during network/TTS latency can pre-arm an interruption.
    listener.reset_interrupt()

    registry = get_registry()

    def _play_audio_file(path):
        global is_speaking
        try:
            if not _ensure_mixer():
                return
            pygame.mixer.music.load(path)
            listener.reset_interrupt()
            listener.start_interrupt_watcher()
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                if stop_speaking or listener.check_interrupt():
                    pygame.mixer.music.stop()
                    print("[Speak] Interrupted by user")
                    break
                pygame.time.Clock().tick(15)

        except Exception as play_exc:
            print(f"[DEBUG][speak] audio playback failed: {play_exc}")
        finally:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass
            is_speaking = False
            listener.stop_interrupt_watcher()

    # Pocket-TTS persistent CUDA streaming option.
    if USE_POCKET_TTS and _JARVIS_TTS_AVAILABLE:
        try:
            def _pocket_playback_started():
                listener.reset_interrupt()
                listener.start_interrupt_watcher()

            def _pocket_should_stop():
                return bool(stop_speaking or listener.check_interrupt())

            if jarvis_tts.stream_speech(
                final_text,
                should_stop=_pocket_should_stop,
                on_playback_start=_pocket_playback_started,
            ):
                registry.set_status("VOICE_TTS", SubsystemState.READY,
                                    "Pocket-TTS CUDA streaming active")
                is_speaking = False
                listener.stop_interrupt_watcher()
                return
            else:
                print("[TTS][ERROR] Pocket-TTS stream_speech returned False; falling back to Edge-TTS")
        except Exception as exc:
            print(f"[TTS][ERROR] Pocket-TTS streaming exception: {exc}; falling back to Edge-TTS")
            error_handler.log_and_demote(
                "VOICE_TTS", exc, "Pocket-TTS streaming pipeline", SubsystemState.DEGRADED
            )
        finally:
            listener.stop_interrupt_watcher()

    # Edge-TTS Option
    try:
        async def _tts():
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            tmp.close()
            if edge_tts is None:
                raise RuntimeError("edge_tts is unavailable")
            communicator = edge_tts.Communicate(final_text, VOICE, rate="+5%")
            await communicator.save(tmp.name)
            return tmp.name

        mp3_path = asyncio.run(_tts())
        registry.set_status("VOICE_TTS", SubsystemState.READY, "Edge-TTS OK")
        _play_audio_file(mp3_path)

    except Exception as exc:
        print(f"[TTS][ERROR] Edge-TTS synthesis failed: {exc}; falling back to Windows SAPI")
        registry.set_status("VOICE_TTS", SubsystemState.DEGRADED, f"Edge-TTS failed: {exc}")

        # SAPI fallback
        try:
            if _win32com_client is None:
                raise RuntimeError("Windows SAPI is unavailable")
            speaker = _win32com_client.Dispatch("SAPI.SpVoice")
            listener.reset_interrupt()
            listener.start_interrupt_watcher()
            speaker.Speak(final_text, 1)  # SVSFlagsAsync
            while not speaker.WaitUntilDone(50):
                if stop_speaking or listener.check_interrupt():
                    speaker.Speak("", 3)  # async + purge queued speech
                    print("[Speak] Interrupted by user")
                    break
            registry.set_status("VOICE_TTS", SubsystemState.DEGRADED, "SAPI fallback OK")
        except Exception as fallback_exc:
            print(f"[TTS][ERROR] Windows SAPI fallback failed: {fallback_exc}; all TTS options exhausted")
            registry.set_status("VOICE_TTS", SubsystemState.OFFLINE, f"All TTS failed: {fallback_exc}")
        finally:
            is_speaking = False
            listener.stop_interrupt_watcher()


def was_interrupted() -> bool:
    return listener.check_interrupt()


def listen(timeout=12, phrase_time_limit=20):
    with runtime_visuals.activity("listening"):
        command = listener.listen_for_command(timeout, phrase_time_limit)
    if command:
        runtime_visuals.update(current_user_transcript=command)
    return command


def _preprocess_command(command: str) -> str:
    if not command:
        return command
    processed, was_translated = core.preprocess_malayalam(command)
    if was_translated:
        print(f"[jarvis][ML→EN] '{command}' → '{processed}'")
        log_event("malayalam_translated", {
            "original":   command,
            "translated": processed,
        })
        memory.log_activity("malayalam_input", command[:60])
    return processed


def wake_word_detect():
    return listener.detect_double_clap()


def _is_interrupt_only_command(command: str) -> bool:
    if not command:
        return False
    normalized = command.strip().lower().rstrip(".,!?")
    return normalized in INTERRUPT_WORDS


# ══════════════════════════════════════════════════════════════════════════════
# SECURE PASSWORD DIALOG
# ══════════════════════════════════════════════════════════════════════════════

def _show_password_dialog(app_name: str, username: str) -> str:
    result = {"password": ""}

    def _dialog_thread():
        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
            root.title(f"Jarvis — Save {app_name} password")
            root.geometry("400x180")
            root.resizable(False, False)
            root.attributes("-topmost", True)
            root.focus_force()

            root.update_idletasks()
            width = root.winfo_width()
            height = root.winfo_height()
            x = (root.winfo_screenwidth() // 2) - (width // 2)
            y = (root.winfo_screenheight() // 2) - (height // 2)
            root.geometry(f"{width}x{height}+{x}+{y}")

            frame = ttk.Frame(root, padding="20")
            frame.pack(fill="both", expand=True)

            label = ttk.Label(
                frame,
                text=f"Enter password for {app_name}:\n(Username: {username})",
                font=("Segoe UI", 10),
            )
            label.pack(pady=(0, 10))

            password_var = tk.StringVar()
            entry = ttk.Entry(frame, show="•", textvariable=password_var, width=40)
            entry.pack(pady=(0, 15))
            entry.focus_set()

            def _submit():
                result["password"] = password_var.get()
                root.destroy()

            def _cancel():
                result["password"] = ""
                root.destroy()

            button_frame = ttk.Frame(frame)
            button_frame.pack()

            save_btn = ttk.Button(button_frame, text="Save", command=_submit, width=12)
            save_btn.pack(side="left", padx=5)

            cancel_btn = ttk.Button(button_frame, text="Cancel", command=_cancel, width=12)
            cancel_btn.pack(side="left", padx=5)

            root.bind("<Return>", lambda e: _submit())
            root.bind("<Escape>", lambda e: _cancel())

            root.mainloop()

        except Exception as exc:
            print(f"[DEBUG][dialog] password dialog failed: {exc}")
            result["password"] = ""

    thread = threading.Thread(target=_dialog_thread)
    thread.start()
    thread.join(timeout=120)

    return result["password"]


# ══════════════════════════════════════════════════════════════════════════════
# SAVE LOGIN FLOW HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def _handle_save_login_flow(action: dict) -> bool:
    global _pending_save_login

    app = (action.get("app", "") or "").strip()

    if not app:
        speak("Which app? Say the name.")
        response = listen(timeout=10)
        if not response:
            speak("Cancelled.")
            _pending_save_login = None
            return True
        app = response.strip().rstrip(".,!?")
        if not app:
            speak("Didn't catch that. Try again.")
            _pending_save_login = None
            return True

    speak(f"What's your {app} username or email? Say it clearly.")
    username_response = listen(timeout=15)

    if not username_response:
        speak("Cancelled — didn't hear a username.")
        _pending_save_login = None
        return True

    username = username_response.strip().rstrip(".,!?")
    username = re.sub(r"\s+at\s+", "@", username, flags=re.IGNORECASE)
    username = re.sub(r"\s+dot\s+", ".", username, flags=re.IGNORECASE)
    username = username.replace(" ", "")

    if not username:
        speak("Didn't catch a valid username. Try again.")
        _pending_save_login = None
        return True

    speak(f"Got it. I've opened a password box on your screen — type your {app} password there and hit save.")

    password = _show_password_dialog(app, username)

    if not password:
        speak("Cancelled — no password entered.")
        _pending_save_login = None
        return True

    save_action = {
        "action":   "save_login",
        "app":      app,
        "username": username,
        "password": password,
    }

    success, message = execute_with_feedback(save_action, original_input=f"save login for {app}")
    if success:
        speak(f"Saved. I'll use it next time you open {app}.")
    else:
        speak(f"Couldn't save that. {message or 'Try again.'}")

    _pending_save_login = None
    return True


# ══════════════════════════════════════════════════════════════════════════════
# FAST PATHS
# ══════════════════════════════════════════════════════════════════════════════

_DIAGNOSTIC_TRIGGERS = {
    "run a checkup", "run checkup", "system checkup", "system diagnostic",
    "run diagnostic", "health check", "run health check",
    "are you okay", "are you ok", "self diagnostic", "self check",
    "check yourself", "diagnose yourself",
}

_FAST_PATH_BLOCKLIST_STARTS = (
    "remember that", "remember i", "note that", "forget that",
    "add obligation", "log obligation",
    "what do you know", "tell me what you know", "show my profile", "my profile",
    "remind me", "set a reminder", "set reminder",
    "i have an", "i have a",
    "assignment due", "exam on", "due tomorrow", "due tonight",
    "install", "download", "set up", "log me into", "sign into",
    "save my login", "save my password", "forget my login", "list logins",
    "scan yourself", "check yourself", "rescan yourself",
    "what changed", "what's new", "what abilities",
)


def _should_skip_fast_path(cmd_lower: str) -> bool:
    for phrase in _FAST_PATH_BLOCKLIST_STARTS:
        if phrase in cmd_lower:
            return True
    return False


def handle_fast_command(command):
    import psutil

    cmd = (command or "").lower().strip()

    if _should_skip_fast_path(cmd):
        print(f"[DEBUG][fast] blocklisted phrase detected, deferring to planner")
        return False

    if any(trigger in cmd for trigger in _DIAGNOSTIC_TRIGGERS):
        try:
            import self_diagnostic
            from session_logger import log_event as _log, save_session as _save
            speak("Running system checkup. One moment.")
            report = self_diagnostic.run_full_checkup()
            _log(
                "diagnostic_report",
                report,
                severity="info",
                module="self_diagnostic",
                tags=["diagnostic", "health"],
            )
            _save()
            speak(jarvisify_response(report.get("summary", "Checkup complete.")))
        except Exception as exc:
            print(f"[DEBUG][diagnostic] fast-path error: {exc}")
            speak("Couldn't complete the diagnostic. Check the console.")
        return True

    if any(item in cmd for item in ("what time", "what's the time", "time now", "current time")):
        speak(f"It's {datetime.datetime.now().strftime('%I:%M %p')}.")
        return True
    if any(item in cmd for item in ("what's the date", "today's date", "what day")):
        speak(datetime.datetime.now().strftime("%A, %d %B %Y."))
        return True
    if "volume up" in cmd:
        _ui_press("volumeup")
        return True
    if "volume down" in cmd:
        _ui_press("volumedown")
        return True
    if "mute" in cmd:
        _ui_press("volumemute")
        return True
    if any(item in cmd for item in ("pause", "resume")) and any(item in cmd for item in ("music", "song", "video", "this")):
        _ui_press("space")
        return True
    if "play" in cmd and any(item in cmd for item in ("this", "video")) and not any(item in cmd for item in ("song", "music", "track")):
        _ui_press("space")
        return True
    if any(item in cmd for item in ("next song", "next track", "skip this")):
        _ui_press("nexttrack")
        return True
    if any(item in cmd for item in ("previous song", "previous track")):
        _ui_press("prevtrack")
        return True
    if "scroll down" in cmd:
        _ui_scroll(-500)
        return True
    if "scroll up" in cmd:
        _ui_scroll(500)
        return True

    if (
        re.search(r"\bbattery\b", cmd)
        or re.search(r"\bcpu\b", cmd)
        or re.search(r"\bram\b", cmd)
        or "system info" in cmd
    ):
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        battery = psutil.sensors_battery()
        battery_info = (
            f"{battery.percent:.0f}% {'charging' if battery.power_plugged else 'on battery'}"
            if battery
            else "unknown"
        )
        speak(f"CPU at {cpu}%. RAM {ram.percent}% used. Battery {battery_info}.")
        return True

    return False


def proactive_loop():
    if _proactive_stop.wait(30):
        return
    while not _proactive_stop.is_set():
        try:
            if ACTIVE:
                should_speak, message = planner.should_speak_proactively()
                if should_speak and message:
                    speak(jarvisify_response(message))
                    memory.log_activity("proactive", message[:60])
        except Exception as exc:
            print(f"[DEBUG][proactive] {exc}")
        _proactive_stop.wait(60)


def _init_face_recognition():
    global _face_rec_available, _face_module
    try:
        import face_recognition_module as face_module
        _face_module = face_module
        if face_module.is_face_registered():
            face_module.start_face_watcher()
            _face_rec_available = True
            print("[DEBUG][face] face recognition active")
            get_registry().set_status("FACE_RECOGNITION", SubsystemState.READY, "Face watcher process started")
        else:
            print("[DEBUG][face] no face registered")
            get_registry().set_status("FACE_RECOGNITION", SubsystemState.DISABLED, "No face registered")
    except Exception as exc:
        print(f"[DEBUG][face] unavailable: {exc}")
        error_handler.log_and_demote("FACE_RECOGNITION", exc, "Face recognition init", SubsystemState.DEGRADED)


def check_face_is_user():
    if not _face_rec_available or _face_module is None:
        return False
    try:
        return _face_module.recognize_face()
    except Exception:
        return False


def execute_with_feedback(action, original_input: str = "", _replan_depth: int = 0):
    if not action:
        return False, None
    action_name = action.get("action", "unknown")
    print(f"[DEBUG][executor] executing: {action}")

    pre_state = {}
    try:
        if hasattr(observer, "capture_pre_action_state"):
            pre_state = observer.capture_pre_action_state(action)
    except Exception as exc:
        print(f"[DEBUG][verify] pre-state capture failed: {exc}")

    try:
        result = executor.execute_with_retry(action)
        if isinstance(result, tuple) and len(result) == 2:
            success, message = result
        else:
            success, message = bool(result), None

        if success:
            try:
                if hasattr(observer, "verify_action_outcome"):
                    verified, verify_msg = observer.verify_action_outcome(action, pre_state)
                    if not verified:
                        print(f"[DEBUG][verify] failed: {verify_msg}")

                        if _replan_depth < 2 and hasattr(planner, "replan_action"):
                            new_action, repl_msg = planner.replan_action(action, verify_msg, original_input)
                            if new_action:
                                return execute_with_feedback(new_action, original_input, _replan_depth=_replan_depth + 1)
                            success = False
                            message = repl_msg or verify_msg
                        else:
                            success = False
                            message = verify_msg
            except Exception as exc:
                print(f"[DEBUG][verify] verification threw: {exc}")

        # Pipe results into self_model experiential layer
        try:
            model = self_model.get_model()
            model.record_outcome(action_name, success, message)
        except Exception as exc:
            print(f"[DEBUG][self_model] record outcome failed: {exc}")

        memory.log_action_result(action_name, success=success, detail=(message or "")[:60])
        if success:
            log_usage(action_name, observer.get_context_hint())
        else:
            log_failure(action_name, (message or "failed")[:60])
            if message:
                smart_msg = _get_smart_error_message(action_name, message)
                message = smart_msg
        log_event("execution_result", {"action": action_name, "success": success, "message": message})
        print(f"[DEBUG][executor] result: success={success}, message={message}")
        return success, message
    except Exception as exc:
        print(f"[DEBUG][executor] error: {exc}")
        
        try:
            model = self_model.get_model()
            model.record_outcome(action_name, False, str(exc))
        except Exception:
            pass

        memory.log_action_result(action_name, success=False, detail=str(exc)[:60])
        log_failure(action_name, str(exc)[:60])
        smart_msg = _get_smart_error_message(action_name, str(exc))
        return False, smart_msg


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP SCANS
# ══════════════════════════════════════════════════════════════════════════════

def _run_self_awareness_scan():
    if not _SELF_AWARENESS_READY:
        return

    def _scan():
        time.sleep(2)
        try:
            scan = self_awareness.scan_self(verbose=True)
            changes = scan.get("changes", {})
            added    = changes.get("added", [])
            removed  = changes.get("removed", [])
            modified = changes.get("modified", [])

            if not any([added, removed, modified]):
                return

            parts = []
            if added:
                if len(added) == 1:
                    parts.append(f"gained a new module: {added[0].replace('.py', '')}")
                elif len(added) <= 3:
                    names = ", ".join(a.replace(".py", "") for a in added)
                    parts.append(f"gained {len(added)} new modules: {names}")
                else:
                    parts.append(f"gained {len(added)} new modules")

            if removed:
                if len(removed) == 1:
                    parts.append(f"lost {removed[0].replace('.py', '')}")
                else:
                    parts.append(f"lost {len(removed)} modules")

            if modified:
                if len(modified) == 1:
                    parts.append(f"{modified[0].replace('.py', '')} was updated")
                elif len(modified) <= 3:
                    names = ", ".join(m.replace(".py", "") for m in modified)
                    parts.append(f"{len(modified)} modules updated: {names}")
                else:
                    parts.append(f"{len(modified)} modules were updated")

            if parts:
                message = "I evolved since last boot — " + "; ".join(parts) + "."
                print(f"[startup] {message}")
                speak(message)

                log_event(
                    "self_awareness_boot_scan",
                    {"added": added, "removed": removed, "modified": len(modified)},
                    severity="info",
                    module="self_awareness",
                    tags=["boot", "evolution"],
                )

        except Exception as exc:
            print(f"[startup] self_awareness scan error: {exc}")

    threading.Thread(target=_scan, daemon=True).start()


def _run_boot_syntax_scan():
    def _scan():
        time.sleep(3)
        try:
            import self_diagnostic
            passed, failed, warnings = self_diagnostic.check_syntax()

            if failed:
                bad_files = []
                for entry in failed[:2]:
                    detail = entry.get("detail", "")
                    fname = detail.split(" — ")[0].strip() if " — " in detail else detail[:40]
                    bad_files.append(fname)

                file_list = " and ".join(bad_files)
                extra = f" and {len(failed) - 2} more" if len(failed) > 2 else ""
                warning_msg = (
                    f"Heads up — {file_list}{extra} "
                    f"{'has' if len(failed) == 1 else 'have'} a syntax error. "
                    f"Some features may not work. Run a checkup for details."
                )

                print(f"[BOOT][syntax] WARNING: {len(failed)} file(s) with syntax errors")
                for entry in failed:
                    print(f"  ✗ {entry.get('detail', '')}")

                log_event(
                    "boot_syntax_warning",
                    {
                        "failed_count":   len(failed),
                        "warning_count":  len(warnings),
                        "failed_details": [e.get("detail", "") for e in failed],
                    },
                    severity="critical",
                    module="self_diagnostic",
                    tags=["boot", "syntax", "critical"],
                )

                runtime_visuals.alert(
                    f"Boot syntax check found {len(failed)} failing file(s)",
                    severity=1.0,
                )
                speak(warning_msg)

            else:
                print(f"[BOOT][syntax] All clear — {len(passed)} file(s) OK")
                log_event(
                    "boot_syntax_ok",
                    {"passed_count": len(passed), "warning_count": len(warnings)},
                    severity="debug",
                    module="self_diagnostic",
                    tags=["boot", "syntax"],
                )

        except Exception as exc:
            print(f"[BOOT][syntax] scan error: {exc}")
            log_event(
                "boot_syntax_scan_error",
                {"error": str(exc)},
                severity="warning",
                module="self_diagnostic",
                tags=["boot", "error"],
            )

    threading.Thread(target=_scan, daemon=True).start()


def _stop_proactive_loop():
    _proactive_stop.set()
    if _proactive_thread and _proactive_thread.is_alive():
        _proactive_thread.join(timeout=3.0)


def _stop_face_watcher():
    global _face_rec_available
    if _face_module is not None:
        _face_module.stop_face_watcher()
    _face_rec_available = False


def _stop_voice_resources():
    """Release voice interruption, playback, and the owned Pocket-TTS worker."""
    global is_speaking, stop_speaking
    stop_speaking = True
    listener.stop_interrupt_watcher()
    try:
        if _JARVIS_TTS_AVAILABLE and hasattr(jarvis_tts, "shutdown_engine"):
            jarvis_tts.shutdown_engine()
    except Exception as exc:
        print(f"[DEBUG][shutdown] Pocket-TTS cleanup failed: {exc}")
    try:
        if pygame is not None and pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
    except Exception as exc:
        print(f"[DEBUG][shutdown] audio cleanup failed: {exc}")
    is_speaking = False

def _start_task_queue():
    asyncio.run(init_task_queue(max_workers=4, use_dedicated_thread=True))


def _stop_task_queue():
    asyncio.run(shutdown_task_queue(timeout=10.0))


def _start_proactive_loop():
    global _proactive_thread
    if _proactive_thread and _proactive_thread.is_alive():
        return
    _proactive_stop.clear()
    _proactive_thread = threading.Thread(
        target=proactive_loop, daemon=True, name="jarvis-proactive-loop"
    )
    _proactive_thread.start()


def _start_evolver():
    import evolver
    evolver.start_evolver(interval_hours=6)


def _stop_evolver():
    import evolver
    evolver.stop_evolver()


def _publish_vision_status():
    """Publish only current, observed vision evidence to the native client."""
    try:
        import vision
        statuses = vision.get_vision_status()
        live = [name.upper() for name in ("screen_capture", "gemini", "tesseract_ocr")
                if statuses[name].get("evidence") == "LIVE" and statuses[name].get("current")]
        runtime_visuals.update(vision_status=" / ".join(live) if live else "UNKNOWN")
    except Exception:
        runtime_visuals.update(vision_status="UNKNOWN")


def startup() -> bool:
    global _startup_done, _lifecycle, _proactive_thread
    with _startup_lock:
        if _startup_done:
            return bool(_lifecycle and _lifecycle.is_started())
        _startup_done = True
        _lifecycle = LifecycleManager(component_stop_timeout=5.0, total_shutdown_timeout=25.0)

        def start_visual_bridge():
            runtime_visuals.start_bridge()
            runtime_visuals.set_base_state("dormant")
            _publish_vision_status()

        def start_voice():
            print("[Startup] Calibrating audio listener...")
            listener.calibrate_ambient_noise()
            if USE_POCKET_TTS and _JARVIS_TTS_AVAILABLE:
                # Pay model/CUDA/voice-state startup cost once.
                # Failure is non-fatal: speak() retains Edge-TTS -> SAPI fallback.
                jarvis_tts.start_engine()

        executor.init(speak_fn=speak, ask_fn=planner.ask)
        _lifecycle.register("RUNTIME_SSE", start_visual_bridge, runtime_visuals.stop_bridge, priority=0)
        _lifecycle.register("VOICE_RESOURCES", start_voice, _stop_voice_resources, priority=10)
        _lifecycle.register("OBSERVER", observer.start_observers, observer.stop_observers, priority=20)
        _lifecycle.register(
            "TASKS", lambda: tasks.init(speak_fn=speak, action_fn=lambda action: executor.execute(action)),
            tasks.stop_tasks, priority=30,
        )
        if _TASK_QUEUE_AVAILABLE:
            _lifecycle.register("TASK_QUEUE", _start_task_queue, _stop_task_queue,
                                priority=40, stop_timeout=12.0)
        _lifecycle.register("PROACTIVE_LOOP", _start_proactive_loop, _stop_proactive_loop, priority=50)
        _lifecycle.register("FACE_RECOGNITION", _init_face_recognition, _stop_face_watcher, priority=60)
        _lifecycle.register(
            "PROACTIVE_SCHEDULER",
            lambda: proactive_scheduler.start(speak_fn=speak, active_getter=lambda: ACTIVE),
            proactive_scheduler.stop_scheduler, priority=70,
        )
        _lifecycle.register("EVOLVER", _start_evolver, _stop_evolver, priority=80)
        _lifecycle.setup_signal_handlers()

        if not _lifecycle.start_all():
            _lifecycle.restore_signal_handlers()
            _startup_done = False
            return False

    speak("Runtime started. Subsystem availability depends on current checks.")
    memory.log_activity("startup", "Jarvis runtime started; subsystem status is evidence-based")
    memory.update_daily_stats("sessions")

    _run_boot_syntax_scan()
    _run_self_awareness_scan()
    return True


def _handle_follow_up(command):
    pending = planner.get_pending_followup()
    if not pending:
        return None
    normalized = (command or "").strip().lower()
    if normalized in FOLLOW_UP_YES and pending.get("action", {}).get("action") == "join_meeting":
        action = pending["action"]
        planner.clear_pending_followup()
        return action, "Joining now."
    if normalized in FOLLOW_UP_NO:
        planner.clear_pending_followup()
        return None, "Alright."
    return None


if __name__ == "__main__":
    try:
        if not startup():
            raise RuntimeError("JARVIS startup failed; see lifecycle diagnostics above")
        consecutive_empty = 0
        MAX_EMPTY_BEFORE_SLEEP = 5

        while not (_lifecycle and _lifecycle.is_shutting_down()):
            runtime_visuals.set_base_state("idle" if ACTIVE else "dormant")
            if not ACTIVE:
                consecutive_empty = 0

                if _face_rec_available and check_face_is_user():
                    ACTIVE = True
                    last_active = time.time()
                    observer.mark_user_input()
                    listener.play_jarvis_ui_sound()

                    minutes_away = _face_module.minutes_since_last_seen()
                    minutes_since_checkin = proactive_scheduler.minutes_since_last_checkin()

                    if minutes_away >= 30 and minutes_since_checkin >= 30:
                        speak("Welcome back.")
                        time.sleep(0.8)
                        proactive_scheduler.run_obligation_checkin(
                            speak=True,
                            context_note="User just returned after being away."
                        )
                    else:
                        speak(random.choice(GREETINGS))
                        pending_msg = proactive_scheduler.deliver_pending_message()
                        if pending_msg:
                            time.sleep(1.5)
                            speak(jarvisify_response(pending_msg))

                    continue

                if wake_word_detect():
                    listener.play_jarvis_ui_sound()
                    ACTIVE = True
                    last_active = time.time()
                    observer.mark_user_input()
                    speak(random.choice(GREETINGS))

                    pending_msg = proactive_scheduler.deliver_pending_message()
                    if pending_msg:
                        time.sleep(1.5)
                        speak(jarvisify_response(pending_msg))
                    continue

            print("[DEBUG][loop] active and waiting for command")
            command = listen()

            if command is None:
                consecutive_empty += 1
                print(f"[DEBUG][loop] no command ({consecutive_empty}/{MAX_EMPTY_BEFORE_SLEEP})")
                if consecutive_empty >= MAX_EMPTY_BEFORE_SLEEP or time.time() - last_active > 30:
                    speak(random.choice(BYES))
                    ACTIVE = False
                    consecutive_empty = 0
                    planner.clear_history()
                    planner.clear_pending_followup()
                    conversation_manager.reset_conversation()
                continue

            consecutive_empty = 0
            last_active = time.time()

            command = _preprocess_command(command)

            if _is_interrupt_only_command(command):
                print(f"[DEBUG][loop] interrupt-only command received: '{command}'")
                continue

            if "register my face" in command or "register face" in command:
                speak("Alright. Look at the camera.")
                threading.Thread(
                    target=lambda: (
                        _face_module.register_face() if _face_module else None,
                        speak("Face registered."),
                    ),
                    daemon=True,
                ).start()
                continue

            if any(keyword in command for keyword in EXIT_KEYWORDS):
                conversation_manager.reset_conversation()
                speak(random.choice(BYES))
                memory.log_activity("exit", "user said goodbye")
                save_session()
                break

            if handle_fast_command(command):
                print("[DEBUG][loop] handled by fast command path")
                continue

            follow_up_result = _handle_follow_up(command)
            if follow_up_result is not None:
                action, response = follow_up_result
                if action:
                    success, exec_message = execute_with_feedback(action, original_input=command)
                    if exec_message:
                        speak(jarvisify_response(exec_message))
                    elif response and success:
                        speak(jarvisify_response(response))
                    elif not success:
                        speak("Something went wrong.")
                else:
                    speak(jarvisify_response(response))
                continue

            try:
                print(f"[DEBUG][planner] input: {command}")
                brain.reset_last_provider()
                action, spoken_response, _model_type = planner.ask(command)
                runtime_visuals.update(provider_model=brain.get_last_provider_model())
                _publish_vision_status()
                log_event("planner_output", {"action": action, "response": spoken_response})
                print(f"[DEBUG][planner] output: action={action}, response={spoken_response}")

                if action and action.get("action") == "save_login" and action.get("needs_dialog"):
                    _handle_save_login_flow(action)
                    continue

                final_response = spoken_response
                if action:
                    if action.get("action") == "exit":
                        conversation_manager.reset_conversation()
                        speak(random.choice(BYES))
                        memory.log_activity("exit", "action triggered exit")
                        save_session()
                        break
                    success, exec_message = execute_with_feedback(action, original_input=command)
                    if exec_message:
                        final_response = exec_message
                    elif not success and not final_response:
                        final_response = "Something went wrong."

                if final_response:
                    speak(jarvisify_response(final_response))
            except Exception as exc:
                print(f"[DEBUG][loop] unhandled error: {exc}")
                speak("Something went wrong.")
    except KeyboardInterrupt:
        pass
    finally:
        save_session()
        runtime_visuals.set_base_state("dormant")
        if _lifecycle:
            _lifecycle.shutdown("Runtime exit")
            _lifecycle.restore_signal_handlers()
        _startup_done = False
        if _runtime_instance_guard is not None:
            _runtime_instance_guard.release()
