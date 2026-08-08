# brain.py — AI Brain (Multi-Model Router)
# Single responsibility: all LLM calls live here.
# Returns strings or dicts. Never speaks, never prints user-facing output.

import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ── Model routing map ──────────────────────────────────────────────────────────
MODEL_MAP = {
    "chat":      "meta-llama/llama-3.3-70b-instruct",
    "reasoning": "nousresearch/hermes-3-llama-3.1-405b",
    "action":    "google/gemma-3-27b-it",
    "fast":      "meta-llama/llama-3.2-3b-instruct",
}
FALLBACK_MODEL = MODEL_MAP["fast"]

PRIMARY_MODEL = MODEL_MAP["chat"]

_ANALYZE_SYSTEM = (
    "You are a message classifier for a desktop assistant. "
    "Analyze the input and return ONLY a valid JSON object — no markdown, no explanation. "
    'Schema: {"type": "notification|important|ignore", "summary": "max 15 words", "action": "notify|store|none"}\n'
    "Rules:\n"
    "  important  → class / meeting / exam update that has a Google Meet link\n"
    "  notification → general academic update (timetable, exam, assignment) with no link\n"
    "  ignore     → noise, greetings, irrelevant chat"
)

_LLM_SYSTEM = (
    "You are Jarvis, a sharp desktop assistant. "
    "Reply in 1-2 short sentences only. "
    "Never explain your reasoning. Never think out loud. "
    "Do not include analysis, thoughts, or internal steps."
)

# ── Sentiment-aware tone hints (Module 3) ──────────────────────────────────────
_SENTIMENT_HINTS = {
    "stressed": (
        "\nThe user seems frustrated. Be extra concise — no fluff, no pleasantries. "
        "Get straight to the point. If something failed, acknowledge it briefly and fix it. "
        "Don't apologize excessively. Be competent and steady."
    ),
    "stressed_streak": (
        "\nThe user has been frustrated for a while now. Stay calm, stay sharp. "
        "Keep responses minimal. Don't ask unnecessary questions. "
        "If you can solve it, just solve it. Be the reliable one in the room."
    ),
    "negative": (
        "\nThe user's tone is slightly off. Keep it short and efficient. "
        "No small talk. Just handle the request cleanly."
    ),
    "positive": "",
    "neutral":  "",
}

# ── Reasoning-leak markers (expanded) ─────────────────────────────────────────
_REASONING_MARKERS = (
    "the user is asking",
    "let me think",
    "let's see",
    "let me check",
    "i should",
    "i need to",
    "step by step",
    "my reasoning",
    "chain of thought",
    "first, i'll",
    "to answer this",
    "thinking about",
    "analyzing",
)


def _extract_content(payload: object) -> str:
    """Safely extract assistant text from an OpenRouter payload."""
    if not isinstance(payload, dict):
        print(f"[DEBUG][brain] malformed payload type: {type(payload).__name__}")
        return ""

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        print(f"[DEBUG][brain] missing choices: {payload}")
        return ""

    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        print(f"[DEBUG][brain] missing message: {payload}")
        return ""

    content = message.get("content")

    if content is None:
        print(f"[DEBUG][brain] response content is None — model may have refused: {payload}")
        return ""

    if isinstance(content, str):
        clean = content.strip()
        if not clean:
            print(f"[DEBUG][brain] empty response content: {payload}")
        return clean

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        clean = "\n".join(parts).strip()
        if not clean:
            print(f"[DEBUG][brain] empty list content: {payload}")
        return clean

    clean = str(content).strip()
    if not clean:
        print(f"[DEBUG][brain] unusable response content: {payload}")
    return clean


def _looks_like_reasoning(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _REASONING_MARKERS)


def _build_system_prompt(
    context: str = "",
    sentiment: dict | None = None,
    allow_actions: bool = True,
    profile_context: str = "",
) -> str:
    """
    Build the final system prompt.

    allow_actions=False is used for plain knowledge / info queries.
    profile_context: optional string from user_profile.get_profile_context_for_prompt()
                     injected AFTER main context, BEFORE sentiment hints.
                     Additive only — does not change existing prompt structure.
    """
    system = _LLM_SYSTEM

    if allow_actions:
        system += (
            "\nIf an action is needed, return exactly one plain JSON object on its own line, "
            "followed by the spoken reply if needed. "
            "Use ONLY the key 'action' for actions. "
            "Never use markdown code fences. "
            "Never invent schemas like {'type': ...} or {'name': ...}."
        )
    else:
        system += (
            "\nThis is a normal information or conversation request, not a desktop action. "
            "Answer directly in plain text only. "
            "Do NOT return JSON. "
            "Do NOT return markdown. "
            "Do NOT return code fences."
        )

    if context:
        system += f"\n\nContext: {context}"

    # ── Profile context injection (additive, after main context) ──────────────
    if profile_context:
        system += f"\n\n{profile_context}"
    # ─────────────────────────────────────────────────────────────────────────

    if sentiment:
        label  = sentiment.get("label", "neutral")
        streak = sentiment.get("streak", 0)

        if label == "stressed" and streak >= 3:
            hint = _SENTIMENT_HINTS.get("stressed_streak", "")
        else:
            hint = _SENTIMENT_HINTS.get(label, "")

        if hint:
            system += hint
            print(f"[DEBUG][brain] sentiment hint injected: {label} (streak={streak})")

    return system


def _post(messages: list, model: str, max_tokens: int = 200) -> str:
    """Low-level POST to OpenRouter. Returns raw content string or ''."""
    if not API_KEY:
        print("[DEBUG][brain] no API key")
        return ""
    try:
        resp = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.4,
            },
            timeout=12,
        )
        try:
            payload = resp.json()
        except ValueError:
            print(f"[DEBUG][brain] invalid JSON from model={model}, status={resp.status_code}")
            return ""

        content = _extract_content(payload)
        if not content:
            print(f"[DEBUG][brain] empty/malformed response from model={model}")
        return content

    except requests.exceptions.Timeout:
        print(f"[DEBUG][brain] request timed out for model={model}")
        return ""
    except Exception as exc:
        print(f"[DEBUG][brain] API error for model={model}: {exc}")
        return ""


# ── Public: classify text (e.g. WhatsApp message) ─────────────────────────────

def analyze(text: str) -> dict:
    """
    Classify a block of text into a structured dict.
    Always returns a safe dict even on failure.
    """
    if not (text or "").strip():
        return {"type": "ignore", "summary": "No content.", "action": "none"}

    raw = _post(
        [
            {"role": "system", "content": _ANALYZE_SYSTEM},
            {"role": "user",   "content": text[:2000]},
        ],
        model=PRIMARY_MODEL,
        max_tokens=120,
    )

    if not raw:
        return {"type": "notification", "summary": text[:80], "action": "notify"}

    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(clean)
        if "type" in result and "summary" in result:
            return result
    except Exception:
        print(f"[DEBUG][brain] JSON parse failed, raw: {raw!r}")

    return {"type": "notification", "summary": raw[:80], "action": "notify"}


# ── Public: general-purpose LLM call ──────────────────────────────────────────

def ask_llm(
    query: str,
    context: str = "",
    history: list | None = None,
    model_type: str = "chat",
    sentiment: dict | None = None,
    allow_actions: bool = True,
    profile_context: str = "",
) -> str:
    """
    General-purpose LLM call. Returns a plain string response.

    Args:
        query:           The user's question or command text.
        context:         Optional extra context injected into the system prompt.
        history:         Optional list of {"role", "content"} dicts for multi-turn.
        model_type:      One of "chat" | "reasoning" | "action" | "fast".
        sentiment:       Optional sentiment dict from planner/core.
        allow_actions:   If False, forces plain-text answers only.
        profile_context: Optional profile string from user_profile.
                         Injected into system prompt additively.
                         Pass empty string to skip (default).
    """
    model = MODEL_MAP.get(model_type, MODEL_MAP["chat"])
    print(f"[DEBUG][brain] routing → model_type={model_type}, model={model}")

    system = _build_system_prompt(
        context         = context,
        sentiment       = sentiment,
        allow_actions   = allow_actions,
        profile_context = profile_context,
    )

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": query})

    result = _post(messages, model=model, max_tokens=220)

    if not result:
        print(f"[DEBUG][brain] empty from model={model}, retrying once")
        result = _post(messages, model=model, max_tokens=220)

    if not result:
        print(f"[DEBUG][brain] falling back to fast model={FALLBACK_MODEL}")
        result = _post(messages, model=FALLBACK_MODEL, max_tokens=220)

    if _looks_like_reasoning(result):
        print(f"[DEBUG][brain] reasoning leak filtered: {result!r}")
        retry_system = _build_system_prompt(
            context         = "",
            sentiment       = sentiment,
            allow_actions   = allow_actions,
            profile_context = "",
        )
        result = _post(
            [
                {"role": "system", "content": retry_system},
                {"role": "user",   "content": query},
            ],
            model=FALLBACK_MODEL,
            max_tokens=120,
        )

    result = (result or "").strip()
    if not result:
        return "I couldn't figure that out."
    return result