# conversation_manager.py
# Iron Man Conversation Intelligence Layer
# Handles rolling short-term memory, auto-summarization, context injection,
# and sentiment tracking (Module 3)

import datetime
import brain
import memory

MAX_TURNS = 12
SUMMARY_TRIGGER = 10

_conversation_buffer = []
_last_summary = ""

# ── Sentiment tracking (Module 3) ─────────────────────────────────────────────
_current_sentiment = {
    "label": "neutral",    # "positive" | "negative" | "neutral" | "stressed"
    "score": 0.0,          # VADER compound score
    "streak": 0,           # consecutive negative/stressed turns
}


def add_turn(role: str, content: str):
    global _conversation_buffer
    if not content:
        return

    _conversation_buffer.append({
        "role": role,
        "content": content.strip()
    })

    if len(_conversation_buffer) > MAX_TURNS:
        _conversation_buffer = _conversation_buffer[-MAX_TURNS:]

    if len(_conversation_buffer) >= SUMMARY_TRIGGER:
        summarize_conversation()


def get_context_block():
    global _last_summary

    history_text = "\n".join(
        f"{t['role']}: {t['content']}"
        for t in _conversation_buffer[-6:]
    )

    context_parts = []
    if _last_summary:
        context_parts.append(f"Conversation summary: {_last_summary}")
    if history_text:
        context_parts.append(f"Recent conversation:\n{history_text}")

    return "\n\n".join(context_parts)


def summarize_conversation():
    global _last_summary, _conversation_buffer
    if not _conversation_buffer:
        return

    transcript = "\n".join(
        f"{t['role']}: {t['content']}"
        for t in _conversation_buffer
    )

    summary_prompt = (
        "Summarize this conversation briefly in under 5 sentences. "
        "Keep important facts, decisions, and context.\n\n"
        f"{transcript}"
    )

    summary = brain.ask_llm(summary_prompt, model_type="fast")

    if summary and len(summary) > 10:
        _last_summary = summary.strip()
        memory.remember(
            key=f"conversation_summary_{datetime.datetime.now().isoformat()}",
            value=_last_summary,
            tags=["conversation"],
            priority="normal"
        )
        _conversation_buffer = _conversation_buffer[-4:]


def reset_conversation():
    global _conversation_buffer, _last_summary, _current_sentiment
    _conversation_buffer = []
    _last_summary = ""
    _current_sentiment = {"label": "neutral", "score": 0.0, "streak": 0}


def get_last_summary():
    return _last_summary


# ── Sentiment API (Module 3) ──────────────────────────────────────────────────

def update_sentiment(sentiment_dict: dict):
    """
    Update current session sentiment.
    Called by planner after core.analyze_sentiment() on each user input.

    Tracks 'streak' — consecutive negative/stressed turns. This lets
    the tone hints escalate if user stays frustrated across multiple turns
    rather than reacting to a single word.
    """
    global _current_sentiment

    label = sentiment_dict.get("label", "neutral")
    score = sentiment_dict.get("score", 0.0)

    if label in ("negative", "stressed"):
        _current_sentiment["streak"] = _current_sentiment.get("streak", 0) + 1
    else:
        # Reset streak on any non-negative turn
        _current_sentiment["streak"] = 0

    _current_sentiment["label"] = label
    _current_sentiment["score"] = score


def get_sentiment() -> dict:
    """Get current sentiment state including streak count."""
    return dict(_current_sentiment)