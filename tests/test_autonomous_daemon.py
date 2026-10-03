"""Unit tests for Headless Daemon Runner & Continuous Cron (Module G)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from autonomous_daemon import (
    AutonomousDaemon,
    run_nightly_maintenance,
    run_morning_preparation,
    schedule_cron_cycle,
    get_daemon_status,
)


@pytest.fixture
def daemon(tmp_path):
    briefing = tmp_path / "test_briefing.json"
    log_file = tmp_path / "test_cron_history.json"
    return AutonomousDaemon(briefing_path=briefing, log_path=log_file)


def test_nightly_maintenance_execution(daemon):
    with patch("synaptic_adapter.schedule_overnight_training") as mock_synaptic:
        mock_synaptic.return_value = {"status": "trained", "samples": 12}

        res = daemon.run_nightly_maintenance()
        assert res["cycle_name"] == "nightly_maintenance"
        assert res["success"] is True
        assert "database_vacuum" in res["details"]
        assert "cache_pruning" in res["details"]
        assert res["details"]["synaptic_plasticity"]["status"] == "trained"


def test_morning_preparation_generation(daemon):
    with patch("inbound_triage.get_morning_briefing_summary") as mock_inbox:
        mock_inbox.return_value = "2 urgent emails regarding Stark Industries R&D."

        res = daemon.run_morning_preparation()
        assert res["cycle_name"] == "morning_preparation"
        assert res["success"] is True
        assert daemon.briefing_path.exists()

        with open(daemon.briefing_path, "r", encoding="utf-8") as f:
            briefing = json.load(f)
        assert briefing["headline"] == "Morning Executive Briefing"
        assert "Stark Industries" in briefing["communications"]


def test_schedule_cron_cycle_dispatch(daemon):
    with patch.object(daemon, "run_nightly_maintenance") as mock_night:
        mock_night.return_value = {"status": "nightly_ran"}
        res_3am = daemon.schedule_cron_cycle(force_hour=3)
        assert res_3am["status"] == "nightly_ran"
        assert mock_night.called

    with patch.object(daemon, "run_morning_preparation") as mock_morning:
        mock_morning.return_value = {"status": "morning_ran"}
        res_7am = daemon.schedule_cron_cycle(force_hour=7)
        assert res_7am["status"] == "morning_ran"
        assert mock_morning.called

    res_noon = daemon.schedule_cron_cycle(force_hour=12)
    assert res_noon["status"] == "quiescent"


def test_daemon_status_reporting(daemon):
    daemon.run_morning_preparation()
    status = daemon.get_daemon_status()
    assert status["cycles_run_count"] == 1
    assert status["last_morning_prep"] is not None
