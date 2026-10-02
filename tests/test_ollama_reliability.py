import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

import brain
import status_registry
from lifecycle import LifecycleManager
from status_registry import EvidenceLevel, RuntimeStatus


class _Response:
    def __init__(self, payload=None, status_code=200, error=None):
        self.status_code = status_code
        self._payload = payload
        self._error = error

    def json(self):
        if self._error:
            raise self._error
        return self._payload


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    registry = RuntimeStatus(tmp_path / "status.json", session_id="ollama-tests", pid=73)
    monkeypatch.setattr(status_registry, "_registry_instance", registry)
    brain.provider_health.reset()
    brain.reset_ollama_runtime_state()
    yield registry
    brain.provider_health.reset()
    brain.reset_ollama_runtime_state()


def _available():
    return {
        "host": brain.OLLAMA_HOST,
        "model": brain.REQUIRED_OLLAMA_MODEL,
        "configured_model": brain.CONFIGURED_OLLAMA_MODEL,
        "models_path": None,
        "models": [brain.REQUIRED_OLLAMA_MODEL],
        "service_state": "ONLINE",
        "model_state": "AVAILABLE",
        "detail": "exact model available",
    }


def test_reachable_daemon_with_exact_model(monkeypatch, isolated_runtime):
    response = _Response({"models": [{"name": "jarvis:latest"}, {"name": "other:1b"}]})
    get = Mock(return_value=response)
    monkeypatch.setattr(brain.requests, "get", get)
    result = brain.discover_ollama(force=True)
    assert result["service_state"] == "ONLINE"
    assert result["model_state"] == "AVAILABLE"
    assert get.call_args.kwargs["timeout"] == (
        brain.OLLAMA_CONNECT_TIMEOUT, brain.OLLAMA_DISCOVERY_TIMEOUT)
    assert isolated_runtime.get_status("OLLAMA_SERVICE")["evidence"] == "PROBED"
    assert isolated_runtime.get_status("OLLAMA_MODEL_JARVIS_MINISTRAL_3B")["evidence"] == "PROBED"


def test_reachable_daemon_missing_exact_model(monkeypatch, isolated_runtime):
    monkeypatch.setattr(brain.requests, "get", lambda *a, **k: _Response({
        "models": [{"name": "jarvis:old"}, {"name": "ministral-3:3b"}],
    }))
    result = brain.discover_ollama(force=True)
    assert result["service_state"] == "ONLINE"
    assert result["model_state"] == "MISSING"
    assert isolated_runtime.get_status("OLLAMA_SERVICE")["evidence"] == "PROBED"
    assert isolated_runtime.get_status("OLLAMA_MODEL_JARVIS_MINISTRAL_3B")["evidence"] == "BLOCKED"


def test_unreachable_daemon(monkeypatch):
    monkeypatch.setattr(brain.requests, "get", Mock(side_effect=requests.ConnectionError()))
    result = brain.discover_ollama(force=True)
    assert (result["service_state"], result["model_state"]) == ("OFFLINE", "UNKNOWN")
    registry = status_registry.get_registry()
    assert registry.get_status("OLLAMA_SERVICE")["evidence"] == "BLOCKED"
    assert registry.get_status("OLLAMA_MODEL_JARVIS_MINISTRAL_3B")["evidence"] == "UNKNOWN"
    assert registry.get_capability("OLLAMA_LOCAL")["evidence"] == "BLOCKED"


def test_discovery_timeout(monkeypatch):
    monkeypatch.setattr(brain.requests, "get", Mock(side_effect=requests.Timeout()))
    started = time.monotonic()
    result = brain.discover_ollama(force=True)
    assert result["service_state"] == "OFFLINE"
    assert "timed out" in result["detail"]
    assert time.monotonic() - started < 0.5


def test_malformed_api_response(monkeypatch):
    monkeypatch.setattr(brain.requests, "get", lambda *a, **k: _Response({"unexpected": []}))
    result = brain.discover_ollama(force=True)
    assert (result["service_state"], result["model_state"]) == ("DEGRADED", "UNKNOWN")
    assert "malformed" in result["detail"]


def test_generation_uses_exact_jarvis_model(monkeypatch):
    client = Mock()
    client.chat.return_value = {
        "message": {"content": "FINAL_OK", "thinking": "private chain of thought"},
    }
    monkeypatch.setattr(brain, "discover_ollama", lambda **kwargs: _available())
    monkeypatch.setattr(brain, "_get_ollama_client", lambda: client)
    content, status = brain.local_brain_request("Reply FINAL_OK", max_tokens=8)
    assert (content, status) == ("FINAL_OK", "ok")
    kwargs = client.chat.call_args.kwargs
    assert kwargs["model"] == "jarvis:latest"
    assert "think" not in kwargs
    assert kwargs["options"]["num_predict"] == 8
    assert "private" not in content


def test_ollama_client_has_bounded_request_timeout(monkeypatch):
    sdk = Mock()
    sdk.Client.return_value = object()
    monkeypatch.setattr(brain, "_ollama", sdk)
    monkeypatch.setattr(brain, "_OLLAMA_READY", True)
    brain.reset_ollama_runtime_state()
    brain._get_ollama_client()
    sdk.Client.assert_called_once_with(host=brain.OLLAMA_HOST, timeout=brain.OLLAMA_REQUEST_TIMEOUT)


def test_generation_timeout_is_classified_without_retry(monkeypatch):
    client = Mock()
    client.chat.side_effect = TimeoutError("bounded request expired")
    monkeypatch.setattr(brain, "discover_ollama", lambda **kwargs: _available())
    monkeypatch.setattr(brain, "_get_ollama_client", lambda: client)
    content, status = brain.local_brain_request("hello")
    assert (content, status) == ("", "timeout")
    assert client.chat.call_count == 1
    assert brain.get_provider_health("OLLAMA", brain.OLLAMA_MODEL)["cause"] == "TIMEOUT"


def test_no_obsolete_model_can_be_selected():
    assert brain.REQUIRED_OLLAMA_MODEL == brain.OLLAMA_MODEL == "jarvis:latest"
    assert "qwen" not in brain.OLLAMA_MODEL.lower()
    source = Path(brain.__file__).read_text(encoding="utf-8")
    assert "qwen3-vl:4b" not in source


def test_cloud_failure_reaches_actual_local_path(monkeypatch):
    client = Mock()
    client.chat.return_value = {"message": {"content": "LOCAL_OK", "thinking": "hidden"}}
    monkeypatch.setattr(brain, "discover_ollama", lambda **kwargs: _available())
    monkeypatch.setattr(brain, "_get_ollama_client", lambda: client)
    with patch("brain._nvidia_call", return_value=("", "offline")), \
         patch("brain._gemini_call", return_value=("", "offline")):
        result = brain.ask_llm("hello", allow_actions=False)
    assert result == "LOCAL_OK"
    assert brain.get_last_provider_model() == "OLLAMA: jarvis:latest"


def test_offline_discovery_does_not_hang_startup_or_shutdown(monkeypatch):
    monkeypatch.setattr(brain.requests, "get", Mock(side_effect=requests.ConnectionError()))
    started = time.monotonic()
    assert brain.discover_ollama(force=True)["service_state"] == "OFFLINE"
    manager = LifecycleManager(component_stop_timeout=0.05, total_shutdown_timeout=0.1)
    assert manager.shutdown("offline Ollama regression") is True
    assert time.monotonic() - started < 0.5


def test_discovery_evidence_expires_instead_of_staying_available(tmp_path):
    now = [100.0]
    registry = RuntimeStatus(tmp_path / "ttl.json", session_id="ttl", pid=5,
                             wall_clock=lambda: now[0], default_ttl=30)
    registry.set_evidence("OLLAMA_MODEL_JARVIS_MINISTRAL_3B", EvidenceLevel.PROBED,
                          "exact model available", ttl=2)
    assert registry.get_status("OLLAMA_MODEL_JARVIS_MINISTRAL_3B")["evidence"] == "PROBED"
    now[0] = 103.0
    status = registry.get_status("OLLAMA_MODEL_JARVIS_MINISTRAL_3B")
    assert status["evidence"] == "UNKNOWN"
    assert status["stale"] is True

