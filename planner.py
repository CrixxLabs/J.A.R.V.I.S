# planner.py — Decision Router
# =======================================================================
# Decides: handle locally OR route to brain (LLM).
# Evaluates execution authorization using central self_model catalog checks.
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

# ── Reliability imports ───────────────────────────────────────────────────────
import status_registry
import runtime_visuals
import personality
from status_registry import SubsystemState
import self_model

load_dotenv()

# ── Load skills once at module import time ─────────────────────────────────────
plugin_loader.load_skills()

_history: list = []
_pending_followup: dict | None = None

DISABLED_ACTIONS = set()

CAPABILITY_QUESTIONS = frozenset((
    "what can you do", "what are your abilities", "what are your features",
    "what do you support", "what are your capabilities",
    "tell me what you can do", "what can jarvis do",
    "what's broken", "what is broken", "what's offline", "what is offline"
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
    "assignment", "exam", "deadline", "obligation", "due", "overdue",
    "pending", "portal", "scan", "submitted", "worksheet", "coursera",
    "install", "download", "set up", "log me into", "sign into",
    "save my login", "forget my login", "list logins",
)
_FAST_MAX_WORDS = 5

_INFO_QUERY_STARTERS = (
    "who is", "what is", "what are", "where is", "when is",
    "why is", "why does", "how is", "how does", "explain",
    "tell me about", "define", "describe", "who was", "what was",
)

# ── Self-awareness trigger phrases ────────────────────────────────────────────
_SELF_AWARE_TRIGGERS = (
    "your code", "how are you made", "how were you made",
    "aware of yourself", "access to your code", "your files",
    "how do you work", "what files", "your architecture",
    "sandboxing", "patching your", "your own code",
    "how are you built", "what makes you run", "your limitations",
    "what are you made of", "how are you running",
    "aware of how you", "aware of your", "do you know your",
    "do you have access", "your source", "your modules",
    "what are you made", "who built you", "how were you built",
    "do you know what you", "do you know how you",
    "know about yourself", "yourself or do you",
    "files you are made", "you are made of",
    "what normally happens", "last time you tried", "recent failures",
    "why can't you", "why can't you use", "are you able to see",
)

# ── Action-leak patterns ───────────────────────────────────────────────────────
_ACTION_LEAK_PATTERNS = (
    re.compile(r"^action_use_[a-z_]+$", re.IGNORECASE),
    re.compile(r"^\{?['\"]?action['\"]?:\s*['\"][a-z_]+['\"]", re.IGNORECASE),
    re.compile(r"^[a-z_]+_action$", re.IGNORECASE),
)


def _validate_and_route_action(action: dict | None, response: str) -> tuple[dict | None, str]:
    """
    Dynamic dependency verification using central self_model capability rules.
    If offfline or disabled, retrieves structured capability fallback redirects.
    """
    if not action:
        return action, response

    action_name = action.get("action")
    if not action_name:
        return action, response

    model = self_model.get_model()
    can_execute, reason = model.can_do(action_name)

    if not can_execute:
        # Pull definition to check for fallbacks
        cap_id = model.get_capability_by_action(action_name)
        cap = model.get_capability(cap_id) if cap_id else None

        if cap and cap.get("fallbacks"):
            for fallback_id in cap["fallbacks"]:
                fallback_cap = model.get_capability(fallback_id)
                if fallback_cap and fallback_cap["evidence"] in ("PROBED", "LIVE"):
                    if fallback_cap["actions"]:
                        new_action = dict(action)
                        new_action["action"] = fallback_cap["actions"][0]
                        warning = f"My {cap['name']} engine is offline. Let me try using my {fallback_cap['name']} engine instead."
                        new_response = f"{warning} {response}" if response else warning
                        print(f"[Planner][SelfModel] Routed action '{action_name}' -> '{new_action['action']}' due to dependency: {reason}")
                        return new_action, new_response

        print(f"[Planner][SelfModel] Blocked '{action_name}': {reason}")
        msg = f"I can't perform that action right now. {reason}."
        return None, msg

    return action, response


def replan_action(failed_action: dict, failure_reason: str, original_input: str = "") -> tuple[dict | None, str]:
    """
    Called by jarvis.py when observer verification confirms an action failed.
    """
    if not isinstance(failed_action, dict):
        return None, "That action couldn't be verified."

    act = failed_action.get("action", "")
    print(f"[Planner][Replan] failed action='{act}' reason='{failure_reason}'")

    if act == "open_app":
        app = (failed_action.get("app", "") or "").strip()
        if app:
            return {"action": "install_app", "app": app}, f"I couldn't open {app}. Want me to install it?"

    if act == "screenshot_describe":
        return {"action": "read_screen"}, "Vision failed. I'll try OCR instead."

    if act == "whatsapp_read":
        return None, "WhatsApp didn't look reachable. Try opening WhatsApp desktop first."

    if act in ("generate_video", "generate_image"):
        return None, f"That generation failed: {failure_reason}"

    if original_input:
        try:
            replan_prompt = (
                f"The user wanted: '{original_input}'. "
                f"We attempted action '{act}' but it failed verification: '{failure_reason}'. "
                "Suggest a fallback in 1 concise sentence."
            )
            raw = brain.ask_llm(replan_prompt, model_type="chat", allow_actions=False)
            if raw and raw.strip():
                return None, raw.strip()
        except Exception:
            pass

    return None, f"That didn't work: {failure_reason}"


def _classify_model_type(text: str) -> str:
    lowered = (text or "").lower().strip()
    words   = lowered.split()
    if len(words) <= _FAST_MAX_WORDS and not any(k in lowered for k in _REASONING_KEYWORDS):
        return "fast"
    if any(k in lowered for k in _REASONING_KEYWORDS):
        return "chat"
    if any(k in lowered for k in _ACTION_KEYWORDS):
        return "action"
    return "chat"


def _looks_like_info_query(text: str) -> bool:
    lowered = (text or "").lower().strip()
    if not lowered:
        return False
    if any(lowered.startswith(s) for s in _INFO_QUERY_STARTERS):
        return True
    if len(lowered.split()) <= 4 and not any(k in lowered for k in _ACTION_KEYWORDS):
        return True
    return False


def _is_self_aware_query(text: str) -> bool:
    """Detect if the user is asking about Jarvis itself or its capabilities."""
    lowered = (text or "").lower().strip()
    return any(trigger in lowered for trigger in _SELF_AWARE_TRIGGERS) or is_capability_question(text)


def add_to_history(role: str, content: str):
    _history.append({"role": role, "content": content})
    if len(_history) > 10:
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


def _creative_capability_response() -> str:
    """Report Creative Studio from canonical runtime evidence, never from a hardcoded claim."""
    try:
        registry = status_registry.get_registry()
        image = registry.get_capability("IMAGE_GENERATION") or {}
        video = registry.get_capability("VIDEO_GENERATION") or {}
        image_ev = image.get("evidence", "UNKNOWN")
        video_ev = video.get("evidence", "UNKNOWN")
        image_state = {"LIVE": "live and verified", "PROBED": "probed", "CONFIGURED": "configured",
                       "CODE": "implemented but not live-verified", "BROKEN": "currently failing",
                       "BLOCKED": "currently blocked", "DISABLED": "disabled"}.get(image_ev, "not currently verified")
        video_state = {"LIVE": "live and verified", "PROBED": "probed", "CONFIGURED": "configured",
                       "CODE": "implemented but not live-verified", "BROKEN": "currently failing",
                       "BLOCKED": "currently blocked", "DISABLED": "disabled"}.get(video_ev, "not currently verified")
        return (f"Image generation is {image_state}. Image-to-video is {video_state}. "
                "Text-to-video and arbitrary image editing are not enabled. "
                "I only claim a generation result after the runtime actually produces the file.")
    except Exception as exc:
        print(f"[Planner] creative capability status unavailable: {type(exc).__name__}")
        return ("I can generate images through Creative Studio, but I won't claim live availability "
                "until the runtime verifies it. Image-to-video is not yet live-verified.")

def get_capability_response() -> str:
    """Generate dynamic list of actually operational and available capabilities."""
    model = self_model.get_model()
    available = [c["name"] for c in model.get_available_capabilities()]
    unavailable = [c["name"] for c in model.get_unavailable_capabilities()]
    response = "I am operational. Here is what I can do right now: " + ", ".join(available[:8]) + "."
    if unavailable:
        response += " Currently offline or unavailable features: " + ", ".join(unavailable[:4]) + "."
    response += " " + _creative_capability_response()
    return response


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


def _is_leaked_action_string(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    for pattern in _ACTION_LEAK_PATTERNS:
        if pattern.match(stripped):
            return True
    if "_" in stripped and " " not in stripped and len(stripped.split("_")) >= 2:
        if stripped.lower().startswith(("action_", "cmd_", "task_")):
            return True
    return False


def _parse_brain_response(raw: str) -> tuple:
    if not raw:
        return None, ""

    from speech_cleaner import extract_conversational_payload
    action, spoken = extract_conversational_payload(raw)
    action = _normalize_brain_action_shape(action)

    if _is_leaked_action_string(spoken):
        print(f"[DEBUG][planner] filtered leaked action string: {spoken}")
        spoken = ""

    return action, spoken


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
            if unit == "second": return amount
            if unit == "minute": return amount * 60
            if unit == "hour":   return amount * 3600

        after_match = re.search(
            r"\bafter\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)\b",
            lowered,
        )
        if after_match:
            amount = int(after_match.group(1))
            unit   = after_match.group(2).rstrip("s")
            if unit == "second": return amount
            if unit == "minute": return amount * 60
            if unit == "hour":   return amount * 3600

    if datetime_str:
        try:
            now = datetime.datetime.now()
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



def _is_conversational_nonfact(text: str) -> bool:
    raw = (text or "").strip()
    low = raw.lower()
    if not low or raw.endswith("?"):
        return True
    prefixes = (
        "i'm asking ", "im asking ", "i am asking ",
        "i'm talking about ", "im talking about ", "i am talking about ",
        "i mean ", "what i mean ", "i meant ",
        "my question is ", "the question is ",
        "in your opinion", "what do you think", "do you think",
        "would you ", "could you ", "can you ", "should you ",
        "why ", "how ", "what ", "when ", "where ", "who ",
    )
    return low.startswith(prefixes)


def _is_explicit_memory_write(text: str) -> bool:
    raw = (text or "").strip()
    low = raw.lower()
    if not raw or _is_conversational_nonfact(raw):
        return False
    if re.match(r"^(?:please\s+)?(?:always\s+)?remember\s+(?:that\s+)?(?:i|my)\b", low):
        return True
    return bool(re.match(r"^my\s+.{1,80}?\s+is\s+.+", low))


def _is_direct_generation_request(text: str, media: str) -> bool:
    raw = (text or "").strip()
    low = raw.lower()
    if not raw or _is_conversational_nonfact(raw):
        return False
    noun = r"(?:video|clip)" if media == "video" else r"(?:image|picture|photo)"
    command = rf"^(?:please\s+)?(?:create|generate|make|draw)\s+(?:me\s+)?(?:an?\s+|the\s+)?{noun}\b"
    if re.search(command, low):
        return True

    # Voice follow-up: Whisper can drop the leading verb after JARVIS just
    # discussed generation, e.g. "an image of a black and gold arc reactor".
    # Accept only creation-shaped fragments, not questions/opinions.
    if media == "image" and re.match(r"^(?:an?\s+)?(?:image|picture|photo)\s+of\s+\S+", low):
        return True
    if media == "video" and re.match(r"^(?:a\s+)?(?:video|clip)\s+of\s+\S+", low):
        return True
    return False


def _deterministic_profile_intent(text: str) -> tuple[dict | None, str] | None:
    """Protect profile memory from LLM/classifier mistakes on obvious facts/questions."""
    raw = (text or "").strip()
    low = raw.lower().strip()
    # Broad profile questions must read the real approved profile, never let the
    # conversational LLM invent what is or is not stored.
    broad_profile_patterns = (
        r"^(?:so\s+)?what(?:\s+all)?(?:\s+things)?\s+do\s+you\s+know\s+about\s+me[?.!]*$",
        r"^(?:so\s+)?tell\s+me\s+what(?:\s+all)?\s+you\s+know\s+about\s+me[?.!]*$",
        r"^(?:so\s+)?what(?:\s+all)?\s+(?:do\s+you\s+)?(?:remember|have\s+saved|know)\s+about\s+me[?.!]*$",
        r"^(?:so\s+)?(?:show|read|give)\s+(?:me\s+)?my\s+(?:saved\s+)?profile[?.!]*$",
    )
    if any(re.match(pattern, low) for pattern in broad_profile_patterns):
        return {"action": "profile_query"}, ""

    # Questions about "my X" are reads, never writes.
    q = re.match(r"^(?:so\s+)?(?:what|which)\s+(?:is|was)\s+my\s+(.+?)[?.!]*$", low)
    if q:
        fact = re.sub(r"[?.!]+$", "", q.group(1)).strip()
        return {"action": "fact_query", "fact_name": fact}, ""
    q = re.match(r"^(?:so\s+)?is\s+my\s+(.+?)\s+(.+?)[?.!]*$", low)
    if q and raw.endswith("?"):
        return {"action": "fact_query", "fact_name": q.group(1).strip()}, ""
    # Permanent writes require deterministic, explicit user intent.
    if _is_explicit_memory_write(raw):
        return {"action": "declarative_fact", "statement": raw}, ""
    return None


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
        raw_text = (intent_data.get("raw_text") or "").strip()
        if re.match(r"^(?:(?:hey\s+)?jarvis\s*,?\s*|please\s+|can\s+you\s+)?(?:close|kill|terminate|exit|quit)\b", raw_text, re.IGNORECASE):
            return None, "Which app should I close?"
        return None

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

    if intent == "add_obligation":
        title = params.get("title", "").strip()
        ob_type = params.get("type", "other")
        due_date = params.get("due_date", "") or entities.get("datetime", "")
        source = params.get("source", "voice")
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
        return {"action": "query_obligations", "mode": mode}, ""

    if intent == "portal_scan":
        return {"action": "portal_scan"}, "Scanning your screen for deadlines."

    if intent == "mark_obligation_done":
        title_hint = params.get("title_hint", "").strip()
        if not title_hint:
            return None, "Which obligation did you finish?"
        return {
            "action":     "mark_obligation_done",
            "title_hint": title_hint,
        }, f"Marking '{title_hint}' as done."

    if intent == "profile_query":
        return {"action": "profile_query"}, ""

    if intent == "profile_forget":
        hint = params.get("hint", "").strip()
        return {"action": "profile_forget", "hint": hint}, ""

    if intent == "profile_remember":
        statement = params.get("statement", "").strip()
        if not statement:
            return None, "What should I remember?"
        return {"action": "profile_remember", "statement": statement}, ""

    if intent == "declarative_fact":
        statement = params.get("statement", "").strip()
        if not statement:
            return None, "What should I remember?"
        return {"action": "declarative_fact", "statement": statement}, ""

    if intent == "fact_query":
        fact_name = params.get("fact_name", "").strip()
        if not fact_name:
            return None, "What fact would you like to know?"
        return {"action": "fact_query", "fact_name": fact_name}, ""

    if intent == "install_app":
        app = params.get("app", "").strip()
        if not app:
            return None, "Which app should I install?"
        return {"action": "install_app", "app": app}, ""

    if intent == "install_and_login":
        app = params.get("app", "").strip()
        if not app:
            return None, "Which app should I install and log you into?"
        return {"action": "install_and_login", "app": app}, ""

    if intent == "open_and_login":
        app = params.get("app", "").strip()
        if not app:
            return None, "Which app should I log you into?"
        return {"action": "open_and_login", "app": app}, ""

    if intent == "save_login":
        app = params.get("app", "").strip()
        return {
            "action": "save_login",
            "app": app,
            "username": "",
            "password": "",
            "needs_dialog": True,
        }, ""

    if intent == "list_logins":
        return {"action": "list_logins"}, ""

    if intent == "delete_login":
        app = params.get("app", "").strip()
        if not app:
            return None, "Which app's login should I forget?"
        return {"action": "delete_login", "app": app}, ""

    if intent == "self_scan":
        return {"action": "self_scan"}, ""

    if intent == "self_capabilities":
        return {"action": "self_capabilities"}, ""

    if intent == "self_changes":
        return {"action": "self_changes"}, ""

    return None


def _trim_context_for_tokens(system_context: str, max_chars: int = 3500) -> str:
    if not system_context or len(system_context) <= max_chars:
        return system_context
    keep_start = max_chars // 2
    keep_end   = max_chars // 2
    trimmed    = system_context[:keep_start] + "\n...[trimmed]...\n" + system_context[-keep_end:]
    print(f"[DEBUG][planner] context trimmed from {len(system_context)} -> {len(trimmed)} chars")
    return trimmed


def _last_assistant_response() -> str:
    for turn in reversed(_history):
        if turn.get("role") == "assistant":
            return str(turn.get("content", ""))
    return ""


def _personality_context(user_input: str, planner_intent: str = "") -> str:
    """Build the cloud-safe contract once; every provider fallback reuses it."""
    try:
        return personality.assemble_personality_context(
            user_input,
            runtime_state=get_state_snapshot(),
            planner_intent=planner_intent,
            provider="cloud",
        )
    except Exception as exc:
        print(f"[DEBUG][planner] personality context unavailable: {type(exc).__name__}")
        return ""


@runtime_visuals.visual_activity("thinking")
def ask(user_input: str, image_b64=None, extra_context: str = "") -> tuple:
    """
    Decide what to do with user_input.
    Returns (action_dict | None, spoken_response_str, model_type_str).
    """
    log_event("planner_input", user_input)
    set_last_intent(user_input or "")
    clear_pending_followup()

    model_type = "chat"
    lowered_input = (user_input or "").lower().strip()

    # Tiny conversational acknowledgements must never reach the action-capable
    # LLM path. They carry no actionable intent and previously could cause the
    # model to invent an action/file/open operation from stale context.
    _FILLER_RESPONSES = {
        "oh": "Yeah.", "oh okay": "Yeah.", "okay": "Okay.", "ok": "Okay.",
        "alright": "Alright.", "all right": "Alright.", "hmm": "Mm-hm.",
        "hmmm": "Mm-hm.", "yeah": "Yeah.", "yep": "Yep.", "yup": "Yep.",
        "thanks": "You're welcome.", "thank you": "You're welcome.",
    }
    if lowered_input in _FILLER_RESPONSES:
        return None, _FILLER_RESPONSES[lowered_input], "fast"

    # Constitutional & Dissent Pre-Flight (Module AE / R)
    from identity_kernel import check_constitutional_dissent
    dissent = check_constitutional_dissent(user_input)
    if dissent:
        log_event("planner_decision", {"action": None, "response": dissent, "route": "constitutional_dissent"})
        return None, dissent, "fast"

    # Working Memory & Session Re-entry (Module AU)
    _REENTRY_PHRASES = (
        "where did we leave off",
        "where did we leave",
        "where were we",
        "what was our focus",
        "what was our last focus",
        "what were we doing",
        "what were we working on",
        "resume session",
        "session status",
        "reentry brief",
        "re-entry brief",
        "what did we do last",
        "active goals",
        "active constraints",
        "active architectural decisions",
        "active architecture",
        "development roadmap",
        "system invariants",
        "system invariant",
        "briefing on our system invariants",
        "working memory",
        "recorded in working memory",
        "layout of the 4-tier working memory",
        "memory layout of the 4-tier",
        "memory layout of the",
        "where did we leave off with the codebase",
    )
    if any(phrase in lowered_input for phrase in _REENTRY_PHRASES):
        from working_memory_pager import generate_conversational_reentry_brief
        reentry_msg = generate_conversational_reentry_brief()
        log_event("planner_decision", {"action": None, "response": reentry_msg, "route": "working_memory_pager"})
        return None, reentry_msg, "fast"

    # Creative capability questions must be answered from runtime evidence,
    # before the general self-model/LLM path gets a chance to improvise.
    creative_capability_question = (
        ("image" in lowered_input or "picture" in lowered_input or "photo" in lowered_input or "video" in lowered_input)
        and any(v in lowered_input for v in ("can you", "are you able", "what can", "what kind", "which", "do you", "are you capable"))
        and any(v in lowered_input for v in ("generate", "create", "make", "produce", "edit", "animate"))
    )
    if creative_capability_question:
        return None, _creative_capability_response(), "fast"

    sentiment       = core.analyze_sentiment(user_input or "")
    conversation_manager.update_sentiment(sentiment)
    sentiment_state = conversation_manager.get_sentiment()
    tone_prefix     = core.get_tone_prefix(sentiment_state.get("label", "neutral"))

    truthful_answer = self_model.get_model().answer_capability_question(user_input)
    if truthful_answer:
        return None, truthful_answer, "fast"
    if is_capability_question(user_input):
        return None, get_capability_response(), "fast"

    if image_b64:
        try:
            import vision
            result = vision.analyze_image_base64(
                image_b64,
                user_input or "Describe this image in detail",
            )
            return None, result, "action"
        except Exception as exc:
            print(f"[Planner] image_b64 processing failed: {exc}")
            return None, "Could not process the image.", "fast"

    # Creative Studio agent controls. Keep capability questions conversational and
    # only execute explicit creation/show commands.
    creative_terms = ("image generation", "generate images", "generate image", "make images",
                      "video generation", "generate videos", "generate video", "creative studio")
    capability_prefixes = ("can you", "are you able", "do you", "what can you", "what are you able")
    if any(term in lowered_input for term in creative_terms) and (
            lowered_input.endswith("?") or lowered_input.startswith(capability_prefixes)):
        return None, (
            "Yes. My NVIDIA Creative Studio image generation is live and verified. "
            "I can generate images in the background while we keep talking. I can also turn an image "
            "into a video through the NVIDIA provider, but that video path still needs its live verification. "
            "Text-to-video and arbitrary image editing are not enabled yet, so I won't pretend they are."
        ), "fast"

    if any(x in lowered_input for x in ("show me the generated", "open the generated", "open that image",
                                         "open the image", "show me the image", "show me that", "open it")):
        return {"action": "open_latest_generated"}, "Opening it.", "action"

    if any(x in lowered_input for x in ("open generated folder", "open the generated folder",
                                         "where did you save it", "where is the generated")):
        return {"action": "open_generated_folder"}, "Opening the generated media folder.", "action"

    if any(x in lowered_input for x in (
            "is it done", "did it finish", "has it finished", "is it finished",
            "has the image finished", "has the video finished", "is the image finished", "is the video finished",
            "generation status", "what's the generation status", "what is the generation status",
            "are you done generating", "are you still generating", "still generating",
            "any update on the generation", "any update on the image", "any update on the video",
            "the image ready", "is the image ready", "is the image done", "image ready",
            "is the video ready", "is the video done", "video ready", "image already",
            "did you finish the image", "did you finish the video",
            "how's the image", "hows the image", "how is the image",
            "how's the video", "hows the video", "how is the video")):
        return {"action": "creative_status"}, "", "action"

    if any(x in lowered_input for x in ("cancel that generation", "cancel the generation", "stop generating",
                                         "cancel image generation", "cancel video generation")):
        return {"action": "cancel_generation"}, "I'll abandon the active generation job.", "action"

    if any(x in lowered_input for x in ("animate the image", "animate that image", "turn that image into a video",
                                         "turn the image into a video", "make that image a video")):
        return {"action": "animate_latest_image", "auto_open": ("show me" in lowered_input or "open" in lowered_input)},                "I'll animate the latest image.", "action"

    try:
        correction = personality.capture_explicit_feedback(
            user_input,
            context="direct conversation feedback",
            original_response=_last_assistant_response(),
        )
    except Exception as exc:
        print(f"[DEBUG][planner] correction capture unavailable: {type(exc).__name__}")
        correction = None
    if correction:
        acknowledgement = "Got it. I'll keep that preference active."
        add_to_history("user", user_input)
        add_to_history("assistant", acknowledgement)
        conversation_manager.add_turn("user", user_input)
        conversation_manager.add_turn("assistant", acknowledgement)
        return None, acknowledgement, "fast"

    if lowered_input in ("exit", "quit", "close"):
        return None, "Shutting down.", "fast"

    if _is_direct_generation_request(user_input, "video"):
        action = {"action": "generate_video", "prompt": user_input}
        action, msg = _validate_and_route_action(action, "Generating video.")
        return action, msg, "action"

    if _is_direct_generation_request(user_input, "image"):
        action = {"action": "generate_image", "prompt": user_input, "auto_open": any(x in lowered_input for x in ("and show me", "then show me", "and open it", "then open it"))}
        action, msg = _validate_and_route_action(action, "Generating image.")
        return action, msg, "action"

    if "creator" in lowered_input or "who made you" in lowered_input:
        return None, "I was built by Sonu as a personal AI assistant running locally on your machine.", "chat"

    if lowered_input in ("hello", "hi"):
        return None, "Hello.", "fast"

    # ── Self-awareness / Capability query path ────────────────────────────────
    if _is_self_aware_query(user_input):
        print(f"[DEBUG][planner] capability/self-aware query, routing with self_model dynamic context")

        try:
            import self_awareness
            self_context_str = self_awareness.get_self_context_for_prompt(compact=True)
        except Exception:
            self_context_str = ""

        self_context = _trim_context_for_tokens(
            f"Known context: {get_context_hint()}\n{format_facts_for_prompt()}\n\n{self_context_str}",
            max_chars=1800,
        )
        add_to_history("user", user_input)
        conversation_manager.add_turn("user", user_input)

        brain.reset_last_provider()
        raw = brain.ask_llm(
            user_input,
            context       = self_context,
            history       = _history[:-1][-4:],
            model_type    = "chat",
            sentiment     = sentiment_state,
            allow_actions = False,
            profile_context = _personality_context(user_input, "self_awareness"),
        )
        add_to_history("assistant", raw)
        conversation_manager.add_turn("assistant", raw)
        update_daily_stats("brain_calls")

        response = (raw or "").strip()
        if not response or response == "I couldn't figure that out.":
            response = get_capability_response()

        if tone_prefix and response:
            response = f"{tone_prefix} {response}"

        log_event("planner_decision", {"action": None, "response": response})
        return None, response, brain.get_last_provider_model() or "chat"
    # ─────────────────────────────────────────────────────────────────────────

    try:
        skill_name, skill_response = plugin_loader.route_to_skill(user_input)
        if skill_name and skill_response:
            log_event("planner_decision", {"skill": skill_name, "response": skill_response})
            return None, skill_response, "action"

        profile_guard = _deterministic_profile_intent(user_input)
        if profile_guard is not None:
            action, response = profile_guard
            action = _normalize_action(action)
            action, response = _validate_and_route_action(action, response)
            log_event("planner_decision", {"action": action, "response": response, "route": "profile_guard"})
            return action, response, "action"

        intent_data = core.classify_intent(user_input)

        # Classifier output is advisory; it cannot authorize permanent memory.
        if intent_data.get("intent") in ("declarative_fact", "profile_remember"):
            if not _is_explicit_memory_write(user_input):
                intent_data = dict(intent_data)
                intent_data["intent"] = "conversation"
                intent_data["params"] = {}

        local_result = _handle_local_intent(intent_data)

        if local_result is not None:
            action, response = local_result
            action = _normalize_action(action) if action else None
            core.update_context(last_action=(action or {}).get("action", ""))

            if tone_prefix and response:
                response = f"{tone_prefix} {response}"

            # Validate planned actions and fallbacks using structured self_model rules
            action, response = _validate_and_route_action(action, response)

            log_event("planner_decision", {"action": action, "response": response})
            return action, response, "action"

        model_type    = _classify_model_type(user_input)
        allow_actions = not _looks_like_info_query(user_input)
        if not allow_actions and model_type == "fast":
            model_type = "chat"

        context_hint         = get_context_hint()
        facts                = format_facts_for_prompt()
        mem_summary          = get_memory_summary()
        conversation_context = conversation_manager.get_context_block()

        # Build prompt context, incorporating self_model dynamic states
        try:
            import self_awareness
            self_model_str = self_awareness.get_self_context_for_prompt(compact=True)
        except Exception:
            self_model_str = ""

        try:
            import creative_agent
            creative_truth = creative_agent.latest_status()
            creative_context = (
                "Creative Studio truth: never invent generated files, screenshots, videos, or opening actions. "
                f"Current job state={creative_truth.get('active')}; latest real asset={creative_truth.get('latest_asset') or 'none'}. "
                "Only claim an asset exists when this runtime state contains a real path."
            )
        except Exception:
            creative_context = (
                "Creative Studio truth: never claim to have generated, saved, or opened an asset unless an actual "
                "runtime action produced it. Do not invent file paths."
            )

        try:
            from working_memory_pager import get_working_memory_pager
            wm_brief = get_working_memory_pager().synthesize_reentry_brief()
            if wm_brief and (wm_brief.active_goals or wm_brief.active_constraints):
                wm_context = f"Working Memory State:\n- Active Goals: {', '.join(wm_brief.active_goals[:2]) or 'None'}\n- Active Constraints: {', '.join(wm_brief.active_constraints[:2]) or 'None'}"
            else:
                wm_context = ""
        except Exception:
            wm_context = ""

        system_context = (
            f"Known context: {context_hint}\n{facts}\nMemory: {mem_summary or 'none'}\n"
            f"{creative_context}\n"
            f"Self Capability State: {self_model_str}\n\n"
            + (f"{wm_context}\n\n" if wm_context else "")
            + f"{conversation_context}"
            + (f"\n{extra_context}" if extra_context else "")
        )

        system_context = _trim_context_for_tokens(system_context, max_chars=1800)

        add_to_history("user", user_input)
        conversation_manager.add_turn("user", user_input)

        brain.reset_last_provider()
        raw = brain.ask_llm(
            user_input,
            context       = system_context,
            history       = _history[:-1][-3:],
            model_type    = model_type,
            sentiment     = sentiment_state,
            allow_actions = allow_actions,
            profile_context = _personality_context(user_input, intent_data.get("intent", model_type)),
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

        if not response and not action:
            response = "I'm not sure how to answer that."

        if tone_prefix and response:
            response = f"{tone_prefix} {response}"

        # Validate planned actions and fallbacks using structured self_model rules
        action, response = _validate_and_route_action(action, response)

        if action and action.get("action") == "join_meeting" and response and "want me to join" in response.lower():
            set_pending_followup({"action": action, "response": response})
            log_event("planner_decision", {"action": None, "response": response})
            return None, response, brain.get_last_provider_model() or model_type

        log_event("planner_decision", {"action": action, "response": response})
        return action, response, brain.get_last_provider_model() or model_type

    except Exception as exc:
        print(f"[DEBUG][planner] ask error: {exc}")
        return None, "Something went wrong.", "fast"
