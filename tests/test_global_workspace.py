"""Unit tests for Global Workspace Theory (GWT) Blackboard."""
import threading
import time
import pytest

import global_workspace
from global_workspace import (
    GlobalWorkspace,
    TOPIC_VISION_FOCUS,
    TOPIC_AUDIO_ENERGY,
    TOPIC_SYSTEM_TELEMETRY,
    TOPIC_ACTIVE_INTENT,
    TOPIC_ERROR_SIGNAL,
    PRIORITY_NORMAL,
    PRIORITY_HIGH,
    PRIORITY_CRITICAL,
    hook_vision,
    hook_audio,
    hook_telemetry,
    hook_intent,
    hook_error,
)


@pytest.fixture(autouse=True)
def reset_workspace():
    """Ensure workspace is clean before and after each test."""
    ws = global_workspace.get_workspace()
    ws.clear()
    ws.clear_preemption()
    yield
    ws.clear()
    ws.clear_preemption()


def test_publish_and_history():
    ws = GlobalWorkspace()
    msg = ws.publish(TOPIC_VISION_FOCUS, {"active_app": "Code.exe", "active_title": "workspace.py"}, source="test", priority=PRIORITY_NORMAL)

    assert msg["topic"] == TOPIC_VISION_FOCUS
    assert msg["source"] == "test"
    assert msg["priority"] == PRIORITY_NORMAL
    assert msg["payload"]["active_app"] == "Code.exe"
    assert "timestamp" in msg

    history = ws.get_history()
    assert len(history) == 1
    assert history[0]["topic"] == TOPIC_VISION_FOCUS

    history_filtered = ws.get_history(topic=TOPIC_AUDIO_ENERGY)
    assert len(history_filtered) == 0


def test_subscribe_and_unsubscribe():
    ws = GlobalWorkspace()
    received_vision = []
    received_all = []

    sub_vision = ws.subscribe(TOPIC_VISION_FOCUS, lambda m: received_vision.append(m))
    sub_all = ws.subscribe("*", lambda m: received_all.append(m))

    ws.publish(TOPIC_VISION_FOCUS, {"app": "Terminal"}, source="obs")
    ws.publish(TOPIC_AUDIO_ENERGY, {"energy": 120.0}, source="mic")

    assert len(received_vision) == 1
    assert received_vision[0]["payload"]["app"] == "Terminal"

    assert len(received_all) == 2

    # Unsubscribe vision
    assert ws.unsubscribe(sub_vision) is True
    ws.publish(TOPIC_VISION_FOCUS, {"app": "Browser"}, source="obs")

    assert len(received_vision) == 1  # Unchanged
    assert len(received_all) == 3


def test_current_workspace_state_snapshot():
    ws = GlobalWorkspace()
    ws.publish(TOPIC_VISION_FOCUS, {"active_app": "notepad.exe", "active_title": "Untitled"})
    ws.publish(TOPIC_AUDIO_ENERGY, {"energy": 300.0, "speech_detected": False})
    ws.publish(TOPIC_SYSTEM_TELEMETRY, {"cpu_percent": 12.5, "ram_percent": 45.0, "battery_percent": 88.0})
    ws.publish(TOPIC_ACTIVE_INTENT, {"query": "open chrome", "intent": "launch_app"})
    ws.publish(TOPIC_ERROR_SIGNAL, {"subsystem": "web", "message": "network timeout", "severity": "warning"})

    state = ws.get_current_workspace_state()
    assert state["vision"]["active_app"] == "notepad.exe"
    assert state["audio"]["energy"] == 300.0
    assert state["telemetry"]["cpu_percent"] == 12.5
    assert state["intent"]["query"] == "open chrome"
    assert len(state["errors"]) == 1
    assert state["errors"][0]["subsystem"] == "web"
    assert state["preempted"] is False
    assert state["last_broadcast"]["topic"] == TOPIC_ERROR_SIGNAL


def test_preemption_on_cpu_spike():
    ws = GlobalWorkspace()
    preemptions = []
    ws.register_preemption_handler(lambda m: preemptions.append(m))

    assert ws.is_preempted() is False

    # Normal CPU message
    ws.publish(TOPIC_SYSTEM_TELEMETRY, {"cpu_percent": 50.0, "ram_percent": 40.0})
    assert ws.is_preempted() is False
    assert len(preemptions) == 0

    # Spike CPU message (> 95%)
    ws.publish(TOPIC_SYSTEM_TELEMETRY, {"cpu_percent": 96.5, "ram_percent": 70.0})
    assert ws.is_preempted() is True
    assert len(preemptions) == 1
    assert "High CPU spike" in ws.get_current_workspace_state()["preemption_reason"]

    # Clear preemption
    ws.clear_preemption()
    assert ws.is_preempted() is False


def test_preemption_on_speech_burst():
    ws = GlobalWorkspace()
    preemptions = []
    ws.register_preemption_handler(lambda m: preemptions.append(m))

    # Speech burst
    ws.publish(TOPIC_AUDIO_ENERGY, {"energy": 2800.0, "speech_detected": True})
    assert ws.is_preempted() is True
    assert len(preemptions) == 1
    assert "Microphone speech burst" in ws.get_current_workspace_state()["preemption_reason"]


def test_preemption_on_critical_error_or_priority():
    ws = GlobalWorkspace()
    preemptions = []
    ws.register_preemption_handler(lambda m: preemptions.append(m))

    ws.publish(TOPIC_ERROR_SIGNAL, {"subsystem": "core", "message": "Kernel panic", "severity": "fatal"})
    assert ws.is_preempted() is True
    assert len(preemptions) == 1

    ws.clear_preemption()
    ws.publish("CUSTOM_TOPIC", {"data": 123}, priority=PRIORITY_CRITICAL)
    assert ws.is_preempted() is True


def test_convenience_hooks():
    v = hook_vision("code.exe", "file.py", screen_changed=True)
    assert v["topic"] == TOPIC_VISION_FOCUS
    assert v["payload"]["screen_changed"] is True

    a = hook_audio(energy=150.0, speech_detected=False)
    assert a["topic"] == TOPIC_AUDIO_ENERGY
    assert a["priority"] == PRIORITY_NORMAL

    t = hook_telemetry(cpu_percent=10.0, ram_percent=30.0, battery_percent=90.0)
    assert t["topic"] == TOPIC_SYSTEM_TELEMETRY

    i = hook_intent("search weather", intent="web_search", plan={"step": 1})
    assert i["topic"] == TOPIC_ACTIVE_INTENT
    assert i["payload"]["intent"] == "web_search"

    e = hook_error("tts", "device missing", severity="warning")
    assert e["topic"] == TOPIC_ERROR_SIGNAL
    assert e["payload"]["severity"] == "warning"

    state = global_workspace.get_current_workspace_state()
    assert state["vision"]["active_app"] == "code.exe"
    assert state["intent"]["query"] == "search weather"


def test_thread_safety():
    ws = GlobalWorkspace()
    thread_count = 10
    messages_per_thread = 50

    def worker(worker_id: int):
        for i in range(messages_per_thread):
            ws.publish(
                TOPIC_VISION_FOCUS,
                {"worker": worker_id, "seq": i},
                source=f"worker_{worker_id}",
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    history = ws.get_history(TOPIC_VISION_FOCUS, limit=1000)
    assert len(history) == thread_count * messages_per_thread
