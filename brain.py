# brain.py — AI Brain (Multi-Model Router)
# Single responsibility: all LLM calls live here.
# Returns strings or dicts. Never speaks, never prints user-facing output.
#
# Routing:
#   1. Ollama (local qwen2.5:3b) — primary for simple queries
#   2. Groq (llama-3.3-70b) — AUTO-USED for complex queries + fallback
#   3. OpenRouter (free) — last resort

import json
import os
import re

import requests
from dotenv import load_dotenv

# Reliability imports
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler

# ── Optional SDK imports (safe if not installed) ───────────────────────────────
try:
    import ollama as _ollama
    _OLLAMA_READY = True
except Exception as _oll_err:
    _OLLAMA_READY = False
    print(f"[brain] ollama SDK not available: {_oll_err}")

try:
    from groq import Groq as _Groq
    _GROQ_READY = True
except Exception as _gq_err:
    _GROQ_READY = False
    print(f"[brain] groq SDK not available: {_gq_err}")

load_dotenv()

# ── API keys ──────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")

# ── Model configuration ───────────────────────────────────────────────────────
OLLAMA_MODEL       = "qwen2.5:3b"
OLLAMA_KEEP_ALIVE  = "30s"
OLLAMA_HOST        = "http://localhost:11434"

GROQ_MODEL_CHAT      = "llama-3.3-70b-versatile"
GROQ_MODEL_FAST      = "llama-3.1-8b-instant"
GROQ_MODEL_REASONING = "llama-3.3-70b-versatile"

OPENROUTER_FALLBACKS = [
    "nvidia/nemotron-nano-9b-v2:free",
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b:free",
]

# ── Groq client (lazy init) ────────────────────────────────────────────────────
_groq_client = None

def _get_groq_client():
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    if not _GROQ_READY or not GROQ_API_KEY:
        get_registry().set_status(
            "GROQ",
            SubsystemState.DISABLED,
            "Groq SDK not installed or GROQ_API_KEY missing in environment"
        )
        return None
    try:
        _groq_client = _Groq(api_key=GROQ_API_KEY)
        return _groq_client
    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="GROQ",
            exception=exc,
            context="Initializing Groq client interface",
            demote_to=SubsystemState.OFFLINE
        )
        return None


# ══════════════════════════════════════════════════════════════════════════════
# COMPLEXITY DETECTION — decides if query needs the big brain (Groq)
# ══════════════════════════════════════════════════════════════════════════════

_COMPLEX_TRIGGERS = (
    # Explanation requests
    "explain", "how does", "how do", "why does", "why do", "why is",
    "what happens when", "walk me through", "break down",
    "in detail", "step by step", "step-by-step",

    # Analysis / reasoning
    "analyze", "analyse", "compare", "contrast", "difference between",
    "pros and cons", "trade-off", "trade off", "tradeoff",
    "advantages", "disadvantages", "evaluate", "assess",

    # Code / technical
    "write a function", "write a script", "write code", "code for",
    "debug", "refactor", "optimize this", "review this code",
    "algorithm for", "implement", "build a", "create a class",

    # Deep questions
    "philosophy", "meaning of", "concept of", "theory of",
    "history of", "origin of", "evolution of",

    # Multi-step / planning
    "plan for", "strategy for", "roadmap", "outline",

    # Manual override — user can force big brain
    "think deeper", "think harder", "use big brain", "use groq",
    "detailed answer", "long answer", "thorough answer",
)

_COMPLEX_WORD_COUNT_THRESHOLD = 15


def _is_complex_query(query: str) -> bool:
    """Auto-detect if a query needs the big Groq model instead of local qwen."""
    if not query:
        return False
    lowered = query.lower().strip()

    if len(lowered.split()) > _COMPLEX_WORD_COUNT_THRESHOLD:
        return True

    for trigger in _COMPLEX_TRIGGERS:
        if trigger in lowered:
            return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# SELF-AWARENESS CONTEXT (DYNAMIC — reads live file scan)
# ══════════════════════════════════════════════════════════════════════════════

def _get_dynamic_self_awareness() -> str:
    """
    Try to load self-awareness context from self_awareness module.
    Falls back to a minimal hardcoded description if scan not available.
    """
    try:
        import self_awareness
        dynamic_context = self_awareness.get_self_context_for_prompt(compact=False)
        if dynamic_context:
            return "\n\n" + dynamic_context + (
                "\n\nYou are JARVIS — a locally running AI assistant built by your user (Sonu/Arju). "
                "You run on Python 3.11 on Windows 11 (Acer Gaming Laptop, i5 12th gen, "
                "16GB RAM, RTX 3050 6GB VRAM). "
                "Your primary brain is qwen2.5:3b via Ollama, with Groq llama-3.3-70b as fallback for complex queries. "
                "Voice verification via Resemblyzer, transcription via Whisper on CUDA. "
                "You are actively being developed and can propose patches to your own code via evolver.py. "
                "When asked about your code or files, answer confidently using the module list above. "
                "Never say 'I don't have access to my code'."
            )
    except Exception as exc:
        print(f"[brain] self_awareness unavailable, using fallback: {exc}")

    # Fallback — minimal hardcoded description
    return (
        "\n\nYou are JARVIS — a locally running AI assistant built by your user (Sonu/Arju). "
        "You run on Python 3.11 on Windows 11. "
        "Your primary brain is qwen2.5:3b via Ollama, with Groq llama-3.3-70b as fallback. "
        "Voice verification via Resemblyzer, transcription via Whisper on CUDA. "
        "You are actively being developed and can propose patches to your own code."
    )


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

_LLM_SYSTEM_DETAILED = (
    "You are Jarvis, a sharp AI assistant. "
    "This is a complex or reasoning question. "
    "Give a clear, informative answer in 3-6 sentences. "
    "Be accurate, direct, and helpful. "
    "Never explain your reasoning process out loud. "
    "Never include internal thoughts or 'step by step' preambles."
)

# ── Sentiment-aware tone hints ─────────────────────────────────────────────────
_SENTIMENT_HINTS = {
    "stressed": (
        "\nThe user seems frustrated. Be extra concise — no fluff, no pleasantries. "
        "Get straight to the point. If something failed, acknowledge it briefly and fix it. "
        "Don't apologize excessively. Be competent and steady."
    ),
    "stressed_streak": (
        "\nThe user has been frustrated for a while. Stay calm, stay sharp. "
        "Keep responses minimal. Don't ask unnecessary questions. "
        "If you can solve it, just solve it."
    ),
    "negative": (
        "\nThe user's tone is slightly off. Keep it short and efficient. "
        "No small talk. Just handle the request cleanly."
    ),
    "positive": "",
    "neutral":  "",
}

# ── Reasoning-leak markers ─────────────────────────────────────────────────────
_REASONING_MARKERS = (
    "the user is asking",
    "let me think",
    "let's see",
    "let me check",
    "step by step",
    "my reasoning",
    "chain of thought",
    "first, i'll",
    "to answer this",
    "thinking about",
    "analyzing this",
)


def _build_system_prompt(
    context: str = "",
    sentiment: dict | None = None,
    allow_actions: bool = True,
    profile_context: str = "",
    detailed: bool = False,
) -> str:
    """Build the final system prompt with dynamic self-awareness."""
    system = _LLM_SYSTEM_DETAILED if detailed else _LLM_SYSTEM

    # ── Inject dynamic self-awareness (reads live codebase state) ─────────────
    system += _get_dynamic_self_awareness()

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
            "Do NOT return JSON. Do NOT return markdown. Do NOT return code fences."
        )

    if context:
        system += f"\n\nContext: {context}"

    if profile_context:
        system += f"\n\n{profile_context}"

    if sentiment:
        label  = sentiment.get("label", "neutral")
        streak = sentiment.get("streak", 0)
        if label == "stressed" and streak >= 3:
            hint = _SENTIMENT_HINTS.get("stressed_streak", "")
        else:
            hint = _SENTIMENT_HINTS.get(label, "")
        if hint:
            system += hint

    return system


def _looks_like_reasoning(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _REASONING_MARKERS)


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER 1: OLLAMA (LOCAL)
# ══════════════════════════════════════════════════════════════════════════════

def _ollama_call(messages: list, max_tokens: int = 220, temperature: float = 0.4) -> tuple:
    """Call local Ollama. Returns (content, status)."""
    registry = get_registry()
    if not _OLLAMA_READY:
        registry.set_status(
            "OLLAMA",
            SubsystemState.DISABLED,
            "Ollama Python SDK is not available"
        )
        return "", "unavailable"
    try:
        resp = _ollama.chat(
            model    = OLLAMA_MODEL,
            messages = messages,
            options  = {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
            keep_alive = OLLAMA_KEEP_ALIVE,
        )
        content = (resp.get("message", {}) or {}).get("content", "") or ""
        content = content.strip()
        if not content:
            # Model responded, but blank text
            registry.set_status(
                "OLLAMA",
                SubsystemState.DEGRADED,
                "Ollama responded with empty payload content"
            )
            return "", "empty"

        registry.set_status(
            "OLLAMA",
            SubsystemState.READY,
            f"Ollama running local {OLLAMA_MODEL} healthy"
        )
        return content, "ok"
    except Exception as exc:
        err_msg = str(exc).lower()
        if "connection" in err_msg or "refused" in err_msg or "connect" in err_msg:
            error_handler.log_and_demote(
                subsystem="OLLAMA",
                exception=exc,
                context="Connecting to Ollama local server instance",
                demote_to=SubsystemState.OFFLINE
            )
            return "", "unavailable"
        
        error_handler.log_and_demote(
            subsystem="OLLAMA",
            exception=exc,
            context="Executing query block on local Ollama client",
            demote_to=SubsystemState.DEGRADED
        )
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER 2: GROQ (CLOUD)
# ══════════════════════════════════════════════════════════════════════════════

def _groq_call(messages: list, model: str, max_tokens: int = 220, temperature: float = 0.4) -> tuple:
    """Call Groq API. Returns (content, status)."""
    registry = get_registry()
    client = _get_groq_client()
    if client is None:
        return "", "unavailable"
    try:
        resp = client.chat.completions.create(
            model       = model,
            messages    = messages,
            max_tokens  = max_tokens,
            temperature = temperature,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        content = (content or "").strip()
        if not content:
            registry.set_status(
                "GROQ",
                SubsystemState.DEGRADED,
                "Groq cloud responded with empty choices payload content"
            )
            return "", "empty"

        registry.set_status(
            "GROQ",
            SubsystemState.READY,
            f"Groq API connection active ({model})"
        )
        return content, "ok"
    except Exception as exc:
        err_msg = str(exc).lower()
        if "rate" in err_msg or "429" in err_msg:
            error_handler.log_and_demote(
                subsystem="GROQ",
                exception=exc,
                context="Groq API rate limit exceeded",
                demote_to=SubsystemState.DEGRADED
            )
            return "", "unavailable"
        if "unauthorized" in err_msg or "401" in err_msg or "invalid" in err_msg:
            error_handler.log_and_demote(
                subsystem="GROQ",
                exception=exc,
                context="Groq endpoint authentication credentials verification",
                demote_to=SubsystemState.DISABLED
            )
            return "", "unavailable"

        error_handler.log_and_demote(
            subsystem="GROQ",
            exception=exc,
            context="Groq API request dispatch loop",
            demote_to=SubsystemState.OFFLINE
        )
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER 3: OPENROUTER (LAST RESORT)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_openrouter_content(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first   = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _openrouter_call(messages: list, model: str, max_tokens: int = 220) -> tuple:
    registry = get_registry()
    if not OPENROUTER_API_KEY:
        registry.set_status(
            "OPENROUTER",
            SubsystemState.DISABLED,
            "OPENROUTER_API_KEY missing in environmental configurations"
        )
        return "", "unavailable"
    try:
        resp = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       model,
                "messages":    messages,
                "max_tokens":  max_tokens,
                "temperature": 0.4,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        error   = payload.get("error", {}) if isinstance(payload, dict) else {}
        if error:
            code = error.get("code", 0)
            if code in (402, 429, 404):
                registry.set_status(
                    "OPENROUTER",
                    SubsystemState.DEGRADED,
                    f"OpenRouter endpoint returned temporary failure code {code}"
                )
                return "", "unavailable"
            
            registry.set_status(
                "OPENROUTER",
                SubsystemState.DEGRADED,
                f"OpenRouter payload error: {error.get('message', 'unknown')}"
            )
            return "", "error"
        content = _extract_openrouter_content(payload)
        if not content:
            registry.set_status(
                "OPENROUTER",
                SubsystemState.DEGRADED,
                "OpenRouter responded with empty payload content"
            )
            return "", "empty"

        registry.set_status(
            "OPENROUTER",
            SubsystemState.READY,
            f"OpenRouter free fallback online ({model})"
        )
        return content, "ok"
    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="OPENROUTER",
            exception=exc,
            context="Connecting and parsing OpenRouter fallback API endpoint",
            demote_to=SubsystemState.OFFLINE
        )
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: CLASSIFY TEXT
# ══════════════════════════════════════════════════════════════════════════════

def analyze(text: str) -> dict:
    if not (text or "").strip():
        return {"type": "ignore", "summary": "No content.", "action": "none"}

    messages = [
        {"role": "system", "content": _ANALYZE_SYSTEM},
        {"role": "user",   "content": text[:2000]},
    ]

    raw, status = _ollama_call(messages, max_tokens=120)
    if status != "ok":
        raw, status = _groq_call(messages, model=GROQ_MODEL_FAST, max_tokens=120)

    if not raw:
        return {"type": "notification", "summary": text[:80], "action": "notify"}

    try:
        clean  = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(clean)
        if "type" in result and "summary" in result:
            return result
    except Exception:
        print(f"[DEBUG][brain] analyze JSON parse failed, raw: {raw!r}")

    return {"type": "notification", "summary": raw[:80], "action": "notify"}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: MAIN LLM CALL WITH SMART ROUTING
# ══════════════════════════════════════════════════════════════════════════════

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
    Smart routing:
    - Complex queries (auto-detected or reasoning type) → Groq big model directly
    - Simple queries → Ollama first, Groq fallback
    - Everything fails → OpenRouter chain
    """
    is_complex = _is_complex_query(query) or model_type == "reasoning"

    system = _build_system_prompt(
        context         = context,
        sentiment       = sentiment,
        allow_actions   = allow_actions,
        profile_context = profile_context,
        detailed        = is_complex,
    )

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": query})

    max_tokens = 400 if is_complex else 220
    result     = ""

    # ── Path A: Complex query → straight to Groq big brain ────────────────────
    if is_complex:
        print(f"[DEBUG][brain] complex query detected → Groq {GROQ_MODEL_REASONING}")
        result, status = _groq_call(messages, model=GROQ_MODEL_REASONING, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query)

        # Groq failed → try local as backup
        print(f"[DEBUG][brain] Groq unavailable, falling back to local Ollama")
        result, status = _ollama_call(messages, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query)

    # ── Path B: Simple query → Ollama first, Groq fallback ────────────────────
    else:
        print(f"[DEBUG][brain] routing → {model_type} → Ollama {OLLAMA_MODEL}")
        result, status = _ollama_call(messages, max_tokens=max_tokens)

        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query)

        # Local failed → Groq fallback
        groq_model = GROQ_MODEL_FAST if model_type == "fast" else GROQ_MODEL_CHAT
        print(f"[DEBUG][brain] Ollama failed ({status}), falling back to Groq {groq_model}")
        result, status = _groq_call(messages, model=groq_model, max_tokens=max_tokens)

        if status == "ok" and result:
            print(f"[DEBUG][brain] ✓ Groq fallback succeeded")
            return _finalize_result(result, sentiment, allow_actions, query)

        # Try bigger Groq if fast failed
        if groq_model != GROQ_MODEL_CHAT:
            print(f"[DEBUG][brain] trying Groq big model {GROQ_MODEL_CHAT}")
            result, status = _groq_call(messages, model=GROQ_MODEL_CHAT, max_tokens=max_tokens)
            if status == "ok" and result:
                return _finalize_result(result, sentiment, allow_actions, query)

    # ── Last resort: OpenRouter ───────────────────────────────────────────────
    print(f"[DEBUG][brain] all primary providers failed, trying OpenRouter")
    for or_model in OPENROUTER_FALLBACKS:
        result, status = _openrouter_call(messages, model=or_model, max_tokens=max_tokens)
        if status == "ok" and result:
            print(f"[DEBUG][brain] ✓ OpenRouter succeeded on {or_model}")
            return _finalize_result(result, sentiment, allow_actions, query)

    print(f"[DEBUG][brain] all providers exhausted")
    return "I couldn't figure that out."


def _finalize_result(result: str, sentiment: dict | None, allow_actions: bool, query: str) -> str:
    """Filter reasoning leaks and clean result."""
    if _looks_like_reasoning(result):
        print(f"[DEBUG][brain] reasoning leak detected, retrying clean")
        retry_system = _build_system_prompt(
            context         = "",
            sentiment       = sentiment,
            allow_actions   = allow_actions,
            profile_context = "",
        )
        clean_messages = [
            {"role": "system", "content": retry_system},
            {"role": "user",   "content": query},
        ]
        retry, status = _ollama_call(clean_messages, max_tokens=120)
        if status == "ok" and retry:
            result = retry

    return (result or "").strip() or "I couldn't figure that out."