import json
from pathlib import Path

import brain
import personality
import planner


def _write_jsonl(directory: Path, filename: str, entries, malformed=False):
    lines = [json.dumps(entry) for entry in entries]
    if malformed:
        lines.insert(1, "{not valid json")
        lines.insert(2, "")
    (directory / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_dir(tmp_path: Path) -> Path:
    profile = [
        {"id": "identity.name", "type": "identity", "fact": "The user prefers the name Arju.", "confidence": 1, "privacy": "normal"},
        {"id": "pref.short", "type": "communication", "fact": "The user prefers concise answers.", "confidence": 1, "privacy": "normal"},
        {"id": "tech.local", "type": "technical", "fact": "Prefer local Python automation.", "confidence": .9, "privacy": "normal"},
        {"id": "project.robot", "type": "project", "fact": "The robotics project uses computer vision.", "confidence": .8, "privacy": "normal"},
        {"id": "private.note", "type": "identity", "fact": "PRIVATE-MARKER", "confidence": 1, "privacy": "private"},
        {"id": "sensitive.note", "type": "identity", "fact": "SENSITIVE-MARKER", "confidence": 1, "privacy": "sensitive"},
    ]
    files = {
        "identity.jsonl": profile[:1] + profile[4:],
        "preferences.jsonl": profile[1:2],
        "communication.jsonl": [],
        "technical_preferences.jsonl": profile[2:3],
        "projects.jsonl": profile[3:4],
        "examples.jsonl": [
            {"id": "ex.robot", "type": "response_preference_example", "context": "debug robotics vision", "bad": "Guess.", "good": "Trace the camera input.", "why": "Diagnose first."}
        ],
        "behavior_rules.jsonl": [
            {"id": "rule.truth", "type": "core_rule", "priority": 100, "rule": "Accuracy overrides style."}
        ],
        "corrections.jsonl": [],
    }
    for filename, entries in files.items():
        _write_jsonl(tmp_path, filename, entries)
    return tmp_path


def test_profile_loading_and_schema(tmp_path):
    store = personality.ProfileStore(_seed_dir(tmp_path))
    loaded = store.load()
    assert len(loaded["profile"]) == 6
    assert loaded["rules"][0]["id"] == "rule.truth"
    assert store.last_issues == []


def test_malformed_and_empty_jsonl_lines_are_skipped(tmp_path):
    data_dir = _seed_dir(tmp_path)
    valid = {"id": "identity.valid", "type": "identity", "fact": "Valid fact."}
    invalid_schema = {"id": "missing.type", "fact": "No type."}
    _write_jsonl(data_dir, "identity.jsonl", [valid, invalid_schema], malformed=True)
    store = personality.ProfileStore(data_dir)
    loaded = store.load()
    assert [entry["id"] for entry in loaded["profile"] if entry["_source_file"] == "identity.jsonl"] == ["identity.valid"]
    assert {issue.reason for issue in store.last_issues} == {"malformed JSON", "missing type"}


def test_relevance_retrieval_selects_matching_profile_and_example(tmp_path):
    engine = personality.PersonalityEngine(personality.ProfileStore(_seed_dir(tmp_path)))
    context = engine.assemble("Help debug the robotics camera vision", provider="cloud")
    audit_ids = {entry["id"] for entry in engine.get_last_audit()["applied"]}
    assert "project.robot" in audit_ids
    assert "ex.robot" in audit_ids
    assert "Trace the camera input." in context


def test_entry_example_and_character_caps(tmp_path):
    engine = personality.PersonalityEngine(personality.ProfileStore(_seed_dir(tmp_path)))
    context = engine.assemble(
        "robotics Python concise", min_entries=0, max_entries=3,
        max_examples=1, max_chars=550, max_tokens=100,
    )
    audit = engine.get_last_audit()
    applied_profile = [item for item in audit["applied"] if not item["id"].startswith("ex.")]
    assert len(applied_profile) <= 3
    assert sum(item["id"].startswith("ex.") for item in audit["applied"]) <= 1
    assert len(context) <= 400
    assert audit["estimated_tokens"] <= 100


def test_privacy_filtering_for_cloud_and_local(tmp_path):
    store = personality.ProfileStore(_seed_dir(tmp_path))
    engine = personality.PersonalityEngine(store)
    cloud = engine.assemble("marker", provider="cloud", max_entries=8)
    assert "PRIVATE-MARKER" not in cloud
    assert "SENSITIVE-MARKER" not in cloud
    assert set(engine.get_last_audit()["excluded_by_privacy"]) >= {"private.note", "sensitive.note"}
    local = engine.assemble("marker", provider="ollama", max_entries=8)
    assert "PRIVATE-MARKER" in local
    assert "SENSITIVE-MARKER" in local


def test_explicit_correction_precedes_profile(tmp_path):
    data_dir = _seed_dir(tmp_path)
    store = personality.ProfileStore(data_dir)
    store.append_correction({
        "timestamp": "2026-09-10T00:00:00+00:00", "context": "chat",
        "original_response": "A long answer", "feedback": "be shorter",
        "derived_rule": "Use one sentence.", "confidence": 1, "scope": "communication",
        "active": True, "privacy": "normal",
    })
    context = personality.PersonalityEngine(store).assemble("Explain this", provider="cloud")
    assert context.index("Explicit corrections:") < context.index("Relevant approved profile:")
    assert "Use one sentence." in context


def test_positive_feedback_candidate_is_stored(tmp_path):
    store = personality.ProfileStore(_seed_dir(tmp_path))
    engine = personality.PersonalityEngine(store)
    saved = engine.capture_feedback(
        "That's exactly how I want you to answer",
        context="debugging",
        original_response="The fault is in the parser.",
    )
    assert saved["active"] is True
    assert saved["original_response"] == "The fault is in the parser."
    assert "Continue using" in saved["derived_rule"]
    assert (tmp_path / "learned" / "corrections.jsonl").exists()
    assert (tmp_path / "corrections.jsonl").read_text(encoding="utf-8") == "\n"


def test_negative_feedback_candidate_is_stored(tmp_path):
    store = personality.ProfileStore(_seed_dir(tmp_path))
    saved = personality.PersonalityEngine(store).capture_feedback(
        "Don't talk to me like that", original_response="Certainly!"
    )
    assert saved["confidence"] == .98
    assert "Avoid the tone" in saved["derived_rule"]
    persisted = store.load()["corrections"]
    assert persisted[-1]["feedback"] == "Don't talk to me like that"


def test_general_emotion_is_not_learned(tmp_path):
    store = personality.ProfileStore(_seed_dir(tmp_path))
    engine = personality.PersonalityEngine(store)
    assert engine.capture_feedback("This bug is making me angry") is None
    assert store.load()["corrections"] == []


def test_explicit_preference_feedback_is_normal_unless_restricted(tmp_path):
    store = personality.ProfileStore(_seed_dir(tmp_path))
    engine = personality.PersonalityEngine(store)
    normal = engine.capture_feedback("Remember that I prefer Python examples")
    sensitive = engine.capture_feedback("Remember that I prefer discussing my medical diagnosis")
    assert normal["privacy"] == "normal"
    assert normal["scope"] == "preference"
    assert sensitive["privacy"] == "sensitive"


def test_provider_independent_context_assembly(tmp_path):
    engine = personality.PersonalityEngine(personality.ProfileStore(_seed_dir(tmp_path)))
    nvidia = engine.assemble("Python automation", provider="nvidia")
    gemini = engine.assemble("Python automation", provider="gemini")
    cloud = engine.assemble("Python automation", provider="cloud")
    assert nvidia == gemini == cloud


def test_deterministic_action_path_does_not_retrieve(monkeypatch):
    monkeypatch.setattr(personality, "assemble_personality_context", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retrieval called")))
    monkeypatch.setattr(personality, "capture_explicit_feedback", lambda *args, **kwargs: None)
    action, response, model = planner.ask("open notepad")
    assert action == {"action": "open_app", "app": "notepad"}
    assert model == "action"


def test_provider_fallback_and_reasoning_retry_preserve_personality(monkeypatch):
    calls = []

    def nvidia(messages, model, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return "Let me think step by step", "ok"
        return "final", "ok"

    # This is a provider-specific unit test. It must never escape into other providers.
    monkeypatch.setattr(brain, "_apinex_call", lambda *args, **kwargs: ("", "offline"))
    monkeypatch.setattr(brain, "_ollama_call", lambda *args, **kwargs: ("", "offline"))
    monkeypatch.setattr(brain, "_nvidia_call", nvidia)
    result = brain.ask_llm("question", profile_context="UNIQUE-PERSONALITY", allow_actions=False)
    assert result == "final"
    assert all("UNIQUE-PERSONALITY" in call[0]["content"] for call in calls)


def test_cloud_prompt_never_contains_full_or_restricted_profile(tmp_path, monkeypatch):
    engine = personality.PersonalityEngine(personality.ProfileStore(_seed_dir(tmp_path)))
    profile_context = engine.assemble("concise answer", provider="cloud", max_entries=3)
    captured = {}

    def nvidia(messages, model, **kwargs):
        captured["system"] = messages[0]["content"]
        return "ok", "ok"

    monkeypatch.setattr(brain, "_apinex_call", lambda *args, **kwargs: ("", "offline"))
    monkeypatch.setattr(brain, "_ollama_call", lambda *args, **kwargs: ("", "offline"))
    monkeypatch.setattr(brain, "_nvidia_call", nvidia)
    brain.ask_llm("concise answer", profile_context=profile_context, allow_actions=False)
    system = captured["system"]
    assert "PRIVATE-MARKER" not in system and "SENSITIVE-MARKER" not in system
    profile_ids = {"identity.name", "pref.short", "tech.local", "project.robot", "private.note", "sensitive.note"}
    assert sum(profile_id in system for profile_id in profile_ids) <= 3
