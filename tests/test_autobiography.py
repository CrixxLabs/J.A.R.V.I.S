"""Tests for Autobiographical Memory — MARK VIII."""
import unittest
from unittest.mock import patch

import autobiography
from status_registry import EvidenceLevel, get_registry


class TestAutobiography(unittest.TestCase):
    def test_get_evolution_milestones(self):
        milestones = autobiography.get_evolution_milestones()
        self.assertIsInstance(milestones, list)
        self.assertGreaterEqual(len(milestones), 8)

        versions = [m["version"] for m in milestones]
        self.assertIn("Mark I", versions)
        self.assertIn("Mark VII", versions)
        self.assertIn("Mark VIII", versions)

    def test_get_git_metrics(self):
        metrics = autobiography.get_git_metrics()
        self.assertIsInstance(metrics, dict)
        self.assertIn("branch", metrics)
        self.assertIn("total_commits", metrics)
        self.assertIn("recent_commits", metrics)

    def test_get_autobiographical_summary(self):
        summary = autobiography.get_autobiographical_summary()
        self.assertIn("J.A.R.V.I.S.", summary)
        self.assertIn("MARK VIII", summary)
        self.assertIn("Arju", summary)
        self.assertIn("Mark VIII", summary)


if __name__ == "__main__":
    unittest.main()
