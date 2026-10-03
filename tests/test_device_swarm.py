"""Unit tests for Cross-Device Peripheral & Mobile Swarm (Module F)."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from device_swarm import (
    DeviceSwarmMesh,
    register_device,
    push_clipboard,
    get_latest_clipboard,
    ingest_mobile_notification,
    ping_device,
    send_device_command,
)


@pytest.fixture
def swarm(tmp_path):
    mesh_file = tmp_path / "test_swarm.json"
    return DeviceSwarmMesh(mesh_path=mesh_file)


def test_register_and_list_devices(swarm):
    dev = swarm.register_device(
        device_id="dev-tablet-01",
        name="StarkPad Pro",
        device_type="tablet_android",
        ip_address="192.168.1.120",
        battery_level=95,
    )
    assert dev["device_id"] == "dev-tablet-01"
    assert dev["name"] == "StarkPad Pro"

    devs = swarm.list_devices()
    dev_ids = [d["device_id"] for d in devs]
    assert "dev-tablet-01" in dev_ids


def test_clipboard_synchronization(swarm):
    text = "https://github.com/tonystark/jarvis-mark-viii"
    res = swarm.push_clipboard(text, source_device="phone")
    assert res["success"] is True
    assert res["source_device"] == "phone"

    latest = swarm.get_latest_clipboard()
    assert latest is not None
    assert latest["content"] == text
    assert latest["source_device"] == "phone"


def test_ingest_mobile_notification_to_global_workspace(swarm):
    with patch("global_workspace.GlobalWorkspace.publish") as mock_pub:
        res = swarm.ingest_mobile_notification(
            device_id="dev-phone-primary",
            app="WhatsApp",
            title="Pepper Potts",
            message="Please call when you are back in the lab.",
        )
        assert res["success"] is True
        assert res["app"] == "WhatsApp"
        assert mock_pub.called


def test_ping_and_command_dispatch(swarm):
    # Ping existing device
    ping_res = swarm.ping_device("dev-phone-primary")
    assert ping_res["success"] is True
    assert ping_res["status"] == "device_reachable"

    # Send command
    cmd_res = swarm.send_device_command(
        device_id="dev-phone-primary",
        command="ring_phone",
        params={"volume": 100},
    )
    assert cmd_res["success"] is True
    assert cmd_res["command"] == "ring_phone"

    # Non-existent device
    bad_ping = swarm.ping_device("dev-unknown")
    assert bad_ping["success"] is False
