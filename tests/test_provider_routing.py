from unittest.mock import patch

import brain
from provider_health import HealthState, ProviderHealth, classify_http


def test_health_cooldown_and_recovery():
    now = [100.0]
    health = ProviderHealth(clock=lambda: now[0])
    health.record_failure("NVIDIA", "model", HealthState.OFFLINE, retry_after=10)
    assert health.can_attempt("NVIDIA", "model") is False
    assert health.get("NVIDIA", "model")["state"] == "COOLDOWN"
    now[0] = 111.0
    assert health.can_attempt("NVIDIA", "model") is True
    health.record_success("NVIDIA", "model")
    assert health.get("NVIDIA", "model")["state"] == "LIVE"


def test_http_failure_classification():
    assert classify_http(401) == HealthState.AUTH_ERROR
    assert classify_http(404, "model not found") == HealthState.MODEL_UNAVAILABLE
    assert classify_http(429) == HealthState.RATE_LIMITED
    assert classify_http(500) == HealthState.SERVER_ERROR
    assert classify_http(400) == HealthState.BAD_REQUEST


@patch("brain._ollama_call")
@patch("brain._gemini_call")
@patch("brain._nvidia_call")
def test_normal_route_prefers_local_ollama(mock_nvidia, mock_gemini, mock_ollama):
    mock_ollama.return_value = ("local", "ok")
    assert brain.ask_llm("Tell me something", allow_actions=False) == "local"
    mock_nvidia.assert_not_called()
    mock_gemini.assert_not_called()


@patch("brain._ollama_call")
@patch("brain._gemini_call")
@patch("brain._nvidia_call")
def test_routine_falls_back_to_nvidia_then_gemini(mock_nvidia, mock_gemini, mock_ollama):
    mock_ollama.return_value = ("", "offline")
    mock_nvidia.return_value = ("nvidia", "ok")
    assert brain.ask_llm("Hello there", allow_actions=False) == "nvidia"
    mock_gemini.assert_not_called()

    mock_nvidia.return_value = ("", "offline")
    mock_gemini.return_value = ("gemini", "ok")
    assert brain.ask_llm("Hello again", allow_actions=False) == "gemini"


@patch("brain._ollama_call", return_value=("", "offline"))
@patch("brain._gemini_call", return_value=("", "offline"))
@patch("brain._nvidia_call", return_value=("", "offline"))
def test_all_provider_failure_is_clean_and_fast(*_mocks):
    result = brain.ask_llm("Hello", allow_actions=False)
    assert "local commands are still available" in result


@patch("brain._ollama_call")
@patch("brain._gemini_call", return_value=("", "offline"))
@patch("brain._nvidia_call", return_value=("", "offline"))
def test_legacy_providers_are_never_in_automatic_route(_nvidia, _gemini, mock_ollama):
    mock_ollama.return_value = ("local", "ok")
    with patch("brain._groq_call") as groq, patch("brain._openrouter_call") as openrouter:
        assert brain.ask_llm("Hello", allow_actions=False) == "local"
        groq.assert_not_called()
        openrouter.assert_not_called()


@patch("brain.requests.post")
def test_nvidia_structured_payload_disables_thinking(mock_post):
    response = mock_post.return_value
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": '{"status":"ok"}'}}]}
    with patch.object(brain, "NVIDIA_API_KEY", "test-key"):
        brain.provider_health.reset()
        result, status = brain._nvidia_call(
            [{"role": "user", "content": "JSON"}], brain.NVIDIA_FAST_MODEL,
            response_format={"type": "json_object"},
        )
    assert status == "ok"
    assert result == '{"status":"ok"}'
    payload = mock_post.call_args.kwargs["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}



@patch("brain._ollama_call")
@patch("brain._gemini_call")
@patch("brain._nvidia_call")
def test_simple_explanation_stays_local(mock_nvidia, mock_gemini, mock_ollama):
    mock_ollama.return_value = ("local explanation", "ok")
    assert brain.ask_llm("Explain what a neural network is", allow_actions=False) == "local explanation"
    mock_nvidia.assert_not_called()
    mock_gemini.assert_not_called()


@patch("brain._ollama_call")
@patch("brain._gemini_call")
@patch("brain._nvidia_call")
def test_complex_architecture_routes_to_nemotron(mock_nvidia, mock_gemini, mock_ollama):
    mock_nvidia.return_value = ("complex answer", "ok")
    assert brain.ask_llm("Analyze this architecture and debug the root cause", allow_actions=False) == "complex answer"
    assert mock_nvidia.call_args.args[1] == brain.NVIDIA_REASONING_MODEL
    mock_ollama.assert_not_called()
    mock_gemini.assert_not_called()
