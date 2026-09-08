"""
Test suite for JARVIS executor module.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import tempfile
import os


class TestExecutor:
    """Test executor action execution."""

    def test_available_actions_list(self):
        """Test that available actions list is populated."""
        from executor import AVAILABLE_ACTIONS_LIST
        
        assert isinstance(AVAILABLE_ACTIONS_LIST, list)
        assert len(AVAILABLE_ACTIONS_LIST) > 0
        assert "open_app" in AVAILABLE_ACTIONS_LIST
        assert "close_app" in AVAILABLE_ACTIONS_LIST
        assert "web_search" in AVAILABLE_ACTIONS_LIST

    def test_get_capabilities_text(self):
        """Test capabilities text generation."""
        from executor import get_capabilities_text
        
        text = get_capabilities_text()
        assert isinstance(text, str)
        assert len(text) > 0
        # Category names use spaces, not underscores
        assert "system control" in text.lower()

    def test_init_sets_callbacks(self):
        """Test that init sets speak and ask callbacks."""
        from executor import init
        
        mock_speak = Mock()
        mock_ask = Mock(return_value=(None, "response"))
        
        init(mock_speak, mock_ask)
        
        # Verify callbacks are set (test indirectly through execution)
        from executor import _speak_fn, _ask_fn
        assert _speak_fn is mock_speak
        assert _ask_fn is mock_ask

    @patch('executor.os.startfile')
    def test_open_app_success(self, mock_startfile):
        """Test successful app opening."""
        from executor import _stable_open_app
        
        success, msg = _stable_open_app("notepad")
        
        assert success is True
        assert "Opening notepad" in msg
        mock_startfile.assert_called_once()

    @patch('executor.os.startfile', side_effect=Exception("Not found"))
    def test_open_app_failure(self, mock_startfile):
        """Test app opening failure."""
        from executor import _stable_open_app
        
        success, msg = _stable_open_app("nonexistent_app")
        
        assert success is False
        assert "Couldn't open" in msg

    @patch('executor.psutil.process_iter')
    def test_close_app_success(self, mock_process_iter):
        """Test successful app closing."""
        from executor import _stable_close_app
        
        mock_proc = Mock()
        mock_proc.info = {"name": "notepad.exe", "pid": 1234}
        mock_proc.terminate = Mock()
        mock_proc.wait = Mock()
        mock_process_iter.return_value = [mock_proc]
        
        success, msg = _stable_close_app("notepad")
        
        assert success is True
        assert "Closed notepad" in msg
        mock_proc.terminate.assert_called_once()

    @patch('executor.psutil.process_iter')
    def test_close_app_not_running(self, mock_process_iter):
        """Test closing app that isn't running."""
        from executor import _stable_close_app
        
        mock_proc = Mock()
        mock_proc.info = {"name": "chrome.exe", "pid": 5678}
        mock_process_iter.return_value = [mock_proc]
        
        success, msg = _stable_close_app("notepad")
        
        assert success is False
        assert "not running" in msg

    @patch('executor.webbrowser.open')
    @patch('executor.time.sleep')
    @patch('executor.pyautogui.hotkey')
    @patch('executor.pyautogui.press')
    def test_join_meeting(self, mock_press, mock_hotkey, mock_sleep, mock_open):
        """Test meeting join sequence."""
        from executor import _stable_join_meeting
        
        success, msg = _stable_join_meeting("https://meet.google.com/abc-def-ghi", "Test Class")
        
        assert success is True
        assert "Joined the meeting" in msg
        mock_open.assert_called_once_with("https://meet.google.com/abc-def-ghi")

    def test_web_search(self):
        """Test web search functionality."""
        from executor import web_search
        
        with patch('executor.requests.get') as mock_get:
            mock_response = Mock()
            mock_response.text = '<html><div class="result__snippet">Test result</div></html>'
            mock_get.return_value = mock_response
            
            with patch('executor._ask_fn') as mock_ask:
                mock_ask.return_value = (None, "Summary of results")
                result = web_search("test query")
                
                assert "Summary of results" in result

    def test_get_weather_fallback(self):
        """Test weather with fallback to web search."""
        from executor import get_weather
        
        with patch('executor.WEATHER_API_KEY', ''):
            with patch('executor.web_search') as mock_search:
                mock_search.return_value = "Weather data"
                with patch('executor._ask_fn') as mock_ask:
                    mock_ask.return_value = (None, "It's 20 degrees and sunny")
                    result = get_weather("London")
                    assert "20 degrees" in result or "sunny" in result

    def test_system_controls(self):
        """Test system control functions."""
        from executor import lock_pc, shutdown_pc, restart_pc
        
        with patch('executor.subprocess.run') as mock_run:
            lock_pc()
            mock_run.assert_called()
            
            shutdown_pc()
            assert mock_run.call_count == 2
            
            restart_pc()
            assert mock_run.call_count == 3

    def test_clipboard_operations(self):
        """Test clipboard read/write."""
        from executor import clipboard_read, clipboard_write
        
        with patch('executor.pyperclip.paste', return_value="clipboard content"):
            result = clipboard_read()
            assert "clipboard content" in result
        
        with patch('executor.pyperclip.copy') as mock_copy:
            result = clipboard_write("test text")
            assert "copied" in result
            mock_copy.assert_called_with("test text")


class TestExecutorIntegration:
    """Integration tests for executor with real dependencies."""

    def test_execute_with_retry_open_app(self):
        """Test execute_with_retry for open_app."""
        from executor import execute_with_retry
        
        with patch('executor.os.startfile'):
            success, msg = execute_with_retry({"action": "open_app", "app": "notepad"})
            assert success is True

    def test_execute_with_retry_invalid_action(self):
        """Test execute_with_retry with invalid action."""
        from executor import execute_with_retry
        
        success, msg = execute_with_retry({"action": "invalid_action"})
        assert success is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])