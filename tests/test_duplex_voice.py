"""Unit tests for Low-Latency Duplex Conversational Voice (Module J)."""
from unittest.mock import MagicMock, patch
import pytest

from duplex_voice import (
    DuplexVoiceSession,
    start_voice_stream,
    synthesize_speech_chunk,
    handle_interrupt,
    is_interrupted,
    reset_stream,
)


@pytest.fixture
def session():
    sess = DuplexVoiceSession(vad_energy_threshold=1500.0)
    sess.start_voice_stream()
    return sess


def test_start_voice_stream(session):
    res = session.start_voice_stream()
    assert res["success"] is True
    assert res["status"] == "stream_active"
    assert session.get_queue_depth() == 0


def test_synthesize_chunks(session):
    r1 = session.synthesize_speech_chunk("Good morning Tony,")
    r2 = session.synthesize_speech_chunk("I have prepared the daily telemetry.")
    assert r1["success"] is True
    assert r2["success"] is True
    assert session.get_queue_depth() == 2


def test_interrupt_below_threshold(session):
    session.synthesize_speech_chunk("Chunk 1")
    res = session.handle_interrupt(energy=500.0)
    assert res["interrupted"] is False
    assert session.get_queue_depth() == 1


def test_rapid_interrupt_cutoff_under_50ms(session):
    session.synthesize_speech_chunk("Chunk 1")
    session.synthesize_speech_chunk("Chunk 2")
    session.synthesize_speech_chunk("Chunk 3")
    assert session.get_queue_depth() == 3

    with patch("global_workspace.GlobalWorkspace.publish") as mock_pub:
        res = session.handle_interrupt(energy=2500.0)
        assert res["interrupted"] is True
        assert res["status"] == "playback_cancelled"
        assert res["dropped_chunks"] == 3
        # Strict latency requirement: sub-50ms
        assert res["cutoff_latency_ms"] < 50.0
        assert session.get_queue_depth() == 0
        assert session.is_interrupted() is True
        assert mock_pub.called


def test_chunk_dropped_when_interrupted(session):
    session.handle_interrupt(energy=3000.0)
    res = session.synthesize_speech_chunk("Post interrupt chunk")
    assert res["success"] is False
    assert res["dropped"] is True


def test_reset_stream(session):
    session.handle_interrupt(energy=3000.0)
    assert session.is_interrupted() is True

    session.reset_stream()
    assert session.is_interrupted() is False
    res = session.synthesize_speech_chunk("Fresh turn")
    assert res["success"] is True
