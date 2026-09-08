"""
Test suite for JARVIS observer module.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestObserver:
    """Test observer state management."""

    def test_detect_app_mapping(self):
        """Test app name detection from window title."""
        from observer import _detect_app
        
        assert _detect_app("Google Chrome - Some Page") == "Chrome"
        assert _detect_app("Visual Studio Code - project") == "VS Code"
        assert _detect_app("Notepad - file.txt") == "Notepad"
        # Titles under 30 chars are returned as-is (no mapping found)
        assert _detect_app("Random Window Title") == "Random Window Title"

    def test_context_hint_generation(self):
        """Test context hint generation for known apps."""
        from observer import get_context_hint
        
        with patch('observer.state', {"active_app": "Chrome", "active_title": "Google"}):
            hint = get_context_hint()
            assert "Chrome" in hint
        
        with patch('observer.state', {"active_app": "VS Code", "active_title": "main.py"}):
            hint = get_context_hint()
            assert "coding" in hint.lower()

    def test_get_state_snapshot(self):
        """Test state snapshot returns copy of state."""
        from observer import get_state_snapshot, state
        
        with patch.dict(state, {"active_app": "TestApp", "cpu_percent": 50}):
            snapshot = get_state_snapshot()
            assert snapshot["active_app"] == "TestApp"
            assert snapshot["cpu_percent"] == 50
            # Ensure it's a copy, not the original
            snapshot["active_app"] = "Modified"
            assert state["active_app"] == "TestApp"

    def test_mark_user_input_updates_idle(self):
        """Test that mark_user_input resets idle timer."""
        from observer import mark_user_input, state
        
        with patch.dict(state, {"last_input_time": 1000.0, "user_idle_seconds": 500}):
            with patch('observer.time.time', return_value=2000.0):
                mark_user_input()
                assert state["last_input_time"] == 2000.0
                assert state["user_idle_seconds"] == 0


class TestObserverVerification:
    """Test action outcome verification."""

    @patch('observer.psutil.process_iter')
    def test_verify_open_app_success(self, mock_process_iter):
        """Test successful open app verification."""
        from observer import verify_action_outcome
        
        mock_proc = Mock()
        mock_proc.info = {"name": "notepad.exe", "pid": 1234}
        mock_process_iter.return_value = [mock_proc]
        
        verified, msg = verify_action_outcome({"action": "open_app", "app": "notepad"})
        
        assert verified is True
        assert "notepad" in msg.lower() or "process" in msg.lower()

    @patch('observer.psutil.process_iter')
    @patch('observer.win32gui.GetForegroundWindow')
    @patch('observer.win32gui.GetWindowText')
    def test_verify_open_app_via_window(self, mock_get_text, mock_get_hwnd, mock_process_iter):
        """Test open app verification via window title."""
        from observer import verify_action_outcome
        
        mock_process_iter.return_value = []
        mock_get_hwnd.return_value = 123
        mock_get_text.return_value = "Untitled - Notepad"
        
        verified, msg = verify_action_outcome({"action": "open_app", "app": "notepad"})
        
        assert verified is True
        assert "notepad" in msg.lower()

    @patch('observer.psutil.process_iter')
    def test_verify_close_app_success(self, mock_process_iter):
        """Test successful close app verification."""
        from observer import verify_action_outcome
        
        mock_process_iter.return_value = []  # No matching processes
        
        verified, msg = verify_action_outcome({"action": "close_app", "app": "notepad"})
        
        assert verified is True
        assert "terminated" in msg.lower() or "not running" in msg.lower()

    @patch('observer.psutil.process_iter')
    def test_verify_close_app_still_running(self, mock_process_iter):
        """Test close app verification when process still running."""
        from observer import verify_action_outcome
        
        mock_proc = Mock()
        mock_proc.info = {"name": "notepad.exe", "pid": 1234}
        mock_process_iter.return_value = [mock_proc]
        
        verified, msg = verify_action_outcome({"action": "close_app", "app": "notepad"})
        
        assert verified is False
        assert "still running" in msg.lower()

    def test_verify_rename_file(self):
        """Test rename file verification."""
        from observer import verify_action_outcome
        
        with patch('observer.os.path.exists', return_value=True):
            verified, msg = verify_action_outcome({
                "action": "rename_file", 
                "old_name": "old.txt", 
                "new_name": "new.txt"
            })
            assert verified is True
            assert "verified" in msg.lower()

    def test_verify_save_login(self):
        """Test save login verification."""
        from observer import verify_action_outcome
        
        with patch('credential_vault.get_credential', return_value={"username": "test", "password": "pass"}):
            verified, msg = verify_action_outcome({"action": "save_login", "app": "testapp"})
            assert verified is True
            assert "verified" in msg.lower()


class TestObserverThreads:
    """Test observer thread lifecycle."""

    def test_start_observers_creates_threads(self):
        """Test that start_observers creates and starts threads."""
        import observer
        
        observer.start_observers()
        # Access the module variable after start_observers() creates the list
        assert len(observer._observer_threads) == 4
        assert all(t.is_alive() for t in observer._observer_threads)
        
        observer.stop_observers()
        # Threads should stop (with timeout)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])