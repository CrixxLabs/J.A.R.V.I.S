import socket
from types import SimpleNamespace
from unittest.mock import patch

import executor


def test_youtube_id_common_urls():
    vid = "dQw4w9WgXcQ"
    assert executor.get_youtube_id(f"https://www.youtube.com/watch?v={vid}") == vid
    assert executor.get_youtube_id(f"https://youtu.be/{vid}?si=x") == vid
    assert executor.get_youtube_id(f"https://www.youtube.com/shorts/{vid}") == vid
    assert executor.get_youtube_id(f"https://www.youtube.com/embed/{vid}") == vid
    assert executor.get_youtube_id(f"https://www.youtube.com/live/{vid}") == vid


def test_youtube_id_rejects_non_youtube_and_short_ids():
    assert executor.get_youtube_id("https://example.com/watch?v=dQw4w9WgXcQ") is None
    assert executor.get_youtube_id("https://youtu.be/a") is None


def test_public_url_rejects_local_targets_without_fetching():
    assert executor._is_public_http_url("http://localhost:5000") is False
    assert executor._is_public_http_url("file:///etc/passwd") is False
    with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 0))]):
        assert executor._is_public_http_url("https://example.test") is False


def test_public_url_accepts_public_ip_resolution():
    with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        assert executor._is_public_http_url("https://example.com/article") is True


def test_stable_text_failure_is_truthful():
    result = executor._stable_from_text("Spotify unavailable", ("unavailable", "failed"))
    assert result[0] is False


def test_stable_text_success_is_truthful():
    result = executor._stable_from_text("Playback started", ("unavailable", "failed"))
    assert result[0] is True


def test_article_redirect_to_private_target_is_never_fetched():
    redirect = SimpleNamespace(
        status_code=302,
        headers={"Location": "http://127.0.0.1/private"},
        close=lambda: None,
    )
    with patch.object(executor, "_is_public_http_url", side_effect=[True, False]), \
         patch.object(executor.requests, "get", return_value=redirect) as get:
        result = executor.summarize_article("https://public.example/article")
    assert "public HTTP or HTTPS" in result
    assert get.call_count == 1
    assert get.call_args.kwargs["allow_redirects"] is False
    assert get.call_args.kwargs["stream"] is True


def test_failed_web_action_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(executor, "web_search", lambda query: "Search failed.")
    success, message = executor.execute({"action": "web_search", "query": "x"})
    assert success is False
    assert message == "Search failed."
