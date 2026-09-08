# user_profile.py — User Profile System
# Persistent, structured understanding of the user.
# Two source types: "manual" (explicitly stated) and "observed" (inferred from behavior).
# Storage: user_profile.json — separate from memory.json
#
# Public API:
#   seed_profile_from_text(text)            → list of extracted entries (NOT saved yet)
#   confirm_and_save_profile(entries)        → saves confirmed entries to disk
#   add_single_fact(statement, verified)     → quick single-fact intake ("remember that I...")
#   run_behavioral_analysis()                → mine observer/memory/obligations for patterns
#   get_profile_summary()                    → human-readable grouped summary string
#   forget_entry(hint)                       → remove best-matching entry
#   get_profile_context_for_prompt(max_entries) → short context string for brain.py injection
#   get_profile_for_scheduler()              → delay-pattern string for proactive_scheduler

import json
import os
import uuid
import datetime
import threading

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE      = os.path.join(BASE_DIR, "user_profile.json")

_lock = threading.Lock()

# ── Valid categories ───────────────────────────────────────────────────────────
VALID_CATEGORIES = {
    "work_habits",
    "study_patterns",
    "communication_style",
    "interests",
    "schedule_patterns",
    "personality",
    "preferences",
    "health",
    "goals",
    "identity",      # ← added: name, background, personal identity facts
    "other",
}

# ── Schema per entry ───────────────────────────────────────────────────────────
# {
#   "id":           str (short uuid),
#   "category":     str (one of VALID_CATEGORIES),
#   "value":        str (human-readable statement),
#   "source":       "manual" | "observed",
#   "confidence":   float 0.0–1.0  (1.0 for manual, inferred for observed),
#   "verified":     bool (True if user explicitly confirmed),
#   "last_updated": ISO 8601 str,
# }


# ══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def _load() -> list:
    if not os.path.exists(PROFILE_FILE):
        return []
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"[user_profile] Load error: {e}")
        return []


def _save(entries: list):
    with _lock:
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[user_profile] Save error: {e}")


def _make_entry(
    category: str,
    value: str,
    source: str = "manual",
    confidence: float = 1.0,
    verified: bool = True,
) -> dict:
    """Create a new profile entry dict."""
    category = category.lower().strip()
    if category not in VALID_CATEGORIES:
        category = "other"
    return {
        "id":           str(uuid.uuid4())[:8],
        "category":     category,
        "value":        value.strip(),
        "source":       source,
        "confidence":   round(min(max(float(confidence), 0.0), 1.0), 2),
        "verified":     bool(verified),
        "last_updated": datetime.datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# LLM EXTRACTION PROMPT
# ══════════════════════════════════════════════════════════════════════════════

_EXTRACTION_SYSTEM = """You are a structured data extractor. The user is seeding a personal profile for their AI assistant.

Extract factual, meaningful statements about the user from the text provided.
Return ONLY a valid JSON array of objects. No markdown, no explanation, no code fences.

Each object must have exactly these fields:
  "category": one of: identity, work_habits, study_patterns, communication_style, interests, schedule_patterns, personality, preferences, health, goals, other
  "value": a clear, concise statement about the user in third-person (e.g. "Prefers working late at night")
  "confidence": 1.0 if stated directly, 0.7 if implied, 0.5 if uncertain
  "verified": true if the text states it as fact, false if the text marks it as uncertain or secondhand

Rules:
- Extract only things that describe the user's identity, patterns, or preferences
- Skip generic statements that aren't personal
- One entry per distinct fact
- Do NOT invent facts not present in the text
- If text says "I think" or "maybe" or "unverified" — set verified: false and confidence: 0.5
- Maximum 20 entries

Return format (JSON array, no other text):
[{"category": "...", "value": "...", "confidence": 1.0, "verified": true}]"""

_SINGLE_FACT_SYSTEM = """You are a structured data extractor. Extract one profile entry from the user's statement.

The user said: "remember that I [statement]"
Convert this into a structured profile entry.

Return ONLY a single valid JSON object. No markdown, no explanation.
Fields: category (one of: identity, work_habits, study_patterns, communication_style, interests, schedule_patterns, personality, preferences, health, goals, other), value (third-person statement), confidence (1.0), verified (true)

Example input: "remember that I hate group projects"
Example output: {"category": "personality", "value": "Dislikes group projects", "confidence": 1.0, "verified": true}"""


# ══════════════════════════════════════════════════════════════════════════════
# MANUAL SEEDING
# ══════════════════════════════════════════════════════════════════════════════

def seed_profile_from_text(text: str) -> list:
    """
    Takes a block of text the user pastes.
    Sends to LLM for structured extraction.
    Returns a list of proposed entry dicts — does NOT save anything.
    Caller must review and pass to confirm_and_save_profile().

    Returns [] on failure (with print explaining why).
    """
    if not (text or "").strip():
        print("[user_profile] seed_profile_from_text: empty text provided")
        return []

    try:
        # Use brain.ask_llm with allow_actions=False and model_type="chat" for higher token limit
        import brain

        print("[user_profile] Sending text to LLM for profile extraction...")

        raw = brain.ask_llm(
            query         = text.strip()[:4000],
            context       = _EXTRACTION_SYSTEM,
            model_type    = "chat",
            allow_actions = False,
        )

        if not raw or not raw.strip():
            print("[user_profile] LLM returned empty response")
            return []

        print(f"[user_profile] Raw LLM response ({len(raw)} chars): {raw[:120]}...")

        # Strip any accidental markdown
        clean = raw.strip()
        for fence in ("```json", "```JSON", "```"):
            clean = clean.replace(fence, "")
        clean = clean.strip()

        # Find the JSON array
        start = clean.find("[")
        end   = clean.rfind("]")
        if start == -1 or end == -1 or end <= start:
            print(f"[user_profile] Could not find JSON array in LLM response: {clean[:200]}")
            return []

        parsed = json.loads(clean[start:end + 1])

        if not isinstance(parsed, list):
            print(f"[user_profile] LLM returned non-list: {type(parsed)}")
            return []

        # Validate and normalise entries
        proposed = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            value = (item.get("value") or "").strip()
            if not value:
                continue
            category = (item.get("category") or "other").lower().strip()
            if category not in VALID_CATEGORIES:
                category = "other"
            confidence = float(item.get("confidence", 1.0))
            verified   = bool(item.get("verified", True))

            proposed.append(_make_entry(
                category   = category,
                value      = value,
                source     = "manual",
                confidence = confidence,
                verified   = verified,
            ))

        print(f"[user_profile] Extracted {len(proposed)} entries from text. Awaiting confirmation.")
        return proposed

    except json.JSONDecodeError as e:
        print(f"[user_profile] JSON parse error: {e}")
        # Try to salvage partial JSON
        return _try_salvage_partial_json(raw if 'raw' in dir() else "")
    except Exception as e:
        print(f"[user_profile] seed_profile_from_text error: {e}")
        return []


def _try_salvage_partial_json(raw: str) -> list:
    """
    If the JSON array was truncated, try to salvage complete objects from it.
    Looks for complete {...} blocks and parses each individually.
    """
    if not raw:
        return []

    proposed = []
    # Find all complete JSON objects within the response
    depth   = 0
    start   = -1
    in_str  = False
    escape  = False

    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                obj_str = raw[start:i + 1]
                try:
                    obj = json.loads(obj_str)
                    if isinstance(obj, dict) and obj.get("value"):
                        category = (obj.get("category") or "other").lower().strip()
                        if category not in VALID_CATEGORIES:
                            category = "other"
                        proposed.append(_make_entry(
                            category   = category,
                            value      = obj["value"].strip(),
                            source     = "manual",
                            confidence = float(obj.get("confidence", 1.0)),
                            verified   = bool(obj.get("verified", True)),
                        ))
                except Exception:
                    pass
                start = -1

    if proposed:
        print(f"[user_profile] Salvaged {len(proposed)} entries from partial JSON.")
    return proposed


def confirm_and_save_profile(entries: list) -> str:
    """
    Save a list of confirmed profile entries to user_profile.json.
    Called AFTER the user reviews the output of seed_profile_from_text().

    Skips duplicate values (case-insensitive match on value string).
    Returns a short spoken-ready confirmation string.
    """
    if not entries:
        return "No entries to save."

    existing        = _load()
    existing_values = {e.get("value", "").lower() for e in existing}

    added   = []
    skipped = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value", "").strip()
        if not value:
            continue
        if value.lower() in existing_values:
            skipped.append(value)
            continue
        if "id" not in entry:
            entry["id"] = str(uuid.uuid4())[:8]
        if "last_updated" not in entry:
            entry["last_updated"] = datetime.datetime.now().isoformat()
        existing.append(entry)
        existing_values.add(value.lower())
        added.append(value)

    _save(existing)

    msg_parts = []
    if added:
        msg_parts.append(f"Saved {len(added)} profile entry{'s' if len(added) != 1 else ''}.")
    if skipped:
        msg_parts.append(f"Skipped {len(skipped)} duplicate(s).")

    result = " ".join(msg_parts) or "Nothing new to save."
    print(f"[user_profile] confirm_and_save_profile: {result}")
    return result


def add_single_fact(statement: str, verified: bool = True) -> list:
    """
    Quick intake for "remember that I [statement]".
    Extracts one structured entry via LLM.
    Returns a list with one proposed entry — does NOT save.
    Caller reviews and passes to confirm_and_save_profile().
    """
    if not (statement or "").strip():
        return []

    try:
        import brain

        clean_statement = statement.strip()
        for prefix in ("remember that i ", "remember i "):
            if clean_statement.lower().startswith(prefix):
                clean_statement = clean_statement[len(prefix):].strip()
                break

        print(f"[user_profile] Extracting single fact: '{clean_statement}'")

        # Single fact is small — ask_llm() token limit is fine here
        raw = brain.ask_llm(
            query         = f"remember that I {clean_statement}",
            context       = _SINGLE_FACT_SYSTEM,
            model_type    = "fast",
            allow_actions = False,
        )

        if not raw or not raw.strip():
            return [_make_entry(
                category   = "other",
                value      = clean_statement.capitalize(),
                source     = "manual",
                confidence = 1.0,
                verified   = verified,
            )]

        clean = raw.strip()
        for fence in ("```json", "```JSON", "```"):
            clean = clean.replace(fence, "")
        clean = clean.strip()

        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return [_make_entry("other", clean_statement.capitalize(), "manual", 1.0, verified)]

        parsed = json.loads(clean[start:end + 1])

        if not isinstance(parsed, dict):
            return [_make_entry("other", clean_statement.capitalize(), "manual", 1.0, verified)]

        entry = _make_entry(
            category   = parsed.get("category", "other"),
            value      = parsed.get("value", clean_statement.capitalize()),
            source     = "manual",
            confidence = float(parsed.get("confidence", 1.0)),
            verified   = verified,
        )
        print(f"[user_profile] Single fact extracted: {entry['category']} → {entry['value']}")
        return [entry]

    except Exception as e:
        print(f"[user_profile] add_single_fact error: {e}")
        return [_make_entry("other", statement.strip().capitalize(), "manual", 1.0, verified)]


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIORAL OBSERVATION (auto-save, source="observed")
# ══════════════════════════════════════════════════════════════════════════════

def run_behavioral_analysis() -> list:
    """
    Mine existing data from observer.py, memory.py, and obligations.py
    to generate "observed" profile entries.

    Auto-saves results to user_profile.json (source="observed").
    Returns list of newly added entries for logging.

    Called weekly from proactive_scheduler.py.
    """
    print("[user_profile] Running behavioral analysis...")
    new_entries = []

    try:
        new_entries.extend(_analyze_active_hours())
    except Exception as e:
        print(f"[user_profile] active hours analysis error: {e}")

    try:
        new_entries.extend(_analyze_app_patterns())
    except Exception as e:
        print(f"[user_profile] app patterns analysis error: {e}")

    try:
        new_entries.extend(_analyze_obligation_patterns())
    except Exception as e:
        print(f"[user_profile] obligation patterns analysis error: {e}")

    try:
        new_entries.extend(_analyze_usage_patterns())
    except Exception as e:
        print(f"[user_profile] usage patterns analysis error: {e}")

    if new_entries:
        existing        = _load()
        existing_values = {e.get("value", "").lower() for e in existing}
        added = []
        for entry in new_entries:
            value = entry.get("value", "")
            if value.lower() not in existing_values:
                existing.append(entry)
                existing_values.add(value.lower())
                added.append(entry)
        if added:
            _save(existing)
            print(f"[user_profile] Behavioral analysis: auto-saved {len(added)} observed entry(s).")
        return added

    print("[user_profile] Behavioral analysis: no new patterns found.")
    return []


def _analyze_active_hours() -> list:
    entries = []
    try:
        import memory as _mem
        m       = _mem._load()
        history = m.get("context_history", [])
        if len(history) < 10:
            return []

        hour_counts = {}
        for event in history:
            ts = event.get("time", "")
            if not ts:
                continue
            try:
                hour = datetime.datetime.fromisoformat(ts).hour
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
            except Exception:
                continue

        if not hour_counts:
            return []

        total = sum(hour_counts.values())
        if total < 10:
            return []

        night_hours = sum(hour_counts.get(h, 0) for h in range(21, 24))
        late_night  = sum(hour_counts.get(h, 0) for h in range(0, 3))
        morning     = sum(hour_counts.get(h, 0) for h in range(6, 12))
        afternoon   = sum(hour_counts.get(h, 0) for h in range(12, 18))
        evening     = sum(hour_counts.get(h, 0) for h in range(18, 21))

        night_total = night_hours + late_night
        confidence  = min(round(total / 100, 2), 0.9)

        if night_total > total * 0.35:
            entries.append(_make_entry(
                category   = "schedule_patterns",
                value      = "Is most active late at night (9pm–3am)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))
        elif morning > total * 0.35:
            entries.append(_make_entry(
                category   = "schedule_patterns",
                value      = "Is most active in the morning (6am–12pm)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))
        elif afternoon > total * 0.35:
            entries.append(_make_entry(
                category   = "schedule_patterns",
                value      = "Is most active in the afternoon (12pm–6pm)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))
        elif evening > total * 0.30:
            entries.append(_make_entry(
                category   = "schedule_patterns",
                value      = "Is most active in the evening (6pm–9pm)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

    except Exception as e:
        print(f"[user_profile] _analyze_active_hours error: {e}")

    return entries


def _analyze_app_patterns() -> list:
    entries = []
    try:
        import memory as _mem
        m       = _mem._load()
        history = m.get("context_history", [])
        if len(history) < 10:
            return []

        app_counts = {}
        for event in history:
            app = event.get("app", "")
            if app and app not in ("Unknown", ""):
                app_counts[app] = app_counts.get(app, 0) + 1

        if not app_counts:
            return []

        total = sum(app_counts.values())
        if total < 10:
            return []

        dominant = [(app, count) for app, count in app_counts.items()
                    if count / total > 0.15]
        dominant.sort(key=lambda x: x[1], reverse=True)

        confidence = min(round(total / 80, 2), 0.85)

        coding_apps = {"VS Code", "PyCharm", "Sublime", "Atom", "Terminal",
                       "PowerShell", "CMD"}
        media_apps  = {"YouTube", "Netflix", "Prime Video", "Twitch", "Spotify"}
        work_apps   = {"Word", "Excel", "PowerPoint", "Notion", "Outlook"}

        coding_count = sum(c for a, c in dominant if a in coding_apps)
        media_count  = sum(c for a, c in dominant if a in media_apps)
        work_count   = sum(c for a, c in dominant if a in work_apps)

        if coding_count > total * 0.20:
            entries.append(_make_entry(
                category   = "work_habits",
                value      = "Spends significant time coding (VS Code, terminal, etc.)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

        if media_count > total * 0.25:
            entries.append(_make_entry(
                category   = "interests",
                value      = "Frequently uses streaming/media apps (YouTube, Netflix, etc.)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

        if work_count > total * 0.15:
            entries.append(_make_entry(
                category   = "work_habits",
                value      = "Regularly uses productivity/office apps (Word, Excel, Notion)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

        if dominant:
            top_app, top_count = dominant[0]
            pct = round(top_count / total * 100)
            entries.append(_make_entry(
                category   = "preferences",
                value      = f"Most frequently used app: {top_app} ({pct}% of tracked time)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

    except Exception as e:
        print(f"[user_profile] _analyze_app_patterns error: {e}")

    return entries


def _analyze_obligation_patterns() -> list:
    entries = []
    try:
        import obligations as _ob

        all_obs = _ob.get_all()
        if len(all_obs) < 3:
            return []

        overdue_count = sum(1 for ob in all_obs if ob.get("status") == "overdue")
        done_count    = sum(1 for ob in all_obs if ob.get("status") == "done")
        total         = len(all_obs)

        if total < 3:
            return []

        overdue_rate = overdue_count / total
        confidence   = min(round(total / 15, 2), 0.85)

        if overdue_rate > 0.4:
            entries.append(_make_entry(
                category   = "study_patterns",
                value      = f"Frequently has overdue obligations ({int(overdue_rate*100)}% of tracked items go overdue)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))
        elif overdue_rate < 0.1 and done_count > 3:
            entries.append(_make_entry(
                category   = "study_patterns",
                value      = "Generally completes obligations on time (low overdue rate)",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

        coursera_obs = [ob for ob in all_obs if ob.get("type") == "coursera"]
        if len(coursera_obs) >= 2:
            coursera_overdue = sum(1 for ob in coursera_obs
                                   if ob.get("status") == "overdue")
            if coursera_overdue / len(coursera_obs) > 0.5:
                entries.append(_make_entry(
                    category   = "study_patterns",
                    value      = "Tends to delay or miss Coursera deadlines specifically",
                    source     = "observed",
                    confidence = min(confidence, 0.75),
                    verified   = False,
                ))

        assignment_obs = [ob for ob in all_obs if ob.get("type") == "assignment"]
        if len(assignment_obs) >= 3:
            assign_overdue = sum(1 for ob in assignment_obs
                                 if ob.get("status") == "overdue")
            if assign_overdue / len(assignment_obs) > 0.5:
                entries.append(_make_entry(
                    category   = "study_patterns",
                    value      = "Frequently delays or misses assignment deadlines",
                    source     = "observed",
                    confidence = min(confidence, 0.8),
                    verified   = False,
                ))

    except Exception as e:
        print(f"[user_profile] _analyze_obligation_patterns error: {e}")

    return entries


def _analyze_usage_patterns() -> list:
    entries = []
    try:
        import memory as _mem
        m     = _mem._load()
        usage = m.get("usage_freq", {})

        if not usage:
            return []

        sorted_usage = sorted(usage.items(),
                              key=lambda x: x[1].get("count", 0), reverse=True)

        top_actions = [(act, data["count"]) for act, data in sorted_usage[:5]
                       if data.get("count", 0) >= 3]

        if not top_actions:
            return []

        confidence   = 0.7
        action_names = {act for act, _ in top_actions}

        if "web_search" in action_names:
            search_count = usage.get("web_search", {}).get("count", 0)
            if search_count >= 10:
                entries.append(_make_entry(
                    category   = "work_habits",
                    value      = f"Frequently uses web search via Jarvis ({search_count} times)",
                    source     = "observed",
                    confidence = confidence,
                    verified   = False,
                ))

        if "morning_briefing" in action_names:
            entries.append(_make_entry(
                category   = "schedule_patterns",
                value      = "Regularly requests morning briefings — likely a morning routine user",
                source     = "observed",
                confidence = confidence,
                verified   = False,
            ))

        prefs         = m.get("preferences", {})
        evening_count = sum(v for k, v in prefs.items() if ":evening" in k)
        morning_count = sum(v for k, v in prefs.items() if ":morning" in k)
        night_count   = sum(v for k, v in prefs.items() if ":night" in k)

        pref_total = evening_count + morning_count + night_count
        if pref_total >= 10:
            if evening_count > pref_total * 0.4:
                entries.append(_make_entry(
                    category   = "schedule_patterns",
                    value      = "Uses Jarvis most in the evening",
                    source     = "observed",
                    confidence = 0.65,
                    verified   = False,
                ))
            elif morning_count > pref_total * 0.4:
                entries.append(_make_entry(
                    category   = "schedule_patterns",
                    value      = "Uses Jarvis most in the morning",
                    source     = "observed",
                    confidence = 0.65,
                    verified   = False,
                ))

    except Exception as e:
        print(f"[user_profile] _analyze_usage_patterns error: {e}")

    return entries


# ══════════════════════════════════════════════════════════════════════════════
# VIEWING & CONTROL
# ══════════════════════════════════════════════════════════════════════════════

def get_profile_summary() -> str:
    """
    Returns a spoken-ready grouped summary of the full profile.
    Groups by category, mentions verified/unverified, manual/observed.
    """
    entries = _load()
    if not entries:
        return "I don't have much on you yet. Tell me something or let me observe your patterns for a while."

    grouped: dict = {}
    for entry in entries:
        cat = entry.get("category", "other")
        grouped.setdefault(cat, []).append(entry)

    parts = []

    category_order = [
        "identity", "personality", "work_habits", "study_patterns",
        "schedule_patterns", "interests", "communication_style",
        "preferences", "health", "goals", "other"
    ]

    for cat in category_order:
        if cat not in grouped:
            continue
        cat_entries = grouped[cat]
        cat_label   = cat.replace("_", " ").title()

        entry_strings = []
        for e in cat_entries:
            value      = e.get("value", "")
            source     = e.get("source", "manual")
            verified   = e.get("verified", True)
            confidence = e.get("confidence", 1.0)

            if source == "observed":
                conf_pct = int(confidence * 100)
                suffix   = f" (observed, {conf_pct}% confident)"
            elif not verified:
                suffix = " (unverified)"
            else:
                suffix = ""

            entry_strings.append(f"{value}{suffix}")

        if entry_strings:
            parts.append(f"{cat_label}: {'; '.join(entry_strings)}")

    if not parts:
        return "Profile exists but has no readable entries yet."

    total    = len(entries)
    manual   = sum(1 for e in entries if e.get("source") == "manual")
    observed = total - manual

    header = (
        f"Here's what I know about you — {total} entry{'s' if total != 1 else ''}, "
        f"{manual} manual, {observed} observed. "
    )

    return header + " | ".join(parts) + "."


def forget_entry(hint: str) -> str:
    """
    Remove the best-matching profile entry by fuzzy word overlap.
    """
    if not (hint or "").strip():
        return "What should I forget? Try: forget that I hate mornings."

    entries = _load()
    if not entries:
        return "I don't have anything saved about you yet."

    clean_hint = hint.lower().strip()
    for prefix in ("forget that i ", "forget that ", "forget i "):
        if clean_hint.startswith(prefix):
            clean_hint = clean_hint[len(prefix):]
            break

    hint_words  = set(clean_hint.split())
    best_match  = None
    best_score  = 0

    for entry in entries:
        entry_words = set(entry.get("value", "").lower().split())
        overlap     = len(hint_words & entry_words)
        if overlap > best_score:
            best_score = overlap
            best_match = entry

    if not best_match or best_score == 0:
        return f"I couldn't find anything matching '{hint}' in your profile."

    entries.remove(best_match)
    _save(entries)

    removed_value = best_match.get("value", "that entry")
    print(f"[user_profile] Removed entry: {removed_value}")
    return f"Done — removed '{removed_value}' from your profile."


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT INJECTION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_profile_context_for_prompt(max_entries: int = 6) -> str:
    """
    Returns a compact context string for injection into brain.py prompts.
    Prioritises: manual + verified entries first, then high-confidence observed.
    """
    entries = _load()
    if not entries:
        return ""

    def _sort_key(e):
        is_manual   = 1 if e.get("source") == "manual" else 0
        is_verified = 1 if e.get("verified") else 0
        confidence  = e.get("confidence", 0.5)
        return (is_manual + is_verified, confidence)

    sorted_entries = sorted(entries, key=_sort_key, reverse=True)
    top = sorted_entries[:max_entries]

    if not top:
        return ""

    lines = []
    for e in top:
        value  = e.get("value", "")
        source = e.get("source", "manual")
        tag    = " [inferred]" if source == "observed" else ""
        lines.append(f"- {value}{tag}")

    return "User profile context:\n" + "\n".join(lines)


def get_profile_for_scheduler() -> str:
    """
    Returns profile entries relevant to deadline/obligation behaviour,
    for injection into proactive_scheduler prompts.
    """
    entries = _load()
    if not entries:
        return ""

    relevant_cats = {"study_patterns", "schedule_patterns", "work_habits"}
    relevant = [
        e for e in entries
        if e.get("category") in relevant_cats
        and e.get("confidence", 0) >= 0.5
    ]

    if not relevant:
        return ""

    lines = []
    for e in relevant:
        value  = e.get("value", "")
        source = e.get("source", "manual")
        tag    = " [inferred]" if source == "observed" else " [stated]"
        lines.append(f"- {value}{tag}")

    return "Known user patterns:\n" + "\n".join(lines)