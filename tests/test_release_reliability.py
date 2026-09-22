from unittest.mock import patch

import brain



def test_long_casual_sentence_does_not_escalate_by_length_alone():
    query = "good morning jarvis i am just talking with you about my day and how things are going right now and nothing needs deep analysis"
    assert brain._is_complex_query(query) is False


def test_debugging_request_is_complex():
    assert brain._is_complex_query("debug this architecture and explain why the race condition occurs") is True


@patch("brain._gemini_call", return_value=("", "offline"))
@patch("brain._nvidia_call", return_value=("", "offline"))
@patch("brain._ollama_call", return_value=("local answer", "ok"))
def test_routine_query_uses_local_first(mock_ollama, mock_nvidia, mock_gemini):
    assert brain.ask_llm("how are you", allow_actions=False) == "local answer"
    mock_ollama.assert_called_once()
    mock_nvidia.assert_not_called()
    mock_gemini.assert_not_called()


@patch("brain._gemini_call", return_value=("", "offline"))
@patch("brain._ollama_call", return_value=("", "offline"))
@patch("brain._nvidia_call", return_value=("cloud answer", "ok"))
def test_complex_query_uses_nemotron_first(mock_nvidia, mock_ollama, mock_gemini):
    assert brain.ask_llm("debug this architecture", allow_actions=False) == "cloud answer"
    mock_nvidia.assert_called_once()
    mock_ollama.assert_not_called()


def test_system_prompt_forbids_unverified_global_health_claims():
    prompt = brain._build_system_prompt(allow_actions=False)
    assert "never claim that all systems" in prompt.lower()
