"""Unit tests for Autonomous Telephony Bridge (Module C)."""
import os
import time
from unittest.mock import MagicMock, patch
import pytest

from telephony_agent import (
    TelephonyAgent,
    make_outbound_alert_call,
    get_call_history,
)


@pytest.fixture
def agent(tmp_path):
    log_file = tmp_path / "test_calls.json"
    return TelephonyAgent(log_path=log_file)


def test_validate_phone_number(agent):
    assert agent.validate_phone_number("+12125550199") is True
    assert agent.validate_phone_number("12125550199") is True
    assert agent.validate_phone_number("+447911123456") is True
    assert agent.validate_phone_number("123") is False
    assert agent.validate_phone_number("not-a-number") is False


def test_make_outbound_alert_call_invalid_number(agent):
    res = agent.make_outbound_alert_call(
        to_number="invalid-123",
        message="Critical system alert",
    )
    assert res["success"] is False
    assert res["status"] == "invalid_number"


def test_make_outbound_alert_call_simulated(agent):
    with patch.dict(os.environ, {}, clear=True):
        res = agent.make_outbound_alert_call(
            to_number="+12125550199",
            message="Severe server outage detected on cluster A.",
        )
        assert res["success"] is True
        assert res["status"] == "call_dispatched"
        assert res["mode"] == "simulated"
        assert "+12125550199" in res["to"]

        history = agent.get_call_history()
        assert len(history) == 1
        assert history[0]["status"] == "simulated"


def test_rate_limiting_enforcement(agent):
    to_num = "+12125550199"

    # Make 3 calls (up to limit)
    for i in range(3):
        res = agent.make_outbound_alert_call(
            to_number=to_num,
            message=f"Test alert #{i}",
        )
        assert res["success"] is True

    # 4th call must be rejected by rate limiter
    res_rejected = agent.make_outbound_alert_call(
        to_number=to_num,
        message="Test alert #4",
    )
    assert res_rejected["success"] is False
    assert res_rejected["status"] == "rate_limited"
    assert "Rate limit exceeded" in res_rejected["message"]
