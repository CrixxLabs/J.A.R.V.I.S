# planner.py — Decision Router
# Decides: handle locally OR route to brain (LLM).
# Returns (action_dict | None, spoken_response_str, model_type_str).
# Never calls speak() or prints user-facing output.

import datetime
import re
import time

from dotenv import load_dotenv

import brain
import core
import conversation_manager
import plugin_loader
from executor import AVAILABLE_ACTIONS_LIST
from memory import (
    format_facts_for_prompt,
    get_memory_summary,
    get_prediction_hint,
    set_last_intent,
    should_avoid,
    update_daily_stats,
)
from observer import get_context_hint, get_state_snapshot
from session_logger import log_event

load_dotenv()

# ── Load skills once at module import time ─────────────────────────────────────
plugin_loader.load_skills()

# ── Conversation history (kept short) ─────────────────────────────────────────
_history: list = []
_pending_followup: dict | None = None

DISABLED_ACTIONS = {
    "send_email", "calendar_today", "calendar_add",
    "spotify_play", "spotify_control",
}

CAPABILITY_QUESTIONS = frozenset((
    "what can you do", "what are your abilities", "what are your features",
    "what do you support", "what are your capabilities",
    "tell me what you can do", "what can jarvis do",
))

_REASONING_KEYWORDS = (
    "code", "debug", "fix the", "write a function", "calculate", "algorithm",
    "explain why", "how does", "why does", "difference between", "compare",
    "what is the", "how to", "program", "script", "logic", "math",
)
_ACTION_KEYWORDS = (
    "open", "close", "play", "search for", "remind me", "timer",
    "whatsapp", "join", "meeting", "volume", "lock", "shutdown", "restart",
    "type", "click", "scroll", "calendar", "weather", "news",
    # Layer 1 obligation actions
    "assignment", "exam", "deadline", "obligation", "due", "overdue",
    "pending", "portal", "scan", "submitted", "worksheet", "coursera",
)
_FAST_MAX_WORDS = 5

_INFO_QUERY_STARTERS = (
    "who is", "what is", "what are", "where is", "when is",
    "why is", "why does", "how is", "how does", "explain",
    "tell me about", "define", "describe", "who was", "what was",
)


def _classify_model_type(text: str) -> str:
    lowered = (text or "").lower().strip()
    words   = lowered.split()
    if len(words) <= _FAST_MAX_WORDS and not any(k in lowered for k in _REASONING_KEYWORDS):
        return "fast"
    if any(k in lowered for k in _REASONING_KEYWORDS):
        return "reasoning"
    if any(k in lowered for k in _ACTION_KEYWORDS):
        return "action"
    return "chat"


def _looks_like_info_query(text: str) -> bool:
    """
    Detect plain knowledge / info requests.
    These should get direct plain-text answers, not action JSON.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return False

    if any(lowered.startswith(s) for s in _INFO_QUERY_STARTERS):
        return True

    # Short noun-phrase queries like: "quantum computing", "elon musk"
    if len(lowered.split()) <= 4 and not any(k in lowered for k in _ACTION_KEYWORDS):
        return True

    return False


def add_to_history(role: str, content: str):
    _history.append({"role": role, "content": content})
    if len(_history) > 20:
        _history.pop(0)


def clear_history():
    _history.clear()


def clear_pending_followup():
    global _pending_followup
    _pending_followup = None


def get_pending_followup():
    return _pending_followup


def set_pending_followup(payload: dict):
    global _pending_followup
    _pending_followup = payload


def is_capability_question(text: str) -> bool:
    lowered = (text or "").lower().strip()
    return any(q in lowered for q in CAPABILITY_QUESTIONS)


def get_capability_response() -> str:
    return (
        "I can open and close apps, check WhatsApp updates, join meetings, "
        "search the web, describe your screen, monitor your system, "
        "set reminders at exact times, track assignments and deadlines, "
        "and handle quick system controls."
    )


_last_proactive_speak = 0.0
_PROACTIVE_COOLDOWN   = 300


def should_speak_proactively() -> tuple:
    global _last_proactive_speak
    now = time.time()
    if now - _last_proactive_speak < _PROACTIVE_COOLDOWN:
        return False, ""
    snapshot = get_state_snapshot()
    battery  = snapshot.get("battery_percent", -1)
    if 0 < battery <= 15 and not snapshot.get("battery_charging", False):
        _last_proactive_speak = now
        return True, f"Battery is at {battery:.0f}%."
    if snapshot.get("cpu_percent", 0) > 90:
        _last_proactive_speak = now
        return True, "CPU is running hot."
    return False, ""


def _normalize_action(action) -> dict | None:
    if not isinstance(action, dict):
        return None
    name = action.get("action")
    if not name or name not in AVAILABLE_ACTIONS_LIST:
        return None
    if name in DISABLED_ACTIONS:
        return None
    return action


def _normalize_brain_action_shape(action) -> dict | None:
    """
    Normalize sloppy LLM JSON into Jarvis's expected action schema.
    """
    if not isinstance(action, dict):
        return None

    if action.get("action"):
        return action

    kind = str(action.get("type", "")).lower().strip()
    name = (
        action.get("name")
        or action.get("app")
        or action.get("app_id")
        or ""
    )

    if kind == "web_search" and action.get("query"):
        return {"action": "web_search", "query": action.get("query", "")}

    if kind in {"open", "launch", "start"} and name:
        return {"action": "open_app", "app": str(name)}

    if kind in {"close", "quit", "kill", "terminate"} and name:
        return {"action": "close_app", "app": str(name)}

    return action


def _parse_brain_response(raw: str) -> tuple:
    """
    Extract (action_dict | None, spoken_str) from a brain.ask_llm() response.
    """
    if not raw:
        return None, ""

    cleaned_raw = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()

    action = None
    json_match = re.search(r"\{[\s\S]*\}", cleaned_raw)
    if json_match:
        candidate = json_match.group(0).strip()
        try:
            import json
            action = json.loads(candidate)
            action = _normalize_brain_action_shape(action)
        except Exception:
            action = None

    spoken = cleaned_raw
    if json_match:
        spoken = cleaned_raw.replace(json_match.group(0), " ").strip()

    spoken = re.sub(r"\s{2,}", " ", spoken).strip()
    return action, spoken


# ── Reminder time resolution (Module 2) ───────────────────────────────────────

def _resolve_reminder_seconds(entities: dict, raw_text: str) -> int:
    from dateutil import parser as dateutil_parser

    datetime_str = (entities or {}).get("datetime", "").strip()

    for source in (datetime_str, raw_text or ""):
        if not source:
            continue
        lowered = source.lower()

        rel_match = re.search(
            r"\bin\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)\b",
            lowered,
        )
        if rel_match:
            amount = int(rel_match.group(1))
            unit   = rel_match.group(2).rstrip("s")
            if unit == "second":
                return amount
            if unit == "minute":
                return amount * 60
            if unit == "hour":
                return amount * 3600

        after_match = re.search(
            r"\bafter\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)\b",
            lowered,
        )
        if after_match:
            amount = int(after_match.group(1))
            unit   = after_match.group(2).rstrip("s")
            if unit == "second":
                return amount
            if unit == "minute":
                return amount * 60
            if unit == "hour":
                return amount * 3600

    if datetime_str:
        try:
            now    = datetime.datetime.now()
            parsed = dateutil_parser.parse(
                datetime_str,
                default=now.replace(second=0, microsecond=0),
                fuzzy=True,
            )
            if parsed <= now:
                parsed += datetime.timedelta(days=1)
            delta = (parsed - now).total_seconds()
            if 0 < delta < 7 * 24 * 3600:
                return int(delta)
        except Exception:
            pass

    if raw_text:
        bare_time = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
            raw_text,
            re.IGNORECASE,
        )
        if bare_time:
            try:
                now    = datetime.datetime.now()
                parsed = dateutil_parser.parse(
                    bare_time.group(1),
                    default=now.replace(second=0, microsecond=0),
                )
                if parsed <= now:
                    parsed += datetime.timedelta(days=1)
                delta = (parsed - now).total_seconds()
                if 0 < delta < 7 * 24 * 3600:
                    return int(delta)
            except Exception:
                pass

    return 3600


# ── Local fast-path: handle known intents without LLM ─────────────────────────

def _handle_local_intent(intent_data: dict) -> tuple | None:
    intent   = intent_data.get("intent", "")
    params   = intent_data.get("params", {})
    entities = intent_data.get("entities", {})

    if intent == "open_app":
        app = params.get("app", "") or entities.get("app", "")
        if app:
            core.update_context(last_app=app)
            return {"action": "open_app", "app": app}, f"Opening {app}."
        return None, "Which app?"

    if intent == "close_app":
        app = params.get("app") or entities.get("app", "") or core._context["last_app"]
        if app:
            core.update_context(last_app=app)
            return {"action": "close_app", "app": app}, f"Closing {app}."
        return None, "Which app should I close?"

    if intent == "check_whatsapp":
        return {"action": "whatsapp_read"}, "Checking WhatsApp."

    if intent == "join_meeting":
        link = params.get("link") or core._context["last_link"]
        if link:
            return {"action": "join_meeting", "link": link,
                    "subject": core._context["last_subject"] or "class"}, "Joining now."
        return {"action": "whatsapp_read"}, "Looking for the meeting link."

    if intent == "get_time":
        return None, f"It's {datetime.datetime.now().strftime('%I:%M %p')}."

    if intent == "get_date":
        return None, datetime.datetime.now().strftime("%A, %d %B %Y.")

    if intent == "web_search":
        return {"action": "web_search", "query": params.get("query", "")}, ""

    if intent == "weather":
        city = params.get("city", "") or entities.get("location", "") or "your city"
        return {"action": "weather", "city": city}, ""

    if intent == "describe_screen":
        return {"action": "screenshot_describe"}, "Looking at your screen."

    if intent == "system_status":
        mode = params.get("mode", "full")
        return {"action": "system_status", "mode": mode}, ""

    if intent == "set_reminder":
        message = params.get("message", "something")
        seconds = _resolve_reminder_seconds(entities, intent_data.get("raw_text", ""))
        fire_at = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        if seconds < 3600:
            time_label = f"in {seconds // 60} minute{'s' if seconds // 60 != 1 else ''}"
        else:
            time_label = f"at {fire_at.strftime('%I:%M %p')}"
        return {
            "action":   "set_reminder",
            "message":  message,
            "seconds":  seconds,
            "entities": entities,
        }, f"Reminder set {time_label} — {message}."

    # ── Layer 1: Obligation intents ───────────────────────────────────────────

    if intent == "add_obligation":
        title    = params.get("title", "").strip()
        ob_type  = params.get("type", "other")
        due_date = params.get("due_date", "") or entities.get("datetime", "")
        source   = params.get("source", "voice")
        if not title:
            return None, "What should I call this obligation?"
        due_label = f", due {due_date}" if due_date else " with no due date set"
        return {
            "action":   "add_obligation",
            "title":    title,
            "type":     ob_type,
            "due_date": due_date,
            "source":   source,
            "notes":    "",
        }, f"Got it — logging {title}{due_label}."

    if intent == "query_obligations":
        mode = params.get("mode", "pending")
        return {
            "action": "query_obligations",
            "mode":   mode,
        }, ""

    if intent == "portal_scan":
        return {
            "action": "portal_scan",
        }, "Scanning your screen for deadlines."

    if intent == "mark_obligation_done":
        title_hint = params.get("title_hint", "").strip()
        if not title_hint:
            return None, "Which obligation did you finish?"
        return {
            "action":     "mark_obligation_done",
            "title_hint": title_hint,
        }, f"Marking '{title_hint}' as done."

    # ─────────────────────────────────────────────────────────────────────────

    return None


# ── Main entry point ───────────────────────────────────────────────────────────

def ask(user_input: str, image_b64=None, extra_context: str = "") -> tuple:
    """
    Decide what to do with user_input.
    Returns (action_dict | None, spoken_response_str, model_type_str).
    """
    log_event("planner_input", user_input)
    set_last_intent(user_input or "")
    clear_pending_followup()

    model_type = "chat"

    sentiment = core.analyze_sentiment(user_input or "")
    conversation_manager.update_sentiment(sentiment)
    sentiment_state = conversation_manager.get_sentiment()

    tone_prefix = core.get_tone_prefix(sentiment_state.get("label", "neutral"))

    if is_capability_question(user_input):
        return None, get_capability_response(), "fast"

    if image_b64:
        return None, "Vision is disabled for now.", "fast"

    lowered_input = (user_input or "").lower().strip()

    if lowered_input in ("exit", "quit", "close"):
        return None, "Shutting down.", "fast"

    if "video" in lowered_input and any(w in lowered_input for w in ("create", "generate", "make")):
        return {"action": "generate_video", "prompt": user_input}, "Generating video.", "action"

    if any(w in lowered_input for w in ("image", "picture")) and any(w in lowered_input for w in ("create", "generate", "make", "draw")):
        return {"action": "generate_image", "prompt": user_input}, "Generating image.", "action"

    if "creator" in lowered_input or "who made you" in lowered_input:
        return None, "I was created as a personal AI assistant project.", "chat"

    if lowered_input in ("hello", "hi"):
        return None, "Hello.", "fast"

    try:
        skill_name, skill_response = plugin_loader.route_to_skill(user_input)
        if skill_name and skill_response:
            log_event("planner_decision", {"skill": skill_name, "response": skill_response})
            return None, skill_response, "action"

        intent_data  = core.classify_intent(user_input)
        local_result = _handle_local_intent(intent_data)

        if local_result is not None:
            action, response = local_result
            action = _normalize_action(action) if action else None
            core.update_context(last_action=(action or {}).get("action", ""))

            if tone_prefix and response:
                response = f"{tone_prefix} {response}"

            log_event("planner_decision", {"action": action, "response": response})
            return action, response, "action"

        model_type = _classify_model_type(user_input)

        # Plain info queries should not use action JSON mode.
        allow_actions = not _looks_like_info_query(user_input)
        if not allow_actions and model_type == "fast":
            model_type = "chat"

        context_hint = get_context_hint()
        facts        = format_facts_for_prompt()
        mem_summary  = get_memory_summary()
        conversation_context = conversation_manager.get_context_block()

        system_context = (
            f"Known context: {context_hint}\n{facts}\nMemory: {mem_summary or 'none'}\n\n"
            f"{conversation_context}"
            + (f"\n{extra_context}" if extra_context else "")
        )

        add_to_history("user", user_input)
        conversation_manager.add_turn("user", user_input)

        raw = brain.ask_llm(
            user_input,
            context=system_context,
            history=_history[:-1],
            model_type=model_type,
            sentiment=sentiment_state,
            allow_actions=allow_actions,
        )
        add_to_history("assistant", raw)
        conversation_manager.add_turn("assistant", raw)
        update_daily_stats("brain_calls")

        action, response = _parse_brain_response(raw)
        action = _normalize_action(action)

        if action and should_avoid(action.get("action", "")):
            print(f"[DEBUG][planner] avoiding unstable action: {action}")
            action   = None
            response = "That hasn't been reliable lately."

        if tone_prefix and response:
            response = f"{tone_prefix} {response}"

        if action and action.get("action") == "join_meeting" and response and "want me to join" in response.lower():
            set_pending_followup({"action": action, "response": response})
            log_event("planner_decision", {"action": None, "response": response})
            return None, response, model_type

        log_event("planner_decision", {"action": action, "response": response})
        return action, response, model_type

    except Exception as exc:
        print(f"[DEBUG][planner] ask error: {exc}")
        return None, "Something went wrong.", "fast"