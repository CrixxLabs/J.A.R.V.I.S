"""Episodic and Semantic Cognitive Knowledge Graph for J.A.R.V.I.S.

Provides persistent relational triple storage (subject, predicate, object, confidence, timestamp)
with SQLite backend, relationship traversal, and context generation for LLM prompt injection.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = str(BASE_DIR / "cognitive_graph.db")

_lock = threading.Lock()


def _get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create a thread-safe connection to the SQLite database with WAL mode."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Initialize the cognitive graph schema."""
    with _lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS triples (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        subject TEXT NOT NULL,
                        predicate TEXT NOT NULL,
                        object TEXT NOT NULL,
                        confidence REAL DEFAULT 1.0,
                        timestamp TEXT NOT NULL,
                        source TEXT DEFAULT 'direct',
                        UNIQUE(subject, predicate, object) ON CONFLICT REPLACE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_subject ON triples(subject)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_predicate ON triples(predicate)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_object ON triples(object)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON triples(timestamp)")
        finally:
            conn.close()


# Ensure DB schema exists on import
init_db()


def add_triple(subject: str, predicate: str, obj: str,
               confidence: float = 1.0, source: str = "direct",
               db_path: str = DB_PATH) -> bool:
    """Insert or update a semantic triple in the cognitive graph.

    Args:
        subject: Entity subject (e.g., "Arju", "Python", "Jarvis")
        predicate: Relation (e.g., "prefers", "works_on", "is_a")
        obj: Entity object or value (e.g., "Dark Mode", "AGI Engine")
        confidence: Confidence score [0.0 - 1.0]
        source: Provenance source (e.g., "direct", "conversation", "consolidation")
        db_path: Path to database file

    Returns:
        True if inserted successfully, False otherwise
    """
    registry = get_registry()

    if not subject or not predicate or not obj:
        registry.set_capability_evidence(
            "COGNITIVE_GRAPH", EvidenceLevel.BROKEN,
            "Invalid empty triple components", source="cognitive graph"
        )
        return False

    sub_clean = subject.strip()
    pred_clean = predicate.strip().lower().replace(" ", "_")
    obj_clean = obj.strip()
    now = datetime.datetime.now().isoformat()
    conf = max(0.0, min(1.0, float(confidence)))

    with _lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                conn.execute("""
                    INSERT INTO triples (subject, predicate, object, confidence, timestamp, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (sub_clean, pred_clean, obj_clean, conf, now, source))

            registry.set_capability_evidence(
                "COGNITIVE_GRAPH", EvidenceLevel.LIVE,
                f"Stored triple: ({sub_clean}) -[{pred_clean}]-> ({obj_clean})",
                source="cognitive graph"
            )
            return True
        except Exception as exc:
            error_handler.log_and_demote(
                "COGNITIVE_GRAPH", exc,
                f"Add triple ({sub_clean}, {pred_clean}, {obj_clean})",
                SubsystemState.DEGRADED
            )
            return False
        finally:
            conn.close()


def add_triples(triples: List[Dict[str, Any]], source: str = "batch",
                db_path: str = DB_PATH) -> int:
    """Batch insert multiple triples.

    Args:
        triples: List of dicts with keys 'subject', 'predicate', 'object', and optional 'confidence'
        source: Batch source tag
        db_path: Path to database file

    Returns:
        Number of successfully inserted triples
    """
    inserted = 0
    for t in triples:
        sub = t.get("subject")
        pred = t.get("predicate")
        obj = t.get("object")
        conf = t.get("confidence", 1.0)
        if sub and pred and obj:
            if add_triple(sub, pred, obj, confidence=conf, source=source, db_path=db_path):
                inserted += 1
    return inserted


def query_triples(subject: Optional[str] = None,
                  predicate: Optional[str] = None,
                  obj: Optional[str] = None,
                  min_confidence: float = 0.0,
                  limit: int = 50,
                  db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Query triples matching specific subject, predicate, or object patterns.

    Returns:
        List of dicts representing matched triples
    """
    conditions = ["confidence >= ?"]
    params: List[Any] = [min_confidence]

    if subject:
        conditions.append("subject LIKE ?")
        params.append(f"%{subject.strip()}%")
    if predicate:
        conditions.append("predicate LIKE ?")
        params.append(f"%{predicate.strip().lower()}%")
    if obj:
        conditions.append("object LIKE ?")
        params.append(f"%{obj.strip()}%")

    query_str = f"""
        SELECT id, subject, predicate, object, confidence, timestamp, source
        FROM triples
        WHERE {' AND '.join(conditions)}
        ORDER BY confidence DESC, timestamp DESC
        LIMIT ?
    """
    params.append(limit)

    with _lock:
        conn = _get_connection(db_path)
        try:
            cursor = conn.execute(query_str, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def get_entity_relations(entity: str, db_path: str = DB_PATH) -> Dict[str, List[Dict[str, Any]]]:
    """Retrieve all outgoing and incoming relations for a given entity."""
    if not entity or not entity.strip():
        return {"outgoing": [], "incoming": []}

    ent = entity.strip()
    outgoing = query_triples(subject=ent, db_path=db_path)
    incoming = query_triples(obj=ent, db_path=db_path)

    return {
        "outgoing": outgoing,
        "incoming": incoming
    }


def find_connections(entity_a: str, entity_b: str, max_hops: int = 2,
                     db_path: str = DB_PATH) -> List[List[Dict[str, Any]]]:
    """Find paths connecting entity_a and entity_b up to max_hops in the graph.

    Args:
        entity_a: Starting entity
        entity_b: Target entity
        max_hops: Max traversal depth (default 2)
        db_path: Database path

    Returns:
        List of relation paths (each path is a list of triple dicts)
    """
    if not entity_a or not entity_b:
        return []

    a_clean = entity_a.strip().lower()
    b_clean = entity_b.strip().lower()

    if a_clean == b_clean:
        return []

    # BFS path search
    paths: List[List[Dict[str, Any]]] = []
    queue: List[Tuple[str, List[Dict[str, Any]], set]] = [(entity_a.strip(), [], {a_clean})]

    while queue:
        curr_node, current_path, visited = queue.pop(0)

        if len(current_path) >= max_hops:
            continue

        # Get outgoing triples from curr_node
        triples = query_triples(subject=curr_node, db_path=db_path)
        for t in triples:
            target = t["object"]
            target_clean = target.lower()

            if target_clean == b_clean:
                paths.append(current_path + [t])
            elif target_clean not in visited and len(current_path) + 1 < max_hops:
                new_visited = set(visited)
                new_visited.add(target_clean)
                queue.append((target, current_path + [t], new_visited))

    return paths


def search_entities(keyword: str, limit: int = 20, db_path: str = DB_PATH) -> List[str]:
    """Search for unique entity names matching keyword across subjects and objects."""
    if not keyword or not keyword.strip():
        return []

    clean_kw = f"%{keyword.strip()}%"
    with _lock:
        conn = _get_connection(db_path)
        try:
            cursor = conn.execute("""
                SELECT DISTINCT subject AS entity FROM triples WHERE subject LIKE ?
                UNION
                SELECT DISTINCT object AS entity FROM triples WHERE object LIKE ?
                LIMIT ?
            """, (clean_kw, clean_kw, limit))
            rows = cursor.fetchall()
            return [row["entity"] for row in rows]
        finally:
            conn.close()


def export_subgraph_for_prompt(entities: List[str], max_triples: int = 8,
                               db_path: str = DB_PATH) -> str:
    """Format relevant triples for prompt injection given a list of contextual entities.

    Args:
        entities: List of entity names to retrieve triples for
        max_triples: Maximum number of triples to include
        db_path: Database path

    Returns:
        Formatted multi-line string for LLM system prompt injection
    """
    if not entities:
        return ""

    collected_triples: List[Dict[str, Any]] = []
    seen_ids = set()

    for ent in entities:
        rels = get_entity_relations(ent, db_path=db_path)
        for t in rels["outgoing"] + rels["incoming"]:
            if t["id"] not in seen_ids:
                seen_ids.add(t["id"])
                collected_triples.append(t)
            if len(collected_triples) >= max_triples:
                break
        if len(collected_triples) >= max_triples:
            break

    if not collected_triples:
        return ""

    lines = []
    for t in collected_triples[:max_triples]:
        lines.append(f"- ({t['subject']}) --[{t['predicate']}]--> ({t['object']})")

    return "Cognitive Knowledge Graph:\n" + "\n".join(lines)


def clear_graph(db_path: str = DB_PATH) -> None:
    """Clear all records from graph (primarily for testing and resets)."""
    with _lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM triples")
        finally:
            conn.close()
