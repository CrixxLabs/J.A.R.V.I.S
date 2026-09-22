import pytest
pytest.importorskip("pyaudio")
import listener

def test_interrupt_threshold_has_hard_floor():
    assert listener._interrupt_voice_threshold(1) >= 500
    assert listener._interrupt_voice_threshold(100) >= 500
