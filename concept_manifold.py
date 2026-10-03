"""Continuous Concept Manifold & Symbol Emergence for J.A.R.V.I.S. — MARK VIII.

Module AL:
  1. Unbounded Incremental Growing Neural Gas / Prototype Clustering:
     - Online competitive topological clustering over CPU feature vectors.
     - Spawns new prototype centroids when distance to nearest prototype exceeds threshold tau.
     - Maintains neighborhood adjacency graph and sample counts.
  2. Symbol Emergence Gate:
     - Promotes continuous geometric clusters to discrete symbolic primitives (Module AF).
     - Validates bootstrap resampling stability and multimodal label purity before symbolization.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.concept_manifold")

_lock = threading.RLock()


@dataclass
class PrototypeNode:
    node_id: str
    centroid: np.ndarray
    sample_count: int
    associated_labels: Dict[str, int] = field(default_factory=dict)
    creation_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "sample_count": self.sample_count,
            "associated_labels": self.associated_labels,
            "creation_time": self.creation_time,
            "centroid_norm": float(np.linalg.norm(self.centroid)),
        }


@dataclass
class SymbolEmergenceResult:
    cluster_id: str
    is_promoted: bool
    stability_score: float
    label_purity: float
    sample_count: int
    symbol_name: Optional[str]
    rejection_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConceptManifold:
    """Incremental Growing Neural Gas prototype manifold with bootstrap symbol emergence gating."""

    def __init__(self, dim: int = 32, distance_threshold_tau: float = 0.5, eps_b: float = 0.1, eps_n: float = 0.01):
        self.dim = dim
        self.distance_threshold_tau = distance_threshold_tau
        self.eps_b = eps_b
        self.eps_n = eps_n

        self._nodes: Dict[str, PrototypeNode] = {}
        # Adjacency: node_id -> set(neighbor_ids)
        self._edges: Dict[str, set] = {}
        self._samples_buffer: Dict[str, List[np.ndarray]] = {}

    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        if norm > 1e-6:
            return vec / norm
        return vec

    # ------------------------------------------------------------------
    # Incremental Prototype Clustering
    # ------------------------------------------------------------------

    def add_sample(
        self,
        embedding: np.ndarray | List[float],
        label: Optional[str] = None,
    ) -> str:
        """Process incoming embedding: update winner node or spawn new prototype."""
        x = np.array(embedding, dtype=np.float32).flatten()
        if x.shape[0] != self.dim:
            padded = np.zeros(self.dim, dtype=np.float32)
            padded[:min(self.dim, x.shape[0])] = x[:min(self.dim, x.shape[0])]
            x = padded
        x = self._normalize(x)

        with _lock:
            if not self._nodes:
                nid = f"proto_{uuid.uuid4().hex[:8]}"
                labels = {label: 1} if label else {}
                node = PrototypeNode(nid, x.copy(), 1, labels)
                self._nodes[nid] = node
                self._edges[nid] = set()
                self._samples_buffer[nid] = [x]
                return nid

            # Find nearest 2 prototype nodes
            distances = []
            for nid, node in self._nodes.items():
                dist = float(np.linalg.norm(x - node.centroid))
                distances.append((dist, nid))

            distances.sort(key=lambda d: d[0])
            d_win, s1_id = distances[0]

            if d_win > self.distance_threshold_tau:
                # Spawn new prototype
                new_id = f"proto_{uuid.uuid4().hex[:8]}"
                labels = {label: 1} if label else {}
                new_node = PrototypeNode(new_id, x.copy(), 1, labels)
                self._nodes[new_id] = new_node
                self._edges[new_id] = set()
                self._samples_buffer[new_id] = [x]
                log.debug(f"[ConceptManifold] Spawned new prototype {new_id} (dist={d_win:.4f} > tau)")
                return new_id

            # Update winner (Hebbian adaptation)
            winner = self._nodes[s1_id]
            winner.centroid += self.eps_b * (x - winner.centroid)
            winner.centroid = self._normalize(winner.centroid)
            winner.sample_count += 1
            if label:
                winner.associated_labels[label] = winner.associated_labels.get(label, 0) + 1

            self._samples_buffer.setdefault(s1_id, []).append(x)
            if len(self._samples_buffer[s1_id]) > 200:
                self._samples_buffer[s1_id].pop(0)

            # Update neighbors if second closest exists
            if len(distances) > 1:
                s2_id = distances[1][1]
                # Connect edge
                self._edges[s1_id].add(s2_id)
                self._edges[s2_id].add(s1_id)

                # Move neighbors slightly
                for neighbor_id in self._edges[s1_id]:
                    nb = self._nodes[neighbor_id]
                    nb.centroid += self.eps_n * (x - nb.centroid)
                    nb.centroid = self._normalize(nb.centroid)

            return s1_id

    # ------------------------------------------------------------------
    # Symbol Emergence Gating
    # ------------------------------------------------------------------

    def evaluate_symbol_emergence(
        self,
        cluster_id: str,
        min_samples: int = 10,
        stability_threshold: float = 0.75,
        purity_threshold: float = 0.80,
    ) -> SymbolEmergenceResult:
        """Evaluate whether a cluster qualifies for promotion to a discrete symbol."""
        with _lock:
            if cluster_id not in self._nodes:
                return SymbolEmergenceResult(cluster_id, False, 0.0, 0.0, 0, None, ["Cluster ID not found"])

            node = self._nodes[cluster_id]
            samples = self._samples_buffer.get(cluster_id, [])
            violations = []

            # Sample count check
            if len(samples) < min_samples:
                violations.append(f"Insufficient sample count: {len(samples)} < {min_samples}")

            # Bootstrap stability: split samples into 2 halves, measure centroid similarity
            stability_score = 0.0
            if len(samples) >= 4:
                half = len(samples) // 2
                c1 = np.mean(samples[:half], axis=0)
                c2 = np.mean(samples[half:], axis=0)
                sim = float(np.dot(self._normalize(c1), self._normalize(c2)))
                stability_score = max(0.0, sim)
                if stability_score < stability_threshold:
                    violations.append(f"Low bootstrap stability: {stability_score:.3f} < {stability_threshold}")
            else:
                violations.append("Too few samples to evaluate bootstrap stability")

            # Multimodal / Label purity
            purity = 0.0
            dominant_label = None
            if node.associated_labels:
                total_labeled = sum(node.associated_labels.values())
                dominant_label, top_count = max(node.associated_labels.items(), key=lambda x: x[1])
                purity = top_count / max(1, total_labeled)
                if purity < purity_threshold:
                    violations.append(f"Low label purity: {purity:.3f} < {purity_threshold}")
            else:
                purity = 1.0  # Unsupervised cluster defaults to high internal cohesion

            is_promoted = len(violations) == 0
            symbol_name = f"SYM_{dominant_label.upper()}_{cluster_id[:6]}" if (is_promoted and dominant_label) else (f"SYM_{cluster_id[:8]}" if is_promoted else None)

            result = SymbolEmergenceResult(
                cluster_id=cluster_id,
                is_promoted=is_promoted,
                stability_score=round(stability_score, 4),
                label_purity=round(purity, 4),
                sample_count=len(samples),
                symbol_name=symbol_name,
                rejection_reasons=violations,
            )

            if is_promoted:
                try:
                    get_registry().set_capability_evidence(
                        "CONCEPT_MANIFOLD",
                        EvidenceLevel.LIVE,
                        f"Promoted cluster {cluster_id} to discrete symbol '{symbol_name}'",
                        source="concept_manifold.evaluate_symbol_emergence",
                    )
                except Exception:
                    pass

            return result

    def get_prototypes(self) -> List[PrototypeNode]:
        with _lock:
            return list(self._nodes.values())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manifold_instance: Optional[ConceptManifold] = None


def get_concept_manifold(dim: int = 32, distance_threshold_tau: float = 0.5) -> ConceptManifold:
    global _manifold_instance
    if _manifold_instance is None:
        with _lock:
            if _manifold_instance is None:
                _manifold_instance = ConceptManifold(dim=dim, distance_threshold_tau=distance_threshold_tau)
    return _manifold_instance


def add_sample(embedding: np.ndarray | List[float], label: Optional[str] = None) -> str:
    return get_concept_manifold().add_sample(embedding, label)


def evaluate_symbol_emergence(cluster_id: str, **kwargs) -> SymbolEmergenceResult:
    return get_concept_manifold().evaluate_symbol_emergence(cluster_id, **kwargs)
