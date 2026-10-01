"""Test suite for memory_consolidator.py sleep cycle and consolidation."""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

import memory_consolidator
import cognitive_graph


class TestMemoryConsolidator:
    """Test cases for memory consolidation and triple extraction."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cognitive_graph.init_db(db_path=path)
        yield path
        # Cleanup
        if os.path.exists(path):
            os.remove(path)
        wal_path = path + "-wal"
        shm_path = path + "-shm"
        if os.path.exists(wal_path):
            os.remove(wal_path)
        if os.path.exists(shm_path):
            os.remove(shm_path)

    def test_extract_entities_from_query_simple(self):
        """Test entity extraction from natural language query."""
        query = "Tell me about Python and Machine Learning"
        entities = memory_consolidator.extract_entities_from_query(query)

        # Should extract capitalized entities
        assert isinstance(entities, list)
        # Function looks for entities in cognitive graph, so without seeded data, result may be empty
        # This tests the pipeline doesn't crash

    def test_extract_entities_from_query_empty(self):
        """Test entity extraction with empty query."""
        entities = memory_consolidator.extract_entities_from_query("")
        assert entities == []

    @patch('memory_consolidator.brain.ask_llm')
    def test_extract_triples_from_text_valid_json(self, mock_brain):
        """Test triple extraction with valid LLM JSON response."""
        mock_brain.return_value = """```json
[
  {"subject": "Arju", "predicate": "prefers", "object": "Dark Mode", "confidence": 0.95},
  {"subject": "Jarvis", "predicate": "runs_on", "object": "CUDA", "confidence": 0.9}
]
```"""

        text = "Arju prefers Dark Mode. Jarvis runs on CUDA."
        triples = memory_consolidator.extract_triples_from_text(text)

        assert len(triples) == 2
        assert triples[0]["subject"] == "Arju"
        assert triples[0]["predicate"] == "prefers"
        assert triples[0]["object"] == "Dark Mode"
        assert triples[1]["subject"] == "Jarvis"

    @patch('memory_consolidator.brain.ask_llm')
    def test_extract_triples_from_text_no_markdown(self, mock_brain):
        """Test triple extraction with raw JSON (no markdown blocks)."""
        mock_brain.return_value = """[
  {"subject": "Python", "predicate": "is_a", "object": "Language", "confidence": 1.0}
]"""

        text = "Python is a programming language."
        triples = memory_consolidator.extract_triples_from_text(text)

        assert len(triples) == 1
        assert triples[0]["subject"] == "Python"
        assert triples[0]["predicate"] == "is_a"

    @patch('memory_consolidator.brain.ask_llm')
    def test_extract_triples_from_text_empty_result(self, mock_brain):
        """Test triple extraction when LLM returns empty array."""
        mock_brain.return_value = "[]"

        text = "This is just random text with no facts."
        triples = memory_consolidator.extract_triples_from_text(text)

        assert len(triples) == 0

    @patch('memory_consolidator.brain.ask_llm')
    def test_extract_triples_from_text_invalid_json(self, mock_brain):
        """Test triple extraction with invalid JSON response."""
        mock_brain.return_value = "This is not valid JSON at all"

        text = "Some text"
        triples = memory_consolidator.extract_triples_from_text(text)

        # Should handle gracefully and return empty list
        assert len(triples) == 0

    def test_extract_triples_from_text_short_input(self):
        """Test rejection of very short text inputs."""
        triples = memory_consolidator.extract_triples_from_text("Hi")
        assert len(triples) == 0

    @patch('memory_consolidator.brain.ask_llm')
    @patch('memory_consolidator.memory.get_memory_summary')
    @patch('memory_consolidator.memory.get_session_notes')
    @patch('memory_consolidator.memory.get_recent_activity')
    @patch('memory_consolidator.cognitive_graph.add_triples')
    def test_consolidate_recent_memory_success(
        self, mock_add_triples, mock_activity, mock_notes, mock_summary, mock_brain
    ):
        """Test successful memory consolidation cycle."""
        # Mock memory sources
        mock_summary.return_value = "Arju is working on Jarvis AGI engine upgrade."
        mock_notes.return_value = ["Implemented cognitive graph", "Added memory consolidation"]
        mock_activity.return_value = [
            {"type": "code", "detail": "Wrote cognitive_graph.py"},
            {"type": "test", "detail": "Tested triple storage"}
        ]

        # Mock LLM extraction
        mock_brain.return_value = """[
  {"subject": "Arju", "predicate": "works_on", "object": "Jarvis", "confidence": 0.95}
]"""

        # Mock graph insertion
        mock_add_triples.return_value = 1

        # Run consolidation
        count = memory_consolidator.consolidate_recent_memory(force=True)

        assert count == 1
        mock_add_triples.assert_called_once()

    @patch('memory_consolidator.memory.get_memory_summary')
    @patch('memory_consolidator.memory.get_session_notes')
    @patch('memory_consolidator.memory.get_recent_activity')
    def test_consolidate_recent_memory_no_sources(
        self, mock_activity, mock_notes, mock_summary
    ):
        """Test consolidation with no memory sources available."""
        mock_summary.return_value = ""
        mock_notes.return_value = []
        mock_activity.return_value = []

        count = memory_consolidator.consolidate_recent_memory(force=True)
        assert count == 0

    @patch('memory_consolidator.consolidate_recent_memory')
    @patch('memory_consolidator.memory.cleanup_old_facts')
    @patch('memory_consolidator.cognitive_graph.query_triples')
    def test_run_sleep_cycle(self, mock_query, mock_cleanup, mock_consolidate):
        """Test full sleep cycle execution."""
        mock_consolidate.return_value = 5
        mock_query.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

        summary = memory_consolidator.run_sleep_cycle()

        assert summary["status"] == "success"
        assert summary["triples_consolidated"] == 5
        assert summary["total_graph_triples"] == 3
        mock_consolidate.assert_called_once_with(force=True)
        mock_cleanup.assert_called_once_with(days=30)

    def test_consolidation_cooldown(self):
        """Test that consolidation respects cooldown timer."""
        # First consolidation should work
        with patch('memory_consolidator.memory.get_memory_summary', return_value=""):
            count1 = memory_consolidator.consolidate_recent_memory(force=True)

        # Immediate second call should be blocked (unless forced)
        with patch('memory_consolidator.memory.get_memory_summary', return_value="test"):
            count2 = memory_consolidator.consolidate_recent_memory(force=False)
            assert count2 == 0

        # Forced call should bypass cooldown
        with patch('memory_consolidator.memory.get_memory_summary', return_value="test"):
            with patch('memory_consolidator.brain.ask_llm', return_value="[]"):
                count3 = memory_consolidator.consolidate_recent_memory(force=True)
                # Returns 0 because no triples extracted, but call proceeds
                assert count3 == 0

    @patch('memory_consolidator.brain.ask_llm')
    def test_predicate_normalization_in_extraction(self, mock_brain):
        """Test that extracted predicates are normalized."""
        mock_brain.return_value = """[
  {"subject": "A", "predicate": "Is Related To", "object": "B", "confidence": 0.8}
]"""

        triples = memory_consolidator.extract_triples_from_text("A is related to B")
        assert len(triples) == 1
        assert triples[0]["predicate"] == "is_related_to"

    @patch('memory_consolidator.brain.ask_llm')
    def test_confidence_defaults(self, mock_brain):
        """Test that missing confidence defaults to 0.9."""
        mock_brain.return_value = """[
  {"subject": "X", "predicate": "relates_to", "object": "Y"}
]"""

        triples = memory_consolidator.extract_triples_from_text("X relates to Y")
        assert len(triples) == 1
        assert triples[0]["confidence"] == 0.9
