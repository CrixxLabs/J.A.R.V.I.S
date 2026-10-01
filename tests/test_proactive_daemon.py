"""Test suite for proactive_daemon.py interruption engine."""
import datetime
import time
import pytest
from unittest.mock import patch, MagicMock

import proactive_daemon


class TestProactiveDaemon:
    """Test cases for proactive telemetry and interruption engine."""

    def setup_method(self):
        """Reset daemon state before each test."""
        proactive_daemon._cooldowns.clear()
        proactive_daemon.stop_daemon()

    def teardown_method(self):
        """Ensure daemon is stopped after each test."""
        proactive_daemon.stop_daemon()

    def test_init_callbacks(self):
        """Test callback initialization."""
        mock_speak = MagicMock()
        mock_active = MagicMock(return_value=True)

        proactive_daemon.init(mock_speak, mock_active)

        assert proactive_daemon._speak_callback is mock_speak
        assert proactive_daemon._active_getter is mock_active

    def test_start_daemon_missing_callbacks(self):
        """Test starting daemon without callbacks fails gracefully."""
        proactive_daemon._speak_callback = None
        proactive_daemon._active_getter = None

        result = proactive_daemon.start_daemon()
        assert result is False

    def test_start_daemon_success(self):
        """Test starting daemon with callbacks."""
        mock_speak = MagicMock()
        mock_active = MagicMock(return_value=True)

        proactive_daemon.init(mock_speak, mock_active)
        result = proactive_daemon.start_daemon()

        assert result is True
        assert proactive_daemon._daemon_running is True

        proactive_daemon.stop_daemon()
        assert proactive_daemon._daemon_running is False

    def test_cooldown_enforcement(self):
        """Test that 15-minute cooldown is enforced per condition."""
        condition = "test_condition"

        # First trigger should be allowed
        assert proactive_daemon._can_notify(condition) is True

        # Immediate second trigger should be blocked
        assert proactive_daemon._can_notify(condition) is False

        # Different condition should still be allowed
        assert proactive_daemon._can_notify("different_condition") is True

    @patch('psutil.sensors_battery')
    def test_battery_check_low_discharging(self, mock_battery):
        """Test battery warning on low charge and discharging."""
        mock_bat = MagicMock()
        mock_bat.percent = 10.0
        mock_bat.power_plugged = False
        mock_battery.return_value = mock_bat

        warning = proactive_daemon._check_battery()

        assert warning is not None
        assert "battery level is critically low" in warning
        assert "10 percent" in warning

    @patch('psutil.sensors_battery')
    def test_battery_check_low_plugged_in(self, mock_battery):
        """Test no battery warning when plugged in even if low."""
        mock_bat = MagicMock()
        mock_bat.percent = 10.0
        mock_bat.power_plugged = True
        mock_battery.return_value = mock_bat

        warning = proactive_daemon._check_battery()
        assert warning is None

    @patch('psutil.sensors_battery')
    def test_battery_check_nominal(self, mock_battery):
        """Test no warning when battery is healthy."""
        mock_bat = MagicMock()
        mock_bat.percent = 80.0
        mock_bat.power_plugged = False
        mock_battery.return_value = mock_bat

        warning = proactive_daemon._check_battery()
        assert warning is None

    @patch('psutil.sensors_battery')
    def test_battery_check_no_sensor(self, mock_battery):
        """Test graceful handling when no battery sensor is available (e.g. desktop)."""
        mock_battery.return_value = None

        warning = proactive_daemon._check_battery()
        assert warning is None

    @patch('proactive_daemon.psutil')
    def test_thermal_check_high_temperature(self, mock_psutil):
        """Test thermal warning when temperature exceeds threshold."""
        entry = MagicMock()
        entry.current = 88.0
        entry.label = "Core 0"
        mock_psutil.sensors_temperatures.return_value = {"coretemp": [entry]}

        warning = proactive_daemon._check_thermals()

        assert warning is not None
        assert "thermal warning" in warning
        assert "88 degrees" in warning

    @patch('proactive_daemon.psutil')
    def test_thermal_check_nominal(self, mock_psutil):
        """Test no thermal warning when temperature is safe."""
        entry = MagicMock()
        entry.current = 55.0
        entry.label = "Core 0"
        mock_psutil.sensors_temperatures.return_value = {"coretemp": [entry]}

        warning = proactive_daemon._check_thermals()
        assert warning is None

    @patch('proactive_daemon.psutil')
    def test_thermal_check_no_sensor(self, mock_psutil):
        """Test handling when temperature sensors are unavailable."""
        mock_psutil.sensors_temperatures.return_value = {}

        warning = proactive_daemon._check_thermals()
        assert warning is None

    @patch('psutil.cpu_percent')
    def test_cpu_load_check_high(self, mock_cpu):
        """Test CPU warning when load exceeds 90%."""
        mock_cpu.return_value = 95.0

        warning = proactive_daemon._check_cpu_load()

        assert warning is not None
        assert "CPU load is sustained at 95 percent" in warning

    @patch('psutil.cpu_percent')
    def test_cpu_load_check_nominal(self, mock_cpu):
        """Test no warning when CPU load is normal."""
        mock_cpu.return_value = 35.0

        warning = proactive_daemon._check_cpu_load()
        assert warning is None

    @patch('obligations._obligations', new_callable=list)
    @patch('obligations._refresh_overdue')
    def test_obligation_check_impending_event(self, mock_refresh, mock_obs):
        """Test reminder for obligation due within 60 minutes."""
        now = datetime.datetime.now()
        due_time = now + datetime.timedelta(minutes=30)

        mock_obs.append({
            "id": "test_ob_1",
            "title": "Machine Learning Assignment",
            "type": "assignment",
            "status": "pending",
            "due_date": due_time.isoformat()
        })

        warning = proactive_daemon._check_obligations()

        assert warning is not None
        assert "Machine Learning Assignment" in warning
        assert "due in" in warning

    @patch('obligations._obligations', new_callable=list)
    @patch('obligations._refresh_overdue')
    def test_obligation_check_far_event(self, mock_refresh, mock_obs):
        """Test no reminder for obligation due far in future (>60 min)."""
        now = datetime.datetime.now()
        due_time = now + datetime.timedelta(hours=5)

        mock_obs.append({
            "id": "test_ob_2",
            "title": "Future Exam",
            "type": "exam",
            "status": "pending",
            "due_date": due_time.isoformat()
        })

        warning = proactive_daemon._check_obligations()
        assert warning is None

    def test_get_cooldown_status(self):
        """Test querying cooldown status dictionary."""
        proactive_daemon._cooldowns["test1"] = time.time() - 300  # 5 min ago

        status = proactive_daemon.get_cooldown_status()
        assert "test1" in status
        assert 500 < status["test1"] <= 600  # Remaining time should be ~600s
