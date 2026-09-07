# memory.py — Extended Memory Module
# Phase 1 upgrade: timestamps, tags, priority levels, smarter retrieval
# Phase 6 additions: usage tracking, failure logging, preferences, session memory
# Phase 6 NEW: light semantic recall layer (pure-Python TF-IDF cosine similarity)

import json
import os
import re
import math
import datetime
import threading

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE  = os.path.join(BASE_DIR, "memory.json")
_lock        = threading.Lock()

# ── Short-term session memory (in-RAM only, resets each run) ──
_session = {
    "last_actions":  [],
    "last_intent":   "",
    "last_app":      "",
}

def _load():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def _save(m):
    with _lock:
        try:
            tmp = MEMORY_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(m, f, indent=2, ensure_ascii=False)
            os.replace(tmp, MEMORY_FILE)
        except Exception as e:
            print(f"[memory save error] {e}")

def _ensure(m):
    for key in ["facts", "activity_log", "context_history", "session_notes",
                "daily_stats", "action_results", "usage_freq", "failure_log", "preferences"]:
        m.setdefault(key, {} if key in ["facts", "daily_stats", "usage_freq", "failure_log", "preferences"] else [])
    return m


# ════════════════════════════════════════
# PHASE 1 — ENHANCED FACTS WITH TAGS, TIMESTAMPS, PRIORITY
# ════════════════════════════════════════

VALID_PRIORITIES = ["low", "normal", "high", "critical"]

def remember(key, value, tags=None, priority="normal"):
    """
    Store a fact with optional tags and priority level.
    priority: low | normal | high | critical
    tags: list of strings e.g. ["personal", "schedule"]
    """
    m = _ensure(_load())
    if priority not in VALID_PRIORITIES:
        priority = "normal"
    m["facts"][key] = {
        "value":     value,
        "tags":      tags or [],
        "priority":  priority,
        "timestamp": datetime.datetime.now().isoformat()
    }
    _save(m)
    print(f"[Memory] fact: {key} = {value} | priority={priority} | tags={tags}")

def recall_all():
    m = _load()
    facts = m.get("facts", {})
    if not facts:
        return "nothing saved yet"
    parts = []
    for k, v in facts.items():
        if isinstance(v, dict):
            parts.append(f"{k}: {v.get('value', '')}")
        else:
            parts.append(f"{k}: {v}")
    return "here's what I got — " + ", ".join(parts)

def recall_by_tag(tag):
    """Return all facts that have a specific tag."""
    m = _load()
    facts = m.get("facts", {})
    results = {}
    for k, v in facts.items():
        if isinstance(v, dict) and tag in v.get("tags", []):
            results[k] = v.get("value", "")
        elif not isinstance(v, dict):
            pass
    return results

def recall_by_priority(priority):
    """Return all facts at or above a given priority level."""
    order = {p: i for i, p in enumerate(VALID_PRIORITIES)}
    min_level = order.get(priority, 1)
    m = _load()
    facts = m.get("facts", {})
    results = {}
    for k, v in facts.items():
        if isinstance(v, dict):
            fact_level = order.get(v.get("priority", "normal"), 1)
            if fact_level >= min_level:
                results[k] = v.get("value", "")
    return results

def forget(key):
    """Remove a fact by key."""
    m = _ensure(_load())
    if key in m["facts"]:
        del m["facts"][key]
        _save(m)
        print(f"[Memory] forgot: {key}")

def cleanup_old_facts(days=30):
    """Remove facts older than N days with priority low or normal."""
    m = _ensure(_load())
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    to_delete = []
    for k, v in m["facts"].items():
        if isinstance(v, dict):
            ts = v.get("timestamp", "")
            priority = v.get("priority", "normal")
            if ts and priority in ["low", "normal"]:
                try:
                    fact_time = datetime.datetime.fromisoformat(ts)
                    if fact_time < cutoff:
                        to_delete.append(k)
                except:
                    pass
    for k in to_delete:
        del m["facts"][k]
    if to_delete:
        _save(m)
        print(f"[Memory] cleaned up {len(to_delete)} old facts")

def format_facts_for_prompt():
    m = _load()
    facts = m.get("facts", {})
    if not facts:
        return ""
    lines = []
    for k, v in facts.items():
        if isinstance(v, dict):
            lines.append(f"- {k}: {v.get('value', '')}")
        else:
            lines.append(f"- {k}: {v}")
    return "What you know about Arju:\n" + "\n".join(lines)


# ════════════════════════════════════════
# ACTIVITY LOG
# ════════════════════════════════════════

def log_activity(activity_type, detail, app=None, tags=None):
    m = _ensure(_load())
    entry = {
        "time":   datetime.datetime.now().isoformat(),
        "type":   activity_type,
        "detail": detail,
        "app":    app or "",
        "tags":   tags or []
    }
    m["activity_log"].append(entry)
    m["activity_log"] = m["activity_log"][-50:]
    _save(m)

def get_recent_activity(n=5):
    m = _load()
    return m.get("activity_log", [])[-n:]

def format_activity_for_prompt():
    recent = get_recent_activity(5)
    if not recent:
        return ""
    lines = [
        f"- [{e.get('time','')[:16].replace('T',' ')}] {e.get('type')}: {e.get('detail')}"
        for e in recent
    ]
    return "Recent activity:\n" + "\n".join(lines)


# ════════════════════════════════════════
# CONTEXT HISTORY
# ════════════════════════════════════════

def log_context_change(app, title):
    m = _ensure(_load())
    last = m["context_history"][-1] if m["context_history"] else {}
    if last.get("app") == app:
        return
    entry = {
        "time":  datetime.datetime.now().isoformat(),
        "app":   app,
        "title": title
    }
    m["context_history"].append(entry)
    m["context_history"] = m["context_history"][-20:]
    _save(m)

def get_context_history(n=3):
    return _load().get("context_history", [])[-n:]

def get_app_time_today():
    m = _load()
    history = m.get("context_history", [])
    today   = datetime.date.today().isoformat()
    today_e = [e for e in history if e.get("time", "").startswith(today)]
    app_time = {}
    for i, e in enumerate(today_e):
        app = e.get("app", "unknown")
        if i + 1 < len(today_e):
            try:
                t1   = datetime.datetime.fromisoformat(e["time"])
                t2   = datetime.datetime.fromisoformat(today_e[i+1]["time"])
                mins = (t2 - t1).seconds // 60
                app_time[app] = app_time.get(app, 0) + mins
            except:
                pass
    return app_time


# ════════════════════════════════════════
# SESSION NOTES
# ════════════════════════════════════════

def add_session_note(note, tags=None, priority="normal"):
    m = _ensure(_load())
    m["session_notes"].append({
        "time":     datetime.datetime.now().isoformat(),
        "note":     note,
        "tags":     tags or [],
        "priority": priority
    })
    m["session_notes"] = m["session_notes"][-30:]
    _save(m)

def get_session_notes(n=3):
    return [e.get("note", "") for e in _load().get("session_notes", [])[-n:]]


# ════════════════════════════════════════
# DAILY STATS
# ════════════════════════════════════════

def update_daily_stats(key, increment=1):
    m = _ensure(_load())
    today = datetime.date.today().isoformat()
    m["daily_stats"].setdefault(today, {})
    m["daily_stats"][today][key] = m["daily_stats"][today].get(key, 0) + increment
    all_days = sorted(m["daily_stats"].keys())
    if len(all_days) > 7:
        for old in all_days[:-7]:
            del m["daily_stats"][old]
    _save(m)

def get_todays_stats():
    return _load().get("daily_stats", {}).get(datetime.date.today().isoformat(), {})


# ════════════════════════════════════════
# ACTION RESULTS
# ════════════════════════════════════════

def log_action_result(action_name, success=True, detail=""):
    m = _ensure(_load())
    entry = {
        "time":    datetime.datetime.now().isoformat(),
        "action":  action_name,
        "success": success,
        "detail":  detail[:80]
    }
    m["action_results"].append(entry)
    m["action_results"] = m["action_results"][-30:]
    _save(m)


# ════════════════════════════════════════
# MEMORY SUMMARY
# ════════════════════════════════════════

def get_memory_summary():
    m = _load()
    parts = []
    facts = m.get("facts", {})
    if facts:
        fact_strs = []
        for k, v in list(facts.items())[:8]:
            val = v.get("value", "") if isinstance(v, dict) else v
            fact_strs.append(f"{k}={val}")
        parts.append("Facts: " + ", ".join(fact_strs))
    recent = m.get("activity_log", [])[-3:]
    if recent:
        parts.append("Recent: " + " | ".join(
            f"{e['type']}:{e['detail'][:25]}" for e in recent))
    ctx = m.get("context_history", [])[-2:]
    if ctx:
        parts.append("Apps: " + " → ".join(e["app"] for e in ctx))
    results = m.get("action_results", [])[-2:]
    if results:
        parts.append("LastActions: " + ", ".join(
            f"{r['action']}({'ok' if r['success'] else 'fail'})" for r in results))
    return "\n".join(parts) if parts else ""


# ════════════════════════════════════════
# PHASE 6 — USAGE TRACKING, FAILURES, PREFERENCES, SESSION
# ════════════════════════════════════════

def log_usage(action, context=""):
    m = _ensure(_load())
    key = action
    m["usage_freq"].setdefault(key, {"count": 0, "contexts": []})
    m["usage_freq"][key]["count"] += 1
    if context:
        ctx_list = m["usage_freq"][key]["contexts"]
        ctx_list.append(context)
        m["usage_freq"][key]["contexts"] = ctx_list[-10:]
    _save(m)
    _session["last_actions"].append(action)
    _session["last_actions"] = _session["last_actions"][-10:]

def get_usage_count(action):
    m = _load()
    return m.get("usage_freq", {}).get(action, {}).get("count", 0)

def log_failure(action, detail=""):
    m = _ensure(_load())
    m["failure_log"].setdefault(action, {"count": 0, "last": ""})
    m["failure_log"][action]["count"] += 1
    m["failure_log"][action]["last"]   = datetime.datetime.now().isoformat()
    if detail:
        m["failure_log"][action]["detail"] = detail[:80]
    _save(m)
    print(f"[Memory] failure logged: {action}")

def should_avoid(action):
    m = _load()
    failures = m.get("failure_log", {}).get(action, {}).get("count", 0)
    usage    = m.get("usage_freq", {}).get(action, {}).get("count", 0)
    if (usage + failures) < 5:
        return False
    return failures > usage

def log_preference(action, context):
    m = _ensure(_load())
    hour    = datetime.datetime.now().hour
    tod     = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    pref_key = f"{action}:{context}:{tod}"
    m["preferences"][pref_key] = m["preferences"].get(pref_key, 0) + 1
    _save(m)

def get_preference(action, context):
    m = _load()
    hour = datetime.datetime.now().hour
    tod  = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    key  = f"{action}:{context}:{tod}"
    return m.get("preferences", {}).get(key, 0)

def get_top_preferences(n=3):
    m = _load()
    prefs = m.get("preferences", {})
    sorted_prefs = sorted(prefs.items(), key=lambda x: x[1], reverse=True)
    return sorted_prefs[:n]

def get_last_action():
    return _session["last_actions"][-1] if _session["last_actions"] else ""

def get_recent_context():
    return {
        "recent_actions": _session["last_actions"][-5:],
        "last_intent":    _session["last_intent"],
        "last_app":       _session["last_app"],
    }

def set_last_intent(intent):
    _session["last_intent"] = intent

def set_last_app(app):
    _session["last_app"] = app

def get_prediction_hint():
    m    = _load()
    freq = m.get("usage_freq", {})
    if not freq:
        return ""
    recent = _session["last_actions"][-3:]
    if not recent:
        return ""
    candidates = sorted(freq.items(), key=lambda x: x[1].get("count", 0), reverse=True)
    for action, data in candidates:
        if action not in recent and data.get("count", 0) >= 3:
            return action
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 6 NEW: LIGHT SEMANTIC RECALL LAYER
# Pure-Python TF-IDF cosine similarity — zero external dependencies.
# Sits alongside the existing key-value store, does NOT replace it.
# ══════════════════════════════════════════════════════════════════════════════

_STOP_WORDS = frozenset((
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some",
    "such", "no", "only", "own", "same", "than", "too", "very",
    "just", "because", "if", "when", "where", "how", "what", "which",
    "who", "whom", "this", "that", "these", "those", "i", "me", "my",
    "we", "our", "you", "your", "he", "him", "his", "she", "her",
    "it", "its", "they", "them", "their", "about", "up", "out",
))


def _tokenize(text: str) -> list:
    """Lowercase, strip punctuation, remove stop words."""
    if not text:
        return []
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _build_tfidf_vectors(facts: dict) -> tuple:
    """
    Build TF-IDF vectors for all facts.
    Returns (doc_list, vocab, idf, tfidf_matrix).
    doc_list: list of (key, value_text) tuples
    """
    doc_list = []
    for k, v in facts.items():
        val = v.get("value", "") if isinstance(v, dict) else str(v)
        combined = f"{k} {val}"
        doc_list.append((k, combined))

    if not doc_list:
        return [], {}, {}, []

    # Tokenize all docs
    tokenized = [_tokenize(doc[1]) for doc in doc_list]
    n_docs = len(tokenized)

    # Build vocabulary
    vocab = {}
    for tokens in tokenized:
        for t in set(tokens):
            if t not in vocab:
                vocab[t] = len(vocab)

    if not vocab:
        return doc_list, vocab, {}, []

    # Compute IDF
    doc_freq = [0] * len(vocab)
    for tokens in tokenized:
        for t in set(tokens):
            if t in vocab:
                doc_freq[vocab[t]] += 1

    idf = {}
    for term, idx in vocab.items():
        idf[term] = math.log((n_docs + 1) / (doc_freq[idx] + 1)) + 1

    # Compute TF-IDF matrix
    tfidf_matrix = []
    for tokens in tokenized:
        tf = {}
        for t in tokens:
            if t in vocab:
                tf[t] = tf.get(t, 0) + 1
        vec = {}
        for t, count in tf.items():
            vec[t] = (count / len(tokens)) * idf.get(t, 1.0)
        tfidf_matrix.append(vec)

    return doc_list, vocab, idf, tfidf_matrix


def _cosine_similarity(vec_a: dict, vec_b: dict) -> float:
    """Cosine similarity between two sparse vectors (dicts)."""
    if not vec_a or not vec_b:
        return 0.0

    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    if not common_keys:
        return 0.0

    dot = sum(vec_a[k] * vec_b[k] for k in common_keys)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)


def semantic_recall(query: str, top_n: int = 3) -> list:
    """
    Find the most semantically relevant facts for a natural language query.
    Uses TF-IDF cosine similarity — no external ML libraries needed.

    Returns a list of dicts: [{"key": ..., "value": ..., "score": ...}, ...]
    """
    if not query or not query.strip():
        return []

    m = _load()
    facts = m.get("facts", {})
    if not facts:
        return []

    doc_list, vocab, idf, tfidf_matrix = _build_tfidf_vectors(facts)
    if not doc_list or not vocab:
        return []

    # Build query vector
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    query_tf = {}
    for t in query_tokens:
        if t in vocab:
            query_tf[t] = query_tf.get(t, 0) + 1

    query_vec = {}
    for t, count in query_tf.items():
        query_vec[t] = (count / len(query_tokens)) * idf.get(t, 1.0)

    if not query_vec:
        return []

    # Score all docs
    scored = []
    for i, (key, text) in enumerate(doc_list):
        score = _cosine_similarity(query_vec, tfidf_matrix[i])
        if score > 0:
            val = facts[key]
            val_text = val.get("value", "") if isinstance(val, dict) else str(val)
            scored.append({
                "key":   key,
                "value": val_text,
                "score": round(score, 4),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def semantic_recall_text(query: str, top_n: int = 3) -> str:
    """
    Convenience wrapper: returns a formatted string for LLM prompt injection.
    """
    results = semantic_recall(query, top_n)
    if not results:
        return ""
    lines = [f"- {r['key']}: {r['value']}" for r in results]
    return "Relevant memories:\n" + "\n".join(lines)