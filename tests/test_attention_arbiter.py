"""Unit tests for Theory of Mind & Attention Economics Engine (Module S)."""
from pathlib import Path
import pytest

from attention_arbiter import (
    AttentionArbiter,
    STATE_AVAILABLE,
    STATE_FOCUSED,
    STATE_DO_NOT_DISTURB,
    STATE_AWAY,
    CHANNEL_VOICE_INTERRUPT,
    CHANNEL_SCHEDULED_DIGEST,
    CHANNEL_PHONE_PUSH,
)


@pytest.fixture
def arbiter(tmp_path):
    bandit_file = tmp_path / "test_bandit.json"
    digest_file = tmp_path / "test_digest.json"
    return AttentionArbiter(bandit_path=bandit_file, digest_path=digest_file)


def test_user_state_estimation(arbiter):
    # 1. Idle > 300s -> AWAY
    s1 = arbiter.estimate_user_state(signals={"idle_seconds": 450.0, "foreground_window": "chrome"})
    assert s1.state == STATE_AWAY

    # 2. Zoom / Meeting window -> DO_NOT_DISTURB
    s2 = arbiter.estimate_user_state(signals={"idle_seconds": 10.0, "foreground_window": "Zoom Meeting 123"})
    assert s2.state == STATE_DO_NOT_DISTURB

    # 3. Fullscreen flag -> DO_NOT_DISTURB
    s3 = arbiter.estimate_user_state(signals={"idle_seconds": 5.0, "is_fullscreen": True})
    assert s3.state == STATE_DO_NOT_DISTURB

    # 4. VS Code active editing -> FOCUSED
    s4 = arbiter.estimate_user_state(signals={"idle_seconds": 15.0, "foreground_window": "VS Code - attention_arbiter.py"})
    assert s4.state == STATE_FOCUSED

    # 5. Normal desktop browsing -> AVAILABLE
    s5 = arbiter.estimate_user_state(signals={"idle_seconds": 40.0, "foreground_window": "Google Chrome - Wikipedia"})
    assert s5.state == STATE_AVAILABLE


def test_voi_interruption_gating(arbiter):
    # Routine low-urgency alert during FOCUSED state -> DO NOT INTERRUPT
    g1 = arbiter.should_interrupt(urgency_score=0.3, stakes="LOW", user_state=STATE_FOCUSED)
    assert g1["should_interrupt"] is False
    assert g1["voi"] < 0
    assert g1["recommended_action"] == "QUEUE_FOR_DIGEST"

    # High urgency in AVAILABLE state -> INTERRUPT
    g2 = arbiter.should_interrupt(urgency_score=0.9, stakes="HIGH", user_state=STATE_AVAILABLE)
    assert g2["should_interrupt"] is True
    assert g2["voi"] > 0
    assert g2["recommended_action"] == "INTERRUPT_IMMEDIATELY"

    # CRITICAL stake in DO_NOT_DISTURB -> Always pre-empts
    g3 = arbiter.should_interrupt(urgency_score=0.2, stakes="CRITICAL", user_state=STATE_DO_NOT_DISTURB)
    assert g3["should_interrupt"] is True
    assert g3["recommended_action"] == "INTERRUPT_IMMEDIATELY"


def test_digest_queue_lifecycle(arbiter):
    id1 = arbiter.queue_alert_for_digest({"title": "Newsletter received", "sender": "arxiv"})
    id2 = arbiter.queue_alert_for_digest({"title": "Disk usage at 70%", "sender": "monitor"})

    pending = arbiter.get_pending_digest()
    assert len(pending) == 2
    assert pending[0]["alert_id"] == id1

    flushed = arbiter.flush_digest()
    assert len(flushed) == 2
    assert len(arbiter.get_pending_digest()) == 0


def test_thompson_sampling_channel_selection_and_feedback(arbiter):
    # When user is AWAY, channel should favor PHONE_PUSH or SCHEDULED_DIGEST
    res = arbiter.select_notification_channel(urgency_score=0.5, stakes="MEDIUM", user_state=STATE_AWAY)
    assert res["selected_channel"] in [CHANNEL_PHONE_PUSH, CHANNEL_SCHEDULED_DIGEST]

    # Provide positive feedback to discrete sound
    fb = arbiter.record_feedback(channel="DISCRETE_SOUND", user_state=STATE_FOCUSED, accepted_or_useful=True)
    assert fb["updated"] is True
    assert fb["alpha"] > 2.0
