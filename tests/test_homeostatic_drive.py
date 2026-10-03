"""Tests for Module AA: Homeostatic Drive & Learning-Progress Curiosity."""
import pytest
from homeostatic_drive import (
    HomeostaticDrive,
    LearningProgressRecord,
    DriveState,
    attention_allocation,
    compute_learning_progress,
    get_homeostatic_drive,
    record_prediction_error,
    update_drive,
)


class TestHomeostaticDrive:
    def test_drive_update_no_urgency(self):
        hd = HomeostaticDrive(setpoints={"novelty": 0.5})
        ds = hd.update_drive("novelty", 0.5)
        assert ds.current == 0.5
        assert ds.urgency == 0.0
        assert not ds.is_urgent

    def test_drive_update_urgent(self):
        hd = HomeostaticDrive(setpoints={"novelty": 0.5})
        # Current = 0.0 → deviation = 0.5 / 0.5 = 1.0 → urgent
        ds = hd.update_drive("novelty", 0.0)
        assert ds.is_urgent

    def test_urgent_drives_filter(self):
        hd = HomeostaticDrive(setpoints={"novelty": 0.5, "competence": 0.7})
        hd.update_drive("novelty", 0.5)     # on target
        hd.update_drive("competence", 0.0)  # far off target
        urgent = hd.urgent_drives()
        assert any(d.drive_id == "competence" for d in urgent)
        assert not any(d.drive_id == "novelty" for d in urgent)

    def test_learning_progress_positive(self):
        """LP should be positive when later predictions are better (lower error)."""
        hd = HomeostaticDrive()
        # Simulated improving trajectory: high error early, low later
        errors = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
        for e in errors:
            hd.record_prediction_error("grid_nav", e)
        lp = hd.compute_learning_progress("grid_nav")
        assert lp > 0.0, f"Expected positive LP, got {lp}"

    def test_learning_progress_negative(self):
        """LP should be negative when later predictions are worse (regressing)."""
        hd = HomeostaticDrive()
        errors = [0.1, 0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 1.0]
        for e in errors:
            hd.record_prediction_error("grid_nav_2", e)
        lp = hd.compute_learning_progress("grid_nav_2")
        assert lp < 0.0, f"Expected negative LP, got {lp}"

    def test_attention_allocation_highest_lp_first(self):
        """Most learnable domain should be returned by most_learnable_domain()."""
        hd = HomeostaticDrive()
        # domain A: big improvement
        for e in [0.9, 0.8, 0.7, 0.1, 0.05, 0.01]:
            hd.record_prediction_error("domain_A", e)
        # domain B: flat (no progress)
        for e in [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]:
            hd.record_prediction_error("domain_B", e)

        top = hd.most_learnable_domain()
        assert top == "domain_A"

    def test_clamp_drive_value(self):
        """update_drive should clamp values outside [0,1]."""
        hd = HomeostaticDrive(setpoints={"energy": 0.8})
        ds = hd.update_drive("energy", 1.5)
        assert ds.current == 1.0
        ds2 = hd.update_drive("energy", -0.3)
        assert ds2.current == 0.0
