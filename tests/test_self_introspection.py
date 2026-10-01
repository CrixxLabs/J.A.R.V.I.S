"""Tests for Codebase AST Introspection Engine — MARK VIII."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import self_introspection


class TestSelfIntrospection(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="jarvis_test_ast_")
        self.root_path = Path(self.test_dir)

        # Create dummy Python file
        py_file = self.root_path / "sample_module.py"
        py_file.write_text('''"""Sample module docstring."""

class TargetController:
    """Controls target behavior."""
    def __init__(self, target_id: str):
        self.target_id = target_id

    def activate_target(self, power_level: int = 100) -> bool:
        """Activate the specified target."""
        return True


def standalone_helper(param_a: str, param_b: int = 0) -> str:
    """Helper utility function."""
    return f"{param_a}_{param_b}"
''', encoding="utf-8")

        # Create dummy C# file
        cs_file = self.root_path / "SampleService.cs"
        cs_file.write_text('''
public class SampleService
{
    public async Task<bool> ProcessItemAsync(string itemId, int count)
    {
        return true;
    }
}
''', encoding="utf-8")

        self.index_file = self.root_path / "test_index.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_index_codebase(self):
        index = self_introspection.index_codebase(
            root_dir=str(self.root_path),
            output_file=str(self.index_file)
        )
        self.assertTrue(os.path.exists(self.index_file))
        self.assertEqual(index["total_files"], 2)

        # Verify python AST parsing
        py_data = index["files"]["sample_module.py"]
        self.assertEqual(py_data["docstring"], "Sample module docstring.")
        self.assertEqual(len(py_data["classes"]), 1)
        self.assertEqual(py_data["classes"][0]["name"], "TargetController")
        self.assertEqual(len(py_data["classes"][0]["methods"]), 2)
        self.assertEqual(len(py_data["functions"]), 1)
        self.assertEqual(py_data["functions"][0]["name"], "standalone_helper")

        # Verify C# symbol parsing
        cs_data = index["files"]["SampleService.cs"]
        self.assertEqual(len(cs_data["classes"]), 1)
        self.assertEqual(cs_data["classes"][0]["name"], "SampleService")
        self.assertEqual(len(cs_data["functions"]), 1)
        self.assertEqual(cs_data["functions"][0]["name"], "ProcessItemAsync")

    def test_get_codebase_context(self):
        self_introspection.index_codebase(
            root_dir=str(self.root_path),
            output_file=str(self.index_file)
        )
        context = self_introspection.get_codebase_context(
            query="TargetController activate_target",
            index_path=str(self.index_file)
        )
        self.assertIn("TargetController", context)
        self.assertIn("activate_target", context)
        self.assertIn("sample_module.py", context)


if __name__ == "__main__":
    unittest.main()
