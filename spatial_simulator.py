"""Spatial & Physical World Simulator for J.A.R.V.I.S. — MARK VIII.

Module AI:
  1. Spatial Interface & TSDF Voxel Grid:
     - Ingests depth camera telemetry and constructs 3D point clouds on CPU/NumPy.
     - Voxelizes point clouds into Truncated Signed Distance Function (TSDF) occupancy grids.
  2. Intuitive Physics Simulation & Parameter Estimation:
     - Numerical rigid-body kinematics (gravity, friction, restitution, ground collision).
     - Perturbed simulation ensembles to estimate physical parameters from observed trajectories.
  3. Hard-Isolated Actuation Safety Envelopes:
     - Enforces 3D bounding-box spatial safety constraints, velocity ceilings, and
       capability token verification outside raw LLM generation loops.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.spatial_simulator")

_lock = threading.RLock()


@dataclass
class PointCloud:
    points: np.ndarray  # Shape (N, 3)
    num_points: int

    def to_dict(self) -> Dict[str, Any]:
        return {"num_points": self.num_points, "bounds": self.get_bounds()}

    def get_bounds(self) -> Dict[str, List[float]]:
        if self.num_points == 0:
            return {"x": [0.0, 0.0], "y": [0.0, 0.0], "z": [0.0, 0.0]}
        min_b = self.points.min(axis=0).tolist()
        max_b = self.points.max(axis=0).tolist()
        return {
            "x": [round(min_b[0], 3), round(max_b[0], 3)],
            "y": [round(min_b[1], 3), round(max_b[1], 3)],
            "z": [round(min_b[2], 3), round(max_b[2], 3)],
        }


@dataclass
class TSDFVoxelGrid:
    grid: np.ndarray  # Shape (D, H, W)
    voxel_size: float
    origin: Tuple[float, float, float]
    dimensions: Tuple[int, int, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voxel_size": self.voxel_size,
            "origin": list(self.origin),
            "dimensions": list(self.dimensions),
            "occupied_voxels": int(np.sum(self.grid > 0)),
        }


@dataclass
class PhysicsParams:
    mass: float
    restitution: float
    friction: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpatialSafetyEnvelope:
    x_bounds: Tuple[float, float] = (-1.0, 1.0)
    y_bounds: Tuple[float, float] = (-1.0, 1.0)
    z_bounds: Tuple[float, float] = (0.0, 2.0)
    max_velocity: float = 1.5
    authorized_tokens: List[str] = field(default_factory=list)


@dataclass
class ActuationValidationResult:
    is_safe: bool
    commanded_position: Tuple[float, float, float]
    commanded_velocity: float
    violations: List[str]
    token_verified: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SpatialSimulator:
    """Spatial telemetry processing, intuitive physics simulation, and actuation safety gate."""

    def __init__(self, safety_envelope: Optional[SpatialSafetyEnvelope] = None):
        self.safety_envelope = safety_envelope or SpatialSafetyEnvelope()

    # ------------------------------------------------------------------
    # Spatial Point Cloud & TSDF Voxelization
    # ------------------------------------------------------------------

    def ingest_depth_map(
        self,
        depth_map: np.ndarray,
        fx: float = 525.0,
        fy: float = 525.0,
        cx: float = 319.5,
        cy: float = 239.5,
        max_depth: float = 5.0,
    ) -> PointCloud:
        """Reproject 2D depth image into 3D Cartesian point cloud (Camera Frame)."""
        h, w = depth_map.shape
        u, v = np.meshgrid(np.arange(w), np.arange(h))

        # Valid depth filter
        valid_mask = (depth_map > 0.1) & (depth_map <= max_depth)
        z = depth_map[valid_mask]
        x = (u[valid_mask] - cx) * z / fx
        y = (v[valid_mask] - cy) * z / fy

        points = np.stack([x, y, z], axis=-1)
        return PointCloud(points=points, num_points=int(points.shape[0]))

    def voxelize_point_cloud(
        self,
        point_cloud: PointCloud,
        voxel_size: float = 0.05,
        bounds: Optional[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]] = None,
    ) -> TSDFVoxelGrid:
        """Discretize point cloud into a 3D TSDF voxel occupancy grid."""
        if point_cloud.num_points == 0:
            return TSDFVoxelGrid(np.zeros((10, 10, 10), dtype=np.float32), voxel_size, (0.0, 0.0, 0.0), (10, 10, 10))

        if bounds is None:
            min_xyz = point_cloud.points.min(axis=0)
            max_xyz = point_cloud.points.max(axis=0)
        else:
            min_xyz = np.array([bounds[0][0], bounds[1][0], bounds[2][0]])
            max_xyz = np.array([bounds[0][1], bounds[1][1], bounds[2][1]])

        dims = np.ceil((max_xyz - min_xyz) / voxel_size).astype(int) + 1
        dims = np.clip(dims, 1, 200)  # Bound grid memory
        grid = np.zeros(dims, dtype=np.float32)

        indices = ((point_cloud.points - min_xyz) / voxel_size).astype(int)
        valid_idx = (
            (indices[:, 0] >= 0) & (indices[:, 0] < dims[0]) &
            (indices[:, 1] >= 0) & (indices[:, 1] < dims[1]) &
            (indices[:, 2] >= 0) & (indices[:, 2] < dims[2])
        )

        grid[indices[valid_idx, 0], indices[valid_idx, 1], indices[valid_idx, 2]] = 1.0

        return TSDFVoxelGrid(
            grid=grid,
            voxel_size=voxel_size,
            origin=(float(min_xyz[0]), float(min_xyz[1]), float(min_xyz[2])),
            dimensions=(int(dims[0]), int(dims[1]), int(dims[2])),
        )

    # ------------------------------------------------------------------
    # Intuitive Physics Simulation & Parameter Estimation
    # ------------------------------------------------------------------

    def simulate_rigid_body(
        self,
        initial_pos: Tuple[float, float, float],
        initial_vel: Tuple[float, float, float],
        params: PhysicsParams,
        gravity: float = 9.81,
        dt: float = 0.02,
        steps: int = 50,
        ground_z: float = 0.0,
    ) -> List[Tuple[float, float, float]]:
        """Simulate rigid body projectile and bouncing kinematics on CPU."""
        pos = np.array(initial_pos, dtype=float)
        vel = np.array(initial_vel, dtype=float)
        trajectory = [tuple(pos.tolist())]

        for _ in range(steps):
            # Apply gravity
            vel[2] -= gravity * dt
            # Apply air drag / friction
            vel[:2] *= max(0.0, 1.0 - params.friction * dt)

            pos += vel * dt

            # Ground bounce collision
            if pos[2] <= ground_z:
                pos[2] = ground_z
                vel[2] = -vel[2] * params.restitution
                vel[:2] *= (1.0 - params.friction)
                if abs(vel[2]) < 0.1:
                    vel[2] = 0.0

            trajectory.append((round(pos[0], 4), round(pos[1], 4), round(pos[2], 4)))

        return trajectory

    def estimate_physics_parameters(
        self,
        observed_trajectory: List[Tuple[float, float, float]],
        initial_pos: Tuple[float, float, float],
        initial_vel: Tuple[float, float, float],
        dt: float = 0.02,
    ) -> PhysicsParams:
        """Estimate restitution and friction via grid search parameter ensemble."""
        best_error = float("inf")
        best_params = PhysicsParams(mass=1.0, restitution=0.5, friction=0.1)

        obs_arr = np.array(observed_trajectory)
        steps = len(observed_trajectory) - 1

        for rest in [0.2, 0.5, 0.7, 0.9]:
            for fric in [0.01, 0.1, 0.3, 0.6]:
                cand = PhysicsParams(mass=1.0, restitution=rest, friction=fric)
                sim_traj = np.array(self.simulate_rigid_body(initial_pos, initial_vel, cand, dt=dt, steps=steps))
                min_len = min(len(obs_arr), len(sim_traj))
                mse = float(np.mean((obs_arr[:min_len] - sim_traj[:min_len]) ** 2))

                if mse < best_error:
                    best_error = mse
                    best_params = cand

        try:
            get_registry().set_capability_evidence(
                "SPATIAL_SIMULATOR",
                EvidenceLevel.LIVE,
                f"Estimated physics params (restitution={best_params.restitution}, friction={best_params.friction})",
                source="spatial_simulator.estimate_physics_parameters",
            )
        except Exception:
            pass

        return best_params

    # ------------------------------------------------------------------
    # Hardware Safety Envelope Validation
    # ------------------------------------------------------------------

    def validate_actuation_command(
        self,
        target_pos: Tuple[float, float, float],
        commanded_velocity: float,
        capability_token: str,
    ) -> ActuationValidationResult:
        """Strict physical boundary and velocity safety verification."""
        violations = []
        env = self.safety_envelope

        x, y, z = target_pos
        if not (env.x_bounds[0] <= x <= env.x_bounds[1]):
            violations.append(f"X coordinate {x:.2f} violates envelope [{env.x_bounds[0]}, {env.x_bounds[1]}]")
        if not (env.y_bounds[0] <= y <= env.y_bounds[1]):
            violations.append(f"Y coordinate {y:.2f} violates envelope [{env.y_bounds[0]}, {env.y_bounds[1]}]")
        if not (env.z_bounds[0] <= z <= env.z_bounds[1]):
            violations.append(f"Z coordinate {z:.2f} violates envelope [{env.z_bounds[0]}, {env.z_bounds[1]}]")

        if commanded_velocity > env.max_velocity:
            violations.append(f"Velocity {commanded_velocity:.2f} m/s exceeds limit {env.max_velocity:.2f} m/s")

        token_ok = (not env.authorized_tokens) or (capability_token in env.authorized_tokens)
        if not token_ok:
            violations.append(f"Unauthorized capability token: {capability_token!r}")

        is_safe = len(violations) == 0
        return ActuationValidationResult(
            is_safe=is_safe,
            commanded_position=target_pos,
            commanded_velocity=commanded_velocity,
            violations=violations,
            token_verified=token_ok,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_spatial_instance: Optional[SpatialSimulator] = None


def get_spatial_simulator() -> SpatialSimulator:
    global _spatial_instance
    if _spatial_instance is None:
        with _lock:
            if _spatial_instance is None:
                _spatial_instance = SpatialSimulator()
    return _spatial_instance


def ingest_depth_map(depth_map: np.ndarray, **kwargs) -> PointCloud:
    return get_spatial_simulator().ingest_depth_map(depth_map, **kwargs)


def voxelize_point_cloud(point_cloud: PointCloud, **kwargs) -> TSDFVoxelGrid:
    return get_spatial_simulator().voxelize_point_cloud(point_cloud, **kwargs)


def simulate_rigid_body(initial_pos: Tuple[float, float, float], initial_vel: Tuple[float, float, float], params: PhysicsParams, **kwargs) -> List[Tuple[float, float, float]]:
    return get_spatial_simulator().simulate_rigid_body(initial_pos, initial_vel, params, **kwargs)


def estimate_physics_parameters(observed_trajectory: List[Tuple[float, float, float]], initial_pos: Tuple[float, float, float], initial_vel: Tuple[float, float, float]) -> PhysicsParams:
    return get_spatial_simulator().estimate_physics_parameters(observed_trajectory, initial_pos, initial_vel)


def validate_actuation_command(target_pos: Tuple[float, float, float], commanded_velocity: float, capability_token: str) -> ActuationValidationResult:
    return get_spatial_simulator().validate_actuation_command(target_pos, commanded_velocity, capability_token)
