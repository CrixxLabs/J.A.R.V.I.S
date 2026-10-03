"""Speech Output Cleaner & JSON Shield for J.A.R.V.I.S. — MARK VIII.

Ensures Pocket-TTS / Edge-TTS / SAPI never speak raw JSON plan fragments,
markdown syntax, or code tokens out loud. Extracts purely the conversational
response from complex/mixed LLM outputs.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

# Conversational keys in order of priority
_CONVERSATIONAL_KEYS = (
    "response",
    "message",
    "speak",
    "spoken",
    "text",
    "reply",
    "content",
    "narration",
    "say",
    "speech",
    "summary",
    "answer",
)

# Action leak & dangling artifact patterns
_LEAKED_PATTERNS = [
    re.compile(r"action_use_[a-z0-9_]+", re.IGNORECASE),
    re.compile(r"\baction_[a-z0-9_]+\b", re.IGNORECASE),
    re.compile(r"\bcmd_[a-z0-9_]+\b", re.IGNORECASE),
    re.compile(r"\btask_[a-z0-9_]+\b", re.IGNORECASE),
    re.compile(r"\{\s*['\"]?action['\"]?\s*:[^}]*\}?", re.IGNORECASE),
    re.compile(r"['\"]?action['\"]?\s*:\s*\{[^}]*\}?", re.IGNORECASE),
]


def extract_conversational_payload(raw: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Parse raw LLM output into an optional action dict and sanitized conversational text.

    If the text contains JSON, it extracts the action dict and the conversational field.
    If no conversational field is found or the JSON was purely an action payload,
    the technical JSON block is stripped so it is never spoken aloud.
    """
    if not raw or not isinstance(raw, str):
        return None, ""

    text = raw.strip()
    action_dict: Optional[Dict[str, Any]] = None
    spoken_text = ""

    # Check for markdown code fences around JSON
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    candidate_json_str = fence_match.group(1).strip() if fence_match else None

    # Check for direct JSON block
    if not candidate_json_str:
        brace_match = re.search(r"(\{[\s\S]*\})", text)
        if brace_match:
            candidate_json_str = brace_match.group(1).strip()

    if candidate_json_str:
        try:
            parsed = json.loads(candidate_json_str)
            if isinstance(parsed, dict):
                # Check for action dictionary
                if "action" in parsed:
                    if isinstance(parsed["action"], dict):
                        action_dict = parsed["action"]
                    elif isinstance(parsed["action"], str):
                        action_dict = parsed
                elif "tool" in parsed or "type" in parsed:
                    action_dict = parsed

                # Check for conversational field
                for key in _CONVERSATIONAL_KEYS:
                    val = parsed.get(key)
                    if isinstance(val, str) and val.strip():
                        spoken_text = val.strip()
                        break

                # If no conversational field inside JSON, remove the JSON block from text
                if not spoken_text:
                    if fence_match:
                        text = text.replace(fence_match.group(0), " ").strip()
                    else:
                        text = text.replace(candidate_json_str, " ").strip()
                else:
                    text = spoken_text
        except Exception:
            # Try partial / broken JSON regex extraction for conversational key
            conv_match = re.search(
                r'["\'](?:' + "|".join(_CONVERSATIONAL_KEYS) + r')["\']\s*:\s*["\']([^"\'\n\r\}]+)',
                text,
                re.IGNORECASE,
            )
            if conv_match:
                spoken_text = conv_match.group(1).strip()
                text = spoken_text
            else:
                # Malformed JSON without clear conversational field -> strip out JSON-like fragments
                text = re.sub(r"\{[\s\S]*?\}", " ", text).strip()

    # Clean the remaining text
    clean = clean_speech_text(text)
    return action_dict, clean


def clean_speech_text(text: str) -> str:
    """Sanitize text to pure conversational speech for Pocket-TTS / Edge-TTS.

    Never outputs raw JSON braces, action identifiers, markdown asterisks,
    or cut token fragments.
    """
    if not text or not isinstance(text, str):
        return ""

    cleaned = text.strip()

    # 1. Strip raw code fences (both enclosed and unclosed/trailing)
    cleaned = re.sub(r"```(?:json|python|bash|sh|cmd|powershell|[a-z0-9_]+)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"`+", " ", cleaned)

    # 2. If it's a raw JSON dict string or has conversational key
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                for key in _CONVERSATIONAL_KEYS:
                    val = parsed.get(key)
                    if isinstance(val, str) and val.strip():
                        return clean_speech_text(val)
                # If only technical action keys exist, return empty string
                return ""
        except Exception:
            pass

    # Partial / truncated JSON key extraction if present
    if "{" in cleaned or '"action":' in cleaned or "'action':" in cleaned:
        for key in _CONVERSATIONAL_KEYS:
            match = re.search(
                r'["\']?' + key + r'["\']?\s*:\s*["\']([^"\'\n\r\}]+)',
                cleaned,
                re.IGNORECASE,
            )
            if match:
                cleaned = match.group(1).strip()
                break

    # 3. Remove leaked action patterns
    for pattern in _LEAKED_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    # 4. Remove markdown headers, list markers, links, tags
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)

    # 5. Remove bold/italics markers and dangling asterisks/underscores
    cleaned = re.sub(r"\*{1,3}", "", cleaned)
    cleaned = re.sub(r"(?<!\w)_+|_+(?!\w)", " ", cleaned)

    # 6. Strip raw JSON tokens/syntax remnants
    cleaned = re.sub(r"[{}\[\]]", " ", cleaned)
    cleaned = re.sub(r'["\']\s*:\s*["\']', " ", cleaned)
    cleaned = re.sub(r'["\']\s*:\s*\{?', " ", cleaned)

    # 7. Strip unwanted prefix tags like [TTS], [Planner], etc.
    cleaned = re.sub(r"^\[[A-Za-z0-9_\-\s]+\]\s*", "", cleaned)

    # 8. Normalize space before punctuation marks (. , ! ? : ;)
    cleaned = re.sub(r"\s+([,.!?:;])", r"\1", cleaned)

    # 9. Normalize whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 10. If only punctuation or single digits remain, return empty
    if cleaned in ("", ".", ",", "!", "?", "-", ":", ";", "'", '"'):
        return ""

    # 11. Strip leading dangling punctuation
    cleaned = re.sub(r"^[\s,.:;!?-]+", "", cleaned).strip()

    return cleaned
