import json
import os

import pytest

import boot_check
import self_model
from status_registry import EvidenceLevel, RuntimeStatus, SubsystemState


@pytest.fixture
def registry(tmp_path):
    return RuntimeStatus(str(tmp_path / "status.json"), session_id="current", pid=42,
                         wall_clock=lambda: 1000.0, default_ttl=60)


def test_code_does_not_imply_live(registry):
    assert registry.get_capability("PLANNER")["evidence"] == "CODE"


def test_credential_presence_is_only_configured(registry):
    registry.set_evidence("SPOTIFY", EvidenceLevel.CONFIGURED, "credentials present")
    assert registry.get_status("SPOTIFY")["evidence"] == "CONFIGURED"


def test_auth_probe_does_not_imply_end_to_end_live(registry):
    registry.set_evidence("EMAIL", EvidenceLevel.PROBED, "SMTP authenticated")
    assert registry.get_capability("GMAIL")["evidence"] == "CODE"


def test_old_persisted_running_is_stale(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"NVIDIA": {"state": "RUNNING", "detail": "old process"}}))
    reg = RuntimeStatus(str(path), session_id="new", pid=2, wall_clock=lambda: 100)
    status = reg.get_status("NVIDIA")
    assert status["evidence"] == "UNKNOWN" and status["stale"]


def test_pid_mismatch_invalidates_live(tmp_path):
    path = tmp_path / "pid.json"
    path.write_text(json.dumps({"NVIDIA": {"state": "READY", "evidence": "LIVE",
        "detail": "ok", "session_id": "same", "pid": 1, "expires_at": 200}}))
    reg = RuntimeStatus(str(path), session_id="same", pid=2, wall_clock=lambda: 100)
    assert reg.get_status("NVIDIA")["evidence"] == "UNKNOWN"


def test_session_mismatch_invalidates_live(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"NVIDIA": {"evidence": "LIVE", "session_id": "old",
        "pid": 2, "expires_at": 200}}))
    reg = RuntimeStatus(str(path), session_id="new", pid=2, wall_clock=lambda: 100)
    assert reg.get_status("NVIDIA")["stale"] is True


def test_external_view_accepts_unexpired_evidence_from_live_process(tmp_path):
    path = tmp_path / "external.json"
    writer = RuntimeStatus(str(path), session_id="runtime", pid=os.getpid(),
                           wall_clock=lambda: 100)
    writer.set_evidence("NVIDIA", EvidenceLevel.LIVE, "generated", ttl=20)
    reader = RuntimeStatus(str(path), session_id="browser", pid=os.getpid(),
                           wall_clock=lambda: 101)
    assert reader.get_status("NVIDIA")["evidence"] == "UNKNOWN"
    assert reader.get_all_external()["NVIDIA"]["evidence"] == "LIVE"


def test_default_session_identifier_changes_between_runs(tmp_path):
    first = RuntimeStatus(str(tmp_path / "first.json"), pid=10)
    second = RuntimeStatus(str(tmp_path / "second.json"), pid=10)
    assert first.session_id != second.session_id


def test_expired_evidence_loses_freshness(tmp_path):
    now = [100.0]
    reg = RuntimeStatus(str(tmp_path / "expiry.json"), session_id="session", pid=7,
                        wall_clock=lambda: now[0], default_ttl=5)
    reg.set_evidence("MEMORY", EvidenceLevel.PROBED, "query succeeded")
    now[0] = 106.0
    status = reg.get_status("MEMORY")
    assert status["evidence"] == "UNKNOWN"
    assert status["current"] is False
    assert status["stale"] is True


def test_old_failure_is_not_current_after_restart(tmp_path):
    path = tmp_path / "old-failure.json"
    first = RuntimeStatus(str(path), session_id="old", pid=1, wall_clock=lambda: 100)
    first.set_evidence("NVIDIA", EvidenceLevel.BROKEN, "probe timed out")
    second = RuntimeStatus(str(path), session_id="new", pid=2, wall_clock=lambda: 101)
    status = second.get_status("NVIDIA")
    assert status["evidence"] == "UNKNOWN"
    assert status["historical_evidence"] == "BROKEN"


def test_current_probe_is_probed(registry):
    registry.set_evidence("MEMORY", EvidenceLevel.PROBED, "query succeeded")
    assert registry.get_status("MEMORY")["evidence"] == "PROBED"


def test_probe_failure_replaces_live_claim(registry):
    registry.set_evidence("NVIDIA", EvidenceLevel.LIVE, "generation succeeded")
    registry.set_evidence("NVIDIA", EvidenceLevel.BROKEN, "next probe failed")
    status = registry.get_status("NVIDIA")
    assert status["evidence"] == "BROKEN"
    assert status["state"] == "DEGRADED"


def test_successful_operation_can_be_live(registry, tmp_path):
    model = self_model.SelfCapabilityModel(str(tmp_path / "experience.json"), registry)
    model.record_outcome("open_app", True)
    assert registry.get_capability("APP_CONTROL")["evidence"] == "LIVE"


def test_blocked_dependency_propagates(registry):
    registry.set_capability_evidence("SCREEN_CAPTURE", EvidenceLevel.LIVE, "captured")
    registry.set_evidence("TESSERACT_OCR", EvidenceLevel.BLOCKED, "binary absent")
    assert registry.get_capability("OCR")["evidence"] == "BLOCKED"


def test_whatsapp_read_requires_ui_automation(registry):
    registry.set_evidence("UI_AUTOMATION", EvidenceLevel.BLOCKED, "desktop unavailable")
    registry.set_capability_evidence("OCR", EvidenceLevel.LIVE, "ocr worked")
    assert registry.get_capability("WHATSAPP_READ")["evidence"] == "BLOCKED"


def test_optional_dependency_does_not_block(registry):
    registry.set_capability_evidence("NVIDIA_NORMAL", EvidenceLevel.PROBED, "reachable")
    registry.set_capability_evidence("ARTICLE_SUMMARY", EvidenceLevel.PROBED, "probe passed")
    registry.set_capability_evidence("OLLAMA_LOCAL", EvidenceLevel.BLOCKED, "daemon down")
    assert registry.get_capability("ARTICLE_SUMMARY")["evidence"] == "PROBED"


def test_ollama_dead_daemon_is_blocked(registry, monkeypatch):
    import brain
    monkeypatch.setattr(brain, "discover_ollama", lambda **kwargs: {
        "service_state": "OFFLINE", "model_state": "UNKNOWN",
        "detail": "Ollama API unreachable",
    })
    state, detail = boot_check.probe_ollama()
    evidence = boot_check._publish(registry, "OLLAMA", state, detail, EvidenceLevel.LIVE,
                                   capabilities=("OLLAMA_LOCAL",))
    assert evidence == EvidenceLevel.BLOCKED


def test_spotify_keys_are_configured_not_live(registry, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(boot_check, "_check_import", lambda name: True)
    state, detail = boot_check.probe_spotify()
    boot_check._publish(registry, "SPOTIFY", state, detail, EvidenceLevel.CONFIGURED,
                        capabilities=("SPOTIFY",))
    assert registry.get_capability("SPOTIFY")["evidence"] == "CONFIGURED"


def test_tesseract_wrapper_without_binary_is_blocked(registry, monkeypatch):
    import ocr_runtime
    monkeypatch.setattr(ocr_runtime, "discover_tesseract", lambda: ocr_runtime.TesseractStatus(
        "UNAVAILABLE", None, None, "binary absent"))
    state, detail = boot_check.probe_tesseract()
    boot_check._publish(registry, "TESSERACT_OCR", state, detail, EvidenceLevel.PROBED)
    assert registry.get_status("TESSERACT_OCR")["evidence"] == "BLOCKED"


def test_calendar_without_oauth_files_is_blocked(registry, monkeypatch):
    monkeypatch.setattr(boot_check.os.path, "exists", lambda path: False)
    state, detail = boot_check.probe_calendar()
    boot_check._publish(registry, "CALENDAR", state, detail, EvidenceLevel.CONFIGURED)
    # Missing OAuth files -> DISABLED (explicitly not configured), not BLOCKED (dependency failed)
    assert registry.get_status("CALENDAR")["evidence"] == "DISABLED"


def test_provider_live_evidence_is_granular(registry):
    registry.set_capability_evidence("NVIDIA_NORMAL", EvidenceLevel.LIVE, "fast generated")
    registry.set_capability_evidence("GEMINI_FALLBACK", EvidenceLevel.LIVE, "fallback generated")
    assert registry.get_capability("NVIDIA_NORMAL")["evidence"] == "LIVE"
    assert registry.get_capability("NVIDIA_REASONING")["evidence"] == "CODE"
    assert registry.get_capability("GEMINI_FALLBACK")["evidence"] == "LIVE"


def test_legacy_providers_remain_legacy(registry):
    for name, capability in (("GROQ", "GROQ_LEGACY"), ("OPENROUTER", "OPENROUTER_LEGACY")):
        registry.set_evidence(name, EvidenceLevel.LEGACY, "legacy")
        assert registry.get_capability(capability)["evidence"] == "LEGACY"


def test_self_model_answer_uses_registry_truth(registry, tmp_path):
    registry.set_evidence("TESSERACT_OCR", EvidenceLevel.BLOCKED, "executable absent")
    model = self_model.SelfCapabilityModel(str(tmp_path / "exp.json"), registry)
    answer = model.answer_capability_question("Can you perform OCR locally?")
    assert "BLOCKED" in answer and "executable absent" in answer


def test_registry_serialization_keeps_legacy_state(registry):
    registry.set_evidence("MEMORY", EvidenceLevel.LIVE, "operation complete")
    payload = json.loads(open(registry._path, encoding="utf-8").read())
    assert payload["MEMORY"]["state"] == "READY"
    assert payload["MEMORY"]["evidence"] == "LIVE"


def test_status_snapshot_is_json_serializable_for_api(registry):
    registry.set_evidence("MEMORY", EvidenceLevel.PROBED, "query succeeded")
    payload = json.loads(json.dumps(registry.get_all()))
    assert payload["MEMORY"]["session_id"] == "current"
    assert payload["MEMORY"]["pid"] == 42
    assert payload["MEMORY"]["evidence_source"] == "runtime"


def test_missing_optional_subsystem_does_not_crash_boot_probe(monkeypatch):
    monkeypatch.setattr(boot_check, "_check_import", lambda _name: False)
    state, detail = boot_check.probe_voice_tts()
    assert state == SubsystemState.DEGRADED
    assert "edge_tts module missing" in detail


def test_boot_publish_does_not_upgrade_code_presence_to_ready(registry):
    evidence = boot_check._publish(
        registry, "FLASK_UI", SubsystemState.READY,
        "Flask code present; server not started", EvidenceLevel.CODE,
    )
    status = registry.get_status("FLASK_UI")
    assert evidence == EvidenceLevel.CODE
    assert status["evidence"] == "CODE"
    assert status["state"] == "UNKNOWN"


def test_secrets_do_not_appear_in_spotify_status(registry, monkeypatch):
    secret = "do-not-print-this-secret"
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", secret)
    monkeypatch.setattr(boot_check, "_check_import", lambda name: True)
    state, detail = boot_check.probe_spotify()
    boot_check._publish(registry, "SPOTIFY", state, detail, EvidenceLevel.CONFIGURED)
    assert secret not in json.dumps(registry.get_status("SPOTIFY"))
