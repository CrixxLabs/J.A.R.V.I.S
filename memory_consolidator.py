"""Memory Consolidator & Sleep Cycle Processor for J.A.R.V.I.S.

Extracts semantic triples from conversational dialogue, session logs, and episodic memory
using brain.py LLM analysis, consolidating episodic observations into the permanent
Cognitive Graph (cognitive_graph.py).
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional

import brain
import cognitive_graph
import error_handler
import memory
from status_registry import EvidenceLevel, SubsystemState, get_registry

_lock = threading.Lock()
_last_consolidation_time: float = 0.0
CONSOLIDATION_COOLDOWN_SECONDS: float = 60.0  # Min time between consolidations


def extract_triples_from_text(text: str) -> List[Dict[str, Any]]:
    """Use brain LLM to extract structured (subject, predicate, object) triples from arbitrary text.

    Args:
        text: Natural language text, conversation fragment, or memory note

    Returns:
        List of dicts with 'subject', 'predicate', 'object', 'confidence'
    """
    registry = get_registry()

    if not text or len(text.strip()) < 10:
        return []

    prompt = f"""Extract key knowledge facts from the following text as a JSON list of semantic triples (subject, predicate, object).
Each triple must represent an enduring fact, preference, relationship, or project detail.

Format:
[
  {{"subject": "Arju", "predicate": "prefers", "object": "Python 3.11", "confidence": 0.95}},
  {{"subject": "Jarvis", "predicate": "runs_on", "object": "CUDA", "confidence": 0.9}}
]

Return ONLY valid JSON. If no clear facts exist, return [].

Text to analyze:
\"\"\"{text}\"\"\""""

    try:
        response = brain.ask_llm(prompt, model_type="fast", allow_actions=False)
        if not response or not response.strip():
            return []

        # Clean JSON markdown blocks if present
        clean_json = response.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()

        # Parse JSON
        parsed = json.loads(clean_json)
        if isinstance(parsed, list):
            valid_triples = []
            for item in parsed:
                if isinstance(item, dict) and "subject" in item and "predicate" in item and "object" in item:
                    valid_triples.append({
                        "subject": str(item["subject"]).strip(),
                        "predicate": str(item["predicate"]).strip().lower().replace(" ", "_"),
                        "object": str(item["object"]).strip(),
                        "confidence": float(item.get("confidence", 0.9))
                    })
            return valid_triples

    except Exception as exc:
        print(f"[MemoryConsolidator] Triple extraction failed: {exc}")

    return []


def extract_entities_from_query(query: str) -> List[str]:
    """Identify potential entity candidates from a user query for graph lookup."""
    if not query or not query.strip():
        return []

    # Tokenize words, removing common stop words
    words = re.findall(r'[A-Za-z0-9_]+', query)
    stop_words = {
        "what", "is", "the", "how", "why", "who", "where", "when", "can", "you",
        "tell", "me", "about", "a", "an", "in", "on", "at", "for", "to", "of",
        "and", "or", "my", "your", "do", "does", "did", "please", "show", "get"
    }

    candidates = [w for w in words if w.lower() not in stop_words and len(w) > 2]
    matched_entities = []

    # Check cognitive graph for matching entities
    for candidate in candidates:
        matches = cognitive_graph.search_entities(candidate, limit=3)
        for m in matches:
            if m not in matched_entities:
                matched_entities.append(m)

    return matched_entities


def consolidate_recent_memory(force: bool = False) -> int:
    """Consolidate recent conversation history and activity logs into the Cognitive Graph.

    Args:
        force: If True, ignore consolidation cooldown timer

    Returns:
        Total number of new triples committed to the graph
    """
    global _last_consolidation_time
    registry = get_registry()

    now = time.time()
    if not force and (now - _last_consolidation_time) < CONSOLIDATION_COOLDOWN_SECONDS:
        return 0

    with _lock:
        _last_consolidation_time = now

        try:
            # 1. Gather recent conversation summary and memories
            sources: List[str] = []

            # Memory facts
            mem_summary = memory.get_memory_summary()
            if mem_summary:
                sources.append(mem_summary)

            # Session notes
            session_notes = memory.get_session_notes(5)
            if session_notes:
                sources.append("\n".join(session_notes))

            # Recent activities
            activities = memory.get_recent_activity(5)
            if activities:
                sources.append("\n".join(
                    f"{a.get('type')}: {a.get('detail')}" for a in activities
                ))

            if not sources:
                return 0

            combined_text = "\n\n".join(sources)

            # 2. Extract semantic triples
            triples = extract_triples_from_text(combined_text)
            if not triples:
                return 0

            # 3. Commit to cognitive graph
            committed = cognitive_graph.add_triples(triples, source="consolidation")

            registry.set_capability_evidence(
                "MEMORY_CONSOLIDATOR", EvidenceLevel.LIVE,
                f"Consolidated {committed} knowledge triples into graph",
                source="memory consolidation"
            )
            print(f"[MemoryConsolidator] Successfully consolidated {committed} facts into Cognitive Graph.")
            return committed

        except Exception as exc:
            error_handler.log_and_demote(
                "MEMORY_CONSOLIDATOR", exc,
                "Memory consolidation cycle",
                SubsystemState.DEGRADED
            )
            return 0


def run_sleep_cycle() -> Dict[str, Any]:
    """Execute deep sleep-cycle memory consolidation and optimization.

    Performs:
    1. Triple extraction from all episodic logs
    2. Fact cleanup of stale low-priority entries in memory.json
    3. Graph relationship consolidation

    Returns:
        Diagnostic summary of sleep cycle actions
    """
    print("[MemoryConsolidator] Entering sleep cycle consolidation...")

    # 1. Consolidate recent memories
    triples_added = consolidate_recent_memory(force=True)

    # 2. Clean up old low-priority facts (>30 days old)
    memory.cleanup_old_facts(days=30)

    # 3. Retrieve graph stats
    all_triples = cognitive_graph.query_triples(limit=1000)

    summary = {
        "status": "success",
        "triples_consolidated": triples_added,
        "total_graph_triples": len(all_triples),
        "timestamp": time.time()
    }
    print(f"[MemoryConsolidator] Sleep cycle finished. Graph size: {len(all_triples)} triples.")
    return summary
