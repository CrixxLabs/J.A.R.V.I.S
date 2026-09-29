# brain.py — AI Brain (Multi-Model Router)
# Single responsibility: all LLM calls live here.
# Returns strings or dicts. Never speaks, never prints user-facing output.
#
# Routing:
#   Routine: Ollama JARVIS/Ministral -> NVIDIA Lightning -> Gemini
#   Complex: NVIDIA reasoning -> Ollama JARVIS/Ministral -> Gemini

import json
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv

# Reliability imports
import status_registry
from status_registry import EvidenceLevel, SubsystemState, get_registry
import error_handler
from provider_health import HealthState, classify_http, health as provider_health

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
NVIDIA_API_KEY     = os.getenv("NVIDIA_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

# ── Model configuration ───────────────────────────────────────────────────────
REQUIRED_OLLAMA_MODEL = "jarvis:latest"
CONFIGURED_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", REQUIRED_OLLAMA_MODEL).strip()
# MARK VII's local route is intentionally pinned. Environment drift must not
# silently select an obsolete or merely similar model.
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
    """Publish successful provider use for routes implemented outside brain.py.

    Vision owns its Gemini request implementation, but provider attribution is
    process-wide.  Keeping this small public boundary prevents a successful
    vision fallback from returning with an UNKNOWN last-provider value.
    """
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
# COMPLEXITY DETECTION — decides if query needs the big brain (Groq)
# ══════════════════════════════════════════════════════════════════════════════

_COMPLEX_TRIGGERS = (
    # Explicit deep-analysis / engineering work.  Ordinary explanation phrases
    # intentionally stay local; Ministral handles routine "how/why/explain" requests.
    "analyze", "analyse", "compare", "contrast", "trade-off", "trade off",
    "tradeoff", "evaluate", "assess",
    "debug", "refactor", "optimize this", "review this code",
    "architecture", "architectural", "system design", "root cause",
    "implement", "write a function", "write a script", "write code",
    "algorithm for", "create a class",
    "strategy for", "roadmap", "multi-stage", "multi step", "multi-step",
    # Manual override.
    "think deeper", "think harder", "use big brain", "use nemotron",
    "deep reasoning", "extreme reasoning", "detailed technical analysis",
)




def _is_complex_query(query: str) -> bool:
    """Return True only when the request actually asks for deeper reasoning.

    Length alone is deliberately not a complexity signal: a long casual sentence
    should stay local, while explicit debugging/architecture/analysis requests can
    escalate to Nemotron.
    """
    if not query:
        return False
    lowered = query.lower().strip()
    return any(trigger in lowered for trigger in _COMPLEX_TRIGGERS)


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
                "Your routine local brain is Ministral 3 3B through Ollama, with NVIDIA Nemotron for complex reasoning and Gemini as a cloud fallback. "
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
        "Your routine local brain is Ministral 3 3B through Ollama, with NVIDIA Nemotron for complex reasoning and Gemini as a cloud fallback. "
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
            "completed, opened, played, sent, changed, generated, saved, or was observed until runtime/tool evidence confirms it. "
            "Never invent filenames, screenshots, generated-media paths, browser tabs, opened windows, or tool results. "
            "If no tool/action was executed, do not describe a tool action as if it happened."
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


def _nvidia_call(messages: list, model: str, max_tokens: int = 220,
                 temperature: float = 0.4, reasoning: bool = False,
                 response_format: dict | None = None) -> tuple:
    """Call NVIDIA's OpenAI-compatible NIM endpoint."""
    if not NVIDIA_API_KEY:
        _mark_failure("NVIDIA", model, HealthState.AUTH_ERROR, "NVIDIA_API_KEY is not configured")
        return "", "unavailable"
    if not provider_health.can_attempt("NVIDIA", model):
        return "", "cooldown"
    body = {
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
            timeout=(3.0, 18.0),
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


def _gemini_call(messages: list, max_tokens: int = 220, temperature: float = 0.4) -> tuple:
    """Call Gemini text generation as the secondary cloud provider."""
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


def _ollama_call(messages: list, max_tokens: int = 220, temperature: float = 0.4, *, fast_fail: bool = False, reasoning_budget: int | None = None) -> tuple:
    """Call the local JARVIS/Ministral model through Ollama. Returns (content, status).

    ``reasoning_budget`` is retained for backward/test compatibility but is intentionally
    ignored: Ministral does not use Qwen's private thinking-channel budget.
    """
    registry = get_registry()
    if not _OLLAMA_READY:
        registry.set_status("OLLAMA", SubsystemState.DISABLED, "Ollama Python SDK is not available")
        _mark_failure("OLLAMA", OLLAMA_MODEL, HealthState.OFFLINE, "Ollama SDK is unavailable")
        return "", "unavailable"
    if not provider_health.can_attempt("OLLAMA", OLLAMA_MODEL):
        return "", "cooldown"
    discovery = discover_ollama()
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

def local_brain_request(prompt: str, *, max_tokens: int = 64) -> tuple:
    """Run the real MARK VII local-brain path without cloud routing."""
    reset_last_provider()
    messages = [
        {"role": "system", "content": "Return only the concise final answer. Do not expose reasoning."},
        {"role": "user", "content": str(prompt)},
    ]
    return _ollama_call(messages, max_tokens=max_tokens, temperature=0.0)


def local_vision_request(prompt: str, image_b64: str, *, max_tokens: int = 320) -> tuple:
    """Run JARVIS/Ministral vision through MARK VII's bounded Ollama provider path.

    Returns ``(content, status)`` and deliberately ignores Ollama's separate
    thinking channel.  The image must already be base64 encoded.
    """
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

    # Classification is routine work: keep it local-first.  Escalate only if
    # the local provider is unavailable or returns unusable output.
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
    """Smart hybrid routing: local JARVIS/Ministral for routine work, Nemotron for complex work."""
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
    lowered = (query or "").lower()
    deep_markers = ("use ultra", "deep reasoning", "extreme reasoning")

    if is_complex:
        if any(marker in lowered for marker in deep_markers):
            nvidia_model = NVIDIA_DEEP_MODEL
        else:
            nvidia_model = NVIDIA_REASONING_MODEL
        print(f"[DEBUG][brain] hybrid route: COMPLEX -> NVIDIA {nvidia_model}")
        result, status = _nvidia_call(messages, nvidia_model, max_tokens=max_tokens, reasoning=True)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="NVIDIA")
        # Complex fallback: local first so JARVIS still works offline, then Gemini.
        print(f"[DEBUG][brain] NVIDIA unavailable ({status}); trying local Ollama")
        result, status = _ollama_call(messages, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="OLLAMA")
        result, status = _gemini_call(messages, max_tokens=max_tokens)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="GEMINI")
    else:
        print(f"[DEBUG][brain] hybrid route: ROUTINE -> OLLAMA {REQUIRED_OLLAMA_MODEL}")
        result, status = _ollama_call(messages, max_tokens=max_tokens, fast_fail=True)
        if status == "ok" and result:
            return _finalize_result(result, sentiment, allow_actions, query, messages, preferred_provider="OLLAMA")
        print(f"[DEBUG][brain] local Ollama unavailable ({status}); trying NVIDIA Lightning")
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
    """Filter reasoning leaks and clean result."""
    if _looks_like_reasoning(result):
        print(f"[DEBUG][brain] reasoning leak detected, retrying clean")
        # Preserve the exact context/personality contract across the cleanup
        # fallback instead of rebuilding a stripped system prompt.
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
        if preferred_provider == "OLLAMA":
            retry, status = _ollama_call(clean_messages, max_tokens=120)
        elif preferred_provider == "GEMINI":
            retry, status = _gemini_call(clean_messages, max_tokens=120)
        elif preferred_provider == "NVIDIA":
            retry, status = _nvidia_call(clean_messages, NVIDIA_FAST_MODEL, max_tokens=120)
        if status != "ok":
            retry, status = _ollama_call(clean_messages, max_tokens=120)
        if status != "ok":
            retry, status = _nvidia_call(clean_messages, NVIDIA_FAST_MODEL, max_tokens=120)
        if status != "ok":
            retry, status = _gemini_call(clean_messages, max_tokens=120)
        if status == "ok" and retry:
            result = retry

    return (result or "").strip() or "I couldn't figure that out."
