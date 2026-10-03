"""Tests for Module AR: Multimodal Spatial Engineering Canvas."""
import math
import pytest
from spatial_engineering import (
    BoundingBox,
    CADSolid,
    ParetoDesignPoint,
    SpatialEngineeringCanvas,
    StructuralSurrogateReport,
    compute_cantilever_beam,
    compute_euler_buckling,
    explore_pareto_tradeoffs,
    get_spatial_engineering_canvas,
)


class TestSpatialEngineering:
    def test_cad_solid_geometry_and_mass(self):
        canvas = SpatialEngineeringCanvas()
        box = CADSolid(
            solid_id="bracket_1",
            solid_type="box",
            parameters={"width": 0.10, "height": 0.05, "depth": 0.20},  # 0.001 m^3
            density_kg_m3=2700.0,
        )
        vol = box.compute_volume()
        assert math.isclose(vol, 0.001, rel_tol=1e-4)
        mass = box.compute_mass()
        assert math.isclose(mass, 2.70, rel_tol=1e-4)

    def test_solid_interference_detection(self):
        canvas = SpatialEngineeringCanvas()
        s1 = CADSolid("s1", "box", {"width": 1.0, "height": 1.0, "depth": 1.0}, position=(0.0, 0.0, 0.0))
        # s2 overlaps with s1
        s2 = CADSolid("s2", "box", {"width": 1.0, "height": 1.0, "depth": 1.0}, position=(0.5, 0.0, 0.0))
        # s3 is far away
        s3 = CADSolid("s3", "box", {"width": 1.0, "height": 1.0, "depth": 1.0}, position=(5.0, 5.0, 5.0))

        canvas.add_solid(s1)
        canvas.add_solid(s2)
        canvas.add_solid(s3)

        assert canvas.check_interference("s1", "s2") is True
        assert canvas.check_interference("s1", "s3") is False

    def test_cantilever_beam_structural_surrogate(self):
        canvas = SpatialEngineeringCanvas()
        # 1m long, 0.02m wide, 0.04m high aluminum beam under 500N load
        report = canvas.compute_cantilever_beam(
            length_m=1.0,
            width_m=0.02,
            height_m=0.04,
            load_n=500.0,
        )
        assert report.check_type == "CANTILEVER_BEAM"
        assert report.max_deflection_m > 0.0
        assert report.max_stress_pa > 0.0
        assert report.safety_factor > 0.0
        assert "Analytical Surrogate" in report.fidelity_stamp

    def test_euler_buckling_surrogate(self):
        canvas = SpatialEngineeringCanvas()
        # 2m column, 0.03m x 0.03m under 1000N axial load
        report = canvas.compute_euler_buckling(
            length_m=2.0,
            width_m=0.03,
            height_m=0.03,
            axial_load_n=1000.0,
        )
        assert report.check_type == "EULER_BUCKLING"
        assert report.critical_buckling_load_n is not None
        assert report.critical_buckling_load_n > 0.0
        assert report.safety_factor > 1.0

    def test_pareto_tradeoff_exploration(self):
        canvas = SpatialEngineeringCanvas()
        pareto_points = canvas.explore_pareto_tradeoffs(
            length_m=0.5,
            load_n=200.0,
            width_range=(0.01, 0.03, 3),
            height_range=(0.02, 0.05, 3),
        )
        assert len(pareto_points) >= 1
        for p in pareto_points:
            assert p.is_pareto_optimal is True
            assert p.mass_kg > 0.0
            assert p.deflection_mm > 0.0
