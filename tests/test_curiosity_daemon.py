"""Test suite for curiosity_daemon.py autonomous curiosity and diagnostic daemon."""
import time
import pytest
from unittest.mock import patch, MagicMock

import curiosity_daemon
import cognitive_graph


class TestCuriosityDaemon:
    """Test cases for autonomous curiosity background daemon."""

    def test_daemon_lifecycle(self):
        """Test starting and stopping the curiosity daemon."""
        daemon = curiosity_daemon.CuriosityDaemon(idle_threshold=100.0, scan_interval=0.1)
        assert daemon.is_running() is False

        started = daemon.start()
        assert started is True
        assert daemon.is_running() is True

        daemon.stop()
        assert daemon.is_running() is False

    def test_user_activity_and_quiescence_check(self):
        """Test quiescence check accurately detects activity vs idle."""
        daemon = curiosity_daemon.CuriosityDaemon(idle_threshold=1.0)
        daemon.mark_user_activity()

        # Immediately active -> not quiescent
        is_q, reason = daemon.is_quiescent()
        assert is_q is False
        assert "Active recently" in reason

        # Simulate time passing
        daemon._last_user_activity = time.time() - 2.0
        with patch('psutil.cpu_percent', return_value=10.0):
            is_q, reason = daemon.is_quiescent()
            assert is_q is True
            assert reason == "Quiescent"

    @patch('psutil.cpu_percent')
    def test_quiescence_cpu_load_block(self, mock_cpu):
        """Test curiosity cycle deferred when CPU load is high."""
        daemon = curiosity_daemon.CuriosityDaemon(idle_threshold=1.0, max_cpu_percent=25.0)
        daemon._last_user_activity = time.time() - 100.0

        mock_cpu.return_value = 85.0
        is_q, reason = daemon.is_quiescent()
        assert is_q is False
        assert "CPU load high" in reason

    def test_probe_capability_gaps(self):
        """Test discovery of unverified or degraded capability gaps."""
        daemon = curiosity_daemon.CuriosityDaemon()
        gaps = daemon.probe_capability_gaps()
        assert isinstance(gaps, list)
        if gaps:
            assert "capability_id" in gaps[0]
            assert "evidence" in gaps[0]

    def test_execute_diagnostic_probe_valid_files(self):
        """Test diagnostic probe evaluation on capability with known files."""
        daemon = curiosity_daemon.CuriosityDaemon()
        gap = {
            "capability_id": "TEST_CAP",
            "name": "Test Capability",
            "code_paths": ["deliberation.py", "preflight_simulator.py"],
            "dependencies": [],
        }
        probe_res = daemon.execute_diagnostic_probe(gap)
        assert probe_res["capability_id"] == "TEST_CAP"
        assert probe_res["diagnosis"] == "VERIFIED_PASS"
        assert "deliberation.py" in probe_res["file_statuses"]
        assert probe_res["file_statuses"]["deliberation.py"]["exists"] is True

    def test_commit_learning_to_graph(self):
        """Test persistence of diagnostic learnings to cognitive graph."""
        daemon = curiosity_daemon.CuriosityDaemon()
        gap = {"capability_id": "TEST_GRAPH_CAP"}
        probe_res = {
            "capability_id": "TEST_GRAPH_CAP",
            "diagnosis": "VERIFIED_PASS",
            "dep_statuses": {"cap:DYNAMIC_EXECUTOR": "LIVE"},
        }
        committed = daemon.commit_learning_to_graph(gap, probe_res)
        assert committed is True

        # Verify in cognitive graph
        triples = cognitive_graph.query_triples(subject="Capability:TEST_GRAPH_CAP")
        assert len(triples) >= 1
        assert any(t["predicate"] == "diagnostic_status" and t["object"] == "VERIFIED_PASS" for t in triples)

    def test_run_curiosity_cycle_forced(self):
        """Test forced curiosity cycle executes and logs progress."""
        daemon = curiosity_daemon.CuriosityDaemon()
        res = daemon.run_curiosity_cycle(force=True)
        assert res["status"] == "completed"
        assert "probed_count" in res
        assert "learnings_committed" in res

    def test_run_curiosity_cycle_skipped_when_busy(self):
        """Test curiosity cycle skips when user active."""
        daemon = curiosity_daemon.CuriosityDaemon(idle_threshold=1000.0)
        daemon.mark_user_activity()

        res = daemon.run_curiosity_cycle(force=False)
        assert res["status"] == "skipped"
        assert "Active recently" in res["reason"]
