"""Tests for Workstream 2: Real-Time Audio Orchestration & Degradation Watchdog."""
import numpy as np
import pytest
from degradation_watchdog import (
    DEFAULT_STAGE_DEADLINES,
    DegradationLevel,
    DegradationStatus,
    DegradationWatchdog,
    get_degradation_watchdog,
    record_stage_latency,
)
from rt_ring_buffer import SPSCAudioRingBuffer, get_audio_ring_buffer
from windows_realtime_tuner import (
    HIGH_PRIORITY_CLASS,
    REALTIME_PRIORITY_CLASS,
    WindowsRealTimeTuner,
    get_realtime_tuner,
)


class TestAudioOrchestration:
    # 1. Lock-Free SPSC Audio Ring Buffer
    def test_spsc_ring_buffer_write_read_and_wraparound(self):
        # Small capacity buffer for testing wraparound (100 samples)
        ring = SPSCAudioRingBuffer(capacity_samples=100, channels=1, dtype=np.float32)

        assert ring.capacity == 100
        assert ring.available_read() == 0
        assert ring.available_write() == 99

        # Write 40 samples
        data1 = np.ones((40, 1), dtype=np.float32) * 0.5
        written = ring.write(data1)
        assert written == 40
        assert ring.available_read() == 40

        # Read 20 samples
        read1 = ring.read(20)
        assert len(read1) == 20
        assert np.allclose(read1, 0.5)
        assert ring.available_read() == 20

        # Write 70 samples -> should wrap around circular boundary
        data2 = np.ones((70, 1), dtype=np.float32) * 0.8
        written2 = ring.write(data2)
        assert written2 == 70
        assert ring.available_read() == 90

        # Read all 90 samples
        read2 = ring.read(90)
        assert len(read2) == 90
        # First 20 are 0.5, remaining 70 are 0.8
        assert np.allclose(read2[:20], 0.5)
        assert np.allclose(read2[20:], 0.8)

    def test_spsc_ring_buffer_underrun_detection(self):
        ring = SPSCAudioRingBuffer(capacity_samples=50, channels=1)
        # Attempt to read from empty buffer -> Underrun
        out = ring.read(10)
        assert len(out) == 0
        assert ring.get_underruns() == 1

        # Write 5, read 10 -> Underrun
        ring.write(np.zeros((5, 1), dtype=np.float32))
        out2 = ring.read(10)
        assert len(out2) == 5
        assert ring.get_underruns() == 2

    # 2. Windows Real-Time Tuner
    def test_windows_realtime_tuner_configuration(self):
        tuner = WindowsRealTimeTuner()

        # MMCSS
        ok_mmcss, _ = tuner.enable_mmcss_pro_audio("Pro Audio")
        assert ok_mmcss is True

        # Priority Class: verify High Priority (0x80) and safety from Realtime (0x100)
        ok_prio, _ = tuner.set_high_process_priority()
        assert ok_prio is True
        assert HIGH_PRIORITY_CLASS == 0x00000080
        assert REALTIME_PRIORITY_CLASS == 0x00000100

        # EcoQoS Throttling disable
        ok_eco, _ = tuner.disable_eco_qos_power_throttling()
        assert ok_eco is True

        # CPU Core affinity
        ok_aff, _ = tuner.set_cpu_affinity(core_mask=0x03)
        assert ok_aff is True

        status = tuner.get_tuning_status()
        assert "high_priority_active" in status
        assert "eco_qos_disabled" in status

    # 3. Degradation Watchdog Fail-Soft Ladder
    def test_degradation_watchdog_step_by_step_ladder_escalation(self):
        # K=3 misses to escalate, M=3 on-time to recover
        watchdog = DegradationWatchdog(k_misses_to_escalate=3, m_ontime_to_recover=3)
        assert watchdog.current_level == DegradationLevel.NORMAL

        # 1st Miss (VAD_VAP budget is 45ms, give 60ms)
        status = watchdog.record_stage_latency("VAD_VAP", 60.0)
        assert status.deadline_missed is True
        assert watchdog.current_level == DegradationLevel.NORMAL

        # 2nd Miss
        watchdog.record_stage_latency("VAD_VAP", 60.0)
        assert watchdog.current_level == DegradationLevel.NORMAL

        # 3rd Miss -> Escalates to DROP_VAP (Level 1)
        status = watchdog.record_stage_latency("VAD_VAP", 60.0)
        assert watchdog.current_level == DegradationLevel.DROP_VAP
        assert any("DROP_VAP" in r for r in status.active_remediations)

        # 3 more misses -> Escalates to SHORTEN_TTS (Level 2)
        for _ in range(3):
            status = watchdog.record_stage_latency("FIRST_CHUNK_TTS", 120.0)  # Budget 90ms
        assert watchdog.current_level == DegradationLevel.SHORTEN_TTS
        assert any("SHORTEN_TTS" in r for r in status.active_remediations)

        # 3 more misses -> Escalates to MUTE_BACKCHANNELS (Level 3)
        for _ in range(3):
            status = watchdog.record_stage_latency("TURN_DECISION", 100.0)  # Budget 80ms
        assert watchdog.current_level == DegradationLevel.MUTE_BACKCHANNELS

        # 3 more misses -> Escalates to SUSPEND_BACKGROUND (Level 4)
        for _ in range(3):
            status = watchdog.record_stage_latency("AUDIO_CALLBACK", 10.0)  # Budget 5ms
        assert watchdog.current_level == DegradationLevel.SUSPEND_BACKGROUND

        # Now test Recovery: 3 on-time ticks -> steps up from SUSPEND_BACKGROUND to MUTE_BACKCHANNELS
        for _ in range(3):
            watchdog.record_stage_latency("AUDIO_CALLBACK", 2.0)
        assert watchdog.current_level == DegradationLevel.MUTE_BACKCHANNELS

        # 3 more on-time ticks -> steps up to SHORTEN_TTS
        for _ in range(3):
            watchdog.record_stage_latency("AUDIO_CALLBACK", 2.0)
        assert watchdog.current_level == DegradationLevel.SHORTEN_TTS
