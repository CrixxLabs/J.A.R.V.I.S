"""Tests for Tiered APInex Cloud Brain & Autonomous Ollama Lifecycle Controller."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

import brain
from brain import (
    APINEX_API_KEY,
    APINEX_BASE_URL,
    APINEX_FALLBACK_MODEL,
    APINEX_FAST_MODEL,
    APINEX_PRO_MODEL,
    _apinex_call,
    _is_complex_query,
    ask_llm,
)
from ollama_daemon import (
    CloudRecoveryWatchdog,
    OllamaLifecycleManager,
    ensure_ollama_running,
    get_ollama_manager,
    is_ollama_running,
    stop_ollama,
)
from speech_cleaner import clean_speech_text, extract_conversational_payload


# ==============================================================================
# TEST 1: APInex Client Live / Mocked Generation with Fast Model
# ==============================================================================

def test_apinex_fast_model_call():
    """Verify APInex client successfully completes generation with free/deepseek-v4.1-flash."""
    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "Respond with OK."},
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "OK. Systems operational."}}]
    }

    with patch("requests.post", return_value=mock_response) as mock_post:
        content, status = _apinex_call(messages, APINEX_FAST_MODEL, max_tokens=64)
        assert status == "ok"
        assert "OK. Systems operational." in content
        assert mock_post.called
        call_args = mock_post.call_args
        assert APINEX_BASE_URL in call_args[0][0]
        assert call_args[1]["json"]["model"] == APINEX_FAST_MODEL


# ==============================================================================
# TEST 2: Intent-based Dispatch (System-1 vs System-2 Deep Reasoning)
# ==============================================================================

def test_intent_based_dispatch_system2():
    """Verify deep deliberation queries route to APINEX_PRO_MODEL (free/deepseek-v4-pro-0813)."""
    complex_query = "Please debug and analyze the AST structure and mathematical proof for this algorithm."
    assert _is_complex_query(complex_query) is True

    simple_query = "Hello, what time is it?"
    assert _is_complex_query(simple_query) is False

    with patch("brain._apinex_call") as mock_apinex:
        mock_apinex.return_value = ("AST analysis complete.", "ok")

        response = ask_llm(complex_query)
        assert "AST analysis complete." in response
        assert mock_apinex.call_count >= 1
        # First call should be with the pro model
        called_model = mock_apinex.call_args_list[0][0][1]
        assert called_model == APINEX_PRO_MODEL


def test_intent_based_dispatch_system1():
    """Verify routine queries route to APINEX_FAST_MODEL (free/deepseek-v4.1-flash)."""
    simple_query = "Hello Jarvis, how are you today?"
    assert _is_complex_query(simple_query) is False

    with patch("brain._apinex_call") as mock_apinex:
        mock_apinex.return_value = ("I am ready.", "ok")

        response = ask_llm(simple_query)
        assert "I am ready." in response
        assert mock_apinex.call_count >= 1
        called_model = mock_apinex.call_args_list[0][0][1]
        assert called_model == APINEX_FAST_MODEL


# ==============================================================================
# TEST 3: Simulated Cloud Outage & Autonomous Local Fallback
# ==============================================================================

def test_simulated_cloud_outage_fallback_to_ollama():
    """Verify automatic fallback to local Ollama when APInex primary and retry fail."""
    query = "What is the status of our deployment?"

    with patch("brain._apinex_call", return_value=("", "timeout")) as mock_apinex, \
         patch("brain._ollama_call", return_value=("Local Ollama online.", "ok")) as mock_ollama, \
         patch.object(get_ollama_manager(), "enter_fallback_mode") as mock_enter_fallback:

        response = ask_llm(query)

        # APInex should have been attempted for primary (fast) and retry (fallback)
        assert mock_apinex.call_count == 2
        # Fallback manager should have been triggered
        assert mock_enter_fallback.called
        # Ollama call should have succeeded and returned the final response
        assert mock_ollama.called
        assert "Local Ollama online." in response


# ==============================================================================
# TEST 4: Ollama Lifecycle Manager Silent Spawning & Resource Reclamation
# ==============================================================================

def test_ollama_lifecycle_manager_spawning_and_termination():
    """Verify OllamaLifecycleManager launches silently and terminates cleanly."""
    manager = OllamaLifecycleManager(host="http://127.0.0.1:11434")

    # Mock subprocess.Popen and requests
    mock_proc = MagicMock()
    mock_proc.terminate = MagicMock()
    mock_proc.wait = MagicMock(return_value=0)

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
         patch("requests.get") as mock_get:

        # First probe fails (offline), second succeeds (ready)
        resp_offline = MagicMock()
        resp_offline.status_code = 503
        resp_online = MagicMock()
        resp_online.status_code = 200

        mock_get.side_effect = [requests.ConnectionError("Offline"), resp_online]

        # Test ensure_running spawns process
        started = manager.ensure_running(timeout=2.0)
        assert started is True
        assert mock_popen.called
        # Verify CREATE_NO_WINDOW flag on Windows
        if os.name == "nt":
            flags = mock_popen.call_args[1].get("creationflags", 0)
            assert flags == subprocess.CREATE_NO_WINDOW

        # Test stop terminates and reclaims
        with patch("subprocess.run") as mock_run:
            stopped = manager.stop(force=True)
            assert stopped is True
            assert mock_proc.terminate.called
            if os.name == "nt":
                assert mock_run.called


# ==============================================================================
# TEST 5: Cloud Recovery Watchdog Background Monitoring
# ==============================================================================

def test_cloud_recovery_watchdog():
    """Verify watchdog thread detects cloud recovery and invokes callback."""
    recovered = False

    def on_recovered():
        nonlocal recovered
        recovered = True

    watchdog = CloudRecoveryWatchdog(
        ping_url="https://api.apinex.bond/v1/models",
        api_key="test-key",
        check_interval=0.1,
        on_cloud_recovered=on_recovered,
    )

    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 500
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200

    with patch("requests.get", side_effect=[requests.ConnectionError(), mock_resp_ok]):
        watchdog.start()
        time.sleep(0.3)
        watchdog.stop()
        watchdog.join(timeout=1.0)

        assert recovered is True
        assert not watchdog.is_alive()


# ==============================================================================
# TEST 6: Speech Cleaner Output Shielding
# ==============================================================================

def test_speech_cleaner_output_shielding():
    """Verify raw markdown fences, json syntax, and action leaks are stripped."""
    raw_output = '```json\n{"action": "open_app", "app": "notepad"}\n```\nOpening Notepad for you now, sir.'
    action, spoken = extract_conversational_payload(raw_output)
    assert action == {"action": "open_app", "app": "notepad"}
    assert spoken == "Opening Notepad for you now, sir."

    dirty_speech = "Here is the code: ```python\nprint('hello')\n```. {status: ok} All done!"
    clean = clean_speech_text(dirty_speech)
    assert "```" not in clean
    assert "{status: ok}" not in clean


# ==============================================================================
# TEST 7: Working Memory Re-Entry Brief Grounding
# ==============================================================================

def test_working_memory_brief_injection():
    """Verify Module AU working memory re-entry brief is synthesized."""
    from working_memory_pager import (
        ItemCategory,
        add_working_memory_item,
        generate_conversational_reentry_brief,
        get_working_memory_pager,
    )

    pager = get_working_memory_pager()
    add_working_memory_item("Complete APInex dual-cloud cognitive integration", ItemCategory.GOAL)
    add_working_memory_item("Enforce 5.0GB RTX 3050 VRAM limit", ItemCategory.ACTIVE_CONSTRAINT)

    brief = generate_conversational_reentry_brief()
    assert "Complete APInex" in brief or "Active constraints" in brief
