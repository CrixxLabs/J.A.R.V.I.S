import base64
from unittest.mock import Mock

import numpy as np
import pytest

import brain
import status_registry
import vision
from status_registry import RuntimeStatus


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    registry = RuntimeStatus(tmp_path / "status.json", session_id="vision-tests", pid=88)
    monkeypatch.setattr(status_registry, "_registry_instance", registry)
    yield registry


def _b64():
    return base64.b64encode(b"small-test-image").decode("ascii")


def test_local_vision_is_primary(monkeypatch, isolated_registry):
    monkeypatch.setattr(brain, "local_vision_request", Mock(return_value=("LOCAL VISION OK", "ok")))
    cloud = Mock(return_value=("CLOUD", "ok"))
    monkeypatch.setattr(vision, "_gemini_vision", cloud)
    assert vision.analyze_image_base64(_b64(), "what is here?") == "LOCAL VISION OK"
    cloud.assert_not_called()
    assert isolated_registry.get_capability("VISION_ROUTER")["evidence"] == "LIVE"
    assert isolated_registry.get_capability("IMAGE_INPUT")["evidence"] == "LIVE"


def test_gemini_fallback_only_after_local_failure(monkeypatch, isolated_registry):
    brain.reset_last_provider()
    monkeypatch.setattr(brain, "local_vision_request", Mock(return_value=("", "offline")))
    cloud = Mock(return_value=("CLOUD FALLBACK OK", "ok"))
    monkeypatch.setattr(vision, "_gemini_vision", cloud)
    assert vision.analyze_image_base64(_b64(), "describe") == "CLOUD FALLBACK OK"
    cloud.assert_called_once()
    assert "Gemini fallback" in isolated_registry.get_capability("VISION_ROUTER")["detail"]
    assert brain.get_last_provider_model() == f"GEMINI: {vision.GEMINI_VISION_MODEL}"


def test_both_providers_fail_truthfully(monkeypatch, isolated_registry):
    monkeypatch.setattr(brain, "local_vision_request", Mock(return_value=("", "timeout")))
    monkeypatch.setattr(vision, "_gemini_vision", Mock(return_value=("", "unavailable")))
    result = vision.analyze_image_base64(_b64(), "describe")
    assert "unavailable" in result.lower()
    assert isolated_registry.get_capability("VISION_ROUTER")["evidence"] == "BLOCKED"


def test_invalid_base64_never_calls_provider(monkeypatch, isolated_registry):
    local = Mock()
    monkeypatch.setattr(brain, "local_vision_request", local)
    result = vision.analyze_image_base64("not base64!", "describe")
    assert "invalid image payload" in result.lower()
    local.assert_not_called()



def test_data_url_input_is_normalized(monkeypatch):
    local = Mock(return_value=("DATA URL OK", "ok"))
    monkeypatch.setattr(brain, "local_vision_request", local)
    payload = _b64()
    result = vision.analyze_image_base64("data:image/png;base64," + payload, "inspect")
    assert result == "DATA URL OK"
    assert local.call_args.args[1] == payload

def test_numpy_image_routes_to_local_vision(monkeypatch):
    local = Mock(return_value=("GREEN SQUARE", "ok"))
    monkeypatch.setattr(brain, "local_vision_request", local)
    monkeypatch.setattr(vision, "_gemini_vision", Mock(return_value=("", "unavailable")))
    img = np.zeros((16, 16, 3), dtype=np.uint8)
    assert vision.describe_image(img, "describe") == "GREEN SQUARE"
    assert local.call_args.args[0] == "describe"
    assert isinstance(local.call_args.args[1], str)


def test_local_vision_request_uses_jarvis_multimodal_model(monkeypatch, isolated_registry):
    monkeypatch.setattr(brain, "discover_ollama", lambda **kwargs: {
        "service_state": "ONLINE", "model_state": "AVAILABLE", "detail": "available"
    })
    monkeypatch.setattr(brain.provider_health, "can_attempt", lambda *args: True)
    client = Mock()
    client.chat.return_value = {"message": {"content": "VISIBLE", "thinking": "PRIVATE"}}
    monkeypatch.setattr(brain, "_get_ollama_client", lambda: client)
    monkeypatch.setattr(brain, "_OLLAMA_READY", True)
    isolated_registry.set_evidence("OLLAMA_MODEL_JARVIS", "PROBED", "available", source="test")
    text, status = brain.local_vision_request("inspect", _b64())
    assert (text, status) == ("VISIBLE", "ok")
    kwargs = client.chat.call_args.kwargs
    assert kwargs["model"] == "jarvis:latest"
    assert kwargs["messages"][0]["images"] == [_b64()]
    assert "think" not in kwargs
    assert isolated_registry.get_capability("OLLAMA_VISION")["evidence"] == "LIVE"


def test_local_vision_timeout_is_bounded_failure(monkeypatch, isolated_registry):
    monkeypatch.setattr(brain, "discover_ollama", lambda **kwargs: {
        "service_state": "ONLINE", "model_state": "AVAILABLE", "detail": "available"
    })
    monkeypatch.setattr(brain.provider_health, "can_attempt", lambda *args: True)
    client = Mock()
    client.chat.side_effect = TimeoutError("request timeout")
    monkeypatch.setattr(brain, "_get_ollama_client", lambda: client)
    monkeypatch.setattr(brain, "_OLLAMA_READY", True)
    assert brain.local_vision_request("inspect", _b64())[1] == "timeout"
    assert isolated_registry.get_capability("OLLAMA_VISION")["evidence"] == "BLOCKED"


def test_webcam_always_releases_handle(monkeypatch):
    cap = Mock()
    cap.isOpened.return_value = True
    cap.read.return_value = (False, None)
    monkeypatch.setattr(vision.cv2, "VideoCapture", Mock(return_value=cap))
    assert vision.capture_webcam(0) is None
    cap.release.assert_called_once()
