"""Unit tests for Omnichannel Inbound Triage Engine (Module B)."""
import json
from pathlib import Path
import pytest

from inbound_triage import (
    InboundTriageManager,
    triage_inbound_message,
    list_pending_drafts,
    approve_draft,
    get_morning_briefing_summary,
)


@pytest.fixture
def triage(tmp_path):
    drafts_file = tmp_path / "test_drafts.json"
    return InboundTriageManager(drafts_path=drafts_file)


def test_priority_scoring_spam(triage):
    score, tier = triage.score_priority(
        sender="promo@casino-win.com",
        subject="Exclusive discount & free gift inside!",
        body="Click here to claim your free gift and win $1000!",
    )
    assert tier == "SPAM"
    assert score <= 0.2


def test_priority_scoring_critical(triage):
    score, tier = triage.score_priority(
        sender="boss@starkindustries.com",
        subject="URGENT: Production outage on server cluster",
        body="The main database is down ASAP response required.",
    )
    assert tier == "CRITICAL"
    assert score >= 0.9


def test_triage_inbound_message_creates_draft(triage):
    res = triage.triage_inbound_message(
        source="email",
        sender="pepper@starkindustries.com",
        subject="Board Meeting Reschedule",
        body="Tony, please confirm if 3 PM works for the board meeting today.",
    )
    assert res["tier"] == "ROUTINE"
    assert res["priority_score"] > 0.4
    assert res["draft_reply"] is not None
    assert "Thank you for reaching out" in res["draft_reply"]

    # Verify staged in pending drafts
    drafts = triage.list_pending_drafts()
    assert len(drafts) == 1
    assert drafts[0]["msg_id"] == res["msg_id"]
    assert drafts[0]["status"] == "pending_approval"


def test_approve_draft(triage):
    res = triage.triage_inbound_message(
        source="whatsapp",
        sender="+12125550199",
        subject="Project Alpha",
        body="URGENT: Need code review on branch alpha.",
    )
    msg_id = res["msg_id"]

    approve_res = triage.approve_draft(msg_id)
    assert approve_res["success"] is True
    assert approve_res["status"] == "dispatched"
    assert approve_res["draft"]["status"] == "approved"

    # Non-existent draft
    bad_res = triage.approve_draft("MSG-NON-EXISTENT")
    assert bad_res["success"] is False


def test_morning_briefing_summary(triage):
    # Quiescent
    summary_empty = triage.get_morning_briefing_summary()
    assert "quiescent" in summary_empty.lower()

    # Add critical and routine items
    triage.triage_inbound_message(
        source="email",
        sender="director@mit.edu",
        subject="URGENT: Final Exam Schedule Confirmed",
        body="Please review your exam timetable immediately.",
    )
    triage.triage_inbound_message(
        source="whatsapp",
        sender="Rhodey",
        subject="Lunch",
        body="Hey Tony, free for lunch later?",
    )

    summary = triage.get_morning_briefing_summary()
    assert "critical priority item" in summary
    assert "Final Exam Schedule Confirmed" in summary
    assert "routine correspondence" in summary
