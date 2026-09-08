"""
Test suite for JARVIS memory module.
"""

import pytest
import tempfile
import os
from memory import (
    remember, recall_all, forget, recall_by_tag, recall_by_priority,
    log_activity, get_recent_activity, format_facts_for_prompt,
    log_action_result, get_memory_summary, log_usage, log_failure,
    should_avoid, semantic_recall, semantic_recall_text
)


class TestMemory:
    """Test memory operations."""

    def setup_method(self):
        """Create a temporary memory file for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.memory_file = os.path.join(self.temp_dir, "memory.json")
        
        # Patch the MEMORY_FILE constant
        import memory
        self.original_file = memory.MEMORY_FILE
        memory.MEMORY_FILE = self.memory_file

    def teardown_method(self):
        """Restore original memory file and cleanup."""
        import memory
        memory.MEMORY_FILE = self.original_file
        if os.path.exists(self.memory_file):
            os.remove(self.memory_file)
        os.rmdir(self.temp_dir)

    def test_remember_and_recall(self):
        """Test basic remember and recall."""
        remember("test_key", "test_value")
        result = recall_all()
        assert "test_key: test_value" in result

    def test_remember_with_tags_and_priority(self):
        """Test remember with tags and priority."""
        remember("key1", "value1", tags=["personal", "important"], priority="high")
        remember("key2", "value2", tags=["work"], priority="normal")
        
        # Recall by tag
        personal = recall_by_tag("personal")
        assert "key1" in personal
        assert "key2" not in personal
        
        # Recall by priority
        high = recall_by_priority("high")
        assert "key1" in high
        assert "key2" not in high

    def test_forget(self):
        """Test forgetting a key."""
        remember("to_forget", "value")
        forget("to_forget")
        result = recall_all()
        assert "to_forget" not in result

    def test_activity_log(self):
        """Test activity logging."""
        log_activity("test_action", "test_detail", app="test_app", tags=["test"])
        recent = get_recent_activity(1)
        assert len(recent) == 1
        assert recent[0]["type"] == "test_action"
        assert recent[0]["detail"] == "test_detail"

    def test_action_result_logging(self):
        """Test action result logging."""
        log_action_result("test_action", success=True, detail="worked")
        log_action_result("test_action", success=False, detail="failed")
        
        import memory
        m = memory._load()
        results = m.get("action_results", [])
        assert len(results) == 2
        assert results[0]["success"] is True
        assert results[1]["success"] is False

    def test_usage_tracking(self):
        """Test usage frequency tracking."""
        import memory
        log_usage("test_action", "context1")
        log_usage("test_action", "context2")
        log_usage("test_action", "context3")
        
        count = memory.get_usage_count("test_action")
        assert count == 3

    def test_failure_tracking(self):
        """Test failure logging and should_avoid."""
        log_failure("unstable_action", "error details")
        log_failure("unstable_action", "more errors")
        
        # Should not avoid yet (not enough usage)
        assert should_avoid("unstable_action") is False
        
        # Add more usage to trigger should_avoid
        for _ in range(10):
            log_usage("unstable_action")
        
        # Now failures > usage ratio should trigger avoid
        # Actually, need failures > usage, so let's add more failures
        for _ in range(15):
            log_failure("unstable_action", "error")
        
        assert should_avoid("unstable_action") is True

    def test_format_facts_for_prompt(self):
        """Test formatting facts for LLM prompt."""
        remember("name", "Test User", priority="high")
        remember("city", "Test City")
        
        formatted = format_facts_for_prompt()
        assert "name: Test User" in formatted
        assert "city: Test City" in formatted

    def test_semantic_recall(self):
        """Test semantic recall with TF-IDF."""
        remember("python_project", "Building a Python web scraper", tags=["code"])
        remember("cooking_recipe", "How to make pasta carbonara", tags=["food"])
        remember("work_meeting", "Team meeting about Python project", tags=["work"])
        
        results = semantic_recall("Python web development", top_n=2)
        assert len(results) <= 2
        # Should find python_project as most relevant
        assert any("python_project" in str(r) for r in results)

    def test_get_memory_summary(self):
        """Test memory summary generation."""
        remember("test", "value")
        log_activity("test", "detail")
        
        summary = get_memory_summary()
        assert "Facts:" in summary
        assert "Recent:" in summary


class TestMemoryPersistence:
    """Test memory persistence across reloads."""

    def test_memory_persists_after_reload(self):
        """Test that memory persists after module reload."""
        import memory
        
        # Use temp file
        temp_dir = tempfile.mkdtemp()
        temp_file = os.path.join(temp_dir, "memory.json")
        original = memory.MEMORY_FILE
        memory.MEMORY_FILE = temp_file
        
        try:
            remember("persist_key", "persist_value")
            
            # Reload module by creating new instance
            memory2 = __import__('memory', fromlist=[''])
            memory2.MEMORY_FILE = temp_file
            result = memory2.recall_all()
            
            assert "persist_key: persist_value" in result
        finally:
            memory.MEMORY_FILE = original
            if os.path.exists(temp_file):
                os.remove(temp_file)
            os.rmdir(temp_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])