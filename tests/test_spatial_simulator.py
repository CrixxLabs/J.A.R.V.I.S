"""Tests for Module AI: Spatial & Physical World Simulator."""
import numpy as np
import pytest
from spatial_simulator import (
    ActuationValidationResult,
    PhysicsParams,
    PointCloud,
    SpatialSafetyEnvelope,
    SpatialSimulator,
    TSDFVoxelGrid,
    estimate_physics_parameters,
    get_spatial_simulator,
    ingest_depth_map,
    simulate_rigid_body,
    validate_actuation_command,
    voxelize_point_cloud,
)


class TestSpatialSimulator:
    def test_depth_to_point_cloud_and_tsdf(self):
        sim = SpatialSimulator()
        depth_image = np.ones((20, 20), dtype=np.float32) * 2.0  # 2m flat plane

        pc = sim.ingest_depth_map(depth_image)
        assert isinstance(pc, PointCloud)
        assert pc.num_points == 400
        bounds = pc.get_bounds()
        assert bounds["z"][0] == 2.0

        voxel_grid = sim.voxelize_point_cloud(pc, voxel_size=0.1)
        assert isinstance(voxel_grid, TSDFVoxelGrid)
        assert voxel_grid.to_dict()["occupied_voxels"] > 0

    def test_rigid_body_physics_simulation(self):
        sim = SpatialSimulator()
        params = PhysicsParams(mass=1.0, restitution=0.5, friction=0.05)
        # Drop from z=2.0 m
        traj = sim.simulate_rigid_body(
            initial_pos=(0.0, 0.0, 2.0),
            initial_vel=(1.0, 0.0, 0.0),
            params=params,
            dt=0.05,
            steps=30,
        )

        assert len(traj) == 31
        # Object must move forward (x increases)
        assert traj[-1][0] > traj[0][0]
        # Object must hit ground and bounce
        z_vals = [p[2] for p in traj]
        assert min(z_vals) == 0.0

    def test_physics_parameter_estimation(self):
        sim = SpatialSimulator()
        true_params = PhysicsParams(mass=1.0, restitution=0.7, friction=0.1)
        init_pos = (0.0, 0.0, 1.0)
        init_vel = (0.5, 0.0, 0.0)

        ground_truth_traj = sim.simulate_rigid_body(init_pos, init_vel, true_params, dt=0.05, steps=30)
        estimated = sim.estimate_physics_parameters(ground_truth_traj, init_pos, init_vel, dt=0.05)

        assert estimated.restitution == 0.7
        assert estimated.friction == 0.1

    def test_actuation_safety_envelope_validation(self):
        envelope = SpatialSafetyEnvelope(
            x_bounds=(-0.5, 0.5),
            y_bounds=(-0.5, 0.5),
            z_bounds=(0.0, 1.0),
            max_velocity=1.0,
            authorized_tokens=["TOKEN_SECURE_ROBOTICS"],
        )
        sim = SpatialSimulator(safety_envelope=envelope)

        # Valid safe actuation
        res_ok = sim.validate_actuation_command(
            target_pos=(0.1, -0.2, 0.5),
            commanded_velocity=0.4,
            capability_token="TOKEN_SECURE_ROBOTICS",
        )
        assert res_ok.is_safe is True
        assert len(res_ok.violations) == 0

        # Out-of-bounds target position
        res_oob = sim.validate_actuation_command(
            target_pos=(1.5, 0.0, 0.5),
            commanded_velocity=0.4,
            capability_token="TOKEN_SECURE_ROBOTICS",
        )
        assert res_oob.is_safe is False
        assert any("X coordinate" in v for v in res_oob.violations)

        # Excessive velocity
        res_fast = sim.validate_actuation_command(
            target_pos=(0.0, 0.0, 0.5),
            commanded_velocity=5.0,
            capability_token="TOKEN_SECURE_ROBOTICS",
        )
        assert res_fast.is_safe is False
        assert any("velocity" in v.lower() for v in res_fast.violations)

        # Invalid token
        res_unauth = sim.validate_actuation_command(
            target_pos=(0.0, 0.0, 0.5),
            commanded_velocity=0.5,
            capability_token="UNAUTHORIZED_TOKEN",
        )
        assert res_unauth.is_safe is False
        assert any("token" in v.lower() for v in res_unauth.violations)
