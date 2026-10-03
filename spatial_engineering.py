"""Multimodal Spatial Engineering Canvas for J.A.R.V.I.S. — MARK VIII.

Module AR:
  1. Parametric Geometry Engine:
     - Programmatic 3D CAD modeling and constructive solid geometry
       (analytical volume, mass, center of mass, bounding boxes, interference checking).
  2. Physics & Structural Surrogates:
     - Analytical beam deflection, bending stress, and Euler column buckling
       checks with explicit fidelity stamps ("Linear Static Coarse, +/-15%").
  3. Multi-Objective Trade-Off Explorer:
     - Pareto frontier optimization over geometric parameters (mass vs stiffness vs clearance)
       outputting structured parameter diffs.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.spatial_engineering")

_lock = threading.RLock()


@dataclass
class BoundingBox:
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    def intersects(self, other: BoundingBox) -> bool:
        return (
            self.min_x <= other.max_x
            and self.max_x >= other.min_x
            and self.min_y <= other.max_y
            and self.max_y >= other.min_y
            and self.min_z <= other.max_z
            and self.max_z >= other.min_z
        )

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class CADSolid:
    solid_id: str
    solid_type: str  # "box", "cylinder", "sphere"
    parameters: Dict[str, float]  # width, height, depth, radius, length
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # x, y, z
    density_kg_m3: float = 2700.0  # Default Aluminum 6061

    def compute_volume(self) -> float:
        st = self.solid_type.lower()
        if st == "box":
            w = self.parameters.get("width", 1.0)
            h = self.parameters.get("height", 1.0)
            d = self.parameters.get("depth", 1.0)
            return w * h * d
        elif st == "cylinder":
            r = self.parameters.get("radius", 0.5)
            l = self.parameters.get("length", self.parameters.get("height", 1.0))
            return math.pi * (r ** 2) * l
        elif st == "sphere":
            r = self.parameters.get("radius", 0.5)
            return (4.0 / 3.0) * math.pi * (r ** 3)
        return 1.0

    def compute_mass(self) -> float:
        return self.compute_volume() * self.density_kg_m3

    def get_bounding_box(self) -> BoundingBox:
        px, py, pz = self.position
        st = self.solid_type.lower()
        if st == "box":
            w = self.parameters.get("width", 1.0) / 2.0
            h = self.parameters.get("height", 1.0) / 2.0
            d = self.parameters.get("depth", 1.0) / 2.0
            return BoundingBox(px - w, px + w, py - h, py + h, pz - d, pz + d)
        elif st == "cylinder":
            r = self.parameters.get("radius", 0.5)
            l = self.parameters.get("length", self.parameters.get("height", 1.0)) / 2.0
            return BoundingBox(px - r, px + r, py - r, py + r, pz - l, pz + l)
        elif st == "sphere":
            r = self.parameters.get("radius", 0.5)
            return BoundingBox(px - r, px + r, py - r, py + r, pz - r, pz + r)
        return BoundingBox(px - 0.5, px + 0.5, py - 0.5, py + 0.5, pz - 0.5, pz + 0.5)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "solid_id": self.solid_id,
            "solid_type": self.solid_type,
            "parameters": self.parameters,
            "position": list(self.position),
            "density_kg_m3": self.density_kg_m3,
            "volume_m3": round(self.compute_volume(), 6),
            "mass_kg": round(self.compute_mass(), 4),
        }


@dataclass
class StructuralSurrogateReport:
    check_type: str  # "CANTILEVER_BEAM", "EULER_BUCKLING"
    max_deflection_m: float
    max_stress_pa: float
    yield_strength_pa: float
    safety_factor: float
    critical_buckling_load_n: Optional[float]
    fidelity_stamp: str = "Linear Static Analytical Surrogate, +/-15%"
    is_safe: bool = True
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ParetoDesignPoint:
    point_id: str
    parameters: Dict[str, float]
    mass_kg: float
    deflection_mm: float
    max_stress_mpa: float
    is_pareto_optimal: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SpatialEngineeringCanvas:
    """Multimodal Spatial CAD Engine, Structural Surrogate, and Trade-Off Explorer."""

    def __init__(self):
        self._solids: Dict[str, CADSolid] = {}

    # ------------------------------------------------------------------
    # 1. Parametric Geometry & Interference
    # ------------------------------------------------------------------

    def add_solid(self, solid: CADSolid) -> None:
        with _lock:
            self._solids[solid.solid_id] = solid

    def check_interference(self, solid_id_a: str, solid_id_b: str) -> bool:
        """Check if two solids collide or interfere in 3D bounding box space."""
        with _lock:
            if solid_id_a not in self._solids or solid_id_b not in self._solids:
                return False
            bb_a = self._solids[solid_id_a].get_bounding_box()
            bb_b = self._solids[solid_id_b].get_bounding_box()
            return bb_a.intersects(bb_b)

    # ------------------------------------------------------------------
    # 2. Physics & Structural Surrogates
    # ------------------------------------------------------------------

    def compute_cantilever_beam(
        self,
        length_m: float,
        width_m: float,
        height_m: float,
        load_n: float,
        youngs_modulus_pa: float = 69e9,  # Aluminum: 69 GPa
        yield_strength_pa: float = 276e6,  # Aluminum 6061-T6: 276 MPa
    ) -> StructuralSurrogateReport:
        """Calculate tip deflection and maximum bending stress for a rectangular cantilever beam."""
        # Second moment of area I = (b * h^3) / 12
        i_xx = (width_m * (height_m ** 3)) / 12.0
        # Tip deflection delta = (F * L^3) / (3 * E * I)
        deflection = (load_n * (length_m ** 3)) / (3.0 * youngs_modulus_pa * i_xx)
        # Max bending stress sigma = (M * c) / I = (F * L * (h / 2)) / I
        c = height_m / 2.0
        max_stress = (load_n * length_m * c) / i_xx

        safety_factor = yield_strength_pa / max(1.0, max_stress)
        is_safe = safety_factor >= 1.5

        warnings = []
        if not is_safe:
            warnings.append(f"Safety factor {safety_factor:.2f} is below target threshold 1.50")
        if deflection > (length_m / 100.0):
            warnings.append(f"Excessive deflection {deflection*1000.0:.2f}mm exceeds L/100 limit")

        report = StructuralSurrogateReport(
            check_type="CANTILEVER_BEAM",
            max_deflection_m=round(deflection, 6),
            max_stress_pa=round(max_stress, 2),
            yield_strength_pa=yield_strength_pa,
            safety_factor=round(safety_factor, 2),
            critical_buckling_load_n=None,
            fidelity_stamp="Linear Static Analytical Surrogate, +/-15%",
            is_safe=is_safe,
            warnings=warnings,
        )

        try:
            get_registry().set_capability_evidence(
                "SPATIAL_ENGINEERING",
                EvidenceLevel.LIVE,
                f"Computed beam surrogate: stress={max_stress/1e6:.1f}MPa, SF={safety_factor:.2f}",
                source="spatial_engineering.compute_cantilever_beam",
            )
        except Exception:
            pass

        return report

    def compute_euler_buckling(
        self,
        length_m: float,
        width_m: float,
        height_m: float,
        axial_load_n: float,
        youngs_modulus_pa: float = 69e9,
        k_column: float = 1.0,  # 1.0 for pinned-pinned, 2.0 for fixed-free
    ) -> StructuralSurrogateReport:
        """Calculate Euler critical buckling load P_cr = (pi^2 * E * I) / (K * L)^2."""
        # Weakest axis moment of area
        i_min = (min(width_m, height_m) ** 3 * max(width_m, height_m)) / 12.0
        effective_length = k_column * length_m
        p_cr = (math.pi ** 2 * youngs_modulus_pa * i_min) / (effective_length ** 2)

        safety_factor = p_cr / max(1.0, axial_load_n)
        is_safe = safety_factor >= 2.0

        warnings = []
        if not is_safe:
            warnings.append(f"Buckling safety factor {safety_factor:.2f} < 2.0 (P_cr={p_cr:.1f}N vs Load={axial_load_n:.1f}N)")

        return StructuralSurrogateReport(
            check_type="EULER_BUCKLING",
            max_deflection_m=0.0,
            max_stress_pa=axial_load_n / (width_m * height_m),
            yield_strength_pa=276e6,
            safety_factor=round(safety_factor, 2),
            critical_buckling_load_n=round(p_cr, 2),
            fidelity_stamp="Euler Column Buckling Surrogate, +/-10%",
            is_safe=is_safe,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 3. Multi-Objective Trade-Off Explorer (Pareto Frontier)
    # ------------------------------------------------------------------

    def explore_pareto_tradeoffs(
        self,
        length_m: float,
        load_n: float,
        width_range: Tuple[float, float, int] = (0.01, 0.05, 5),  # min, max, steps
        height_range: Tuple[float, float, int] = (0.01, 0.05, 5),
        density_kg_m3: float = 2700.0,
        youngs_modulus_pa: float = 69e9,
    ) -> List[ParetoDesignPoint]:
        """Generate parameter grid and isolate non-dominated Pareto design candidates (Mass vs Deflection)."""
        w_min, w_max, w_steps = width_range
        h_min, h_max, h_steps = height_range

        widths = [w_min + i * (w_max - w_min) / max(1, w_steps - 1) for i in range(w_steps)]
        heights = [h_min + j * (h_max - h_min) / max(1, h_steps - 1) for j in range(h_steps)]

        candidates: List[ParetoDesignPoint] = []
        pid = 1

        for w in widths:
            for h in heights:
                vol = w * h * length_m
                mass = vol * density_kg_m3
                report = self.compute_cantilever_beam(length_m, w, h, load_n, youngs_modulus_pa)
                candidates.append(
                    ParetoDesignPoint(
                        point_id=f"P_{pid}",
                        parameters={"width_m": round(w, 4), "height_m": round(h, 4), "length_m": round(length_m, 4)},
                        mass_kg=round(mass, 4),
                        deflection_mm=round(report.max_deflection_m * 1000.0, 4),
                        max_stress_mpa=round(report.max_stress_pa / 1e6, 2),
                        is_pareto_optimal=True,
                    )
                )
                pid += 1

        # Non-dominated filter: Minimize mass and minimize deflection
        for i, c1 in enumerate(candidates):
            for j, c2 in enumerate(candidates):
                if i != j:
                    # If c2 is strictly better or equal in both and strictly better in at least one
                    if (c2.mass_kg <= c1.mass_kg and c2.deflection_mm <= c1.deflection_mm) and (
                        c2.mass_kg < c1.mass_kg or c2.deflection_mm < c1.deflection_mm
                    ):
                        c1.is_pareto_optimal = False
                        break

        pareto_front = [c for c in candidates if c.is_pareto_optimal]
        pareto_front.sort(key=lambda p: p.mass_kg)
        return pareto_front


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_canvas_instance: Optional[SpatialEngineeringCanvas] = None


def get_spatial_engineering_canvas() -> SpatialEngineeringCanvas:
    global _canvas_instance
    if _canvas_instance is None:
        with _lock:
            if _canvas_instance is None:
                _canvas_instance = SpatialEngineeringCanvas()
    return _canvas_instance


def compute_cantilever_beam(length_m: float, width_m: float, height_m: float, load_n: float, **kwargs) -> StructuralSurrogateReport:
    return get_spatial_engineering_canvas().compute_cantilever_beam(length_m, width_m, height_m, load_n, **kwargs)


def compute_euler_buckling(length_m: float, width_m: float, height_m: float, axial_load_n: float, **kwargs) -> StructuralSurrogateReport:
    return get_spatial_engineering_canvas().compute_euler_buckling(length_m, width_m, height_m, axial_load_n, **kwargs)


def explore_pareto_tradeoffs(length_m: float, load_n: float, **kwargs) -> List[ParetoDesignPoint]:
    return get_spatial_engineering_canvas().explore_pareto_tradeoffs(length_m, load_n, **kwargs)
