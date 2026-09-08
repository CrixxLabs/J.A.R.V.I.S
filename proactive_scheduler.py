# proactive_scheduler.py — Proactive Obligation Reasoning Loop (Layer 2)
#
# Separate from jarvis.py's existing proactive_loop (which handles battery/CPU).
# This loop reasons about obligations.json on its own schedule using brain.py.
#
# Architecture:
#   - Background daemon thread, started once from jarvis.startup()
#   - Configurable check-in times (edit CHECKIN_TIMES below)
#   - At each check-in: pulls obligation context, sends to LLM reasoning model,
#     speaks result unprompted if Jarvis is ACTIVE
#   - Cross-references observer.py activity as a reasoning signal
#   - run_obligation_checkin() is also exported for Layer 3 face-recognition sweep

import threading
import time
import datetime

# ── Configuration — edit these freely ─────────────────────────────────────────
CHECKIN_TIMES = ["09:00", "21:00"]
_CHECKIN_COOLDOWN_MINUTES = 30

_active_ref = {"value": False}

_speak_fn   = None
_jarvis_active_getter = None

_last_checkin_time: datetime.datetime | None = None
_lock = threading.Lock()

# ── Weekly analysis tracking ───────────────────────────────────────────────────
# Tracks the last date a behavioral analysis ran so we only fire once per week.
_last_analysis_date: datetime.date | None = None
_analysis_lock = threading.Lock()

# ── Init ──────────────────────────────────────────────────────────────────────

def init(speak_fn, active_getter):
    global _speak_fn, _jarvis_active_getter
    _speak_fn             = speak_fn
    _jarvis_active_getter = active_getter
    print("[proactive_scheduler] Initialized.")


def _is_jarvis_active() -> bool:
    if _jarvis_active_getter is None:
        return False
    try:
        return bool(_jarvis_active_getter())
    except Exception:
        return False


# ── Observer cross-reference ──────────────────────────────────────────────────

def _build_activity_signal() -> str:
    try:
        from observer import get_state_snapshot
        snap = get_state_snapshot()

        app          = snap.get("active_app", "")
        title        = snap.get("active_title", "")
        idle_seconds = snap.get("user_idle_seconds", 0)
        youtube_time = snap.get("youtube_time", 0)

        parts = []

        if app:
            parts.append(f"User currently has {app} open (window: \"{title[:60]}\").")

        if idle_seconds > 1800:
            idle_min = int(idle_seconds // 60)
            parts.append(f"User has been idle for {idle_min} minutes.")
        elif idle_seconds > 600:
            idle_min = int(idle_seconds // 60)
            parts.append(f"User has been idle for {idle_min} minutes.")

        if youtube_time >= 2700:
            yt_min = int(youtube_time // 60)
            parts.append(
                f"User has spent {yt_min} minutes on YouTube today — "
                f"possibly taking a break from work."
            )

        return " ".join(parts) if parts else "No specific activity context available."

    except Exception as e:
        print(f"[proactive_scheduler] observer signal error: {e}")
        return "Activity context unavailable."


def _check_obligation_app_overlap(obligations_context: str) -> str:
    try:
        from observer import get_state_snapshot
        snap  = get_state_snapshot()
        title = (snap.get("active_title", "") or "").lower()
        app   = (snap.get("active_app", "") or "").lower()

        if not title and not app:
            return ""

        import obligations as _ob
        all_obs = _ob.get_pending() + _ob.get_overdue()

        overlap_signals = []
        for ob in all_obs:
            ob_title_words = [
                w for w in ob.get("title", "").lower().split()
                if len(w) > 3
            ]
            for word in ob_title_words:
                if word in title or word in app:
                    overlap_signals.append(
                        f"User has something related to '{ob['title']}' open right now."
                    )
                    break

        return " ".join(overlap_signals)

    except Exception as e:
        print(f"[proactive_scheduler] overlap check error: {e}")
        return ""


# ── Core reasoning function ───────────────────────────────────────────────────

def run_obligation_checkin(speak: bool = True, context_note: str = "") -> str:
    """
    Pull obligations, build a reasoning prompt, send to LLM, return the result.
    Optionally speak the result via _speak_fn.
    """
    global _last_checkin_time

    try:
        import obligations
        import brain

        context_block = obligations.build_context_block()

        if context_block.strip() == "No active obligations.":
            result = "All clear — no pending obligations right now."
            if speak and _speak_fn:
                _speak_fn(result)
            return result

        activity_signal = _build_activity_signal()
        overlap_signal  = _check_obligation_app_overlap(context_block)

        now_str = datetime.datetime.now().strftime("%A, %d %B %Y, %I:%M %p")

        prompt_parts = [
            f"Current date and time: {now_str}.",
            "",
            "Here are the user's current academic obligations:",
            context_block,
            "",
            f"Current activity context: {activity_signal}",
        ]

        if overlap_signal:
            prompt_parts.append(f"Overlap detected: {overlap_signal}")

        if context_note:
            prompt_parts.append(f"Additional context: {context_note}")

        prompt = "\n".join(prompt_parts)

        system_prompt = (
            "You are Jarvis, a sharp personal AI assistant. "
            "The user has asked for a status sweep of their academic obligations. "
            "Reason about urgency: overdue items first, then due today, then due soon, then pending. "
            "Be specific — use the actual obligation names. "
            "Keep it to 2-4 sentences maximum. "
            "Speak directly to the user in first-person assistant voice. "
            "Do NOT list items as bullet points — speak naturally. "
            "Do NOT say 'Here is your summary' or similar filler. "
            "If something is overdue, say so plainly. "
            "If everything looks fine, say so briefly. "
            "Do NOT return JSON. Plain conversational text only."
        )

        # ── Profile context injection (additive) ──────────────────────────────
        profile_signal = ""
        try:
            from user_profile import get_profile_for_scheduler
            profile_signal = get_profile_for_scheduler()
            if profile_signal:
                prompt_parts.append(f"\n{profile_signal}")
                prompt = "\n".join(prompt_parts)
                print("[proactive_scheduler] Profile context injected into obligation reasoning.")
        except Exception as profile_err:
            print(f"[proactive_scheduler] profile context error (non-fatal): {profile_err}")
        # ─────────────────────────────────────────────────────────────────────

        print("[proactive_scheduler] Sending obligation reasoning to LLM...")

        raw = brain.ask_llm(
            query      = prompt,
            context    = system_prompt,
            model_type = "reasoning",
            allow_actions = False,
        )

        result = (raw or "").strip()

        if not result:
            print("[proactive_scheduler] LLM returned empty — skipping.")
            return ""

        print(f"[proactive_scheduler] Reasoning result: {result[:120]}...")

        with _lock:
            _last_checkin_time = datetime.datetime.now()

        if speak and _speak_fn:
            _speak_fn(result)

        return result

    except Exception as e:
        print(f"[proactive_scheduler] run_obligation_checkin error: {e}")
        return ""


# ── Cooldown check ────────────────────────────────────────────────────────────

def _checkin_on_cooldown() -> bool:
    with _lock:
        if _last_checkin_time is None:
            return False
        elapsed_minutes = (datetime.datetime.now() - _last_checkin_time).total_seconds() / 60
        return elapsed_minutes < _CHECKIN_COOLDOWN_MINUTES


def minutes_since_last_checkin() -> float:
    with _lock:
        if _last_checkin_time is None:
            return 9999.0
        return (datetime.datetime.now() - _last_checkin_time).total_seconds() / 60


# ── Weekly behavioral analysis ────────────────────────────────────────────────

def _should_run_weekly_analysis() -> bool:
    """
    Returns True if a weekly behavioral analysis is due.
    Fires once per week — on Monday, or if it's never run before.
    Uses day-of-week so it doesn't require a persistent timestamp file.
    """
    with _analysis_lock:
        today = datetime.date.today()
        if _last_analysis_date is None:
            return True
        days_since = (today - _last_analysis_date).days
        return days_since >= 7


def _run_weekly_analysis():
    """
    Run user_profile behavioral analysis in a background thread.
    Auto-saves observed entries. Does not speak — passive background job.
    """
    global _last_analysis_date
    try:
        print("[proactive_scheduler] Running weekly behavioral profile analysis...")
        from user_profile import run_behavioral_analysis
        new_entries = run_behavioral_analysis()
        with _analysis_lock:
            _last_analysis_date = datetime.date.today()
        if new_entries:
            print(
                f"[proactive_scheduler] Weekly analysis complete — "
                f"{len(new_entries)} new observed profile entry(s) saved."
            )
        else:
            print("[proactive_scheduler] Weekly analysis complete — no new patterns found.")
    except Exception as e:
        print(f"[proactive_scheduler] weekly analysis error: {e}")


# ── Scheduler thread ──────────────────────────────────────────────────────────

def _scheduler_loop():
    global _scheduler_shutdown
    print(f"[proactive_scheduler] Scheduler running. Check-in times: {CHECKIN_TIMES}")
    _fired_this_minute: set = set()

    while not _scheduler_shutdown:
        try:
            now_str = datetime.datetime.now().strftime("%H:%M")

            if now_str == "00:00":
                _fired_this_minute.clear()

            if now_str in CHECKIN_TIMES and now_str not in _fired_this_minute:
                _fired_this_minute.add(now_str)

                if _checkin_on_cooldown():
                    print(f"[proactive_scheduler] {now_str} check-in skipped — on cooldown.")
                else:
                    print(f"[proactive_scheduler] Scheduled check-in at {now_str}.")

                    if _is_jarvis_active():
                        run_obligation_checkin(speak=True)
                    else:
                        result = run_obligation_checkin(speak=False)
                        if result:
                            _store_pending_message(result)
                            print(
                                "[proactive_scheduler] Stored pending message "
                                f"(Jarvis inactive): {result[:80]}..."
                            )

            # ── Weekly behavioral analysis hook ───────────────────────────────
            # Fires at 03:00 on any day if analysis hasn't run this week.
            # Chosen at 3am to avoid competing with active-hour check-ins.
            if now_str == "03:00" and f"analysis_{now_str}" not in _fired_this_minute:
                if _should_run_weekly_analysis():
                    _fired_this_minute.add(f"analysis_{now_str}")
                    threading.Thread(
                        target=_run_weekly_analysis,
                        daemon=True,
                        name="WeeklyProfileAnalysis",
                    ).start()
            # ─────────────────────────────────────────────────────────────────

        except Exception as e:
            print(f"[proactive_scheduler] scheduler loop error: {e}")

        time.sleep(30)


# ── Pending message store ─────────────────────────────────────────────────────

_pending_message: str = ""
_pending_message_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_shutdown = False


def _store_pending_message(message: str):
    global _pending_message
    with _pending_message_lock:
        _pending_message = message


def deliver_pending_message() -> str:
    global _pending_message
    with _pending_message_lock:
        msg = _pending_message
        _pending_message = ""
    return msg


def stop_scheduler():
    """Signal the scheduler loop to stop."""
    global _scheduler_shutdown, _scheduler_thread
    _scheduler_shutdown = True
    if _scheduler_thread and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=3.0)
    print("[proactive_scheduler] Scheduler stopped.")


# ── Start ─────────────────────────────────────────────────────────────────────

def start(speak_fn, active_getter):
    global _scheduler_thread, _scheduler_shutdown
    init(speak_fn, active_getter)
    _scheduler_shutdown = False
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="ProactiveScheduler")
    _scheduler_thread.start()
    print("[proactive_scheduler] Background scheduler started.")