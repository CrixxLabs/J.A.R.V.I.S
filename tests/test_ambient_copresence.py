"""Tests for Module AQ: Continuous Ambient Co-Presence & Deixis Resolution."""
import pytest
from ambient_copresence import (
    AmbientCopresenceTracker,
    DeixisResolutionResult,
    EventType,
    MultimodalEvent,
    ParametricReplayResult,
    ReferentEntity,
    get_ambient_tracker,
    ingest_event,
    replay_parametric_action,
    resolve_deixis,
)


class TestAmbientCopresence:
    def test_multimodal_event_fusion_and_referent_registration(self):
        tracker = AmbientCopresenceTracker()
        t_now = 1000.0

        # Ingest editor diagnostic event
        tracker.ingest_event(
            event_type=EventType.EDITOR_DIAGNOSTIC,
            payload={"file_path": "src/parser.py", "line": 42, "message": "SyntaxError: invalid syntax"},
            timestamp_mono_ms=t_now * 1000.0,
        )

        # Ingest cursor selection on function
        tracker.ingest_event(
            event_type=EventType.CURSOR_SELECTION,
            payload={"file_path": "src/parser.py", "symbol": "parse_ast_tokens", "dwell_ms": 1200.0},
            timestamp_mono_ms=(t_now + 1.0) * 1000.0,
        )

        assert len(tracker._referents) >= 2
        # Check that ast_function was registered
        has_func = any(r.entity_type == "ast_function" and r.name == "parse_ast_tokens" for r in tracker._referents.values())
        assert has_func is True

    def test_deictic_disambiguation_with_type_cues(self):
        tracker = AmbientCopresenceTracker()
        t_base = 1000.0

        # Register a file, a function, and an error
        tracker.ingest_event(
            event_type=EventType.WINDOW_FOCUS,
            payload={"app_name": "VSCode", "window_title": "Editor - main.py"},
            timestamp_mono_ms=t_base * 1000.0,
        )
        tracker.ingest_event(
            event_type=EventType.EDITOR_DIAGNOSTIC,
            payload={"file_path": "src/auth.py", "line": 15, "message": "TypeError: NoneType"},
            timestamp_mono_ms=(t_base + 1.0) * 1000.0,
        )
        tracker.ingest_event(
            event_type=EventType.CURSOR_SELECTION,
            payload={"file_path": "src/auth.py", "symbol": "authenticate_user", "dwell_ms": 2000.0},
            timestamp_mono_ms=(t_base + 2.0) * 1000.0,
        )

        # "the error" -> should resolve to diagnostic_error
        res_error = tracker.resolve_deixis("What caused the error?", now_sec=t_base + 3.0)
        assert res_error.resolved_entity is not None
        assert res_error.resolved_entity.entity_type == "diagnostic_error"

        # "that function" -> should resolve to authenticate_user
        res_func = tracker.resolve_deixis("Refactor that function", now_sec=t_base + 3.0)
        assert res_func.resolved_entity is not None
        assert res_func.resolved_entity.entity_type == "ast_function"
        assert res_func.resolved_entity.name == "authenticate_user"

    def test_parametric_action_replay(self):
        tracker = AmbientCopresenceTracker()

        # Record action in log
        tracker.ingest_event(
            event_type=EventType.ACTION_EXECUTION,
            payload={"verb": "extrude_boss", "target": "bracket_sketch", "params": {"depth": 10.0, "unit": "mm"}},
            timestamp_mono_ms=1000.0,
        )

        # "make it thinner"
        replay1 = tracker.replay_parametric_action("make it thinner")
        assert replay1 is not None
        assert replay1.action_verb == "extrude_boss"
        assert replay1.modified_params["depth"] == 5.0

        # "do that again with depth 25"
        replay2 = tracker.replay_parametric_action("do that again with depth 25")
        assert replay2 is not None
        assert replay2.modified_params["depth"] == 25.0
