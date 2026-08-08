import asyncio
import datetime
import os
import random
import re
import tempfile
import threading
import time

import edge_tts
import pygame
import pyautogui
import speech_recognition as sr
import win32com.client
from dotenv import load_dotenv

import core
import executor
import memory
import observer
import planner
import tasks
import conversation_manager
import proactive_scheduler
import listener
from memory import log_failure, log_usage
from session_logger import log_event, save_session

load_dotenv()

VOICE = "en-US-GuyNeural"
stop_speaking = False
is_speaking = False
_startup_done = False
ACTIVE = False
last_active = time.time()

try:
    pygame.mixer.init()
except Exception as exc:
    print(f"[DEBUG][audio] mixer init failed: {exc}")


_STRIP_PHRASES = [
    "certainly!",
    "certainly,",
    "of course!",
    "of course,",
    "absolutely!",
    "absolutely,",
    "great question",
    "i'd be happy to",
    "i'd be glad to",
    "i'm happy to help",
    "as an ai",
    "as a language model",
    "i think",
    "i believe",
    "i'm sorry",
    "i apologize",
    "sorry about that",
    "please note that",
    "it's worth noting",
    "i hope this helps",
    "let me know if",
    "feel free to ask",
    "would you like me to",
    "do you want me to",
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
    "exit",
    "bye",
    "goodbye",
    "shut down",
    "shutdown",
    "sleep",
    "turn off",
    "quit",
    "close jarvis",
]

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


def speak(text):
    global stop_speaking, is_speaking
    clean_lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or re.match(r"^\{.*\}$", line):
            continue
        clean_lines.append(line)
    final_text = " ".join(clean_lines).strip()
    if not final_text:
        return

    final_text = re.sub(r"```(?:json)?", "", final_text, flags=re.IGNORECASE)
    final_text = final_text.replace("```", "")
    final_text = re.sub(r'\{.*?\}', '', final_text, flags=re.DOTALL).strip()
    final_text = re.sub(r'\s+', ' ', final_text).strip()

    if not final_text:
        return

    print(f"Jarvis: {final_text}")
    log_event("jarvis_response", final_text)
    memory.log_activity("speak", final_text[:60])
    stop_speaking = False
    is_speaking = True

    try:
        async def _tts():
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            tmp.close()
            communicator = edge_tts.Communicate(final_text, VOICE, rate="+5%")
            await communicator.save(tmp.name)
            return tmp.name

        mp3_path = asyncio.run(_tts())

        def _play_audio(path):
            global is_speaking
            try:
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    if stop_speaking:
                        pygame.mixer.music.stop()
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

        threading.Thread(target=_play_audio, args=(mp3_path,), daemon=True).start()

    except Exception as exc:
        print(f"[DEBUG][speak] edge_tts failed: {exc}")
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(final_text)
        except Exception as fallback_exc:
            print(f"[DEBUG][speak] SAPI fallback failed: {fallback_exc}")
        finally:
            is_speaking = False


def listen(timeout=12, phrase_time_limit=20):
    """Voice capture using personalized listener from listener.py"""
    return listener.listen_for_command(timeout, phrase_time_limit)


def _preprocess_command(command: str) -> str:
    """
    Preprocessing step between listen() and planner.ask().
    Detects Malayalam input and translates to English.
    """
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
    """Clap detection using listener.py"""
    return listener.detect_double_clap()


# ── Diagnostic trigger phrases (handled pre-LLM for reliability) ──────────────
_DIAGNOSTIC_TRIGGERS = {
    "run a checkup",
    "run checkup",
    "system checkup",
    "system diagnostic",
    "run diagnostic",
    "health check",
    "run health check",
    "are you okay",
    "are you ok",
    "self diagnostic",
    "self check",
    "check yourself",
    "diagnose yourself",
}


def handle_fast_command(command):
    import psutil

    cmd = (command or "").lower()

    # ── Diagnostic fast-path ──────────────────────────────────────────────────
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
    # ─────────────────────────────────────────────────────────────────────────

    if any(item in cmd for item in ("what time", "what's the time", "time now", "current time")):
        speak(f"It's {datetime.datetime.now().strftime('%I:%M %p')}.")
        return True
    if any(item in cmd for item in ("what's the date", "today's date", "what day")):
        speak(datetime.datetime.now().strftime("%A, %d %B %Y."))
        return True
    if "volume up" in cmd:
        pyautogui.press("volumeup")
        return True
    if "volume down" in cmd:
        pyautogui.press("volumedown")
        return True
    if "mute" in cmd:
        pyautogui.press("volumemute")
        return True
    if any(item in cmd for item in ("pause", "resume")) and any(item in cmd for item in ("music", "song", "video", "this")):
        pyautogui.press("space")
        return True
    if "play" in cmd and any(item in cmd for item in ("this", "video")) and not any(item in cmd for item in ("song", "music", "track")):
        pyautogui.press("space")
        return True
    if any(item in cmd for item in ("next song", "next track", "skip this")):
        pyautogui.press("nexttrack")
        return True
    if any(item in cmd for item in ("previous song", "previous track")):
        pyautogui.press("prevtrack")
        return True
    if "scroll down" in cmd:
        pyautogui.scroll(-500)
        return True
    if "scroll up" in cmd:
        pyautogui.scroll(500)
        return True
    if any(item in cmd for item in ("battery", "cpu", "ram", "system info")):
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
    time.sleep(30)
    while True:
        try:
            if ACTIVE:
                should_speak, message = planner.should_speak_proactively()
                if should_speak and message:
                    speak(jarvisify_response(message))
                    memory.log_activity("proactive", message[:60])
        except Exception as exc:
            print(f"[DEBUG][proactive] {exc}")
        time.sleep(60)


def _init_face_recognition():
    global _face_rec_available, _face_module
    try:
        import face_recognition_module as face_module

        _face_module = face_module
        if face_module.is_face_registered():
            face_module.start_face_watcher()
            _face_rec_available = True
            print("[DEBUG][face] face recognition active")
        else:
            print("[DEBUG][face] no face registered")
    except Exception as exc:
        print(f"[DEBUG][face] unavailable: {exc}")


def check_face_is_user():
    if not _face_rec_available or _face_module is None:
        return False
    try:
        return _face_module.recognize_face()
    except Exception:
        return False


def execute_with_feedback(action):
    if not action:
        return False, None
    action_name = action.get("action", "unknown")
    print(f"[DEBUG][executor] executing: {action}")
    try:
        result = executor.execute_with_retry(action)
        if isinstance(result, tuple) and len(result) == 2:
            success, message = result
        else:
            success, message = bool(result), None

        memory.log_action_result(action_name, success=success, detail=(message or "")[:60])
        if success:
            log_usage(action_name, observer.get_context_hint())
        else:
            log_failure(action_name, (message or "failed")[:60])
        log_event("execution_result", {"action": action_name, "success": success, "message": message})
        print(f"[DEBUG][executor] result: success={success}, message={message}")
        return success, message
    except Exception as exc:
        print(f"[DEBUG][executor] error: {exc}")
        memory.log_action_result(action_name, success=False, detail=str(exc)[:60])
        log_failure(action_name, str(exc)[:60])
        return False, "Something went wrong."


def _run_boot_syntax_scan():
    """
    Silent background syntax scan run at the end of startup().
    - Checks all .py files for syntax errors using self_diagnostic.check_syntax()
    - If any failures found: speaks a warning and logs at severity=critical
    - If only warnings: logs silently, does NOT speak (don't alarm on minor issues)
    - Never blocks startup — runs in a daemon thread, completes after
      "Systems up." so the user hears the boot message first
    """
    def _scan():
        # Small delay so "Systems up." finishes speaking before any warning
        time.sleep(3)
        try:
            import self_diagnostic
            passed, failed, warnings = self_diagnostic.check_syntax()

            if failed:
                # Build a concise spoken warning — max 2 filenames aloud
                bad_files = []
                for entry in failed[:2]:
                    detail = entry.get("detail", "")
                    # Extract just the filename from the detail string
                    # detail format: "some_file.py — error message"
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

                # Log as critical
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

                # Speak the warning
                speak(warning_msg)

            else:
                # Clean boot — log silently at debug level, do not speak
                print(f"[BOOT][syntax] All clear — {len(passed)} file(s) OK")
                log_event(
                    "boot_syntax_ok",
                    {"passed_count": len(passed), "warning_count": len(warnings)},
                    severity="debug",
                    module="self_diagnostic",
                    tags=["boot", "syntax"],
                )

        except Exception as exc:
            # The scan itself crashed — log it but never crash startup
            print(f"[BOOT][syntax] scan error: {exc}")
            log_event(
                "boot_syntax_scan_error",
                {"error": str(exc)},
                severity="warning",
                module="self_diagnostic",
                tags=["boot", "error"],
            )

    threading.Thread(target=_scan, daemon=True).start()


def startup():
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    
    # Calibrate listener once at startup
    print("[Startup] Calibrating audio listener...")
    listener.calibrate_ambient_noise()
    
    observer.start_observers()
    executor.init(speak_fn=speak, ask_fn=planner.ask)
    tasks.init(speak_fn=speak, action_fn=lambda action: executor.execute(action))
    threading.Thread(target=proactive_loop, daemon=True).start()
    threading.Thread(target=_init_face_recognition, daemon=True).start()

    # ── Layer 2: start obligation reasoning scheduler ─────────────────────────
    proactive_scheduler.start(
        speak_fn      = speak,
        active_getter = lambda: ACTIVE,
    )
    # ─────────────────────────────────────────────────────────────────────────

    speak("Systems up.")
    memory.log_activity("startup", "Jarvis online")
    memory.update_daily_stats("sessions")

    try:
        import evolver
        evolver.start_evolver(interval_hours=6)
    except Exception as exc:
        print(f"[DEBUG][startup] evolver not started: {exc}")

    # ── Boot-time syntax scan (runs in background, speaks only if broken) ─────
    _run_boot_syntax_scan()
    # ─────────────────────────────────────────────────────────────────────────


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
    startup()

    try:
        consecutive_empty = 0
        MAX_EMPTY_BEFORE_SLEEP = 5

        while True:
            if not ACTIVE:
                consecutive_empty = 0

                if _face_rec_available and check_face_is_user():
                    ACTIVE = True
                    last_active = time.time()
                    observer.mark_user_input()
                    listener.play_jarvis_ui_sound()

                    # ══════════════════════════════════════════════════════════
                    # ── Layer 3: "Daddy's Home" greeting ──────────────────────
                    # ══════════════════════════════════════════════════════════
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
                    # ══════════════════════════════════════════════════════════

                    continue

                if wake_word_detect():
                    listener.play_jarvis_ui_sound()
                    ACTIVE = True
                    last_active = time.time()
                    observer.mark_user_input()
                    speak(random.choice(GREETINGS))

                    # ── Layer 2: deliver any pending obligation message ────────
                    pending_msg = proactive_scheduler.deliver_pending_message()
                    if pending_msg:
                        time.sleep(1.5)
                        speak(jarvisify_response(pending_msg))
                    # ─────────────────────────────────────────────────────────
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
                    success, exec_message = execute_with_feedback(action)
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
                action, spoken_response, _model_type = planner.ask(command)
                log_event("planner_output", {"action": action, "response": spoken_response})
                print(f"[DEBUG][planner] output: action={action}, response={spoken_response}")

                final_response = spoken_response
                if action:
                    if action.get("action") == "exit":
                        conversation_manager.reset_conversation()
                        speak(random.choice(BYES))
                        memory.log_activity("exit", "action triggered exit")
                        save_session()
                        break
                    success, exec_message = execute_with_feedback(action)
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