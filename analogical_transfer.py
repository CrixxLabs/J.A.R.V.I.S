"""Analogical Transfer Engine for J.A.R.V.I.S. — MARK VIII.

Module Z: Structure-Mapping Engine (SME)
  1. Structural Alignment:
     - Represents domains as typed, relational graphs of (entity, relation, entity) triples.
     - Aligns source- to target-domain graphs by mapping entities and relations that
       participate in structurally identical (isomorphic) subgraphs — Gentner's SME.
  2. Systematicity Principle:
     - Prefers deeper / more connected relational correspondences over shallow
       feature-level surface matches (systematicity score = depth * connectivity).
  3. Candidate Inference Projection:
     - Projects unmapped relations from the source into the target as candidate
       (tentative) inferences.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.analogical_transfer")

_lock = threading.RLock()


@dataclass
class Relation:
    """A typed directed relation between two entities."""
    relation_id: str
    predicate: str
    arg1: str
    arg2: str
    order: int = 1          # 1 = first-order, 2 = higher-order (relation over relations)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Domain:
    """A relational knowledge domain — set of entities and typed relations."""
    domain_id: str
    entities: List[str] = field(default_factory=list)
    relations: List[Relation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Mapping:
    """A structural correspondence between source and target domains."""
    mapping_id: str
    entity_map: Dict[str, str]          # source entity -> target entity
    relation_correspondences: List[Tuple[str, str]]  # (source_rid, target_rid)
    systematicity_score: float
    projected_inferences: List[Dict[str, Any]]   # candidate relations for target
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StructureMappingEngine:
    """SME-style analogical mapper: structural alignment + inference projection."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Core alignment
    # ------------------------------------------------------------------

    def _build_rel_index(
        self, relations: List[Relation]
    ) -> Dict[str, List[Relation]]:
        """Index relations by predicate."""
        idx: Dict[str, List[Relation]] = {}
        for r in relations:
            idx.setdefault(r.predicate, []).append(r)
        return idx

    def _systematicity_score(
        self, matched_relations: List[Tuple[Relation, Relation]], entity_map: Dict[str, str]
    ) -> float:
        """Score an alignment by depth (HOT) and connectivity (shared args)."""
        if not matched_relations:
            return 0.0

        # Depth: prefer higher-order relations
        depth_score = sum(sr.order + tr.order for sr, tr in matched_relations) / len(matched_relations)

        # Connectivity: reward entity-map richness
        connectivity = len(entity_map)

        return round(depth_score * (1.0 + 0.1 * connectivity), 4)

    def align(
        self,
        source: Domain,
        target: Domain,
        max_mappings: int = 5,
    ) -> List[Mapping]:
        """Find structural correspondences between source and target domains.

        Returns candidate mappings ranked by systematicity score (best first).
        """
        src_idx = self._build_rel_index(source.relations)
        tgt_idx = self._build_rel_index(target.relations)

        # Common predicates — alignment seed
        common_predicates = set(src_idx) & set(tgt_idx)
        if not common_predicates:
            log.info("[SME] No common predicates; empty alignment.")
            return []

        # Generate candidate entity maps via predicate-level product
        # For each shared predicate, try arg1->arg1, arg2->arg2 alignment
        candidate_maps: List[Dict[str, str]] = []
        for pred in common_predicates:
            for sr in src_idx[pred]:
                for tr in tgt_idx[pred]:
                    cmap = {sr.arg1: tr.arg1, sr.arg2: tr.arg2}
                    candidate_maps.append(cmap)

        # Consolidate and score each entity map
        mappings: List[Mapping] = []
        seen_maps: Set[FrozenSet] = set()

        for emap in candidate_maps:
            frozen_key = frozenset(emap.items())
            if frozen_key in seen_maps:
                continue
            seen_maps.add(frozen_key)

            # Find matching source-target relation pairs under this entity map
            matched: List[Tuple[Relation, Relation]] = []
            relation_corr: List[Tuple[str, str]] = []

            for pred in common_predicates:
                for sr in src_idx.get(pred, []):
                    mapped_arg1 = emap.get(sr.arg1)
                    mapped_arg2 = emap.get(sr.arg2)
                    for tr in tgt_idx.get(pred, []):
                        if tr.arg1 == mapped_arg1 and tr.arg2 == mapped_arg2:
                            matched.append((sr, tr))
                            relation_corr.append((sr.relation_id, tr.relation_id))

            if not matched:
                continue

            score = self._systematicity_score(matched, emap)

            # Project source relations not yet represented in target
            matched_src_ids = {sr.relation_id for sr, _ in matched}
            projected = []
            for sr in source.relations:
                if sr.relation_id in matched_src_ids:
                    continue
                # Check if both args are mapped
                if sr.arg1 in emap and sr.arg2 in emap:
                    projected.append({
                        "predicate": sr.predicate,
                        "arg1": emap[sr.arg1],
                        "arg2": emap[sr.arg2],
                        "order": sr.order,
                        "source_relation_id": sr.relation_id,
                    })

            mappings.append(
                Mapping(
                    mapping_id=f"map_{uuid.uuid4().hex[:8]}",
                    entity_map=emap,
                    relation_correspondences=relation_corr,
                    systematicity_score=score,
                    projected_inferences=projected,
                )
            )

        mappings.sort(key=lambda m: m.systematicity_score, reverse=True)
        result = mappings[:max_mappings]

        try:
            get_registry().set_capability_evidence(
                "ANALOGICAL_TRANSFER",
                EvidenceLevel.LIVE,
                f"Aligned {source.domain_id}->{target.domain_id}: "
                f"{len(result)} mappings, best_score={result[0].systematicity_score if result else 0}",
                source="analogical_transfer.align",
            )
        except Exception:
            pass

        return result

    def best_mapping(self, source: Domain, target: Domain) -> Optional[Mapping]:
        """Return the single highest-systematicity mapping."""
        ms = self.align(source, target, max_mappings=1)
        return ms[0] if ms else None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_sme_instance: Optional[StructureMappingEngine] = None


def get_sme() -> StructureMappingEngine:
    global _sme_instance
    if _sme_instance is None:
        with _lock:
            if _sme_instance is None:
                _sme_instance = StructureMappingEngine()
    return _sme_instance


def align(source: Domain, target: Domain, max_mappings: int = 5) -> List[Mapping]:
    return get_sme().align(source, target, max_mappings)


def best_mapping(source: Domain, target: Domain) -> Optional[Mapping]:
    return get_sme().best_mapping(source, target)
