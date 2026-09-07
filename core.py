# core.py — Lightweight Context & Intent Helper
# Responsibilities: context tracking, intent classification, reference resolution,
#                   WhatsApp text parsing utilities, sentiment analysis,
#                   Malayalam translation preprocessing (Module 4).
# NOT responsible for: decisions, LLM calls, speaking.

import datetime
import re

# ── spaCy NLP layer (loaded once, optional — fails gracefully if not installed) ─
_nlp = None
_NLP_READY = False

try:
    import spacy
    _nlp = spacy.load("en_core_web_sm")
    _NLP_READY = True
    print("[core] spaCy NLP loaded (en_core_web_sm).")
except Exception as _spacy_err:
    print(f"[core] spaCy not available — keyword-only mode. ({_spacy_err})")

# ── VADER sentiment analyzer (loaded once, optional) ──────────────────────────
_vader = None
_VADER_READY = False

try:
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
    _VADER_READY = True
    print("[core] VADER sentiment analyzer loaded.")
except Exception as _vader_err:
    print(f"[core] VADER not available — no sentiment analysis. ({_vader_err})")

# ── deep-translator (Module 4 — Malayalam support) ────────────────────────────
_TRANSLATOR_READY = False

try:
    from deep_translator import GoogleTranslator as _GoogleTranslator
    _GoogleTranslator(source="auto", target="en").translate("test")
    _TRANSLATOR_READY = True
    print("[core] deep-translator loaded (Malayalam support active).")
except Exception as _trans_err:
    print(f"[core] deep-translator not available — Malayalam translation disabled. ({_trans_err})")

# ── langdetect (Module 4) ─────────────────────────────────────────────────────
_LANGDETECT_READY = False

try:
    from langdetect import detect as _langdetect
    _LANGDETECT_READY = True
    print("[core] langdetect loaded.")
except Exception as _ld_err:
    print(f"[core] langdetect not available. ({_ld_err})")


# ── Shared context (within one session) ───────────────────────────────────────
_context = {
    "last_app":     "",
    "last_action":  "",
    "last_subject": "",
    "last_link":    "",
    "last_class":   {},
}


def update_context(**kwargs):
    for key, value in kwargs.items():
        if key in _context:
            _context[key] = value


def get_context():
    return dict(_context)


# ── Reference resolution ───────────────────────────────────────────────────────
def resolve_reference(text: str) -> str:
    lowered = (text or "").lower().strip()
    if lowered in {"close it", "close that", "kill it"} and _context["last_app"]:
        return f"close {_context['last_app']}"
    if lowered in {"open it", "open that"} and _context["last_app"]:
        return f"open {_context['last_app']}"
    if lowered in {"yes", "yeah", "yep", "join it", "join that"} and _context["last_link"]:
        return f"join meeting {_context['last_link']}"
    return text or ""


# ── Private extraction helpers ─────────────────────────────────────────────────
def _extract_app_name(text: str, keyword: str) -> str:
    """
    Extract app name after a keyword like 'open' or 'close'.
    Stops at conjunctions like 'and', 'then', 'also' to avoid grabbing
    multi-part commands as one app name.
    """
    pattern = rf"\b(?:{keyword})\b\s+(.+)$"
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return ""
    app = match.group(1).strip()

    # Cut off at conjunctions / follow-up verbs
    cutoff_pattern = re.compile(
        r"\s+(?:and|then|also|after that|,)\s+.*$",
        re.IGNORECASE,
    )
    app = cutoff_pattern.sub("", app).strip()
    verb_cutoff = re.compile(
        r"\s+(?:type|write|search|play|open|close|make|create|do)\s+.*$",
        re.IGNORECASE,
    )
    app = verb_cutoff.sub("", app).strip()

    app = re.sub(r"\b(app|application|please|jarvis)\b", "", app, flags=re.IGNORECASE).strip()
    app = app.rstrip(".,!?;:").strip()
    return app


def _extract_time(text: str) -> str:
    match = re.search(
        r"\b(\d{1,2}[:.]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))\b", text, re.IGNORECASE
    )
    return match.group(1).strip() if match else ""


def _extract_subject(text: str) -> str:
    subject_map = {
        "excel": "Excel", "python": "Python", "java": "Java",
        "ai": "AI", "ml": "ML", "machine learning": "Machine Learning",
        "dbms": "DBMS", "os": "OS", "english": "English", "revision": "Revision",
    }
    lowered = (text or "").lower()
    for keyword, label in subject_map.items():
        if keyword in lowered:
            return label
    match = re.search(
        r"\b([A-Za-z][A-Za-z0-9/& ]{1,30})\s+(class|session|lecture|exam)\b",
        text or "",
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().title()
    return "Class"


def _extract_city(text: str) -> str:
    match = re.search(
        r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z\s]{1,30}?)(?:\s+today|\s+now|\s+tomorrow|$|\?)",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().title()
    return ""


def _detect_system_status_mode(lowered: str) -> str:
    if any(w in lowered for w in ("gpu", "vram", "graphics card", "graphics", "nvidia")):
        return "gpu"
    if "battery" in lowered and not any(w in lowered for w in ("cpu", "ram", "gpu", "system", "full")):
        return "battery"
    if any(w in lowered for w in ("cpu", "ram", "memory usage")) and "system" not in lowered:
        return "cpu"
    return "full"


# ── Malayalam translation (Module 4) ──────────────────────────────────────────

_MALAYALAM_RE = re.compile(r"[\u0D00-\u0D7F]")


def _is_malayalam(text: str) -> bool:
    return bool(_MALAYALAM_RE.search(text or ""))


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "en"
    if _is_malayalam(text):
        return "ml"
    if _LANGDETECT_READY:
        try:
            from langdetect import detect as _ld
            lang = _ld(text.strip())
            return lang or "en"
        except Exception:
            pass
    return "en"


def translate_to_english(text: str) -> str:
    if not text or not text.strip():
        return text or ""
    if not _TRANSLATOR_READY:
        return text
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target="en").translate(text.strip())
        if translated and translated.strip():
            result = translated.strip()
            print(f"[core][ML→EN] '{text}' → '{result}'")
            return result
    except Exception as e:
        print(f"[core] translation failed: {e}")
    return text


def preprocess_malayalam(text: str) -> tuple:
    if not text or not text.strip():
        return text or "", False
    if _is_malayalam(text):
        translated = translate_to_english(text)
        return translated, True
    lang = detect_language(text)
    if lang == "ml":
        translated = translate_to_english(text)
        return translated, True
    return text, False


# ── Sentiment analysis (Module 3) ─────────────────────────────────────────────

_STRESS_PHRASES = (
    "not working", "doesn't work", "broken", "stupid", "annoying",
    "hate this", "frustrated", "useless", "ugh", "dammit", "damn",
    "what the hell", "wtf", "for god's sake", "come on", "seriously",
    "fed up", "sick of", "tired of", "waste of time", "nothing works",
    "this sucks", "fix this", "why won't", "stop it", "shut up",
    "i give up", "forget it", "screw this", "are you deaf",
    "i said", "i already told you", "how many times",
)

_TONE_PREFIXES = {
    "stressed": [
        "Easy there.",
        "Alright, I'm on it.",
        "Got it. Let me handle this.",
        "I hear you.",
        "Right, sorting it out.",
        "On it. No worries.",
    ],
    "negative": [
        "Understood.",
        "I've got this.",
        "Right away.",
        "Let me take care of that.",
        "Say no more.",
    ],
    "positive": [],
    "neutral":  [],
}

_prefix_counters = {"stressed": 0, "negative": 0}


def analyze_sentiment(text: str) -> dict:
    if not _VADER_READY or not _vader or not text:
        return {"label": "neutral", "score": 0.0, "raw": {}}

    lowered = text.lower().strip()

    if any(phrase in lowered for phrase in _STRESS_PHRASES):
        scores = _vader.polarity_scores(text)
        return {"label": "stressed", "score": scores.get("compound", -0.5), "raw": scores}

    scores = _vader.polarity_scores(text)
    compound = scores.get("compound", 0.0)

    if compound <= -0.4:
        label = "negative"
    elif compound <= -0.15:
        label = "negative"
    elif compound >= 0.3:
        label = "positive"
    else:
        label = "neutral"

    return {"label": label, "score": compound, "raw": scores}


def get_tone_prefix(sentiment_label: str) -> str:
    options = _TONE_PREFIXES.get(sentiment_label, [])
    if not options:
        return ""
    idx = _prefix_counters.get(sentiment_label, 0) % len(options)
    _prefix_counters[sentiment_label] = idx + 1
    return options[idx]


# ── spaCy NLP helpers ──────────────────────────────────────────────────────────

_VERB_INTENT_MAP = {
    "pull":      "open_app",
    "boot":      "open_app",
    "load":      "open_app",
    "fire":      "open_app",
    "bring":     "open_app",
    "spin":      "open_app",
    "wake":      "open_app",
    "shut":      "close_app",
    "terminate": "close_app",
    "end":       "close_app",
    "stop":      "close_app",
    "force":     "close_app",
    "look":      "web_search",
    "browse":    "web_search",
    "surf":      "web_search",
    "forecast":  "weather",
    "describe":  "describe_screen",
    "analyze":   "describe_screen",
    "read":      "describe_screen",
    "see":       "describe_screen",
    "tell":      "get_time",
    "check":     "system_status",
    "monitor":   "system_status",
    "report":    "system_status",
}

_SHORT_COMMAND_PATTERNS = [
    (re.compile(r"^boot\s+(.+)$",       re.IGNORECASE), "open_app"),
    (re.compile(r"^load\s+(.+)$",       re.IGNORECASE), "open_app"),
    (re.compile(r"^spin up\s+(.+)$",    re.IGNORECASE), "open_app"),
    (re.compile(r"^wake up\s+(.+)$",    re.IGNORECASE), "open_app"),
    (re.compile(r"^bring up\s+(.+)$",   re.IGNORECASE), "open_app"),
    (re.compile(r"^terminate\s+(.+)$",  re.IGNORECASE), "close_app"),
    (re.compile(r"^end\s+(.+)$",        re.IGNORECASE), "close_app"),
    (re.compile(r"^force quit\s+(.+)$", re.IGNORECASE), "close_app"),
]

_OBJECT_REFINERS = {
    "get_time":        ("time", "clock", "hour", "minute"),
    "web_search":      ("web", "internet", "online", "google", "search"),
    "describe_screen": ("screen", "display", "monitor", "window"),
    "system_status":   ("system", "cpu", "ram", "battery", "gpu", "memory", "performance"),
    "weather":         ("weather", "forecast", "rain", "temperature", "humid"),
}

_AMBIGUOUS_VERBS = {"tell", "check", "read", "see", "look"}

_FILE_EXT_PATTERN = re.compile(
    r"\b([\w\-. ]{1,40}\.(pdf|docx?|xlsx?|pptx?|txt|csv|py|js|ts|json|mp4|mp3|png|jpg|jpeg|zip|exe))\b",
    re.IGNORECASE,
)

_KNOWN_APPS = frozenset((
    "chrome", "firefox", "edge", "safari", "notepad", "calculator", "calc",
    "spotify", "discord", "slack", "teams", "zoom", "whatsapp", "telegram",
    "vscode", "vs code", "visual studio", "pycharm", "sublime", "atom",
    "word", "excel", "powerpoint", "outlook", "onenote", "paint", "vlc",
    "steam", "obs", "photoshop", "illustrator", "premiere", "after effects",
    "cmd", "terminal", "powershell", "explorer", "task manager",
))


def _get_doc(text: str):
    if not _NLP_READY or not _nlp:
        return None
    try:
        return _nlp(text)
    except Exception:
        return None


def _extract_nlp_app_name(doc) -> str:
    for token in doc:
        if token.dep_ in ("dobj", "pobj", "attr") and token.pos_ in ("NOUN", "PROPN"):
            if token.lemma_.lower() not in ("screen", "display", "system", "time", "date"):
                return token.text.title()
    for token in doc:
        if token.pos_ == "PROPN":
            return token.text.title()
    return ""


def _extract_entities(doc, text: str) -> dict:
    if doc is None:
        return {}

    entities = {
        "datetime": "",
        "location": "",
        "app":      "",
        "file":     "",
        "person":   "",
        "org":      "",
    }

    lowered = (text or "").lower()

    for ent in doc.ents:
        label = ent.label_
        value = ent.text.strip()

        if label in ("DATE", "TIME") and not entities["datetime"]:
            entities["datetime"] = value
        elif label == "GPE" and not entities["location"]:
            entities["location"] = value.title()
        elif label == "PERSON" and not entities["person"]:
            entities["person"] = value.title()
        elif label in ("ORG", "PRODUCT") and not entities["app"]:
            if value.lower() in _KNOWN_APPS:
                entities["app"] = value.title()
            elif label == "ORG" and not entities["org"]:
                entities["org"] = value

    if not entities["app"]:
        for app in _KNOWN_APPS:
            if re.search(rf"\b{re.escape(app)}\b", lowered):
                entities["app"] = app.title()
                break

    file_match = _FILE_EXT_PATTERN.search(text or "")
    if file_match:
        entities["file"] = file_match.group(1).strip()

    if not entities["datetime"]:
        time_match = re.search(
            r"\b(in\s+\d+\s+(?:minute|minutes|hour|hours|second|seconds)"
            r"|after\s+\d+\s+(?:minute|minutes|hour|hours)"
            r"|\d{1,2}\s*(?:am|pm)"
            r"|\d{1,2}[:.]\d{2}\s*(?:am|pm)?)\b",
            text or "",
            re.IGNORECASE,
        )
        if time_match:
            entities["datetime"] = time_match.group(1).strip()

    return entities


def _check_short_command(text: str) -> dict | None:
    stripped = text.strip()
    for pattern, intent in _SHORT_COMMAND_PATTERNS:
        m = pattern.match(stripped)
        if m:
            target = m.group(1).strip()
            target = re.sub(
                r"\b(app|application|please|jarvis)\b", "", target,
                flags=re.IGNORECASE,
            ).strip()
            if not target:
                return None
            doc      = _get_doc(stripped)
            entities = _extract_entities(doc, stripped)
            if intent == "open_app":
                return {
                    "intent":   "open_app",
                    "params":   {"app": target.title()},
                    "entities": entities,
                    "raw_text": stripped,
                }
            if intent == "close_app":
                return {
                    "intent":   "close_app",
                    "params":   {"app": target.title()},
                    "entities": entities,
                    "raw_text": stripped,
                }
    return None


def _nlp_classify_intent(text: str, doc) -> dict | None:
    if doc is None:
        return None

    lowered = text.lower().strip()

    root_verb = None
    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            root_verb = token.lemma_.lower()
            break

    all_nouns = " ".join(
        t.lemma_.lower() for t in doc
        if t.pos_ in ("NOUN", "PROPN", "ADJ")
    )

    candidate_intent = _VERB_INTENT_MAP.get(root_verb) if root_verb else None

    if root_verb == "pull":
        particles = [t.text.lower() for t in doc if t.dep_ == "prt"]
        if "up" in particles or "down" in particles:
            candidate_intent = "open_app"

    if root_verb == "fire":
        particles = [t.text.lower() for t in doc if t.dep_ == "prt"]
        if "up" in particles:
            candidate_intent = "open_app"

    if root_verb == "shut":
        obj_tokens = [t.text.lower() for t in doc if t.dep_ in ("dobj", "pobj")]
        if obj_tokens and obj_tokens[0] not in ("system", "computer", "pc", "laptop"):
            candidate_intent = "close_app"
        else:
            candidate_intent = None

    if candidate_intent and root_verb in _AMBIGUOUS_VERBS:
        refiners = _OBJECT_REFINERS.get(candidate_intent, ())
        if not any(r in all_nouns or r in lowered for r in refiners):
            candidate_intent = None

    if candidate_intent is None:
        if any(w in lowered for w in ("weather", "forecast")):
            candidate_intent = "weather"
        elif any(w in lowered for w in ("time", "clock")):
            candidate_intent = "get_time"
        elif any(w in lowered for w in ("date", "today", "day")):
            candidate_intent = "get_date"

    if candidate_intent is None:
        return None

    entities = _extract_entities(doc, text)

    if candidate_intent == "open_app":
        app = _extract_nlp_app_name(doc) or entities.get("app", "")
        if not app:
            return None
        return {"intent": "open_app", "params": {"app": app}, "entities": entities, "raw_text": text}

    if candidate_intent == "close_app":
        app = _extract_nlp_app_name(doc) or entities.get("app", "") or _context["last_app"]
        return {"intent": "close_app", "params": {"app": app}, "entities": entities, "raw_text": text}

    if candidate_intent == "web_search":
        query = re.sub(
            r"\b(look up|look|browse|surf|search for|search)\b", "", text, flags=re.IGNORECASE
        ).strip()
        return {"intent": "web_search", "params": {"query": query or text}, "entities": entities, "raw_text": text}

    if candidate_intent == "weather":
        city = _extract_city(text) or entities.get("location", "")
        return {"intent": "weather", "params": {"city": city or "your city"}, "entities": entities, "raw_text": text}

    if candidate_intent == "describe_screen":
        return {"intent": "describe_screen", "params": {}, "entities": entities, "raw_text": text}

    if candidate_intent == "system_status":
        mode = _detect_system_status_mode(lowered)
        return {"intent": "system_status", "params": {"mode": mode}, "entities": entities, "raw_text": text}

    if candidate_intent == "get_time":
        return {"intent": "get_time", "params": {}, "entities": entities, "raw_text": text}

    if candidate_intent == "get_date":
        return {"intent": "get_date", "params": {}, "entities": entities, "raw_text": text}

    return None


# ── Obligation intent helpers (Layer 1) ───────────────────────────────────────

_ADD_OBLIGATION_PHRASES = (
    "i have an assignment",
    "i have a test",
    "i have an exam",
    "i have a quiz",
    "i have a worksheet",
    "i have coursera",
    "assignment due",
    "exam on",
    "test on",
    "quiz due",
    "quiz on",
    "worksheet due",
    "submission due",
    "project due",
    "coursera deadline",
    "coursera due",
    "deadline for",
    "deadline is",
    "due on",
    "due by",
    "due next",
    "due this",
    "due friday",
    "due monday",
    "due tuesday",
    "due wednesday",
    "due thursday",
    "due saturday",
    "due sunday",
    "due tonight",
    "due tomorrow",
    "need to submit",
    "have to submit",
    "remind me about the",
    "add obligation",
    "log obligation",
    "note that i have",
)

_QUERY_OBLIGATION_PHRASES = (
    "what's pending",
    "what is pending",
    "what do i have due",
    "what's due",
    "what is due",
    "what are my obligations",
    "show my obligations",
    "list my obligations",
    "what are my deadlines",
    "show my deadlines",
    "list deadlines",
    "upcoming deadlines",
    "what's overdue",
    "what is overdue",
    "anything overdue",
    "am i overdue",
    "what assignments do i have",
    "show assignments",
    "pending work",
    "my pending tasks",
    "what do i owe",
    "obligation status",
    "deadline check",
)

_PORTAL_SCAN_PHRASES = (
    "scan the portal",
    "scan my screen for deadlines",
    "scan coursera",
    "read the portal",
    "check the portal for deadlines",
    "scan portal",
    "read screen for deadlines",
    "detect deadlines",
    "find deadlines on screen",
    "scan screen for assignments",
)

_MARK_DONE_PHRASES = (
    "mark done",
    "mark as done",
    "i finished",
    "i completed",
    "i submitted",
    "mark complete",
    "obligation done",
    "i'm done with",
    "done with",
    "finished the",
    "submitted the",
    "completed the",
)


def _extract_obligation_title(text: str) -> str:
    lowered = text.lower().strip()
    strip_prefixes = (
        "remind me about the ",
        "remind me about ",
        "note that i have ",
        "add obligation ",
        "log obligation ",
        "i have an ",
        "i have a ",
        "i have ",
        "need to submit ",
        "have to submit ",
    )
    for prefix in strip_prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip()
    return text.strip()


def _infer_obligation_type_from_text(text: str) -> str:
    lowered = text.lower()
    if any(w in lowered for w in ("exam", "test", "midterm", "final", "viva")):
        return "exam"
    if any(w in lowered for w in ("coursera", "quiz", "week ", "module")):
        return "coursera"
    if any(w in lowered for w in ("worksheet", "exercise", "practice sheet")):
        return "worksheet"
    if any(w in lowered for w in ("assignment", "project", "submission", "submit", "homework")):
        return "assignment"
    return "other"


# ── User profile intent helpers ───────────────────────────────────────────────

_PROFILE_QUERY_PHRASES = (
    "what do you know about me",
    "what do u know about me",
    "tell me what you know about me",
    "show me my profile",
    "what's my profile",
    "what is my profile",
    "show my profile",
    "my profile",
    "what have you learned about me",
    "what do you remember about me",
)

_PROFILE_FORGET_PHRASES = (
    "forget that i",
    "forget that I",
    "remove that i",
    "delete that i",
    "don't remember that i",
    "stop remembering that i",
)

_PROFILE_REMEMBER_PHRASES = (
    "remember that i",
    "remember that I",
    "note that i",
    "note about me",
    "add to my profile",
    "save that i",
)


def _strip_profile_prefix(text: str, prefixes: tuple) -> str:
    lowered = text.lower().strip()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if lowered.startswith(prefix.lower()):
            return text[len(prefix):].strip()
    return text.strip()


# ══════════════════════════════════════════════════════════════════════════════
# NEW: IRON MAN AUTOMATION INTENT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# ── Install intent phrases ────────────────────────────────────────────────────
_INSTALL_PHRASES = (
    "install ",
    "instal ",         # Whisper drops the double-l sometimes
    "instals ",        # "install spotify" → "instals fortify"
    "installs ",
    "get me ",
    "download ",
    "set up ",
    "setup ",
    "grab me ",
    "get ",
)

_INSTALL_AND_LOGIN_PHRASES = (
    "install and log me in",
    "install and login",
    "install and sign in",
    "install and set up",
    "install and open",
    "get and log me in",
    "download and log me in",
    "set up and log me in",
)

# ── Auto-login intent phrases ─────────────────────────────────────────────────
_OPEN_AND_LOGIN_PHRASES = (
    "log me into",
    "log me in to",
    "log into",
    "sign me into",
    "sign me in to",
    "sign into",
    "open and log me in",
    "open and login",
)

# ── Save credentials phrases ──────────────────────────────────────────────────
_SAVE_LOGIN_PHRASES = (
    "save my login",
    "save my password",
    "save my credentials",
    "remember my login",
    "remember my password",
    "store my login",
    "store my password",
    "add my login",
    "save login for",
    "remember login for",
)

# ── List saved logins ─────────────────────────────────────────────────────────
_LIST_LOGINS_PHRASES = (
    "list my logins",
    "list saved logins",
    "list saved passwords",
    "show my logins",
    "show saved logins",
    "what logins do you have",
    "what passwords do you have",
    "what apps do you have saved",
    "show saved credentials",
    "list credentials",
)

# ── Delete saved login ────────────────────────────────────────────────────────
_DELETE_LOGIN_PHRASES = (
    "delete my login",
    "delete saved login",
    "remove my login",
    "remove saved login",
    "forget my login for",
    "forget my password for",
    "delete password for",
    "remove password for",
)

# ── Self-awareness phrases ────────────────────────────────────────────────────
_SELF_SCAN_PHRASES = (
    "scan yourself",
    "rescan yourself",
    "check yourself",
    "scan your code",
    "scan your files",
    "reinventory yourself",
    "check your abilities",
    "rescan your files",
    "update your self awareness",
    "update your self-awareness",
)

_SELF_CAPABILITIES_PHRASES = (
    "what are your abilities",
    "what abilities do you have",
    "list your abilities",
    "list your capabilities",
    "what can you actually do",
    "show your capabilities",
    "show me your abilities",
    "what modules do you have",
    "what files do you have",
    "list your modules",
    "list your files",
)

_SELF_CHANGES_PHRASES = (
    "what changed",
    "what has changed",
    "what's new",
    "what is new",
    "what did you gain",
    "what abilities did you gain",
    "what's different",
    "what evolved",
    "how have you evolved",
    "any updates to yourself",
    "any new files",
    # ── Handle Whisper transcription variants ─────────────────────────
    "change since",
    "changed since",
    "changed after",
    "change after",
    "since last boot",
    "since previous boot",
    "since the last boot",
    "since the previous boot",
    "after the last boot",
    "after the previous boot",
    "did something change",
    "anything change",
    "anything changed",
    "anything new",
    "what did you learn",
)


# ── App name extraction from install/login commands ────────────────────────────

_KNOWN_INSTALLABLE_APPS = frozenset((
    "spotify", "discord", "chrome", "firefox", "brave", "vscode", "vs code",
    "visual studio code", "notion", "slack", "zoom", "obs", "obs studio",
    "vlc", "7zip", "notepad++", "git", "python", "node", "nodejs", "postman",
    "figma", "gimp", "blender", "audacity", "steam", "epic games", "telegram",
    "microsoft edge", "edge", "powertoys", "whatsapp",
))


# Whisper commonly mishears these — map to correct app names
_WHISPER_APP_CORRECTIONS = {
    "fortify":       "spotify",
    "spot if I":     "spotify",
    "spot if i":     "spotify",
    "spot if":       "spotify",
    "spotifi":       "spotify",
    "vs go":         "vs code",
    "vs cord":       "vs code",
    "these code":    "vs code",
    "disco":         "discord",
    "diss cord":     "discord",
    "notion app":    "notion",
    "slack app":     "slack",
    "zoom app":      "zoom",
    "chrome browser":"chrome",
    "google chrome": "chrome",
    "fire fox":      "firefox",
    "brave browser": "brave",
    "seven zip":     "7zip",
    "seven-zip":     "7zip",
    "note pad":      "notepad",
    "note pad plus plus": "notepad++",
    "v.s. code":     "vs code",
    "vscode":        "vs code",
}


def _correct_whisper_app_name(app: str) -> str:
    """Fix common Whisper mishears for app names."""
    if not app:
        return app
    normalized = app.lower().strip()
    return _WHISPER_APP_CORRECTIONS.get(normalized, app)


def _extract_app_from_install(text: str) -> str:
    """
    Pull the app name out of an install-style command.
    Handles: "install spotify", "get me chrome", "download vs code", etc.
    Also corrects common Whisper mishears (e.g. "fortify" → "spotify").
    """
    lowered = text.lower().strip()

    # Try each install phrase — pick the one that matches at start
    for phrase in sorted(_INSTALL_PHRASES, key=len, reverse=True):
        if lowered.startswith(phrase):
            remainder = text[len(phrase):].strip()
            # Strip common suffixes
            remainder = re.sub(
                r"\s+(?:for me|please|jarvis|now|app|application)\.?$",
                "",
                remainder,
                flags=re.IGNORECASE,
            ).strip()
            # Cut at conjunctions
            remainder = re.sub(
                r"\s+(?:and|then|also|,)\s+.*$",
                "",
                remainder,
                flags=re.IGNORECASE,
            ).strip()
            cleaned = remainder.rstrip(".,!?").strip()
            # Apply Whisper corrections
            return _correct_whisper_app_name(cleaned)

    return ""


def _extract_app_from_login(text: str) -> str:
    """
    Pull the app name out of a login-style command.
    Handles: "log me into spotify", "sign into discord", etc.
    """
    lowered = text.lower().strip()

    for phrase in sorted(_OPEN_AND_LOGIN_PHRASES, key=len, reverse=True):
        if phrase in lowered:
            idx = lowered.index(phrase)
            remainder = text[idx + len(phrase):].strip()
            remainder = re.sub(
                r"\s+(?:for me|please|jarvis|now)\.?$",
                "",
                remainder,
                flags=re.IGNORECASE,
            ).strip()
            return remainder.rstrip(".,!?").strip()

    return ""


def _extract_app_from_save_login(text: str) -> str:
    """
    Pull the app name from 'save my [app] login' or 'save login for [app]'.
    """
    lowered = text.lower().strip()

    # Pattern: "save my <app> login/password"
    match = re.search(
        r"(?:save|remember|store|add)\s+my\s+(.+?)\s+(?:login|password|credentials?)",
        lowered,
    )
    if match:
        return match.group(1).strip().rstrip(".,!?")

    # Pattern: "save login for <app>"
    match = re.search(
        r"(?:save|remember|store)\s+(?:login|password|credentials?)\s+for\s+(.+?)(?:\.|$)",
        lowered,
    )
    if match:
        return match.group(1).strip().rstrip(".,!?")

    return ""


def _extract_app_from_delete_login(text: str) -> str:
    """
    Pull the app name from 'delete my [app] login' or 'forget my login for [app]'.
    """
    lowered = text.lower().strip()

    # Pattern: "forget my login for <app>" / "delete password for <app>"
    match = re.search(
        r"(?:delete|remove|forget)\s+(?:my\s+)?(?:login|password|credentials?)\s+for\s+(.+?)(?:\.|$)",
        lowered,
    )
    if match:
        return match.group(1).strip().rstrip(".,!?")

    # Pattern: "delete my <app> login"
    match = re.search(
        r"(?:delete|remove|forget)\s+my\s+(.+?)\s+(?:login|password|credentials?)",
        lowered,
    )
    if match:
        return match.group(1).strip().rstrip(".,!?")

    return ""


def _looks_like_installable_app_command(text: str) -> bool:
    """
    Distinguish 'install spotify' (install intent) from
    'get me the time' (not install intent).

    Returns True only if we're confident this is an install request.
    """
    lowered = text.lower().strip()

    # Explicit strong signals
    strong_signals = ("install ", "download ", "set up ", "setup ")
    if any(lowered.startswith(s) for s in strong_signals):
        return True

    # Weaker signals ("get me", "grab me") — require known app in text
    weak_signals = ("get me ", "grab me ", "get ")
    for signal in weak_signals:
        if lowered.startswith(signal):
            for app in _KNOWN_INSTALLABLE_APPS:
                if app in lowered:
                    return True
            return False

    return False


# ══════════════════════════════════════════════════════════════════════════════
# INTENT CLASSIFICATION (MAIN)
# ══════════════════════════════════════════════════════════════════════════════

def classify_intent(text: str) -> dict:
    resolved = resolve_reference(text)
    lowered  = resolved.lower().strip()

    doc = _get_doc(resolved) if _NLP_READY else None

    if len(lowered.split()) <= 4:
        short_result = _check_short_command(resolved)
        if short_result is not None:
            print(f"[core][SHORT] classified '{resolved}' → {short_result['intent']}")
            return short_result

    # ══════════════════════════════════════════════════════════════════════════
    # NEW: Iron Man automation intents (checked BEFORE open/close)
    # ══════════════════════════════════════════════════════════════════════════

    # ── install_and_login (multi-step combined) ──────────────────────────────
    if any(phrase in lowered for phrase in _INSTALL_AND_LOGIN_PHRASES):
        app = _extract_app_from_install(resolved)
        # If that didn't work, try harder — look for known apps in text
        if not app:
            for known in _KNOWN_INSTALLABLE_APPS:
                if known in lowered:
                    app = known
                    break
        return {
            "intent":   "install_and_login",
            "params":   {"app": app},
            "entities": {"app": app},
            "raw_text": resolved,
        }

    # ── install_app ──────────────────────────────────────────────────────────
    if _looks_like_installable_app_command(resolved):
        app = _extract_app_from_install(resolved)
        if app:
            return {
                "intent":   "install_app",
                "params":   {"app": app},
                "entities": {"app": app},
                "raw_text": resolved,
            }

    # ── open_and_login ───────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _OPEN_AND_LOGIN_PHRASES):
        app = _extract_app_from_login(resolved)
        if app:
            return {
                "intent":   "open_and_login",
                "params":   {"app": app},
                "entities": {"app": app},
                "raw_text": resolved,
            }

    # ── save_login ───────────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _SAVE_LOGIN_PHRASES):
        app = _extract_app_from_save_login(resolved)
        return {
            "intent":   "save_login",
            "params":   {"app": app},
            "entities": {"app": app},
            "raw_text": resolved,
        }

    # ── list_logins ──────────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _LIST_LOGINS_PHRASES):
        return {
            "intent":   "list_logins",
            "params":   {},
            "entities": {},
            "raw_text": resolved,
        }

    # ── delete_login ─────────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _DELETE_LOGIN_PHRASES):
        app = _extract_app_from_delete_login(resolved)
        return {
            "intent":   "delete_login",
            "params":   {"app": app},
            "entities": {"app": app},
            "raw_text": resolved,
        }

    # ── self_scan ────────────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _SELF_SCAN_PHRASES):
        return {
            "intent":   "self_scan",
            "params":   {},
            "entities": {},
            "raw_text": resolved,
        }

    # ── self_capabilities ────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _SELF_CAPABILITIES_PHRASES):
        return {
            "intent":   "self_capabilities",
            "params":   {},
            "entities": {},
            "raw_text": resolved,
        }

    # ── self_changes ─────────────────────────────────────────────────────────
    if any(phrase in lowered for phrase in _SELF_CHANGES_PHRASES):
        return {
            "intent":   "self_changes",
            "params":   {},
            "entities": {},
            "raw_text": resolved,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # END new Iron Man intents
    # ══════════════════════════════════════════════════════════════════════════

    if any(lowered.startswith(p) for p in ("open ", "launch ", "start ")):
        app = _extract_app_name(resolved, "open|launch|start")
        entities = _extract_entities(doc, resolved)
        if not app and entities.get("app"):
            app = entities["app"]
        return {"intent": "open_app", "params": {"app": app}, "entities": entities, "raw_text": resolved}

    if any(w in lowered for w in ("close ", "kill ", "quit ", "exit ")) or lowered.startswith("close"):
        app = _extract_app_name(resolved, "close|kill|quit|exit")
        entities = _extract_entities(doc, resolved)
        if not app and entities.get("app"):
            app = entities["app"]
        return {"intent": "close_app", "params": {"app": app}, "entities": entities, "raw_text": resolved}

    if any(phrase in lowered for phrase in (
        "any class updates", "class updates", "class update", "any update",
        "check whatsapp", "read whatsapp", "any class", "meeting update",
        "timetable", "schedule",
    )):
        return {"intent": "check_whatsapp", "params": {}, "entities": {}, "raw_text": resolved}

    if lowered.startswith("join ") or "join meeting" in lowered or "join class" in lowered:
        link_match = re.search(r"(https?://\S+|meet\.google\.com/\S+)", resolved, re.IGNORECASE)
        link = link_match.group(1) if link_match else _context["last_link"]
        if link and link.startswith("meet.google.com"):
            link = f"https://{link}"
        return {"intent": "join_meeting", "params": {"link": link}, "entities": {}, "raw_text": resolved}

    if any(w in lowered for w in ("what time", "time now", "current time")):
        return {"intent": "get_time", "params": {}, "entities": {}, "raw_text": resolved}

    if any(w in lowered for w in ("what's the date", "today's date", "what day")):
        return {"intent": "get_date", "params": {}, "entities": {}, "raw_text": resolved}

    if any(phrase in lowered for phrase in (
        "describe my screen", "describe the screen", "describe screen",
        "what's on my screen", "what is on my screen", "what's on screen",
        "what do you see", "what can you see", "look at my screen",
        "look at the screen", "analyze my screen", "analyze the screen",
        "analyze screen", "screenshot", "take a screenshot",
        "what am i looking at", "see my screen",
    )):
        return {"intent": "describe_screen", "params": {}, "entities": {}, "raw_text": resolved}

    if any(w in lowered for w in (
        "gpu", "vram", "graphics card", "system status", "system check",
        "system info", "how's my system", "how is my system",
        "system stats", "system performance",
    )) or (
        any(w in lowered for w in ("cpu", "ram", "battery"))
        and not any(w in lowered for w in ("time", "date"))
    ):
        mode = _detect_system_status_mode(lowered)
        return {"intent": "system_status", "params": {"mode": mode}, "entities": {}, "raw_text": resolved}

    if any(w in lowered for w in ("weather", "temperature", "rain", "forecast", "humid")):
        if not any(w in lowered for w in ("gpu", "cpu", "vram")):
            city = _extract_city(resolved)
            entities = _extract_entities(doc, resolved)
            if not city and entities.get("location"):
                city = entities["location"]
            return {"intent": "weather", "params": {"city": city or "your city"}, "entities": entities, "raw_text": resolved}

    if any(w in lowered for w in ("search ", "google ", "look up ", "find ")):
        query = re.sub(r"\b(search|google|look up|find)\b", "", resolved, flags=re.IGNORECASE).strip()
        entities = _extract_entities(doc, resolved)
        return {"intent": "web_search", "params": {"query": query or resolved}, "entities": entities, "raw_text": resolved}

    if any(phrase in lowered for phrase in (
        "remind me", "set a reminder", "set reminder", "reminder for", "reminder at",
        "remind me to", "reminder to",
    )):
        _is_obligation = any(phrase in lowered for phrase in _ADD_OBLIGATION_PHRASES)
        if not _is_obligation:
            entities = _extract_entities(doc, resolved)
            message = re.sub(
                r"\b(remind me to|remind me|set a reminder for|set reminder for|"
                r"set a reminder|set reminder|reminder for|reminder at|reminder to)\b",
                "", resolved, flags=re.IGNORECASE,
            ).strip().strip(".,")
            return {
                "intent":   "set_reminder",
                "params":   {"message": message},
                "entities": entities,
                "raw_text": resolved,
            }

    # ── Layer 1: Obligation intents ───────────────────────────────────────────

    if any(phrase in lowered for phrase in _PORTAL_SCAN_PHRASES):
        return {
            "intent":   "portal_scan",
            "params":   {},
            "entities": {},
            "raw_text": resolved,
        }

    if any(phrase in lowered for phrase in _MARK_DONE_PHRASES):
        title_hint = resolved.strip()
        for phrase in sorted(_MARK_DONE_PHRASES, key=len, reverse=True):
            if phrase in lowered:
                idx = lowered.index(phrase)
                title_hint = resolved[idx + len(phrase):].strip().strip(".,")
                break
        entities = _extract_entities(doc, resolved)
        return {
            "intent":   "mark_obligation_done",
            "params":   {"title_hint": title_hint},
            "entities": entities,
            "raw_text": resolved,
        }

    if any(phrase in lowered for phrase in _QUERY_OBLIGATION_PHRASES):
        if any(w in lowered for w in ("overdue", "late", "missed", "past due")):
            mode = "overdue"
        elif any(w in lowered for w in ("soon", "upcoming", "this week", "today", "tomorrow")):
            mode = "due_soon"
        else:
            mode = "pending"
        return {
            "intent":   "query_obligations",
            "params":   {"mode": mode},
            "entities": {},
            "raw_text": resolved,
        }

    if any(phrase in lowered for phrase in _ADD_OBLIGATION_PHRASES):
        entities  = _extract_entities(doc, resolved)
        title     = _extract_obligation_title(resolved)
        ob_type   = _infer_obligation_type_from_text(resolved)
        due_date  = entities.get("datetime", "")
        return {
            "intent":  "add_obligation",
            "params":  {
                "title":    title,
                "type":     ob_type,
                "due_date": due_date,
                "source":   "voice",
            },
            "entities": entities,
            "raw_text": resolved,
        }

    # ── User profile intents ──────────────────────────────────────────────────

    if any(phrase in lowered for phrase in _PROFILE_QUERY_PHRASES):
        return {
            "intent":   "profile_query",
            "params":   {},
            "entities": {},
            "raw_text": resolved,
        }

    if any(phrase in lowered for phrase in _PROFILE_FORGET_PHRASES):
        hint = _strip_profile_prefix(resolved, _PROFILE_FORGET_PHRASES)
        return {
            "intent":   "profile_forget",
            "params":   {"hint": hint},
            "entities": {},
            "raw_text": resolved,
        }

    if any(phrase in lowered for phrase in _PROFILE_REMEMBER_PHRASES):
        statement = _strip_profile_prefix(resolved, _PROFILE_REMEMBER_PHRASES)
        return {
            "intent":   "profile_remember",
            "params":   {"statement": statement},
            "entities": {},
            "raw_text": resolved,
        }

    # ─────────────────────────────────────────────────────────────────────────

    if _NLP_READY and doc is not None:
        nlp_result = _nlp_classify_intent(resolved, doc)
        if nlp_result is not None:
            print(f"[core][NLP] classified '{resolved}' → {nlp_result['intent']}")
            return nlp_result

    entities = _extract_entities(doc, resolved) if doc is not None else {}
    return {"intent": "knowledge_query", "params": {"query": resolved}, "entities": entities, "raw_text": resolved}


# ── WhatsApp text parsing utilities ───────────────────────────────────────────
_NOISE_TOKENS = frozenset((
    "whatsapp", "search", "type a message", "chats", "updates", "calls",
    "status", "starred messages", "new chat", "settings", "archived",
    "muted", "online", "typing",
))


def parse_whatsapp_text(text: str) -> list:
    if not text:
        return []
    cleaned_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(noise in line.lower() for noise in _NOISE_TOKENS):
            continue
        cleaned_lines.append(line)

    full_text = " ".join(cleaned_lines)
    lowered   = full_text.lower()
    links = []
    for line in cleaned_lines:
        for match in re.findall(r"(https?://\S+|meet\.google\.com/\S+)", line, re.IGNORECASE):
            link = match.rstrip(".,)")
            if link.startswith("meet.google.com"):
                link = f"https://{link}"
            if link not in links:
                links.append(link)

    subject = _extract_subject(full_text)
    when    = _extract_time(full_text)
    events  = []

    if any(t in lowered for t in ("class", "lecture", "session", "meeting")) or any(
        "meet.google.com" in lk for lk in links
    ):
        meeting_link = next((lk for lk in links if "meet.google.com" in lk), "")
        event = {
            "type": "class", "subject": subject, "time": when,
            "link": meeting_link, "priority": 1 if meeting_link else 2, "text": full_text,
        }
        events.append(event)
        if meeting_link:
            update_context(last_link=meeting_link, last_subject=subject, last_class=event)

    if any(t in lowered for t in ("timetable", "schedule", "tt")):
        events.append({"type": "timetable", "subject": subject, "time": when,
                       "link": links[0] if links else "", "priority": 3, "text": full_text})

    if any(t in lowered for t in ("exam", "test", "quiz")):
        events.append({"type": "exam", "subject": subject, "time": when,
                       "link": links[0] if links else "", "priority": 2, "text": full_text})

    if not events:
        events.append({"type": "general", "subject": subject, "time": when,
                       "link": links[0] if links else "", "priority": 5, "text": full_text})

    events.sort(key=lambda item: item.get("priority", 5))
    return events


def format_whatsapp_response(events: list) -> tuple:
    if not events:
        return None, "No important updates found."
    top        = events[0]
    event_type = top.get("type")
    subject    = top.get("subject") or "class"
    when       = top.get("time")
    link       = top.get("link")

    if event_type == "class" and link:
        time_text = f" at {when}" if when else ""
        return {"action": "join_meeting", "link": link, "subject": subject}, \
               f"There's a {subject} class{time_text}. Want me to join?"
    if event_type == "class":
        time_text = f" at {when}" if when else ""
        return None, f"There's a {subject} class update{time_text}."
    if event_type == "timetable":
        return None, "Timetable detected. Saving it."
    if event_type == "exam":
        time_text = f" at {when}" if when else ""
        return None, f"Exam update for {subject}{time_text}."
    return None, "No important updates found."