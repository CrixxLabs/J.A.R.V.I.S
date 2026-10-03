"""Tests for Module AF: Meta-Ontology Evolution Engine."""
import pytest
from meta_ontology import (
    ConceptMigrationRecord,
    DiscoveredPrimitive,
    FormalConcept,
    MetaOntology,
    discover_primitives,
    evolve_concept_schema,
    get_meta_ontology,
    induce_formal_concepts,
)


class TestMetaOntology:
    def test_primitive_discovery_compression(self):
        ontology = MetaOntology()

        # Programs containing repeated sub-expression `math.sqrt(x**2 + y**2)`
        programs = [
            """
def distance_1(x, y):
    val = math.sqrt(x**2 + y**2)
    return val + 1
""",
            """
def distance_2(x, y):
    val = math.sqrt(x**2 + y**2)
    return val * 2
""",
            """
def distance_3(x, y):
    val = math.sqrt(x**2 + y**2)
    return val - 5
""",
        ]

        prims = ontology.discover_primitives(programs, min_frequency=2, kappa=0.0)
        assert len(prims) > 0

        # Find the euclidean distance expression
        dist_prim = next((p for p in prims if "math.sqrt" in p.subexpression_code or "x ** 2" in p.subexpression_code), None)
        assert dist_prim is not None
        assert dist_prim.frequency >= 3
        assert dist_prim.is_accepted is True

    def test_formal_concept_induction(self):
        ontology = MetaOntology()

        # Binary context: animals with features
        context = {
            "dog": {"mammal", "four_legged", "barks", "carnivore"},
            "cat": {"mammal", "four_legged", "meows", "carnivore"},
            "eagle": {"bird", "two_legged", "flies", "carnivore"},
        }

        concepts = ontology.induce_formal_concepts(context)
        assert len(concepts) > 0

        # Check that a common quadruped mammal category is induced
        mammal_concept = next((c for c in concepts if "mammal" in c.intent), None)
        assert mammal_concept is not None
        assert "dog" in mammal_concept.extent
        assert "cat" in mammal_concept.extent
        assert "eagle" not in mammal_concept.extent

    def test_bitemporal_schema_evolution(self):
        ontology = MetaOntology()

        mig = ontology.evolve_concept_schema(
            source_concept_id="concept_user_location_v1",
            target_concept_id="concept_user_geopoint_v2",
            transform_mapping={"lat_lon_str": "coordinates_tuple"},
            reason="Upgraded scalar string geolocation to WGS84 tuple schema",
        )

        assert mig.source_concept_id == "concept_user_location_v1"
        assert mig.target_concept_id == "concept_user_geopoint_v2"
        assert mig.valid_to is None

        # Subsequent migration terminates the previous one
        mig2 = ontology.evolve_concept_schema(
            source_concept_id="concept_user_location_v1",
            target_concept_id="concept_user_geopoint_v3",
            transform_mapping={"coordinates_tuple": "geojson_point"},
            reason="Upgraded to GeoJSON format",
        )

        assert mig.valid_to is not None
        assert mig2.valid_to is None
