"""
Test suite for JARVIS planner module.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestPlanner:
    """Test planner routing and decision logic."""

    def test_capability_question_returns_fast_response(self):
        """Test that capability questions return fast response without LLM call."""
        from planner import is_capability_question, get_capability_response
        
        assert is_capability_question("what can you do") is True
        assert is_capability_question("what are your abilities") is True
        assert is_capability_question("open notepad") is False
        
        response = get_capability_response()
        assert isinstance(response, str)
        assert len(response) > 0

    def test_fast_path_time_query(self):
        """Test fast path for time queries."""
        from jarvis import handle_fast_command
        
        with patch('jarvis.speak') as mock_speak:
            result = handle_fast_command("what time is it")
            assert result is True
            mock_speak.assert_called_once()

    def test_fast_path_date_query(self):
        """Test fast path for date queries."""
        from jarvis import handle_fast_command
        
        with patch('jarvis.speak') as mock_speak:
            result = handle_fast_command("what's the date")
            assert result is True
            mock_speak.assert_called_once()

    def test_fast_path_volume_control(self):
        """Test fast path for volume control."""
        from jarvis import handle_fast_command
        
        with patch('jarvis.pyautogui.press') as mock_press:
            result = handle_fast_command("volume up")
            assert result is True
            mock_press.assert_called_with("volumeup")

    def test_model_type_classification(self):
        """Test model type classification logic."""
        from planner import _classify_model_type
        
        # Short queries (≤5 words) without reasoning keywords return "fast"
        assert _classify_model_type("open notepad") == "fast"
        assert _classify_model_type("hi") == "fast"
        assert _classify_model_type("hello") == "fast"
        
        # Queries with reasoning keywords return "chat"
        assert _classify_model_type("explain why this works") == "chat"
        assert _classify_model_type("how does this work") == "chat"
        assert _classify_model_type("debug this code") == "chat"
        
        # Longer queries with action keywords return "action"
        assert _classify_model_type("please open notepad and write a letter") == "action"

    def test_action_leak_filtering(self):
        """Test that action leak patterns are filtered from spoken responses."""
        from planner import _is_leaked_action_string
        
        assert _is_leaked_action_string("action_use_open_app") is True
        assert _is_leaked_action_string('{"action": "open_app"}') is True
        assert _is_leaked_action_string("hello world") is False
        assert _is_leaked_action_string("") is False

    def test_reminder_seconds_resolution(self):
        """Test reminder time resolution."""
        from planner import _resolve_reminder_seconds
        
        # Relative time
        assert _resolve_reminder_seconds({}, "in 5 minutes") == 300
        assert _resolve_reminder_seconds({}, "in 1 hour") == 3600
        assert _resolve_reminder_seconds({}, "in 30 seconds") == 30


class TestPlannerIntegration:
    """Integration tests for planner with mocked dependencies."""

    @patch('planner.brain.ask_llm')
    def test_knowledge_query_routes_to_brain(self, mock_ask):
        """Test that knowledge queries route to brain."""
        from planner import ask
        
        mock_ask.return_value = "Paris is the capital of France."
        
        action, response, model_type = ask("What is the capital of France?")
        
        assert action is None
        assert response == "Paris is the capital of France."
        assert model_type == "chat"
        mock_ask.assert_called_once()

    @patch('planner.brain.ask_llm')
    def test_action_query_returns_action(self, mock_ask):
        """Test that action queries return action dict."""
        from planner import ask
        
        mock_ask.return_value = '{"action": "open_app", "app": "notepad"} Opening notepad.'
        
        action, response, model_type = ask("open notepad")
        
        assert action is not None
        assert action.get("action") == "open_app"
        assert action.get("app") == "notepad"
        mock_ask.assert_not_called()

    @patch('planner.brain.get_last_provider_model', return_value='NVIDIA: nvidia/test-model')
    @patch('planner.brain.ask_llm', return_value='A concise answer.')
    def test_planner_reports_actual_provider_model(self, _mock_ask, _mock_provider):
        from planner import ask

        action, response, provider_model = ask("Why is the sky blue?")
        assert action is None
        assert response == "A concise answer."
        assert provider_model == "NVIDIA: nvidia/test-model"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
