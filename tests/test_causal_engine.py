"""Tests for Causal World State Transition Modeling — MARK VIII."""
import os
import tempfile
import unittest

import causal_engine
from causal_engine import StateSurpriseException


class TestCausalEngine(unittest.TestCase):
    def test_capture_current_state(self):
        state = causal_engine.capture_current_state()
        self.assertIsInstance(state, dict)
        self.assertIn("cpu_percent", state)
        self.assertIn("ram_percent", state)
        self.assertIn("timestamp", state)

    def test_predict_state_transition(self):
        pred_app = causal_engine.predict_state_transition("open_app", {"app": "spotify"})
        self.assertEqual(pred_app["expected_effects"]["process_started"], "spotify")

        pred_file = causal_engine.predict_state_transition("create_file", {"path": "test.txt"})
        self.assertEqual(pred_file["expected_effects"]["file_created"], "test.txt")

    def test_verify_causal_transition_nominal(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf_path = tf.name

        try:
            initial_state = {"sample_processes": []}
            final_state = {"sample_processes": []}

            # File exists as expected
            verified, surprise, details = causal_engine.verify_causal_transition(
                action="create_file",
                params={"path": tf_path},
                initial_state=initial_state,
                final_state=final_state,
                strict=True,
            )
            self.assertTrue(verified)
            self.assertLess(surprise, 0.5)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_verify_causal_transition_surprise(self):
        non_existent_file = "C:/non_existent_path_xyz123_456.txt"
        initial_state = {}
        final_state = {}

        # Expect file to be created, but it was not
        verified, surprise, details = causal_engine.verify_causal_transition(
            action="create_file",
            params={"path": non_existent_file},
            initial_state=initial_state,
            final_state=final_state,
            strict=False,
        )
        self.assertFalse(verified)
        self.assertGreaterEqual(surprise, 0.6)

        # In strict mode, it must raise StateSurpriseException
        with self.assertRaises(StateSurpriseException):
            causal_engine.verify_causal_transition(
                action="create_file",
                params={"path": non_existent_file},
                initial_state=initial_state,
                final_state=final_state,
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
