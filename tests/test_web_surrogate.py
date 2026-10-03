"""Unit tests for Autonomous Web Surrogacy Engine (Module A)."""
from unittest.mock import MagicMock, patch
import pytest

from web_surrogate import (
    WebSurrogate,
    browse_and_act,
    extract_page_content,
    book_reservation,
)


@pytest.fixture
def surrogate():
    return WebSurrogate(headless=True, timeout=5.0)


def test_sanitize_url(surrogate):
    assert surrogate._sanitize_url("example.com") == "https://example.com"
    assert surrogate._sanitize_url("http://example.com") == "http://example.com"
    assert surrogate._sanitize_url("https://example.com") == "https://example.com"


def test_validate_goal_safety(surrogate):
    safe_res = surrogate._validate_goal_safety("Search for machine learning papers on arXiv")
    assert safe_res["safe"] is True

    unsafe_res = surrogate._validate_goal_safety("Input my credit_card and password to checkout")
    assert unsafe_res["safe"] is False
    assert "sensitive credential" in unsafe_res["reason"]


def test_extract_page_content_mocked(surrogate):
    mock_html = """
    <html>
        <head><title>Test Page Title</title></head>
        <body>
            <h1>Welcome to Testing</h1>
            <p>This is a paragraph with key information.</p>
            <script>console.log('ignore this');</script>
        </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.text = mock_html
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        res = surrogate.extract_page_content("https://example.com/test")
        assert res["success"] is True
        assert res["title"] == "Test Page Title"
        assert "Welcome to Testing" in res["content"]
        assert "This is a paragraph with key information." in res["content"]
        assert "console.log" not in res["content"]


def test_browse_and_act_safety_rejection(surrogate):
    res = surrogate.browse_and_act("transfer money from my bank_account")
    assert res.success is False
    assert res.status == "safety_rejection"
    assert "sensitive credential" in res.error


def test_browse_and_act_flow(surrogate):
    mock_extract = {
        "success": True,
        "title": "Wikipedia - Artificial Intelligence",
        "content": "Artificial intelligence is intelligence demonstrated by machines.",
        "char_count": 65,
    }
    with patch.object(surrogate, "extract_page_content", return_value=mock_extract):
        res = surrogate.browse_and_act("Look up Artificial Intelligence on wikipedia")
        assert res.success is True
        assert res.status == "completed"
        assert res.title == "Wikipedia - Artificial Intelligence"
        assert len(res.actions_taken) >= 2


def test_book_reservation(surrogate):
    mock_sim_res = MagicMock()
    mock_sim_res.success = True
    with patch("preflight_simulator.simulate_python_execution", return_value=mock_sim_res):
        res = surrogate.book_reservation(
            service="Le Bernardin",
            date="2026-10-15",
            time_slot="19:30",
            party_size=4,
            contact_info={"name": "Tony Stark", "phone": "+12125550199"},
        )
        assert res["success"] is True
        assert res["status"] == "reservation_confirmed"
        assert "Le Bernardin" in res["message"]
        assert res["manifest"]["party_size"] == 4

    # Invalid party size
    invalid_res = surrogate.book_reservation(
        service="Test",
        date="2026-10-15",
        time_slot="19:30",
        party_size=0,
    )
    assert invalid_res["success"] is False
    assert invalid_res["status"] == "invalid_parameters"
