"""Tests for Experience Distillation Engine — MARK VIII."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import experience_distiller


class TestExperienceDistiller(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="jarvis_test_distill_")
        self.output_file = Path(self.test_dir) / "distilled_memories.jsonl"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_distill_execution_sample(self):
        record = experience_distiller.distill_execution_sample(
            instruction="Synthesize a matrix multiplication function in Python.",
            context="Parameters: matrix A, matrix B",
            response="def matmul(A, B): return [[sum(a*b for a,b in zip(X_row, Y_col)) for Y_col in zip(*B)] for X_row in A]",
            metadata={"verification_score": 1.0, "capability": "SKILL_SYNTHESIZER"},
            output_file=self.output_file,
        )

        self.assertTrue(self.output_file.exists())
        self.assertEqual(record["alpaca"]["instruction"], "Synthesize a matrix multiplication function in Python.")
        self.assertEqual(record["metadata"]["capability"], "SKILL_SYNTHESIZER")

    def test_export_dataset_alpaca_and_sharegpt(self):
        # Distill 2 samples
        experience_distiller.distill_execution_sample(
            instruction="Task 1", response="Response 1", output_file=self.output_file
        )
        experience_distiller.distill_execution_sample(
            instruction="Task 2", response="Response 2", output_file=self.output_file
        )

        alpaca_export = Path(self.test_dir) / "alpaca.json"
        count_alpaca = experience_distiller.export_dataset(
            output_path=str(alpaca_export),
            format_type="alpaca",
            source_file=self.output_file,
        )
        self.assertEqual(count_alpaca, 2)
        with open(alpaca_export, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["instruction"], "Task 1")

        sharegpt_export = Path(self.test_dir) / "sharegpt.json"
        count_sharegpt = experience_distiller.export_dataset(
            output_path=str(sharegpt_export),
            format_type="sharegpt",
            source_file=self.output_file,
        )
        self.assertEqual(count_sharegpt, 2)
        with open(sharegpt_export, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(len(data), 2)
            self.assertIn("conversations", data[0])


if __name__ == "__main__":
    unittest.main()
