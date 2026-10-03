"""Unit tests for Ambient IoT & Physical Space Mesh (Module E)."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from smart_space import (
    SmartSpaceController,
    set_space_profile,
    control_device,
    get_space_state,
    run_power_watchdog,
)


@pytest.fixture
def space(tmp_path):
    state_file = tmp_path / "test_space.json"
    return SmartSpaceController(state_path=state_file)


def test_set_valid_space_profile(space):
    res = space.set_space_profile("focus")
    assert res["success"] is True
    assert res["profile"] == "focus"
    assert res["spec"]["dnd"] is True
    assert res["spec"]["lights"]["brightness"] == 230

    state = space.get_space_state()
    assert state["active_profile"] == "focus"
    assert state["devices"]["light.lab_overhead"]["state"] == "on"


def test_set_invalid_space_profile(space):
    res = space.set_space_profile("non_existent_profile")
    assert res["success"] is False
    assert res["status"] == "invalid_profile"


def test_control_device_toggle(space):
    entity = "switch.desk_lamp"
    res1 = space.control_device(entity, "turn_on")
    assert res1["success"] is True
    assert res1["current_state"]["state"] == "on"

    res2 = space.control_device(entity, "turn_off")
    assert res2["success"] is True
    assert res2["current_state"]["state"] == "off"

    res3 = space.control_device(entity, "toggle")
    assert res3["current_state"]["state"] == "on"


def test_power_watchdog_cutoff(space):
    # Over 80% and charging -> cut off charger
    res = space.run_power_watchdog(battery_percent=85.0, is_charging=True)
    assert res["success"] is True
    assert res["action_taken"] == "cutoff_charger_at_ceiling"
    assert space.get_space_state()["devices"]["switch.laptop_charger"]["state"] == "off"


def test_power_watchdog_engage(space):
    # Under 20% and not charging -> engage charger
    res = space.run_power_watchdog(battery_percent=15.0, is_charging=False)
    assert res["success"] is True
    assert res["action_taken"] == "engage_charger_at_floor"
    assert space.get_space_state()["devices"]["switch.laptop_charger"]["state"] == "on"


def test_power_watchdog_nominal(space):
    # In nominal zone -> no action
    res = space.run_power_watchdog(battery_percent=55.0, is_charging=True)
    assert res["action_taken"] == "none"
