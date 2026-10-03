"""Meta-Ontology Evolution Engine for J.A.R.V.I.S. — MARK VIII.

Module AF:
  1. Primitive Discovery by Program Graph Compression (Stitch-Style):
     - Extracts recurring sub-expressions across program ASTs.
     - Accepts new library primitives only when Minimum Description Length decreases:
       Delta L = L_new - L_old < -kappa.
  2. Category Induction & Formal Concept Lattice:
     - Induces categories over state feature vectors and formal attribute-object matrices.
     - Builds concept lattices with formal extents (instances) and intents (attributes).
  3. Bitemporal Schema Evolution:
     - Tracks immutable concept identifiers with forward/backward migration mappings.
     - Records valid-time and transaction-time intervals for semantic versioning.
"""
from __future__ import annotations

import ast
import collections
import hashlib
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.meta_ontology")

_lock = threading.RLock()


@dataclass
class DiscoveredPrimitive:
    primitive_id: str
    subexpression_code: str
    frequency: int
    compression_gain_delta_l: float
    is_accepted: bool
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FormalConcept:
    concept_id: str
    name: str
    extent: List[str]      # Objects belonging to the concept
    intent: List[str]      # Attributes shared by all objects in the extent
    level: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConceptMigrationRecord:
    migration_id: str
    source_concept_id: str
    target_concept_id: str
    transform_mapping: Dict[str, str]
    valid_from: float
    valid_to: Optional[float]
    tx_time: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MetaOntology:
    """Meta-Ontology Evolution Engine managing program primitives, concept lattices, and bitemporal schema migrations."""

    def __init__(self):
        self._primitives: Dict[str, DiscoveredPrimitive] = {}
        self._concepts: Dict[str, FormalConcept] = {}
        self._migrations: List[ConceptMigrationRecord] = []

    # ------------------------------------------------------------------
    # Stitch-Style Primitive Discovery via AST Compression
    # ------------------------------------------------------------------

    def _extract_ast_subtrees(self, code_str: str) -> List[str]:
        """Parse code and extract unparsed sub-expressions (calls, comparisons, binops)."""
        subtrees = []
        try:
            tree = ast.parse(code_str)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Call, ast.BinOp, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.Subscript)):
                    try:
                        expr_code = ast.unparse(node).strip()
                        if len(expr_code) > 3 and "\n" not in expr_code:
                            subtrees.append(expr_code)
                    except Exception:
                        pass
        except Exception as exc:
            log.debug(f"[MetaOntology] AST parse failed for subtrees: {exc}")
        return subtrees

    def discover_primitives(
        self,
        programs: List[str],
        min_frequency: int = 2,
        kappa: float = 5.0,
    ) -> List[DiscoveredPrimitive]:
        """Extract recurring sub-expressions and evaluate description length delta (Delta L)."""
        subtree_counts: Dict[str, int] = collections.defaultdict(int)

        # Baseline total program size in characters / AST tokens
        base_total_len = sum(len(p) for p in programs)

        for p in programs:
            subtrees = self._extract_ast_subtrees(p)
            for st in set(subtrees):
                subtree_counts[st] += 1

        discovered: List[DiscoveredPrimitive] = []

        for expr, freq in subtree_counts.items():
            if freq < min_frequency:
                continue

            expr_len = len(expr)
            # Size of new library primitive definition: expr_len + overhead (~15 chars)
            library_cost = expr_len + 15

            # Savings from replacing `freq` occurrences with short symbol name (e.g. `fn_x()` ~ 6 chars)
            usage_savings = freq * max(0, expr_len - 6)

            # Delta L = New_Total_Length - Old_Total_Length
            delta_l = library_cost - usage_savings
            is_accepted = delta_l < -kappa

            pid = f"prim_{hashlib.sha256(expr.encode('utf-8')).hexdigest()[:8]}"
            primitive = DiscoveredPrimitive(
                primitive_id=pid,
                subexpression_code=expr,
                frequency=freq,
                compression_gain_delta_l=round(delta_l, 2),
                is_accepted=is_accepted,
                metadata={"library_cost": library_cost, "usage_savings": usage_savings},
            )
            discovered.append(primitive)

            if is_accepted:
                with _lock:
                    self._primitives[pid] = primitive

        discovered.sort(key=lambda p: p.compression_gain_delta_l)

        try:
            get_registry().set_capability_evidence(
                "META_ONTOLOGY",
                EvidenceLevel.LIVE,
                f"Discovered {len(discovered)} candidate primitives ({sum(1 for p in discovered if p.is_accepted)} accepted)",
                source="meta_ontology.discover_primitives",
            )
        except Exception:
            pass

        return discovered

    # ------------------------------------------------------------------
    # Category Induction & Formal Concept Lattice
    # ------------------------------------------------------------------

    def induce_formal_concepts(
        self,
        object_attributes: Dict[str, Set[str]],
    ) -> List[FormalConcept]:
        """Build formal concept lattice (Extents & Intents) from binary context (G, M, I)."""
        if not object_attributes:
            return []

        all_attributes: Set[str] = set()
        for attrs in object_attributes.values():
            all_attributes.update(attrs)

        # Generate closed attribute sets (intents) and their extents
        concepts: List[FormalConcept] = []
        seen_extents: Set[Tuple[str, ...]] = set()

        # Attribute combinations
        for attr in all_attributes:
            # Objects possessing this attribute
            extent = sorted([obj for obj, attrs in object_attributes.items() if attr in attrs])
            if not extent:
                continue

            # Full shared intent for this extent
            shared_intent = set(all_attributes)
            for obj in extent:
                shared_intent &= object_attributes[obj]

            extent_tuple = tuple(extent)
            if extent_tuple in seen_extents:
                continue
            seen_extents.add(extent_tuple)

            cid = f"concept_{uuid.uuid4().hex[:8]}"
            concept = FormalConcept(
                concept_id=cid,
                name=f"Category_{'_'.join(sorted(shared_intent))}",
                extent=extent,
                intent=sorted(shared_intent),
                level=len(shared_intent),
            )
            concepts.append(concept)
            with _lock:
                self._concepts[cid] = concept

        concepts.sort(key=lambda c: len(c.extent), reverse=True)
        return concepts

    # ------------------------------------------------------------------
    # Bitemporal Schema Evolution
    # ------------------------------------------------------------------

    def evolve_concept_schema(
        self,
        source_concept_id: str,
        target_concept_id: str,
        transform_mapping: Dict[str, str],
        reason: str,
        valid_from: Optional[float] = None,
    ) -> ConceptMigrationRecord:
        """Register bitemporal concept migration with transform mappings."""
        now = time.time()
        vf = valid_from if valid_from is not None else now
        mid = f"mig_{uuid.uuid4().hex[:8]}"

        record = ConceptMigrationRecord(
            migration_id=mid,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            transform_mapping=transform_mapping,
            valid_from=vf,
            valid_to=None,
            tx_time=now,
            reason=reason,
        )

        with _lock:
            # Terminate previous open migrations for source
            for m in self._migrations:
                if m.source_concept_id == source_concept_id and m.valid_to is None:
                    m.valid_to = vf
            self._migrations.append(record)

        log.info(f"[MetaOntology] Schema evolved {source_concept_id} -> {target_concept_id} ({reason})")
        return record

    def get_concept_migrations(self, concept_id: str) -> List[ConceptMigrationRecord]:
        with _lock:
            return [
                m for m in self._migrations
                if m.source_concept_id == concept_id or m.target_concept_id == concept_id
            ]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_meta_ontology_instance: Optional[MetaOntology] = None


def get_meta_ontology() -> MetaOntology:
    global _meta_ontology_instance
    if _meta_ontology_instance is None:
        with _lock:
            if _meta_ontology_instance is None:
                _meta_ontology_instance = MetaOntology()
    return _meta_ontology_instance


def discover_primitives(programs: List[str], min_frequency: int = 2, kappa: float = 5.0) -> List[DiscoveredPrimitive]:
    return get_meta_ontology().discover_primitives(programs, min_frequency, kappa)


def induce_formal_concepts(object_attributes: Dict[str, Set[str]]) -> List[FormalConcept]:
    return get_meta_ontology().induce_formal_concepts(object_attributes)


def evolve_concept_schema(
    source_concept_id: str, target_concept_id: str, transform_mapping: Dict[str, str], reason: str
) -> ConceptMigrationRecord:
    return get_meta_ontology().evolve_concept_schema(source_concept_id, target_concept_id, transform_mapping, reason)
