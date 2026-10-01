"""Tests for Autonomous Skill Synthesizer (Voyager Paradigm) — MARK VIII."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import cognitive_graph
import skill_synthesizer
from status_registry import EvidenceLevel, get_registry


class TestSkillSynthesizer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="jarvis_test_skills_")
        self.orig_skills_dir = skill_synthesizer.SKILLS_DIR
        skill_synthesizer.SKILLS_DIR = skill_synthesizer.Path(self.test_dir)

    def tearDown(self):
        skill_synthesizer.SKILLS_DIR = self.orig_skills_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sanitize_skill_name(self):
        self.assertEqual(skill_synthesizer.sanitize_skill_name("calculate fibonacci sequence"), "calculate_fibonacci_sequence")
        self.assertEqual(skill_synthesizer.sanitize_skill_name("123 test skill!"), "skill_123_test_skill")
        self.assertTrue(skill_synthesizer.sanitize_skill_name("").startswith("skill_"))

    def test_validate_skill_in_sandbox_success(self):
        valid_code = """
def execute(params: dict) -> dict:
    val = params.get("number", 5)
    return {"success": True, "message": f"Square is {val * val}", "result": val * val}
"""
        passed, out, res = skill_synthesizer.validate_skill_in_sandbox(valid_code, {"number": 6})
        self.assertTrue(passed)
        self.assertIsNotNone(res)
        self.assertEqual(res.get("result"), 36)

    def test_validate_skill_in_sandbox_failure(self):
        broken_code = """
def execute(params: dict) -> dict:
    raise ValueError("Intentional syntax/runtime crash")
"""
        passed, err, res = skill_synthesizer.validate_skill_in_sandbox(broken_code, {})
        self.assertFalse(passed)
        self.assertIn("ValueError", err)

    def test_save_and_mount_skill(self):
        code = """
def execute(params: dict) -> dict:
    return {"success": True, "message": "Mounted successfully"}
"""
        success, file_path, module = skill_synthesizer.save_and_mount_skill("test_mount_unit", code)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(file_path))
        self.assertIsNotNone(module)
        self.assertTrue(hasattr(module, "execute"))
        res = module.execute({})
        self.assertEqual(res["message"], "Mounted successfully")

    @patch("brain.ask_llm")
    def test_synthesize_skill_flow(self, mock_ask):
        mock_ask.return_value = """
```python
def execute(params: dict) -> dict:
    name = params.get("name", "Arju")
    return {"success": True, "message": f"Hello {name}", "result": name}
```
"""
        res = skill_synthesizer.synthesize_skill(
            task_description="Greet the user warmly",
            skill_name="greet_user_test",
            params={"name": "Tony"},
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["skill_name"], "greet_user_test")
        self.assertIsNotNone(res["module"])

        # Verify cognitive graph triple was added
        triples = cognitive_graph.query_triples(subject="Skill:greet_user_test")
        self.assertTrue(len(triples) > 0)

    @patch("brain.ask_llm")
    def test_synthesize_and_execute_skill(self, mock_ask):
        mock_ask.return_value = """
```python
def execute(params: dict) -> dict:
    x = params.get("x", 10)
    y = params.get("y", 20)
    return {"success": True, "message": f"Sum is {x + y}", "result": x + y}
```
"""
        success, msg = skill_synthesizer.synthesize_and_execute_skill(
            task_description="Add two numbers together",
            params={"x": 15, "y": 25},
            skill_name="add_numbers_test",
        )
        self.assertTrue(success)
        self.assertIn("Sum is 40", msg)

    @patch("brain.ask_llm")
    def test_synthesis_retry_auto_patch(self, mock_ask):
        # 1st call returns broken code, 2nd call returns fixed code
        mock_ask.side_effect = [
            "```python\ndef execute(params: dict) -> dict:\n    undefined_symbol_error()\n```",
            "```python\ndef execute(params: dict) -> dict:\n    return {'success': True, 'message': 'Fixed!'}\n```",
        ]
        res = skill_synthesizer.synthesize_skill(
            task_description="Resilient task",
            skill_name="retry_skill_test",
            params={},
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["retries"], 1)


if __name__ == "__main__":
    unittest.main()
