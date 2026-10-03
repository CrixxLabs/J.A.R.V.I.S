"""Tests for Module AL: Continuous Concept Manifold & Symbol Emergence."""
import numpy as np
import pytest
from concept_manifold import (
    ConceptManifold,
    PrototypeNode,
    SymbolEmergenceResult,
    add_sample,
    evaluate_symbol_emergence,
    get_concept_manifold,
)


class TestConceptManifold:
    def test_incremental_clustering_spawns_prototypes(self):
        manifold = ConceptManifold(dim=8, distance_threshold_tau=0.4)

        # First sample creates first prototype
        v1 = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        id1 = manifold.add_sample(v1, label="cat")
        assert len(manifold.get_prototypes()) == 1

        # Second sample close to first updates it
        v1_close = np.array([0.98, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        id1_close = manifold.add_sample(v1_close, label="cat")
        assert id1_close == id1
        assert len(manifold.get_prototypes()) == 1

        # Third sample orthogonal / far creates new prototype
        v2 = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
        id2 = manifold.add_sample(v2, label="car")
        assert id2 != id1
        assert len(manifold.get_prototypes()) == 2

    def test_symbol_emergence_gating_success(self):
        manifold = ConceptManifold(dim=8, distance_threshold_tau=0.5)

        # Feed 15 consistent samples with same label
        base = np.array([1.0, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        for _ in range(15):
            noise = np.random.randn(8) * 0.02
            cid = manifold.add_sample(base + noise, label="cat")

        res = manifold.evaluate_symbol_emergence(cid, min_samples=10, stability_threshold=0.8, purity_threshold=0.8)
        assert res.is_promoted is True
        assert res.symbol_name is not None
        assert "CAT" in res.symbol_name
        assert res.stability_score > 0.8
        assert res.label_purity == 1.0

    def test_symbol_emergence_gating_rejections(self):
        manifold = ConceptManifold(dim=8, distance_threshold_tau=0.5)

        # Too few samples
        base = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        cid = manifold.add_sample(base, label="dog")

        res_few = manifold.evaluate_symbol_emergence(cid, min_samples=10)
        assert res_few.is_promoted is False
        assert any("Insufficient sample count" in r for r in res_few.rejection_reasons)
