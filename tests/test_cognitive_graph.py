"""Test suite for cognitive_graph.py knowledge graph storage and traversal."""
import os
import tempfile
import pytest
from pathlib import Path

import cognitive_graph


class TestCognitiveGraph:
    """Test cases for semantic triple storage and query."""

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

    def test_add_single_triple(self, temp_db):
        """Test adding a single semantic triple."""
        result = cognitive_graph.add_triple(
            "Arju", "prefers", "Python 3.11",
            confidence=0.95, source="test",
            db_path=temp_db
        )

        assert result is True

        # Query back the triple
        triples = cognitive_graph.query_triples(subject="Arju", db_path=temp_db)
        assert len(triples) == 1
        assert triples[0]["subject"] == "Arju"
        assert triples[0]["predicate"] == "prefers"
        assert triples[0]["object"] == "Python 3.11"
        assert triples[0]["confidence"] == 0.95

    def test_add_triple_empty_components(self, temp_db):
        """Test rejection of triples with empty components."""
        result = cognitive_graph.add_triple("", "predicate", "object", db_path=temp_db)
        assert result is False

        result = cognitive_graph.add_triple("subject", "", "object", db_path=temp_db)
        assert result is False

        result = cognitive_graph.add_triple("subject", "predicate", "", db_path=temp_db)
        assert result is False

    def test_add_triples_batch(self, temp_db):
        """Test batch triple insertion."""
        triples = [
            {"subject": "Jarvis", "predicate": "runs_on", "object": "CUDA", "confidence": 1.0},
            {"subject": "Jarvis", "predicate": "uses", "object": "Ollama", "confidence": 0.9},
            {"subject": "Arju", "predicate": "owns", "object": "Jarvis", "confidence": 1.0},
        ]

        count = cognitive_graph.add_triples(triples, source="batch_test", db_path=temp_db)
        assert count == 3

        # Verify all inserted
        all_triples = cognitive_graph.query_triples(db_path=temp_db, limit=10)
        assert len(all_triples) == 3

    def test_query_triples_by_subject(self, temp_db):
        """Test querying by subject."""
        cognitive_graph.add_triple("Python", "is_a", "Language", db_path=temp_db)
        cognitive_graph.add_triple("Python", "version", "3.11", db_path=temp_db)
        cognitive_graph.add_triple("Java", "is_a", "Language", db_path=temp_db)

        results = cognitive_graph.query_triples(subject="Python", db_path=temp_db)
        assert len(results) == 2
        assert all(r["subject"] == "Python" for r in results)

    def test_query_triples_by_predicate(self, temp_db):
        """Test querying by predicate."""
        cognitive_graph.add_triple("Python", "is_a", "Language", db_path=temp_db)
        cognitive_graph.add_triple("Java", "is_a", "Language", db_path=temp_db)
        cognitive_graph.add_triple("Python", "version", "3.11", db_path=temp_db)

        results = cognitive_graph.query_triples(predicate="is_a", db_path=temp_db)
        assert len(results) == 2
        assert all(r["predicate"] == "is_a" for r in results)

    def test_query_triples_by_object(self, temp_db):
        """Test querying by object."""
        cognitive_graph.add_triple("Python", "is_a", "Language", db_path=temp_db)
        cognitive_graph.add_triple("Java", "is_a", "Language", db_path=temp_db)
        cognitive_graph.add_triple("Python", "version", "3.11", db_path=temp_db)

        results = cognitive_graph.query_triples(obj="Language", db_path=temp_db)
        assert len(results) == 2
        assert all(r["object"] == "Language" for r in results)

    def test_query_triples_min_confidence(self, temp_db):
        """Test filtering by minimum confidence."""
        cognitive_graph.add_triple("A", "relates_to", "B", confidence=0.5, db_path=temp_db)
        cognitive_graph.add_triple("C", "relates_to", "D", confidence=0.9, db_path=temp_db)

        results = cognitive_graph.query_triples(min_confidence=0.8, db_path=temp_db)
        assert len(results) == 1
        assert results[0]["confidence"] >= 0.8

    def test_get_entity_relations(self, temp_db):
        """Test retrieving outgoing and incoming relations for an entity."""
        cognitive_graph.add_triple("Arju", "owns", "Jarvis", db_path=temp_db)
        cognitive_graph.add_triple("Jarvis", "runs_on", "Windows", db_path=temp_db)
        cognitive_graph.add_triple("Python", "powers", "Jarvis", db_path=temp_db)

        relations = cognitive_graph.get_entity_relations("Jarvis", db_path=temp_db)

        assert len(relations["outgoing"]) == 1  # Jarvis -> runs_on -> Windows
        assert relations["outgoing"][0]["predicate"] == "runs_on"

        assert len(relations["incoming"]) == 2  # Arju -> owns -> Jarvis, Python -> powers -> Jarvis
        incoming_preds = {r["predicate"] for r in relations["incoming"]}
        assert "owns" in incoming_preds
        assert "powers" in incoming_preds

    def test_find_connections_direct(self, temp_db):
        """Test finding direct connection between two entities."""
        cognitive_graph.add_triple("Arju", "owns", "Jarvis", db_path=temp_db)

        paths = cognitive_graph.find_connections("Arju", "Jarvis", max_hops=2, db_path=temp_db)

        assert len(paths) >= 1
        assert len(paths[0]) == 1
        assert paths[0][0]["subject"] == "Arju"
        assert paths[0][0]["object"] == "Jarvis"

    def test_find_connections_two_hops(self, temp_db):
        """Test finding two-hop connection."""
        cognitive_graph.add_triple("Arju", "owns", "Jarvis", db_path=temp_db)
        cognitive_graph.add_triple("Jarvis", "runs_on", "Windows", db_path=temp_db)

        paths = cognitive_graph.find_connections("Arju", "Windows", max_hops=2, db_path=temp_db)

        assert len(paths) >= 1
        assert len(paths[0]) == 2
        assert paths[0][0]["subject"] == "Arju"
        assert paths[0][1]["object"] == "Windows"

    def test_find_connections_no_path(self, temp_db):
        """Test finding connections when no path exists."""
        cognitive_graph.add_triple("A", "relates_to", "B", db_path=temp_db)
        cognitive_graph.add_triple("C", "relates_to", "D", db_path=temp_db)

        paths = cognitive_graph.find_connections("A", "D", max_hops=2, db_path=temp_db)
        assert len(paths) == 0

    def test_search_entities(self, temp_db):
        """Test entity keyword search."""
        cognitive_graph.add_triple("Python 3.11", "is_a", "Language", db_path=temp_db)
        cognitive_graph.add_triple("PyTorch", "is_a", "Framework", db_path=temp_db)
        cognitive_graph.add_triple("Java", "is_a", "Language", db_path=temp_db)

        results = cognitive_graph.search_entities("Py", db_path=temp_db)
        assert len(results) >= 2
        assert "Python 3.11" in results or any("Python" in r for r in results)
        assert "PyTorch" in results or any("PyTorch" in r for r in results)

    def test_export_subgraph_for_prompt(self, temp_db):
        """Test formatting subgraph for LLM prompt injection."""
        cognitive_graph.add_triple("Arju", "owns", "Jarvis", db_path=temp_db)
        cognitive_graph.add_triple("Jarvis", "runs_on", "CUDA", db_path=temp_db)
        cognitive_graph.add_triple("Arju", "prefers", "Python", db_path=temp_db)

        prompt_text = cognitive_graph.export_subgraph_for_prompt(
            ["Arju", "Jarvis"], max_triples=5, db_path=temp_db
        )

        assert "Cognitive Knowledge Graph:" in prompt_text
        assert "Arju" in prompt_text
        assert "Jarvis" in prompt_text
        assert "owns" in prompt_text or "runs_on" in prompt_text

    def test_export_subgraph_empty(self, temp_db):
        """Test export with no matching entities."""
        prompt_text = cognitive_graph.export_subgraph_for_prompt(
            ["NonexistentEntity"], max_triples=5, db_path=temp_db
        )
        assert prompt_text == ""

    def test_clear_graph(self, temp_db):
        """Test clearing all triples."""
        cognitive_graph.add_triple("A", "relates_to", "B", db_path=temp_db)
        cognitive_graph.add_triple("C", "relates_to", "D", db_path=temp_db)

        cognitive_graph.clear_graph(db_path=temp_db)

        all_triples = cognitive_graph.query_triples(db_path=temp_db, limit=100)
        assert len(all_triples) == 0

    def test_duplicate_triple_replacement(self, temp_db):
        """Test that duplicate triples are replaced (UNIQUE constraint)."""
        cognitive_graph.add_triple("A", "relates_to", "B", confidence=0.5, db_path=temp_db)
        cognitive_graph.add_triple("A", "relates_to", "B", confidence=0.9, db_path=temp_db)

        results = cognitive_graph.query_triples(subject="A", db_path=temp_db)
        assert len(results) == 1
        # The newer triple should replace the older one
        assert results[0]["confidence"] == 0.9

    def test_predicate_normalization(self, temp_db):
        """Test that predicate names are normalized (lowercase, underscores)."""
        cognitive_graph.add_triple("A", "Is Related To", "B", db_path=temp_db)

        results = cognitive_graph.query_triples(subject="A", db_path=temp_db)
        assert len(results) == 1
        assert results[0]["predicate"] == "is_related_to"
