"""Privacy-aware, provider-independent personality retrieval for MARK VII.

Approved JSONL profile data is read from Data/personality.  The existing
memory.py and user_profile.py stores remain responsible for episodic memory and
the legacy mutable user profile respectively; this module only owns personality
retrieval and explicit correction feedback.
"""

from __future__ import annotations

import datetime as _datetime
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "Data" / "personality"

PROFILE_FILES = (
    "identity.jsonl",
    "preferences.jsonl",
    "communication.jsonl",
    "technical_preferences.jsonl",
    "projects.jsonl",
)
EXAMPLE_FILE = "examples.jsonl"
RULE_FILE = "behavior_rules.jsonl"
CORRECTION_FILE = "corrections.jsonl"
PRIVACY_CLASSES = {"normal", "private", "sensitive"}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_'-]*", re.IGNORECASE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for",
    "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "that", "the", "this", "to", "user", "want", "what", "when", "with",
    "you", "your",
}


def _tokens(value: Any) -> set[str]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return {token.lower() for token in _TOKEN_RE.findall(text) if token.lower() not in _STOP_WORDS}


def _entry_text(entry: dict) -> str:
    fields = (
        "fact", "rule", "trait", "context", "name", "role", "status",
        "preferred_behavior", "preferred_pattern", "why", "good",
        "derived_rule", "feedback", "scope", "jarvis_behavior", "do",
        "avoid", "principles",
    )
    parts = []
    for field in fields:
        value = entry.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
        elif value is not None and str(value).strip():
            parts.append(str(value))
    return " ".join(parts).strip()


def _clamp_confidence(value: Any, default: float = 0.7) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LoadIssue:
    file: str
    line: int
    reason: str


class ProfileStore:
    """Load validated JSONL entries and append explicit correction records."""

    def __init__(
        self,
        data_dir: str | os.PathLike[str] = PROFILE_DIR,
        learned_dir: str | os.PathLike[str] | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.learned_dir = Path(learned_dir) if learned_dir is not None else self.data_dir / "learned"
        self._write_lock = threading.Lock()
        self.last_issues: list[LoadIssue] = []

    def _load_file(
        self,
        filename: str,
        kind: str,
        *,
        directory: Path | None = None,
        missing_ok: bool = False,
    ) -> list[dict]:
        root = directory or self.data_dir
        path = root / filename
        issue_filename = str(path.relative_to(self.data_dir)) if path.is_relative_to(self.data_dir) else str(path)
        entries: list[dict] = []
        if not path.exists():
            if not missing_ok:
                self.last_issues.append(LoadIssue(issue_filename, 0, "missing file"))
            return entries

        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            self.last_issues.append(LoadIssue(issue_filename, 0, type(exc).__name__))
            return entries

        for line_number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                self.last_issues.append(LoadIssue(issue_filename, line_number, "malformed JSON"))
                continue
            if not isinstance(entry, dict):
                self.last_issues.append(LoadIssue(issue_filename, line_number, "entry is not an object"))
                continue
            if not isinstance(entry.get("id"), str) or not entry["id"].strip():
                self.last_issues.append(LoadIssue(issue_filename, line_number, "missing id"))
                continue
            if not isinstance(entry.get("type"), str) or not entry["type"].strip():
                self.last_issues.append(LoadIssue(issue_filename, line_number, "missing type"))
                continue
            if kind == "correction":
                correction_fields = (
                    "timestamp", "context", "original_response", "feedback",
                    "derived_rule", "scope",
                )
                if any(not isinstance(entry.get(field), str) for field in correction_fields):
                    self.last_issues.append(LoadIssue(issue_filename, line_number, "invalid correction schema"))
                    continue
                if "active" in entry and not isinstance(entry["active"], bool):
                    self.last_issues.append(LoadIssue(issue_filename, line_number, "invalid correction schema"))
                    continue
            if not _entry_text(entry):
                self.last_issues.append(LoadIssue(issue_filename, line_number, "missing usable content"))
                continue
            privacy = str(entry.get("privacy", "normal")).lower()
            if privacy not in PRIVACY_CLASSES:
                self.last_issues.append(LoadIssue(issue_filename, line_number, "invalid privacy class"))
                continue
            normalized = dict(entry)
            normalized["privacy"] = privacy
            normalized["confidence"] = _clamp_confidence(entry.get("confidence"))
            normalized["_source_file"] = issue_filename
            normalized["_kind"] = kind
            entries.append(normalized)
        return entries

    def load(self) -> dict[str, list[dict]]:
        self.last_issues = []
        return {
            "profile": [
                entry
                for filename in PROFILE_FILES
                for entry in self._load_file(filename, "profile")
            ],
            "examples": self._load_file(EXAMPLE_FILE, "example"),
            "rules": self._load_file(RULE_FILE, "rule"),
            "corrections": (
                self._load_file(CORRECTION_FILE, "correction")
                + self._load_file(
                    CORRECTION_FILE,
                    "correction",
                    directory=self.learned_dir,
                    missing_ok=True,
                )
            ),
        }

    def append_correction(self, correction: dict) -> dict:
        validated = _normalize_correction(correction)
        path = self.learned_dir / CORRECTION_FILE
        self.learned_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(payload + "\n")
        return validated


def _normalize_correction(correction: dict) -> dict:
    if not isinstance(correction, dict):
        raise ValueError("correction must be an object")
    required = ("timestamp", "context", "original_response", "feedback", "derived_rule", "scope")
    if any(not isinstance(correction.get(field), str) for field in required):
        raise ValueError("correction has missing or invalid fields")
    if not correction["derived_rule"].strip() or not correction["feedback"].strip():
        raise ValueError("correction feedback and rule cannot be empty")
    result = dict(correction)
    result.setdefault("id", "correction." + _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f"))
    result["type"] = "explicit_correction"
    result["confidence"] = _clamp_confidence(result.get("confidence"), 0.95)
    result["active"] = bool(result.get("active", True))
    privacy = str(result.get("privacy", "normal")).lower()
    result["privacy"] = privacy if privacy in PRIVACY_CLASSES else "private"
    return result


def detect_feedback(
    user_text: str,
    *,
    context: str = "conversation",
    original_response: str = "",
) -> dict | None:
    """Recognize deliberate preference feedback without classifying general emotion."""
    raw = (user_text or "").strip()
    lowered = re.sub(r"\s+", " ", raw.lower())
    derived_rule = ""
    scope = "communication"
    confidence = 0.98
    privacy = "normal"

    if re.search(r"\b(be shorter|keep (?:it|your answers?) shorter|too (?:long|wordy))\b", lowered):
        derived_rule = "Keep responses shorter and lead with the result."
    elif re.search(r"\b(don't|do not) talk to me like that\b", lowered):
        derived_rule = "Avoid the tone and style used in the referenced response."
    elif re.search(r"\b(don't|do not) say that again\b", lowered):
        derived_rule = "Do not repeat the phrasing used in the referenced response."
    elif re.search(r"\b(that(?:'s| is) exactly how i want you to answer|answer like that)\b", lowered):
        derived_rule = "Continue using the concise response style shown in the referenced response."
        confidence = 0.96
    else:
        match = re.search(r"\bremember that i prefer\s+(.+)$", raw, re.IGNORECASE)
        if match:
            preference = match.group(1).strip().rstrip(".!?")
            if not preference:
                return None
            derived_rule = f"The user explicitly prefers {preference}."
            scope = "preference"
            sensitive_terms = {
                "health", "medical", "diagnosis", "medication", "legal", "lawsuit",
                "bank", "account number", "credit card", "financial", "intimate",
                "family conflict",
            }
            privacy = "sensitive" if any(term in preference.lower() for term in sensitive_terms) else "normal"
            if "private" in lowered or "don't share" in lowered or "do not share" in lowered:
                privacy = "private"
        else:
            return None

    return _normalize_correction({
        "timestamp": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "context": (context or "conversation")[:500],
        "original_response": (original_response or "")[:2000],
        "feedback": raw[:1000],
        "derived_rule": derived_rule,
        "confidence": confidence,
        "scope": scope,
        "active": True,
        "privacy": privacy,
    })


class PersonalityEngine:
    """Retrieve and assemble a compact personality contract with an audit trail."""

    def __init__(self, store: ProfileStore | None = None):
        self.store = store or ProfileStore()
        self._audit_local = threading.local()

    @staticmethod
    def _allowed(entry: dict, provider: str, allow_restricted: bool) -> bool:
        if allow_restricted:
            return True
        privacy = entry.get("privacy", "normal")
        return provider.lower() not in {"cloud", "nvidia", "gemini"} or privacy == "normal"

    @staticmethod
    def _rank(entries: Iterable[dict], query_text: str) -> list[tuple[float, dict, str]]:
        candidates = list(entries)
        if not candidates:
            return []
        query_tokens = _tokens(query_text)
        documents = [_tokens(_entry_text(entry)) for entry in candidates]
        document_count = len(documents)
        document_frequency: dict[str, int] = {}
        for tokens in documents:
            for token in tokens:
                document_frequency[token] = document_frequency.get(token, 0) + 1

        def weights(tokens: set[str]) -> dict[str, float]:
            return {
                token: math.log((document_count + 1) / (document_frequency.get(token, 0) + 1)) + 1.0
                for token in tokens
            }

        query_vector = weights(query_tokens)
        query_norm = math.sqrt(sum(value * value for value in query_vector.values())) or 1.0
        ranked = []
        for entry, tokens in zip(candidates, documents):
            vector = weights(tokens)
            norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
            cosine = sum(query_vector.get(token, 0.0) * value for token, value in vector.items()) / (query_norm * norm)
            confidence = _clamp_confidence(entry.get("confidence"))
            generic_bonus = 0.08 if entry.get("context") in {"all", "default"} else 0.0
            score = cosine + generic_bonus + confidence * 0.04
            reason = "lexical/TF-IDF match" if cosine > 0 else "high-confidence profile fallback"
            ranked.append((score, entry, reason))
        return sorted(ranked, key=lambda item: (item[0], item[1].get("confidence", 0)), reverse=True)

    def assemble(
        self,
        user_text: str,
        *,
        runtime_state: dict | None = None,
        planner_intent: str = "",
        provider: str = "cloud",
        allow_restricted: bool = False,
        inferred_preferences: Iterable[str] | None = None,
        min_entries: int = 3,
        max_entries: int = 6,
        max_examples: int = 2,
        max_corrections: int = 3,
        max_chars: int = 3200,
        max_tokens: int = 800,
    ) -> str:
        min_entries = max(0, min(min_entries, 8))
        max_entries = max(min_entries, min(max_entries, 8))
        max_examples = max(0, min(max_examples, 3))
        max_corrections = max(0, min(max_corrections, 3))
        max_chars = max(160, min(max_chars, max(40, max_tokens) * 4))
        loaded = self.store.load()
        query_text = " ".join(filter(None, (
            user_text,
            planner_intent,
            json.dumps(runtime_state or {}, ensure_ascii=False, sort_keys=True),
        )))
        excluded: list[str] = []

        def permitted(entries: Iterable[dict]) -> list[dict]:
            result = []
            for entry in entries:
                if self._allowed(entry, provider, allow_restricted):
                    result.append(entry)
                else:
                    excluded.append(entry.get("id", "unknown"))
            return result

        profile_ranked = self._rank(permitted(loaded["profile"]), query_text)
        selected_profile = profile_ranked[:max_entries]
        if len(selected_profile) < min_entries:
            selected_profile = profile_ranked[:min_entries]

        example_ranked = self._rank(permitted(loaded["examples"]), query_text)
        selected_examples = [item for item in example_ranked if item[0] >= 0.10][:max_examples]

        active_corrections = [entry for entry in permitted(loaded["corrections"]) if entry.get("active", True)]
        correction_ranked = self._rank(active_corrections, query_text)
        correction_ranked.sort(
            key=lambda item: (item[1].get("confidence", 0), item[1].get("timestamp", ""), item[0]),
            reverse=True,
        )
        selected_corrections = correction_ranked[:max_corrections]

        rules = sorted(
            permitted(loaded["rules"]),
            key=lambda entry: entry.get("priority", 0),
            reverse=True,
        )
        lines = [
            "JARVIS PERSONALITY CONTRACT",
            "Precedence: explicit corrections > approved profile > inferred runtime preference > generic default.",
            "Core rules:",
        ]
        for rule in rules:
            lines.append(f"- [{rule['id']}] {rule.get('rule', _entry_text(rule))}")
        if selected_corrections:
            lines.append("Explicit corrections:")
            for _, entry, _ in selected_corrections:
                lines.append(f"- [{entry['id']}] {entry.get('derived_rule', _entry_text(entry))}")
        if selected_profile:
            lines.append("Relevant approved profile:")
            for _, entry, _ in selected_profile:
                lines.append(f"- [{entry['id']}] {_entry_text(entry)}")
        if selected_examples:
            lines.append("Relevant response examples (imitate the good response, never the bad one):")
            for _, entry, _ in selected_examples:
                lines.append(f"- [{entry['id']}] Context: {entry.get('context', '')} Good: {entry.get('good', '')}")
        inferred = [str(value).strip() for value in (inferred_preferences or []) if str(value).strip()][:2]
        if inferred:
            lines.append("Low-priority inferred runtime hints:")
            lines.extend(f"- {value}" for value in inferred)

        context = "\n".join(lines)
        if len(context) > max_chars:
            context = context[: max(0, max_chars - 20)].rstrip() + "\n[context capped]"

        applied = []
        for score, entry, reason in selected_corrections + selected_profile + selected_examples:
            applied.append({"id": entry["id"], "score": round(score, 4), "reason": reason})
        self._audit_local.value = {
            "provider_policy": provider,
            "allow_restricted": allow_restricted,
            "applied": applied,
            "core_rule_ids": [entry["id"] for entry in rules],
            "excluded_by_privacy": excluded,
            "load_issues": [issue.__dict__ for issue in self.store.last_issues],
            "context_chars": len(context),
            "estimated_tokens": math.ceil(len(context) / 4),
        }
        return context

    def capture_feedback(self, user_text: str, *, context: str = "conversation", original_response: str = "") -> dict | None:
        correction = detect_feedback(user_text, context=context, original_response=original_response)
        return self.store.append_correction(correction) if correction else None

    def get_last_audit(self) -> dict:
        """Developer-only inspection API; it is intentionally not connected to WPF."""
        return dict(getattr(self._audit_local, "value", {}))


engine = PersonalityEngine()


def assemble_personality_context(user_text: str, **kwargs) -> str:
    return engine.assemble(user_text, **kwargs)


def capture_explicit_feedback(user_text: str, **kwargs) -> dict | None:
    return engine.capture_feedback(user_text, **kwargs)


def get_last_personality_audit() -> dict:
    return engine.get_last_audit()
