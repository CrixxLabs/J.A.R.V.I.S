import json

from status_registry import RuntimeStatus


def test_stale_test_artifacts_are_removed_on_load(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "MEMORY": {"state": "READY", "detail": "real"},
        "FAIL_COMP": {"state": "OFFLINE", "detail": "test"},
        "TEST_THING": {"state": "READY", "detail": "test"},
        "HIGH_PRIORITY": {"state": "READY", "detail": "test"},
    }), encoding="utf-8")
    registry = RuntimeStatus(str(path))
    statuses = registry.get_all()
    assert "MEMORY" in statuses
    assert "FAIL_COMP" not in statuses
    assert "TEST_THING" not in statuses
    assert "HIGH_PRIORITY" not in statuses
