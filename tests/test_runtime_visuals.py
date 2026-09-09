import json
import urllib.request

import runtime_visuals


def test_activity_priority_and_restoration():
    hub = runtime_visuals.VisualStateHub()
    hub.set_base_state("idle")
    assert hub.snapshot()["operational_state"] == "idle"

    with hub.activity("thinking"):
        assert hub.snapshot()["operational_state"] == "thinking"
        with hub.activity("executing", current_action={"action": "open_app"}):
            snapshot = hub.snapshot()
            assert snapshot["operational_state"] == "executing"
            assert snapshot["current_action"] == "open_app"
            assert snapshot["task_count"] == 1
            with hub.activity("speaking", current_jarvis_response="Opening it."):
                assert hub.snapshot()["operational_state"] == "speaking"
            assert hub.snapshot()["operational_state"] == "executing"
        assert hub.snapshot()["operational_state"] == "thinking"

    snapshot = hub.snapshot()
    assert snapshot["operational_state"] == "idle"
    assert snapshot["task_count"] == 0
    assert snapshot["current_action"] is None


def test_local_bridge_snapshot_and_event_stream():
    runtime_visuals.stop_bridge()
    runtime_visuals.set_base_state("dormant")
    host, port = runtime_visuals.start_bridge(host="127.0.0.1", port=0)
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/v1/snapshot", timeout=2) as response:
            snapshot = json.load(response)
        assert snapshot["operational_state"] == "dormant"

        with urllib.request.urlopen(f"http://{host}:{port}/v1/events", timeout=2) as response:
            lines = [response.readline().decode("utf-8").strip() for _ in range(3)]
        data_line = next(line for line in lines if line.startswith("data: "))
        event = json.loads(data_line[6:])
        assert event["operational_state"] == "dormant"
        assert isinstance(event["sequence"], int)
    finally:
        runtime_visuals.stop_bridge()

