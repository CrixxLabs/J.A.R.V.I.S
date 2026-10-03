# brain.py — AI Brain (Multi-Tier APInex Cloud Router & Autonomous Ollama Lifecycle)
# Single responsibility: all LLM calls live here.
# Returns strings or dicts. Never speaks, never prints user-facing output.
#
# Multi-Tier Routing:
#   System-1 Fast Reflex: APInex DeepSeek Flash (free/deepseek-v4.1-flash) (sub-second TTFT)
#   System-2 Deep Reasoning: APInex DeepSeek Pro (free/deepseek-v4-pro-0813) (complex deliberation)
#   Cloud Retry: APInex Fallback (free/glm-5.3-flash) on 429/500/timeout
#   Autonomous Local Fallback: Ollama (jarvis:latest / ministral-3:latest) silently spawned
#   Secondary Cloud: NVIDIA Nemotron / Gemini
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# Reliability imports
import error_handler
import status_registry
from provider_health import HealthState, classify_http, health as provider_health
from speech_cleaner import clean_speech_text
from status_registry import EvidenceLevel, SubsystemState, get_registry

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

# ── APInex Multi-Tier Cloud Brain Configuration ───────────────────────────────
APINEX_API_KEY        = os.getenv("APINEX_API_KEY", "").strip()
APINEX_BASE_URL       = os.getenv("APINEX_BASE_URL", "https://api.apinex.bond/v1").rstrip("/")
APINEX_FAST_MODEL     = os.getenv("APINEX_FAST_MODEL", "free/deepseek-v4.1-flash").strip()
APINEX_PRO_MODEL      = os.getenv("APINEX_PRO_MODEL", "free/deepseek-v4-pro-0813").strip()
APINEX_FALLBACK_MODEL = os.getenv("APINEX_FALLBACK_MODEL", "free/glm-5.3-flash").strip()
APINEX_CONNECT_TIMEOUT = float(os.getenv("APINEX_CONNECT_TIMEOUT", "1.5"))
APINEX_FAST_REQUEST_TIMEOUT = float(os.getenv("APINEX_FAST_REQUEST_TIMEOUT", "3.0"))
APINEX_PRO_REQUEST_TIMEOUT  = float(os.getenv("APINEX_PRO_REQUEST_TIMEOUT", "15.0"))

# ── Inception Labs (Mercury Diffusion) ────────────────────────────────────────
INCEPTION_API_KEY     = os.getenv("INCEPTION_API_KEY", "").strip()
INCEPTION_BASE_URL    = os.getenv("INCEPTION_BASE_URL", "https://api.inceptionlabs.ai/v1").rstrip("/")
INCEPTION_MODEL       = os.getenv("INCEPTION_MODEL", "mercury-2.5").strip()
INCEPTION_CONNECT_TIMEOUT = float(os.getenv("INCEPTION_CONNECT_TIMEOUT", "1.5"))
INCEPTION_REQUEST_TIMEOUT   = float(os.getenv("INCEPTION_REQUEST_TIMEOUT", "30.0"))

# ── Other Cloud API keys ──────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
NVIDIA_API_KEY     = os.getenv("NVIDIA_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

# ── Model configuration ───────────────────────────────────────────────────────
REQUIRED_OLLAMA_MODEL = "jarvis:latest"
CONFIGURED_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", REQUIRED_OLLAMA_MODEL).strip()
OLLAMA_MODEL       = REQUIRED_OLLAMA_MODEL
OLLAMA_KEEP_ALIVE  = os.getenv("OLLAMA_KEEP_ALIVE", "10m")
OLLAMA_HOST        = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_CONNECT_TIMEOUT = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "1.0"))
OLLAMA_DISCOVERY_TIMEOUT = float(os.getenv("OLLAMA_DISCOVERY_TIMEOUT", "2.0"))
OLLAMA_REQUEST_TIMEOUT = float(os.getenv("OLLAMA_REQUEST_TIMEOUT", "30.0"))
OLLAMA_DISCOVERY_TTL = float(os.getenv("OLLAMA_DISCOVERY_TTL", "30.0"))

NVIDIA_BASE_URL       = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
NVIDIA_FAST_MODEL     = os.getenv("NVIDIA_FAST_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
NVIDIA_REASONING_MODEL = os.getenv("NVIDIA_REASONING_MODEL", "nvidia/nemotron-3-super-120b-a12b")
NVIDIA_DEEP_MODEL     = os.getenv("NVIDIA_DEEP_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
NVIDIA_CONNECT_TIMEOUT = float(os.getenv("NVIDIA_CONNECT_TIMEOUT", "1.5"))
NVIDIA_REQUEST_TIMEOUT = float(os.getenv("NVIDIA_REQUEST_TIMEOUT", "8.0"))
GEMINI_TEXT_MODEL     = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite")

GROQ_MODEL_CHAT      = "llama-3.3-70b-versatile"
GROQ_MODEL_FAST      = "llama-3.1-8b-instant"
GROQ_MODEL_REASONING = "llama-3.3-70b-versatile"

OPENROUTER_FALLBACKS = [
    "nvidia/nemotron-nano-9b-v2:free",
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b:free",
]

for _provider, _model in (
    ("APINEX", APINEX_FAST_MODEL),
    ("APINEX", APINEX_PRO_MODEL),
    ("APINEX", APINEX_FALLBACK_MODEL),
    ("INCEPTION", INCEPTION_MODEL),
    ("NVIDIA", NVIDIA_FAST_MODEL),
    ("NVIDIA", NVIDIA_REASONING_MODEL),
    ("NVIDIA", NVIDIA_DEEP_MODEL),
    ("GEMINI", GEMINI_TEXT_MODEL),
    ("OLLAMA", OLLAMA_MODEL),
):
    provider_health.configure(_provider, _model)

_route_local = threading.local()
_ollama_client = None
_ollama_discovery_cache = None
_ollama_discovery_lock = threading.RLock()


def reset_last_provider() -> None:
    _route_local.provider_model = None


def get_last_provider_model() -> str | None:
    return getattr(_route_local, "provider_model", None)


def get_provider_health(provider: str, model: str | None = None) -> dict:
    return provider_health.get(provider, model)


def record_provider_success(provider: str, model: str) -> None:
    """Publish successful provider use for routes implemented outside brain.py."""
    normalized_provider = str(provider).strip().upper()
    normalized_model = str(model).strip()
    if not normalized_provider or not normalized_model:
        raise ValueError("provider and model are required")
    _mark_live(normalized_provider, normalized_model)


def _mark_live(provider: str, model: str) -> None:
    provider_health.record_success(provider, model, f"Live generation succeeded ({model})")
    registry = get_registry()
    registry.set_evidence(provider, EvidenceLevel.LIVE, f"Generation succeeded ({model})",
                          source="provider generation")
    cap_id = _provider_capability(provider, model)
    if cap_id:
        registry.set_capability_evidence(cap_id, EvidenceLevel.LIVE,
                                         f"Generation succeeded ({model})",
                                         source="provider generation")
    _route_local.provider_model = f"{provider}: {model}"


def _mark_failure(provider: str, model: str, state: HealthState, detail: str, retry_after=None) -> None:
    provider_health.record_failure(provider, model, state, detail, retry_after=retry_after)
    evidence = (EvidenceLevel.BLOCKED if state in (
                    HealthState.AUTH_ERROR, HealthState.OFFLINE,
                    HealthState.MODEL_UNAVAILABLE, HealthState.TIMEOUT)
                else EvidenceLevel.BROKEN)
    registry = get_registry()
    registry.set_evidence(provider, evidence, f"{state.value}: {detail[:160]}",
                          source="provider generation")
    cap_id = _provider_capability(provider, model)
    if cap_id:
        registry.set_capability_evidence(cap_id, evidence,
                                         f"{state.value}: {detail[:160]}",
                                         source="provider generation")


def _provider_capability(provider: str, model: str) -> str | None:
    provider = provider.upper()
    if provider == "APINEX":
        return "APINEX_PRO" if model == APINEX_PRO_MODEL else "APINEX_FAST"
    if provider == "INCEPTION":
        return "INCEPTION_MAIN"
    if provider == "NVIDIA":
        return "NVIDIA_NORMAL" if model == NVIDIA_FAST_MODEL else "NVIDIA_REASONING"
    return {"GEMINI": "GEMINI_FALLBACK", "OLLAMA": "OLLAMA_LOCAL"}.get(provider)


def _get_ollama_client():
    global _ollama_client
    if _ollama_client is None and _OLLAMA_READY:
        _ollama_client = _ollama.Client(host=OLLAMA_HOST, timeout=OLLAMA_REQUEST_TIMEOUT)
    return _ollama_client


def reset_ollama_runtime_state() -> None:
    """Clear process-local Ollama discovery/client state (primarily for tests)."""
    global _ollama_client, _ollama_discovery_cache
    with _ollama_discovery_lock:
        _ollama_client = None
        _ollama_discovery_cache = None


def _publish_ollama_discovery(result: dict) -> None:
    registry = get_registry()
    service_evidence = {
        "ONLINE": EvidenceLevel.PROBED,
        "OFFLINE": EvidenceLevel.BLOCKED,
        "DEGRADED": EvidenceLevel.BROKEN,
    }[result["service_state"]]
    model_evidence = {
        "AVAILABLE": EvidenceLevel.PROBED,
        "MISSING": EvidenceLevel.BLOCKED,
        "UNKNOWN": EvidenceLevel.UNKNOWN,
    }[result["model_state"]]
    if result["model_state"] == "AVAILABLE":
        usable_evidence = EvidenceLevel.PROBED
    elif result["model_state"] == "MISSING" or result["service_state"] == "OFFLINE":
        usable_evidence = EvidenceLevel.BLOCKED
    else:
        usable_evidence = EvidenceLevel.BROKEN
    ttl = OLLAMA_DISCOVERY_TTL
    registry.set_evidence(
        "OLLAMA_SERVICE", service_evidence, result["detail"],
        source="Ollama /api/tags discovery", ttl=ttl,
    )
    registry.set_evidence(
        "OLLAMA_MODEL_JARVIS_MINISTRAL_3B", model_evidence, result["detail"],
        source="Ollama /api/tags exact model discovery", ttl=ttl,
    )
    registry.set_evidence(
        "OLLAMA", usable_evidence, result["detail"],
        source="Ollama discovery", ttl=ttl,
    )
    registry.set_capability_evidence(
        "OLLAMA_LOCAL", usable_evidence, result["detail"],
        source="Ollama discovery", ttl=ttl,
    )


def discover_ollama(*, force: bool = False) -> dict:
    """Bounded authoritative discovery of the pinned local model via Ollama API."""
    global _ollama_discovery_cache
    now = time.monotonic()
    with _ollama_discovery_lock:
        if (not force and _ollama_discovery_cache
                and now - _ollama_discovery_cache[0] < OLLAMA_DISCOVERY_TTL):
            return dict(_ollama_discovery_cache[1])

    base = {
        "host": OLLAMA_HOST,
        "model": REQUIRED_OLLAMA_MODEL,
        "configured_model": CONFIGURED_OLLAMA_MODEL,
        "models_path": os.getenv("OLLAMA_MODELS", "") or None,
        "models": [],
    }
    try:
        response = requests.get(
            f"{OLLAMA_HOST}/api/tags",
            timeout=(OLLAMA_CONNECT_TIMEOUT, OLLAMA_DISCOVERY_TIMEOUT),
        )
        if response.status_code != 200:
            result = {**base, "service_state": "DEGRADED", "model_state": "UNKNOWN",
                      "detail": f"Ollama /api/tags returned HTTP {response.status_code}; exact model availability unknown"}
        else:
            payload = response.json()
            entries = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                raise ValueError("/api/tags payload has no models list")
            names = []
            for entry in entries:
                if isinstance(entry, dict):
                    name = entry.get("name") or entry.get("model")
                    if isinstance(name, str):
                        names.append(name)
            found = REQUIRED_OLLAMA_MODEL in names
            drift = (f"; ignored configured model {CONFIGURED_OLLAMA_MODEL!r}"
                     if CONFIGURED_OLLAMA_MODEL != REQUIRED_OLLAMA_MODEL else "")
            result = {
                **base,
                "models": names,
                "service_state": "ONLINE",
                "model_state": "AVAILABLE" if found else "MISSING",
                "detail": (f"Ollama API reachable; exact model {REQUIRED_OLLAMA_MODEL} "
                           f"{'available' if found else 'missing'}{drift}"),
            }
    except requests.Timeout:
        result = {**base, "service_state": "OFFLINE", "model_state": "UNKNOWN",
                  "detail": "Ollama /api/tags timed out within the bounded discovery window"}
    except requests.ConnectionError:
        result = {**base, "service_state": "OFFLINE", "model_state": "UNKNOWN",
                  "detail": "Ollama API is unreachable at the configured local endpoint"}
    except (requests.RequestException, ValueError, TypeError) as exc:
        result = {**base, "service_state": "DEGRADED", "model_state": "UNKNOWN",
                  "detail": f"Ollama discovery returned malformed/unusable data ({type(exc).__name__})"}

    with _ollama_discovery_lock:
        _ollama_discovery_cache = (time.monotonic(), dict(result))
    _publish_ollama_discovery(result)
    return dict(result)


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
# COMPLEXITY DETECTION — decides if query needs System-2 Deep Reasoning
# ══════════════════════════════════════════════════════════════════════════════

_COMPLEX_TRIGGERS = (
    "analyze", "analyse", "compare", "contrast", "trade-off", "trade off",
    "tradeoff", "evaluate", "assess",
    "debug", "refactor", "optimize this", "review this code",
    "architecture", "architectural", "system design", "root cause",
    "implement", "write a function", "write a script", "write code",
    "algorithm for", "create a class", "ast analysis", "symbolic math",
    "proof", "structural deliberation",
    "strategy for", "roadmap", "multi-stage", "multi step", "multi-step",
    # Manual override.
    "think deeper", "think harder", "use big brain", "use nemotron",
    "deep reasoning", "extreme reasoning", "detailed technical analysis",
    "use pro model", "deep deliberation",
)


def _is_complex_query(query: str) -> bool:
    """Return True only when the request asks for deeper System-2 reasoning."""
    if not query:
        return False
    lowered = query.lower().strip()
    return any(trigger in lowered for trigger in _COMPLEX_TRIGGERS)


# ══════════════════════════════════════════════════════════════════════════════
# SELF-AWARENESS & WORKING MEMORY CONTEXT
# ══════════════════════════════════════════════════════════════════════════════

def _get_dynamic_self_awareness() -> str:
    """Try to load self-awareness context from self_awareness module."""
    try:
        import self_awareness
        dynamic_context = self_awareness.get_self_context_for_prompt(compact=False)
        if dynamic_context:
            return "\n\n" + dynamic_context + (
                "\n\nYou are JARVIS — an autonomous desktop AI assistant built by your user (Sonu/Arju). "
                "You run on Python 3.11 on Windows 11 (Acer Gaming Laptop, i5 12th gen, 16GB RAM, RTX 3050 6GB VRAM). "
                "Your cognition pipeline operates on a dual-tier APInex Cloud Brain (DeepSeek Flash & Pro) with autonomous local Ollama fallback. "
                "Voice verification via Resemblyzer, transcription via Whisper on CUDA. "
                "When asked about your code or files, answer confidently using the module list above."
            )
    except Exception as exc:
        print(f"[brain] self_awareness unavailable, using fallback: {exc}")

    return (
        "\n\nYou are JARVIS — an autonomous desktop AI assistant built by your user (Sonu/Arju). "
        "You run on Python 3.11 on Windows 11. "
        "Your cognition operates on dual-tier APInex Cloud Brain with autonomous local Ollama fallback. "
        "Voice verification via Resemblyzer, transcription via Whisper on CUDA."
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
    """Build the final system prompt with dynamic self-awareness and working memory grounding."""
    system = _LLM_SYSTEM_DETAILED if detailed else _LLM_SYSTEM

    system += _get_dynamic_self_awareness()
    system += (
        "\nRuntime truth rule: never claim that all systems, subsystems, providers, or "
        "capabilities are live/healthy unless current runtime evidence explicitly supplied in "
        "the context supports that exact claim. Otherwise describe only your conversational "
        "availability without inventing health status."
    )

    if allow_actions:
        system += (
            "\nIf an action is needed, return exactly one plain JSON object on its own line, "
            "followed by the spoken reply if needed. "
            "Use ONLY the key 'action' for actions. "
            "Never use markdown code fences. "
            "Never invent schemas like {'type': ...} or {'name': ...}. "
            "An action request is only a request to the executor: never claim it succeeded, "
            "completed, opened, played, sent, changed, generated, saved, or was observed until runtime/tool evidence confirms it."
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


def _strip_scratchpad(text: str) -> str:
    """Sanitize model output by removing thinking tags (<thought>, <think>) and CoT meta-talk."""
    if not text:
        return ""

    cleaned = text

    # Strip explicit thinking tags
    cleaned = re.sub(r"<(thought|think)>[\s\S]*?</\1>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^<(thought|think)>[\s\S]*?(?=(?:\r?\n\r?\n)|$)", "", cleaned, flags=re.IGNORECASE)

    meta_prefixes = (
        "we must",
        "we need to",
        "we can say",
        "we should",
        "must not claim",
        "must not say",
        "the user is asking",
        "let me think",
        "let's think",
        "step by step:",
        "my plan:",
        "plan:",
        "internal reasoning:",
        "reasoning process:",
    )

    lines = cleaned.splitlines()
    first_clean_idx = 0

    while first_clean_idx < len(lines):
        line = lines[first_clean_idx].strip()
        if not line:
            first_clean_idx += 1
            continue

        lowered_line = line.lower()
        if any(lowered_line.startswith(prefix) for prefix in meta_prefixes) or (
            ("we must" in lowered_line or "we can say" in lowered_line or "must not claim" in lowered_line)
            and len(line.split()) < 30
        ):
            first_clean_idx += 1
            continue
        break

    if 0 < first_clean_idx < len(lines):
        cleaned = "\n".join(lines[first_clean_idx:])
    elif first_clean_idx >= len(lines) and lines:
        remaining = "\n".join(lines[first_clean_idx:])
        if remaining.strip():
            cleaned = remaining

    return cleaned.strip()


def _looks_like_reasoning(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _REASONING_MARKERS)


def _safe_error_detail(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or response.reason)[:160]
    except Exception:
        pass
    return str(getattr(response, "reason", "request failed"))[:160]


def _retry_after(response) -> float | None:
    try:
        return float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER: APINEX MULTI-TIER CLOUD (PRIMARY DUAL-TIER BRAIN)
# ══════════════════════════════════════════════════════════════════════════════

def _apinex_call(
    messages: list,
    model: str,
    max_tokens: int = 220,
    temperature: float = 0.4,
    timeout: tuple[float, float] | None = None,
    response_format: dict | None = None,
) -> tuple[str, str]:
    """Call APInex OpenAI-compatible cloud endpoint."""
    if not APINEX_API_KEY:
        _mark_failure("APINEX", model, HealthState.AUTH_ERROR, "APINEX_API_KEY is not configured")
        return "", "unavailable"
    if not provider_health.can_attempt("APINEX", model):
        return "", "cooldown"

    call_timeout = timeout or (
        APINEX_CONNECT_TIMEOUT,
        APINEX_PRO_REQUEST_TIMEOUT if model == APINEX_PRO_MODEL else APINEX_FAST_REQUEST_TIMEOUT,
    )

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        body["response_format"] = response_format

    try:
        response = requests.post(
            f"{APINEX_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {APINEX_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=call_timeout,
        )
        if response.status_code != 200:
            detail = _safe_error_detail(response)
            state = classify_http(response.status_code, detail)
            _mark_failure("APINEX", model, state, detail, _retry_after(response))
            return "", state.value.lower()

        payload = response.json()
        choices = payload.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = (message.get("content") or "").strip()
        if not content:
            _mark_failure("APINEX", model, HealthState.SERVER_ERROR, "Empty completion")
            return "", "empty"

        _mark_live("APINEX", model)
        return content, "ok"
    except requests.Timeout:
        _mark_failure("APINEX", model, HealthState.TIMEOUT, "APInex generation timed out")
        return "", "timeout"
    except requests.ConnectionError:
        _mark_failure("APINEX", model, HealthState.OFFLINE, "APInex endpoint unreachable")
        return "", "offline"
    except (requests.RequestException, ValueError) as exc:
        _mark_failure("APINEX", model, HealthState.SERVER_ERROR, type(exc).__name__)
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER: NVIDIA NIM (SECONDARY CLOUD)
# ══════════════════════════════════════════════════════════════════════════════

def _nvidia_call(
    messages: list,
    model: str,
    max_tokens: int = 220,
    temperature: float = 0.4,
    reasoning: bool = False,
    response_format: dict | None = None,
) -> tuple[str, str]:
    """Call NVIDIA's OpenAI-compatible NIM endpoint."""
    if not NVIDIA_API_KEY:
        _mark_failure("NVIDIA", model, HealthState.AUTH_ERROR, "NVIDIA_API_KEY is not configured")
        return "", "unavailable"
    if not provider_health.can_attempt("NVIDIA", model):
        return "", "cooldown"
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": reasoning},
    }
    if reasoning:
        body["reasoning_budget"] = min(256, max(64, max_tokens // 3))
    if response_format:
        body["response_format"] = response_format
    try:
        response = requests.post(
            f"{NVIDIA_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=(NVIDIA_CONNECT_TIMEOUT, NVIDIA_REQUEST_TIMEOUT),
        )
        if response.status_code != 200:
            detail = _safe_error_detail(response)
            state = classify_http(response.status_code, detail)
            _mark_failure("NVIDIA", model, state, detail, _retry_after(response))
            return "", state.value.lower()
        payload = response.json()
        choices = payload.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = (message.get("content") or "").strip()
        if not content:
            _mark_failure("NVIDIA", model, HealthState.SERVER_ERROR, "Empty completion")
            return "", "empty"
        _mark_live("NVIDIA", model)
        return content, "ok"
    except requests.Timeout:
        _mark_failure("NVIDIA", model, HealthState.TIMEOUT, "Generation timed out")
        return "", "timeout"
    except requests.ConnectionError:
        _mark_failure("NVIDIA", model, HealthState.OFFLINE, "Endpoint unreachable")
        return "", "offline"
    except (requests.RequestException, ValueError) as exc:
        _mark_failure("NVIDIA", model, HealthState.SERVER_ERROR, type(exc).__name__)
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER: INCEPTION LABS / MERCURY DIFFUSION (MERKURY TIER)
# ══════════════════════════════════════════════════════════════════════════════

def _inception_call(messages: list, model: str, max_tokens: int = 220, temperature: float = 0.4) -> tuple[str, str]:
    """Call Inception Labs Mercury Diffusion (Mercury-2.5) via its OpenAI-compatible API."""
    if not INCEPTION_API_KEY:
        _mark_failure("INCEPTION", model, HealthState.AUTH_ERROR, "INCEPTION_API_KEY is not configured")
        return "", "unavailable"
    if not provider_health.can_attempt("INCEPTION", model):
        return "", "cooldown"
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        response = requests.post(
            f"{INCEPTION_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {INCEPTION_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=(INCEPTION_CONNECT_TIMEOUT, INCEPTION_REQUEST_TIMEOUT),
        )
        if response.status_code != 200:
            detail = _safe_error_detail(response)
            state = classify_http(response.status_code, detail)
            _mark_failure("INCEPTION", model, state, detail, _retry_after(response))
            return "", state.value.lower()
        payload = response.json()
        choices = payload.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = (message.get("content") or "").strip()
        if not content:
            _mark_failure("INCEPTION", model, HealthState.SERVER_ERROR, "Empty completion")
            return "", "empty"
        _mark_live("INCEPTION", model)
        return content, "ok"
    except requests.Timeout:
        _mark_failure("INCEPTION", model, HealthState.TIMEOUT, "Generation timed out")
        return "", "timeout"
    except requests.ConnectionError:
        _mark_failure("INCEPTION", model, HealthState.OFFLINE, "Endpoint unreachable")
        return "", "offline"
    except (requests.RequestException, ValueError) as exc:
        _mark_failure("INCEPTION", model, HealthState.SERVER_ERROR, type(exc).__name__)
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER: GEMINI (SECONDARY CLOUD)
# ══════════════════════════════════════════════════════════════════════════════

def _gemini_call(messages: list, max_tokens: int = 220, temperature: float = 0.4) -> tuple[str, str]:
    """Call Gemini text generation as secondary cloud fallback."""
    model = GEMINI_TEXT_MODEL
    if not GEMINI_API_KEY:
        _mark_failure("GEMINI", model, HealthState.AUTH_ERROR, "GEMINI_API_KEY is not configured")
        return "", "unavailable"
    if not provider_health.can_attempt("GEMINI", model):
        return "", "cooldown"
    system_parts = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    contents = []
    for message in messages:
        if message.get("role") == "system":
            continue
        role = "model" if message.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": str(message.get("content", ""))}]})
    body = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": GEMINI_API_KEY},
            json=body,
            timeout=(3.0, 15.0),
        )
        if response.status_code != 200:
            detail = _safe_error_detail(response)
            state = classify_http(response.status_code, detail)
            _mark_failure("GEMINI", model, state, detail, _retry_after(response))
            return "", state.value.lower()
        payload = response.json()
        content = "".join(
            str(part.get("text", ""))
            for candidate in payload.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
        ).strip()
        if not content:
            _mark_failure("GEMINI", model, HealthState.SERVER_ERROR, "Empty completion")
            return "", "empty"
        _mark_live("GEMINI", model)
        return content, "ok"
    except requests.Timeout:
        _mark_failure("GEMINI", model, HealthState.TIMEOUT, "Generation timed out")
        return "", "timeout"
    except requests.ConnectionError:
        _mark_failure("GEMINI", model, HealthState.OFFLINE, "Endpoint unreachable")
        return "", "offline"
    except (requests.RequestException, ValueError) as exc:
        _mark_failure("GEMINI", model, HealthState.SERVER_ERROR, type(exc).__name__)
        return "", "error"


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER: OLLAMA (AUTONOMOUS LOCAL FALLBACK)
# ══════════════════════════════════════════════════════════════════════════════

def _ollama_call(
    messages: list,
    max_tokens: int = 220,
    temperature: float = 0.4,
    *,
    fast_fail: bool = False,
    reasoning_budget: int | None = None,
) -> tuple[str, str]:
    """Call local Ollama model. Returns (content, status)."""
    registry = get_registry()
    if not _OLLAMA_READY:
        registry.set_status("OLLAMA", SubsystemState.DISABLED, "Ollama Python SDK is not available")
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.OFFLINE, "Ollama SDK is unavailable")
        return "", "unavailable"
    if not provider_health.can_attempt("OLLAMA", OLLAMA_MODEL):
        return "", "cooldown"

    discovery = discover_ollama()
    if discovery["service_state"] == "OFFLINE":
        # Attempt autonomous on-demand spawn if discovery failed
        try:
            from ollama_daemon import ensure_ollama_running
            if ensure_ollama_running(timeout=4.0):
                discovery = discover_ollama(force=True)
        except Exception:
            pass

    if discovery["service_state"] == "OFFLINE":
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.OFFLINE, discovery["detail"])
        return "", "offline"
    if discovery["model_state"] == "MISSING":
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.MODEL_UNAVAILABLE, discovery["detail"])
        return "", "model_missing"
    if discovery["model_state"] != "AVAILABLE":
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.SERVER_ERROR, discovery["detail"])
        return "", "discovery_error"
    try:
        started = time.perf_counter()
        resp = _get_ollama_client().chat(
            model=OLLAMA_MODEL,
            messages=messages,
            options={"num_predict": max(1, int(max_tokens)), "temperature": temperature},
            keep_alive=OLLAMA_KEEP_ALIVE,
        )
        message = resp.get("message", {}) if hasattr(resp, "get") else getattr(resp, "message", {})
        content = (message.get("content", "") if hasattr(message, "get") else getattr(message, "content", "")) or ""
        content = str(content).strip()
        if not content:
            _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.SERVER_ERROR, "Ollama returned no visible content")
            return "", "empty"
        elapsed = time.perf_counter() - started
        print(f"[PERF][brain] Ollama completed in {elapsed:.2f}s (max_tokens={max(1, int(max_tokens))})")
        _mark_live("OLLAMA", OLLAMA_MODEL)
        return content, "ok"
    except (TimeoutError,) as exc:
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.TIMEOUT, type(exc).__name__)
        return "", "timeout"
    except Exception as exc:
        err_msg = str(exc).lower()
        exc_name = type(exc).__name__.lower()
        if "timeout" in err_msg or "timeout" in exc_name:
            _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.TIMEOUT, "Local generation timed out")
            return "", "timeout"
        if "model" in err_msg and ("not found" in err_msg or "missing" in err_msg):
            _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.MODEL_UNAVAILABLE, f"Exact model {OLLAMA_MODEL} is unavailable")
            return "", "model_missing"
        if "connection" in err_msg or "refused" in err_msg or "connect" in err_msg:
            _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.OFFLINE, "Local daemon unreachable")
            return "", "offline"
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.SERVER_ERROR, type(exc).__name__)
        return "", "error"


def local_brain_request(prompt: str, *, max_tokens: int = 64) -> tuple[str, str]:
    """Run the real local-brain path without cloud routing."""
    reset_last_provider()
    messages = [
        {"role": "system", "content": "Return only the concise final answer. Do not expose reasoning."},
        {"role": "user", "content": str(prompt)},
    ]
    return _ollama_call(messages, max_tokens=max_tokens, temperature=0.0)


def local_vision_request(prompt: str, image_b64: str, *, max_tokens: int = 320) -> tuple[str, str]:
    """Run JARVIS vision through Ollama provider path."""
    registry = get_registry()
    reset_last_provider()
    if not isinstance(image_b64, str) or not image_b64.strip():
        registry.set_capability_evidence(
            "OLLAMA_VISION", EvidenceLevel.BROKEN, "Image payload is empty",
            source="local vision validation",
        )
        return "", "invalid_image"
    if not _OLLAMA_READY:
        registry.set_capability_evidence(
            "OLLAMA_VISION", EvidenceLevel.BLOCKED, "Ollama Python SDK is unavailable",
            source="local vision provider",
        )
        return "", "unavailable"
    if not provider_health.can_attempt("OLLAMA", OLLAMA_MODEL):
        return "", "cooldown"
    discovery = discover_ollama()
    if discovery["service_state"] == "OFFLINE":
        registry.set_capability_evidence("OLLAMA_VISION", EvidenceLevel.BLOCKED, discovery["detail"], source="Ollama discovery")
        return "", "offline"
    if discovery["model_state"] == "MISSING":
        registry.set_capability_evidence("OLLAMA_VISION", EvidenceLevel.BLOCKED, discovery["detail"], source="Ollama discovery")
        return "", "model_missing"
    if discovery["model_state"] != "AVAILABLE":
        registry.set_capability_evidence("OLLAMA_VISION", EvidenceLevel.BROKEN, discovery["detail"], source="Ollama discovery")
        return "", "discovery_error"
    try:
        resp = _get_ollama_client().chat(
            model=OLLAMA_MODEL,
            messages=[{
                "role": "user",
                "content": str(prompt or "Describe this image accurately."),
                "images": [image_b64],
            }],
            options={
                "num_predict": max(512, min(int(max_tokens), 768)),
                "temperature": 0.0,
            },
            keep_alive=OLLAMA_KEEP_ALIVE,
        )
        message = resp.get("message", {}) if hasattr(resp, "get") else getattr(resp, "message", {})
        content = (message.get("content", "") if hasattr(message, "get") else getattr(message, "content", "")) or ""
        content = str(content).strip()
        if not content:
            registry.set_capability_evidence(
                "OLLAMA_VISION", EvidenceLevel.BROKEN,
                "Ollama vision returned empty content", source="local vision generation",
            )
            return "", "empty"
        _mark_live("OLLAMA", OLLAMA_MODEL)
        registry.set_capability_evidence(
            "OLLAMA_VISION", EvidenceLevel.LIVE,
            f"Multimodal generation succeeded ({OLLAMA_MODEL})", source="local vision generation",
        )
        _route_local.provider_model = f"OLLAMA: {OLLAMA_MODEL}"
        return content, "ok"
    except Exception as exc:
        text = str(exc).lower()
        status = "timeout" if "timeout" in text or "timeout" in type(exc).__name__.lower() else "error"
        evidence = EvidenceLevel.BLOCKED if status == "timeout" else EvidenceLevel.BROKEN
        registry.set_capability_evidence(
            "OLLAMA_VISION", evidence, f"{type(exc).__name__}: {str(exc)[:140]}",
            source="local vision generation",
        )
        return "", status


# ── Groq & OpenRouter legacy stubs ───────────────────────────────────────────

def _groq_call(messages: list, model: str, max_tokens: int = 220, temperature: float = 0.4) -> tuple[str, str]:
    return "", "unavailable"


def _openrouter_call(messages: list, model: str, max_tokens: int = 220) -> tuple[str, str]:
    return "", "unavailable"


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

    # Try APInex fast model first, then local/cloud fallbacks
    raw, status = _apinex_call(
        messages, APINEX_FAST_MODEL, max_tokens=120, temperature=0.0,
        response_format={"type": "json_object"},
    )
    if status != "ok":
        raw, status = _ollama_call(messages, max_tokens=120, temperature=0.0)
    if status != "ok":
        raw, status = _nvidia_call(
            messages, NVIDIA_FAST_MODEL, max_tokens=120, temperature=0.0,
            response_format={"type": "json_object"},
        )
    if status != "ok":
        raw, status = _gemini_call(messages, max_tokens=120, temperature=0.0)

    if not raw:
        return {"type": "notification", "summary": text[:80], "action": "notify"}

    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(clean)
        if "type" in result and "summary" in result:
            return result
    except Exception:
        print(f"[DEBUG][brain] analyze JSON parse failed, raw: {raw!r}")

    return {"type": "notification", "summary": raw[:80], "action": "notify"}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: MAIN LLM CALL WITH TIERED DUAL-CLOUD & AUTONOMOUS LOCAL FALLBACK
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
    """Multi-tier cognitive router:
    1. System-1 Fast Reflex: APInex Flash (free/deepseek-v4.1-flash) for rapid voice/dialogue.
    2. System-2 Deep Reasoning: APInex Pro (free/deepseek-v4-pro-0813) for complex deliberation.
    3. Cloud Retry: APInex Fallback (free/glm-5.3-flash) on 429/500/timeout.
    4. Autonomous Local Fallback: Ollama dynamically spawned & VRAM reclaimed on cloud recovery.
    5. Secondary Cloud: Inception Mercury-2.5, then NVIDIA / Gemini.
    """
    reset_last_provider()
    is_complex = _is_complex_query(query) or model_type == "reasoning"

    system = _build_system_prompt(
        context=context, sentiment=sentiment, allow_actions=allow_actions,
        profile_context=profile_context, detailed=is_complex,
    )
    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": query})

    max_tokens = 400 if is_complex else 220

    if is_complex:
        # ── System-2 Deep Reasoning: APInex Pro (free/deepseek-v4-pro-0813) ────
        print(f"[DEBUG][brain] hybrid route: SYSTEM-2 DEEP REASONING -> APINEX {APINEX_PRO_MODEL}")
        result, status = _apinex_call(
            messages, APINEX_PRO_MODEL, max_tokens=max_tokens,
            timeout=(APINEX_CONNECT_TIMEOUT, APINEX_PRO_REQUEST_TIMEOUT),
        )
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="APINEX")

        # Cloud Retry: Fallback model
        print(f"[DEBUG][brain] APInex Pro unavailable ({status}); retrying with cloud fallback {APINEX_FALLBACK_MODEL}")
        result, status = _apinex_call(messages, APINEX_FALLBACK_MODEL, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="APINEX")

        # Autonomous Local Fallback: Spawns Ollama silently on cloud outage
        print(f"[DEBUG][brain] APInex cloud unavailable; activating autonomous Ollama daemon...")
        try:
            from ollama_daemon import get_ollama_manager
            get_ollama_manager().enter_fallback_mode(
                ping_url=f"{APINEX_BASE_URL}/models",
                api_key=APINEX_API_KEY,
            )
        except Exception as e:
            print(f"[DEBUG][brain] Ollama lifecycle trigger exception: {e}")

        result, status = _ollama_call(messages, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="OLLAMA")

        # Secondary Cloud Providers
        print(f"[DEBUG][brain] Ollama unavailable ({status}); trying Inception Mercury-2.5")
        result, status = _inception_call(messages, INCEPTION_MODEL, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="INCEPTION")

        print(f"[DEBUG][brain] Inception Mercury unavailable ({status}); trying NVIDIA reasoning")
        result, status = _nvidia_call(messages, NVIDIA_REASONING_MODEL, max_tokens=max_tokens, reasoning=True)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="NVIDIA")

        result, status = _gemini_call(messages, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="GEMINI")

    else:
        # ── System-1 Fast Reflex: APInex Flash (free/deepseek-v4.1-flash) ──────
        print(f"[DEBUG][brain] hybrid route: SYSTEM-1 FAST REFLEX -> APINEX {APINEX_FAST_MODEL}")
        result, status = _apinex_call(
            messages, APINEX_FAST_MODEL, max_tokens=max_tokens,
            timeout=(APINEX_CONNECT_TIMEOUT, APINEX_FAST_REQUEST_TIMEOUT),
        )
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="APINEX")

        # Cloud Retry: Fallback model
        print(f"[DEBUG][brain] APInex Flash unavailable ({status}); retrying with cloud fallback {APINEX_FALLBACK_MODEL}")
        result, status = _apinex_call(messages, APINEX_FALLBACK_MODEL, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="APINEX")

        # Autonomous Local Fallback: Spawns Ollama silently on cloud outage
        print(f"[DEBUG][brain] APInex cloud unavailable; activating autonomous Ollama daemon...")
        try:
            from ollama_daemon import get_ollama_manager
            get_ollama_manager().enter_fallback_mode(
                ping_url=f"{APINEX_BASE_URL}/models",
                api_key=APINEX_API_KEY,
            )
        except Exception as e:
            print(f"[DEBUG][brain] Ollama lifecycle trigger exception: {e}")

        result, status = _ollama_call(messages, max_tokens=max_tokens, fast_fail=True)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="OLLAMA")

        # Secondary Cloud Providers
        print(f"[DEBUG][brain] local Ollama unavailable ({status}); trying Inception Mercury-2.5")
        result, status = _inception_call(messages, INCEPTION_MODEL, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="INCEPTION")

        print(f"[DEBUG][brain] Inception Mercury unavailable ({status}); trying NVIDIA Lightning")
        result, status = _nvidia_call(messages, NVIDIA_FAST_MODEL, max_tokens=max_tokens, reasoning=False)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="NVIDIA")

        result, status = _gemini_call(messages, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="GEMINI")

    print("[DEBUG][brain] all configured brain providers unavailable")
    return "I can't reach an AI provider right now, but local commands are still available."


def _finalize_result(
    result: str,
    sentiment: dict | None,
    allow_actions: bool,
    query: str,
    original_messages: list | None = None,
    preferred_provider: str | None = None,
) -> str:
    """Filter reasoning leaks, strip scratchpads, and sanitize result through speech cleaner."""
    result = _strip_scratchpad(result)
    if _looks_like_reasoning(result):
        print("[DEBUG][brain] reasoning leak detected, retrying clean")
        clean_messages = [dict(message) for message in (original_messages or [])]
        if not clean_messages:
            clean_messages = [
                {"role": "system", "content": _build_system_prompt(
                    sentiment=sentiment, allow_actions=allow_actions,
                )},
                {"role": "user", "content": query},
            ]
        clean_messages[0]["content"] += "\nReturn only the final answer; do not expose reasoning."
        retry, status = "", "offline"

        if preferred_provider == "APINEX":
            retry, status = _apinex_call(clean_messages, APINEX_FAST_MODEL, max_tokens=120)
        elif preferred_provider == "OLLAMA":
            retry, status = _ollama_call(clean_messages, max_tokens=120)
        elif preferred_provider == "GEMINI":
            retry, status = _gemini_call(clean_messages, max_tokens=120)
        elif preferred_provider == "NVIDIA":
            retry, status = _nvidia_call(clean_messages, NVIDIA_FAST_MODEL, max_tokens=120)

        if status != "ok":
            retry, status = _apinex_call(clean_messages, APINEX_FAST_MODEL, max_tokens=120)
        if status != "ok":
            retry, status = _ollama_call(clean_messages, max_tokens=120)
        if status != "ok":
            retry, status = _nvidia_call(clean_messages, NVIDIA_FAST_MODEL, max_tokens=120)
        if status != "ok":
            retry, status = _gemini_call(clean_messages, max_tokens=120)

        if status == "ok" and retry:
            result = _strip_scratchpad(retry)

    cleaned = _strip_scratchpad(result)
    return cleaned or "I couldn't figure that out."
