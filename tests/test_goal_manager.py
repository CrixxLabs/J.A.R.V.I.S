"""Tests for Hierarchical Goal Management Engine — MARK VIII."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import goal_manager
from goal_manager import GoalStatus, MilestoneStatus


class TestGoalManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="jarvis_test_goals_")
        self.goals_file = Path(self.test_dir) / "persistent_goals.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_and_load_goal(self):
        milestones = [
            {"id": "m1", "title": "Setup repository", "dependencies": []},
            {"id": "m2", "title": "Implement core logic", "dependencies": ["m1"]},
            {"id": "m3", "title": "Deploy service", "dependencies": ["m2"]},
        ]
        goal = goal_manager.create_goal(
            title="Launch Project Apollo",
            description="End-to-end automated deployment",
            milestones=milestones,
            goals_file=self.goals_file,
        )
        self.assertIsNotNone(goal["id"])
        self.assertEqual(len(goal["milestones"]), 3)

        # Verify disk persistence
        loaded = goal_manager.get_goal(goal["id"], goals_file=self.goals_file)
        self.assertEqual(loaded["title"], "Launch Project Apollo")

    def test_ready_milestones_and_execution_lifecycle(self):
        milestones = [
            {"id": "step1", "title": "Fetch Data", "dependencies": []},
            {"id": "step2", "title": "Process Data", "dependencies": ["step1"]},
        ]
        goal = goal_manager.create_goal(
            title="Data Pipeline",
            milestones=milestones,
            goals_file=self.goals_file,
        )
        goal_id = goal["id"]

        # Initially, only step1 is ready
        ready = goal_manager.get_ready_milestones(goal_id, goals_file=self.goals_file)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], "step1")

        # Complete step1
        goal_manager.update_milestone_status(
            goal_id, "step1", MilestoneStatus.VERIFIED.value,
            artifact={"records": 100},
            goals_file=self.goals_file,
        )

        # Now step2 is ready
        ready2 = goal_manager.get_ready_milestones(goal_id, goals_file=self.goals_file)
        self.assertEqual(len(ready2), 1)
        self.assertEqual(ready2[0]["id"], "step2")

        # Complete step2 -> goal should complete
        updated = goal_manager.update_milestone_status(
            goal_id, "step2", MilestoneStatus.VERIFIED.value,
            artifact={"output": "success"},
            goals_file=self.goals_file,
        )
        self.assertEqual(updated["status"], GoalStatus.COMPLETED.value)

    def test_localized_replanning(self):
        milestones = [
            {"id": "m1", "title": "Scrape Website A", "dependencies": []},
            {"id": "m2", "title": "Parse Data A", "dependencies": ["m1"]},
            {"id": "m3", "title": "Save to DB", "dependencies": ["m2"]},
        ]
        goal = goal_manager.create_goal(
            title="Web Scraping",
            milestones=milestones,
            goals_file=self.goals_file,
        )
        goal_id = goal["id"]

        # m1 succeeds
        goal_manager.update_milestone_status(
            goal_id, "m1", MilestoneStatus.VERIFIED.value,
            goals_file=self.goals_file,
        )

        # m2 fails
        goal_manager.update_milestone_status(
            goal_id, "m2", MilestoneStatus.FAILED.value,
            error="Cloudflare captcha blocked request",
            goals_file=self.goals_file,
        )

        # Localized replan: replace m2 (and dependent m3) with alternative API flow
        replacements = [
            {"id": "m2_alt", "title": "Use Direct API B", "dependencies": ["m1"]},
            {"id": "m3_alt", "title": "Save to DB", "dependencies": ["m2_alt"]},
        ]
        replanned = goal_manager.replan_goal(
            goal_id,
            failed_milestone_id="m2",
            replacement_milestones=replacements,
            goals_file=self.goals_file,
        )

        m_ids = [m["id"] for m in replanned["milestones"]]
        # m1 is kept (it was verified)
        self.assertIn("m1", m_ids)
        # m2 and m3 are removed
        self.assertNotIn("m2", m_ids)
        self.assertNotIn("m3", m_ids)
        # new ones are present
        self.assertIn("m2_alt", m_ids)
        self.assertIn("m3_alt", m_ids)
        self.assertEqual(replanned["status"], GoalStatus.IN_PROGRESS.value)


if __name__ == "__main__":
    unittest.main()
