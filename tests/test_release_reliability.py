from unittest.mock import patch

import brain


def test_long_casual_sentence_does_not_escalate_by_length_alone():
    query = "good morning jarvis i am just talking with you about my day and how things are going right now and nothing needs deep analysis"
    assert brain._is_complex_query(query) is False


def test_debugging_request_is_complex():
    assert brain._is_complex_query("debug this architecture and explain why the race condition occurs") is True


@patch("brain._apinex_call", return_value=("", "offline"))
@patch("brain._ollama_call", return_value=("local answer", "ok"))
@patch("brain._nvidia_call", return_value=("", "offline"))
@patch("brain._gemini_call", return_value=("", "offline"))
def test_routine_query_uses_apinex_first_falls_back_to_local(mock_gemini, mock_nvidia, mock_ollama, mock_apinex):
    assert brain.ask_llm("how are you", allow_actions=False) == "local answer"
    # Fast path: APInex Flash, APInex fallback, then Ollama.
    assert mock_apinex.call_count == 2
    mock_ollama.assert_called_once()
    mock_nvidia.assert_not_called()
    mock_gemini.assert_not_called()


@patch("brain._apinex_call", return_value=("", "offline"))
@patch("brain._ollama_call", return_value=("", "offline"))
@patch("brain._nvidia_call", return_value=("cloud answer", "ok"))
@patch("brain._gemini_call", return_value=("", "offline"))
def test_complex_query_uses_apinex_pro_first_falls_back_to_nvidia(mock_gemini, mock_nvidia, mock_ollama, mock_apinex):
    assert brain.ask_llm("debug this architecture", allow_actions=False) == "cloud answer"
    # Pro path: APInex Pro, APInex fallback, Ollama, then NVIDIA.
    assert mock_apinex.call_count == 2
    mock_ollama.assert_called_once()
    assert mock_nvidia.call_count == 1
    mock_gemini.assert_not_called()


def test_system_prompt_forbids_unverified_global_health_claims():
    prompt = brain._build_system_prompt(allow_actions=False)
    assert "never claim that all systems" in prompt.lower()
