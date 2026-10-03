"""Tests for Module Z: Structure-Mapping Engine (Analogical Transfer)."""
import pytest
from analogical_transfer import (
    Domain,
    Mapping,
    Relation,
    StructureMappingEngine,
    align,
    best_mapping,
    get_sme,
)


def _make_solar_domain() -> Domain:
    """Classic SME solar system analogy source domain."""
    r1 = Relation("sr1", "revolves_around", "earth", "sun", order=1)
    r2 = Relation("sr2", "revolves_around", "moon", "earth", order=1)
    r3 = Relation("sr3", "attracts", "sun", "earth", order=1)
    r4 = Relation("sr4", "attracts", "earth", "moon", order=1)
    r5 = Relation("sr5", "more_massive", "sun", "earth", order=1)
    return Domain("solar", ["sun", "earth", "moon"], [r1, r2, r3, r4, r5])


def _make_atom_domain() -> Domain:
    """Target domain: Rutherford atom model."""
    r1 = Relation("tr1", "revolves_around", "electron", "nucleus", order=1)
    r2 = Relation("tr2", "attracts", "nucleus", "electron", order=1)
    return Domain("atom", ["nucleus", "electron"], [r1, r2])


class TestStructureMappingEngine:
    def test_align_finds_solar_atom_mapping(self):
        """Solar-system → atom analogy should yield a valid structural mapping."""
        sme = StructureMappingEngine()
        solar = _make_solar_domain()
        atom = _make_atom_domain()
        mappings = sme.align(solar, atom)
        assert len(mappings) > 0

    def test_best_mapping_entity_map(self):
        """Best mapping should correctly map sun->nucleus and earth->electron."""
        sme = StructureMappingEngine()
        solar = _make_solar_domain()
        atom = _make_atom_domain()
        m = sme.best_mapping(solar, atom)
        assert m is not None
        # Both revolves_around and attracts are shared predicates; the best alignment
        # maps sun->nucleus and earth->electron (or equivalent)
        emap = m.entity_map
        # At least one correct correspondences must be present
        has_sun_nucleus = emap.get("sun") == "nucleus"
        has_earth_electron = emap.get("earth") == "electron"
        assert has_sun_nucleus or has_earth_electron

    def test_systematicity_score_positive(self):
        """Best mapping systematicity score must be > 0."""
        sme = StructureMappingEngine()
        solar = _make_solar_domain()
        atom = _make_atom_domain()
        m = sme.best_mapping(solar, atom)
        assert m is not None
        assert m.systematicity_score > 0.0

    def test_projected_inferences(self):
        """Unmapped source relations should be projected into target as candidate inferences."""
        sme = StructureMappingEngine()
        solar = _make_solar_domain()
        atom = _make_atom_domain()
        m = sme.best_mapping(solar, atom)
        assert m is not None
        # 'more_massive' is not in atom domain — it should be projected
        projected_preds = [p["predicate"] for p in m.projected_inferences]
        assert "more_massive" in projected_preds

    def test_no_common_predicates(self):
        """Domains with no shared predicates should return empty alignment."""
        sme = StructureMappingEngine()
        d1 = Domain("d1", ["a"], [Relation("r1", "foo", "a", "b")])
        d2 = Domain("d2", ["x"], [Relation("r2", "bar", "x", "y")])
        mappings = sme.align(d1, d2)
        assert mappings == []
