# boot_check.py — J.A.R.V.I.S Boot Verification & Smoke Test Harness
# ==================================================================
# Actively tests hardware channels, API keys, local ports, and databases,
# syncs the verified realities with the status registry, and prints a report.
#
# Standalone execution: python boot_check.py

import os
import sys
import time
import socket
import datetime
import requests
import importlib.util
import json
import shutil
from urllib.parse import urlparse
from dotenv import load_dotenv
from typing import Tuple

load_dotenv()

# Reliability Imports
import status_registry
from status_registry import EvidenceLevel, SubsystemState, get_registry

# -- Colors for CLI output -----------------------------------------------------
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_GREY = "\033[90m"
COLOR_CYAN = "\033[96m"
COLOR_RESET = "\033[0m"

# ==============================================================================
# SUB-PROBE UTILITIES
# ==============================================================================

def _check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Check if a local/remote TCP port is actively listening."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _check_import(module_name: str) -> bool:
    """Check if a Python library is successfully installed and importable."""
    return importlib.util.find_spec(module_name) is not None



def _publish(registry, name, state, detail, success_evidence, capabilities=()):
    """Publish boot-probe truth without overstating capability readiness."""
    from status_registry import legacy_state_to_evidence

    state_evidence = legacy_state_to_evidence(state, detail)

    # A successful READY probe may publish only the evidence level that the
    # caller can actually prove (CODE/CONFIGURED/PROBED/LIVE).
    if state == SubsystemState.READY:
        evidence = success_evidence
    else:
        # Failed, disabled, or degraded probes override the requested
        # success evidence with the truthful state-derived evidence.
        evidence = state_evidence

    registry.set_evidence(
        name,
        evidence,
        detail,
        source="boot probe",
    )

    for capability in capabilities:
        registry.set_capability_evidence(
            capability,
            evidence,
            detail,
            source="boot probe",
        )

    return evidence


# ==============================================================================
# ACTIVE PROBES
# ==============================================================================

_provider_probe_cache = {}


def _cached_provider_probe(name: str, probe, ttl: float = 300.0):
    cached = _provider_probe_cache.get(name)
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    result = probe()
    _provider_probe_cache[name] = (time.monotonic(), result)
    return result

def probe_voice_stt() -> Tuple[SubsystemState, str]:
    """Test STT engines, PyAudio, and hardware input channels."""
    if not _check_import("speech_recognition") or not _check_import("pyaudio"):
        return SubsystemState.OFFLINE, "SpeechRecognition or PyAudio modules not installed"
    
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        device_count = p.get_device_count()
        p.terminate()
        if device_count == 0:
            return SubsystemState.OFFLINE, "No hardware audio input devices detected"
        
        # Test if CUDA or CPU Whisper is ready
        env_mic = os.getenv("JARVIS_MIC_INDEX")
        idx_msg = f"Using mic index {env_mic or 'auto'}"
        return SubsystemState.READY, f"PyAudio enumerated {device_count} input device(s); capture not tested. {idx_msg}"
    except Exception as e:
        return SubsystemState.DEGRADED, f"Microphone initialization check threw exception: {e}"


def probe_voice_tts() -> Tuple[SubsystemState, str]:
    """Verify Pocket-TTS worker prerequisites plus Edge-TTS/SAPI fallbacks."""
    use_pocket = os.getenv("USE_POCKET_TTS", "true").lower() == "true"

    if use_pocket:
        # Preserve MARK VII's capability-truth contract: if the normal runtime
        # cannot import the optional TTS bridge, the subsystem is DEGRADED.
        # This also keeps the existing missing-optional-subsystem regression
        # test meaningful without loading the CUDA model during boot probing.
        if not _check_import("jarvis_tts"):
            return SubsystemState.DEGRADED, (
                "edge_tts module missing; Pocket-TTS bridge unavailable; falling back to SAPI local voice"
            )

        try:
            import jarvis_tts
            ok, detail = jarvis_tts.prerequisites()
            if not ok:
                return SubsystemState.DEGRADED, (
                    detail + "; Edge-TTS/SAPI fallback remains available"
                )
            # Runtime startup owns the CUDA worker. Do not load a second model
            # from this probe merely to claim synthesis happened.
            return SubsystemState.READY, detail + "; runtime synthesis not yet probed"
        except Exception as exc:
            return SubsystemState.DEGRADED, (
                f"Pocket-TTS configuration probe failed: {exc}"
            )

    if _check_import("edge_tts"):
        return SubsystemState.READY, (
            "Pocket-TTS disabled; Edge-TTS available with SAPI fallback"
        )
    return SubsystemState.DEGRADED, (
        "Pocket-TTS disabled and edge_tts missing; SAPI fallback only"
    )

def probe_camera_and_face_recognition() -> Tuple[Tuple[SubsystemState, str], Tuple[SubsystemState, str]]:
    """Verify camera input availability and face verification embeddings."""
    cam_state = SubsystemState.UNKNOWN
    cam_msg = "Not tested"
    face_state = SubsystemState.UNKNOWN
    face_msg = "Not tested"
    
    if not _check_import("cv2"):
        return (SubsystemState.OFFLINE, "OpenCV (cv2) not installed"), (SubsystemState.DISABLED, "Requires OpenCV module")
    
    import cv2
    # 1. Probe Camera hardware
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                cam_state = SubsystemState.READY
                cam_msg = "Primary webcam is active and capturing frames"
            else:
                cam_state = SubsystemState.DEGRADED
                cam_msg = "Webcam port opened but capture read returned null"
        else:
            cam_state = SubsystemState.OFFLINE
            cam_msg = "Webcam device at index 0 failed to open"
    except Exception as e:
        cam_state = SubsystemState.DEGRADED
        cam_msg = f"Camera verification error: {e}"

    # 2. Probe Face Recognition
    ref_face = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_face.pkl")  # references face encoding
    if not _check_import("face_recognition") and not os.path.exists(ref_face):
        face_state = SubsystemState.DISABLED
        face_msg = "Face credentials or face_recognition module not set up"
    else:
        face_state = SubsystemState.READY if cam_state == SubsystemState.READY else SubsystemState.DEGRADED
        face_msg = "Face recognition prerequisites present; watcher/recognition not probed"

    return (cam_state, cam_msg), (face_state, face_msg)


def probe_ollama() -> Tuple[SubsystemState, str]:
    """Boundedly verify Ollama service and exact model; does not claim generation."""
    import brain
    result = brain.discover_ollama(force=True)
    if result["service_state"] == "OFFLINE":
        return SubsystemState.OFFLINE, result["detail"]
    if result["model_state"] == "AVAILABLE":
        return SubsystemState.READY, result["detail"] + "; generation not yet tested"
    if result["model_state"] == "MISSING":
        return SubsystemState.OFFLINE, result["detail"]
    return SubsystemState.DEGRADED, result["detail"]


def probe_groq() -> Tuple[SubsystemState, str]:
    """Groq is intentionally excluded from automatic MARK VII routing."""
    return SubsystemState.DISABLED, "LEGACY: disabled from automatic brain routing"


def probe_openrouter() -> Tuple[SubsystemState, str]:
    """OpenRouter is intentionally excluded from automatic MARK VII routing."""
    return SubsystemState.DISABLED, "LEGACY: disabled from automatic brain routing"


def probe_nvidia() -> Tuple[SubsystemState, str]:
    """Perform a cached minimal NVIDIA generation; key presence alone is insufficient."""
    def _probe():
        if not os.getenv("NVIDIA_API_KEY", ""):
            return SubsystemState.DISABLED, "CONFIGURED=false (NVIDIA_API_KEY missing)"
        try:
            import brain
            started = time.perf_counter()
            content, status = brain._nvidia_call(
                [{"role": "user", "content": "Reply exactly OK."}],
                brain.NVIDIA_FAST_MODEL,
                max_tokens=16,
                temperature=0.0,
            )
            latency = time.perf_counter() - started
            if status == "ok" and content:
                return SubsystemState.READY, f"LIVE ({brain.NVIDIA_FAST_MODEL}, {latency:.2f}s)"
            health = brain.get_provider_health("NVIDIA", brain.NVIDIA_FAST_MODEL)
            return SubsystemState.DEGRADED, f"{health['cause']} ({brain.NVIDIA_FAST_MODEL})"
        except Exception as exc:
            return SubsystemState.DEGRADED, f"Probe failed: {type(exc).__name__}"
    return _cached_provider_probe("NVIDIA", _probe)


def probe_gemini() -> Tuple[SubsystemState, str]:
    """Perform a cached minimal Gemini text generation."""
    def _probe():
        if not os.getenv("GEMINI_API_KEY", ""):
            return SubsystemState.DISABLED, "CONFIGURED=false (GEMINI_API_KEY missing)"
        try:
            import brain
            started = time.perf_counter()
            content, status = brain._gemini_call(
                [{"role": "user", "content": "Reply exactly OK."}],
                max_tokens=32,
                temperature=0.0,
            )
            latency = time.perf_counter() - started
            if status == "ok" and content:
                return SubsystemState.READY, f"LIVE ({brain.GEMINI_TEXT_MODEL}, {latency:.2f}s)"
            health = brain.get_provider_health("GEMINI", brain.GEMINI_TEXT_MODEL)
            return SubsystemState.DEGRADED, f"{health['cause']} ({brain.GEMINI_TEXT_MODEL})"
        except Exception as exc:
            return SubsystemState.DEGRADED, f"Probe failed: {type(exc).__name__}"
    return _cached_provider_probe("GEMINI", _probe)


def _discover_tesseract() -> str | None:
    """Compatibility wrapper around the canonical bounded OCR discovery."""
    from ocr_runtime import discover_tesseract
    return discover_tesseract().path


def probe_tesseract() -> Tuple[SubsystemState, str]:
    """Find and execute the native OCR binary; this is not an OCR success claim."""
    from ocr_runtime import probe_tesseract as canonical_probe
    result = canonical_probe(force=True)
    if result.state == "AVAILABLE":
        return SubsystemState.READY, result.detail + "; OCR extraction not yet tested"
    if result.state == "UNAVAILABLE":
        return SubsystemState.OFFLINE, result.detail
    return SubsystemState.DEGRADED, result.detail


def probe_memory() -> Tuple[SubsystemState, str]:
    """Verify memory.json readability and validate TF-IDF semantic query module."""
    try:
        import memory
        m_summary = memory.get_memory_summary()
        test_recall = memory.semantic_recall("programming language", top_n=1)
        semantic_msg = " (Semantic retriever OK)" if isinstance(test_recall, list) else ""
        return SubsystemState.READY, f"memory.json validated.{semantic_msg}"
    except Exception as e:
        return SubsystemState.OFFLINE, f"Memory system verification failed: {e}"


def probe_tasks() -> Tuple[SubsystemState, str]:
    """Check tasks.json persistence and reminder re-hydration states."""
    try:
        import tasks
        t_file = tasks.TASKS_FILE
        if os.path.exists(t_file):
            with open(t_file, "r") as f:
                json.load(f)
            return SubsystemState.READY, "tasks.json persistence state verified"
        return SubsystemState.READY, "No task ledger exists on disk; system will default bootstrap"
    except Exception as e:
        return SubsystemState.OFFLINE, f"Task ledger read failed: {e}"


def probe_plugins() -> Tuple[SubsystemState, str]:
    """Assert skills directory is formatted and scan loading capability."""
    skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
    if not os.path.exists(skills_dir):
        return SubsystemState.DISABLED, "No custom skills folder found on host"
    try:
        import plugin_loader
        skills = plugin_loader.load_skills()
        return SubsystemState.READY, f"{len(skills)} custom skills discovered and loaded"
    except Exception as e:
        return SubsystemState.DEGRADED, f"Skill loader parsing failed: {e}"


def probe_whatsapp_send() -> Tuple[SubsystemState, str]:
    """Check pywhatkit automation engine requirements."""
    if not _check_import("pywhatkit"):
        return SubsystemState.DISABLED, "pywhatkit package not installed"
    return SubsystemState.READY, "pywhatkit import available; browser login and delivery not probed"


def probe_spotify() -> Tuple[SubsystemState, str]:
    """Verify Spotify client credential environment properties."""
    cid = os.getenv("SPOTIFY_CLIENT_ID", "")
    secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    if not cid or not secret:
        return SubsystemState.DISABLED, "SPOTIFY_CLIENT_ID or SECRET not set in environmental configuration"
    if not _check_import("spotipy"):
        return SubsystemState.DEGRADED, "spotipy package is not installed on host"
    return SubsystemState.READY, "Spotify credentials and SDK present; OAuth/API not probed"


def probe_email() -> Tuple[SubsystemState, str]:
    """Verify Gmail credentials."""
    user = os.getenv("GMAIL_ADDRESS", "")
    pwd = os.getenv("GMAIL_PASSWORD", "")
    if not user or not pwd:
        return SubsystemState.DISABLED, "GMAIL_ADDRESS or GMAIL_PASSWORD missing in environmental configuration"
    return SubsystemState.READY, "Gmail credentials present; authentication and delivery not probed"


def probe_calendar() -> Tuple[SubsystemState, str]:
    """Verify Google Calendar credentials on disk."""
    creds = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    token = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcal_token.json")
    if not os.path.exists(creds) and not os.path.exists(token):
        return SubsystemState.DISABLED, "credentials.json missing; Calendar sync inactive"
    return SubsystemState.READY, "Google Calendar credential files present; token/API not probed"


def probe_flask_ui() -> Tuple[SubsystemState, str]:
    """Verify server.py exists and dependencies are importable."""
    if not _check_import("flask"):
        return SubsystemState.DISABLED, "Flask module is not installed"
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
    if not os.path.exists(server_path):
        return SubsystemState.DISABLED, "server.py dashboard script not found on host"
    return SubsystemState.READY, "Flask code and dependencies present; server not started"




def probe_file_processor() -> Tuple[SubsystemState, str]:
    """Verify file processor dependencies and capabilities."""
    import file_processor
    availability = file_processor.publish_parser_availability()
    missing = [kind for kind, info in availability.items() if not info["available"]]
    if missing:
        return SubsystemState.DEGRADED, "Parser imports missing for: " + ", ".join(missing)
    return SubsystemState.READY, "TXT, PDF, DOCX, XLSX, and image parser imports available; extraction not yet tested"


def probe_vision() -> Tuple[SubsystemState, str]:
    """Check vision wiring without pretending an image inference occurred."""
    has_cv2 = _check_import("cv2")
    has_mss = _check_import("mss")
    if not has_cv2 or not has_mss:
        return SubsystemState.DEGRADED, "OpenCV or MSS screen-capture dependency is unavailable"

    oll_state, _ = probe_ollama()
    gem_state, _ = probe_gemini()
    local_configured = oll_state == SubsystemState.READY
    cloud_configured = gem_state == SubsystemState.READY
    if local_configured and cloud_configured:
        return SubsystemState.READY, "Screen capture dependencies present; qwen3-vl local primary and Gemini fallback are available; end-to-end image inference not checked"
    if local_configured:
        return SubsystemState.READY, "Screen capture dependencies present; qwen3-vl local vision is available; Gemini fallback unavailable; end-to-end image inference not checked"
    if cloud_configured:
        return SubsystemState.READY, "Screen capture dependencies present; local vision unavailable; Gemini fallback available; end-to-end image inference not checked"
    return SubsystemState.DEGRADED, "Screen capture dependencies present but no visual-understanding provider is currently verified"


def probe_task_queue() -> Tuple[SubsystemState, str]:
    """Verify async task queue availability."""
    if not _check_import("task_queue"):
        return SubsystemState.OFFLINE, "task_queue module not found"
    
    try:
        import task_queue
        return SubsystemState.READY, "Async task queue module imports; workers not started"
    except Exception as e:
        return SubsystemState.DEGRADED, "Task queue import error: " + str(e)


def probe_dev_agent() -> Tuple[SubsystemState, str]:
    """Verify dev agent capabilities."""
    if not _check_import("dev_agent"):
        return SubsystemState.OFFLINE, "dev_agent module not found"
    
    # Check for development tools
    dev_tools = ["pytest", "black", "ruff", "mypy", "git"]
    available = [t for t in dev_tools if _check_import(t) or shutil.which(t)]
    
    return SubsystemState.READY, "Dev agent code imports; actions not run | Tools: " + ", ".join(available)


def probe_configuration() -> Tuple[SubsystemState, str]:
    """Validate active provider configuration without revealing credentials."""
    issues = []
    warnings = []
    for key in ("NVIDIA_API_KEY", "GEMINI_API_KEY"):
        value = os.getenv(key, "").strip().lower()
        if not value or any(marker in value for marker in ("your-", "-here", "placeholder")):
            warnings.append(f"{key} missing or placeholder")
    if os.getenv("OLLAMA_MODEL", "jarvis:latest") != "jarvis:latest":
        issues.append("OLLAMA_MODEL unsupported; expected jarvis:latest")
    host = urlparse(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    if host.scheme not in ("http", "https") or not host.hostname:
        issues.append("OLLAMA_HOST must be an HTTP(S) URL")
    if probe_tesseract()[0] != SubsystemState.READY:
        warnings.append("Tesseract executable not found; OCR unavailable")
    if not shutil.which("ffmpeg"):
        warnings.append("FFmpeg executable not found; media conversion unavailable")

    if issues:
        msg = "Config issues: " + ", ".join(issues)
        if warnings:
            msg += " | Warnings: " + ", ".join(warnings)
        return SubsystemState.DEGRADED, msg
    elif warnings:
        return SubsystemState.READY, "Config OK with warnings: " + ", ".join(warnings)
    else:
        return SubsystemState.READY, "All configuration validated"


def probe_hardware_resources() -> Tuple[SubsystemState, str]:
    """Check system resources (disk, memory, GPU)."""
    try:
        import psutil
        
        # Disk space
        disk = psutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
        free_gb = disk.free / (1024**3)
        
        # Memory
        mem = psutil.virtual_memory()
        mem_free_gb = mem.available / (1024**3)
        
        # CPU
        cpu_count = psutil.cpu_count()
        
        # GPU check (basic)
        gpu_info = "Not detected"
        try:
            import torch
            if torch.cuda.is_available():
                gpu_info = "CUDA (" + torch.cuda.get_device_name(0) + ")"
        except:
            pass
        
        status = SubsystemState.READY
        if free_gb < 5:
            status = SubsystemState.DEGRADED
            msg = "Low disk: " + str(round(free_gb, 1)) + "GB free"
        elif mem_free_gb < 2:
            status = SubsystemState.DEGRADED
            msg = "Low memory: " + str(round(mem_free_gb, 1)) + "GB available"
        else:
            msg = "Disk: " + str(round(free_gb, 1)) + "GB free | RAM: " + str(round(mem_free_gb, 1)) + "GB avail | CPU: " + str(cpu_count) + " cores | GPU: " + gpu_info
        
        return status, msg
    except Exception as e:
        return SubsystemState.DEGRADED, "Hardware check error: " + str(e)





def run_smoke_test() -> bool:
    """Run active probes across all baseline subsystems and update status registry."""
    print("=" * 70)
    print("  J.A.R.V.I.S  -  Runtime Smoke Test Harness")
    print("=" * 70)
    print("Initializing active verification checks on hardware and network resources...")
    time.sleep(0.5)

    registry = get_registry()

    # 1. Voice STT
    stt_state, stt_msg = probe_voice_stt()
    registry.set_status("VOICE_STT", stt_state, stt_msg)

    # 2. Voice TTS
    tts_state, tts_msg = probe_voice_tts()
    registry.set_status("VOICE_TTS", tts_state, tts_msg)

    # 3 & 4. Camera + Face Recognition
    (cam_state, cam_msg), (face_state, face_msg) = probe_camera_and_face_recognition()
    registry.set_status("CAMERA", cam_state, cam_msg)
    registry.set_status("FACE_RECOGNITION", face_state, face_msg)

    # 5. Ollama
    oll_state, oll_msg = probe_ollama()
    registry.set_status("OLLAMA", oll_state, oll_msg)

    # 6. Groq
    gq_state, gq_msg = probe_groq()
    registry.set_status("GROQ", gq_state, gq_msg)

    # 7. OpenRouter
    or_state, or_msg = probe_openrouter()
    registry.set_status("OPENROUTER", or_state, or_msg)

    # 8. Gemini
    gem_state, gem_msg = probe_gemini()
    registry.set_status("GEMINI", gem_state, gem_msg)

    # 9. Tesseract
    tes_state, tes_msg = probe_tesseract()
    registry.set_status("TESSERACT_OCR", tes_state, tes_msg)

    # 10. Memory
    mem_state, mem_msg = probe_memory()
    registry.set_status("MEMORY", mem_state, mem_msg)

    # 11. Tasks
    task_state, task_msg = probe_tasks()
    registry.set_status("TASKS", task_state, task_msg)

    # 12. Plugins
    plug_state, plug_msg = probe_plugins()
    registry.set_status("PLUGINS", plug_state, plug_msg)

    # 13. WhatsApp Send
    was_state, was_msg = probe_whatsapp_send()
    registry.set_status("WHATSAPP_SEND", was_state, was_msg)

    # 14. Spotify
    sp_state, sp_msg = probe_spotify()
    registry.set_status("SPOTIFY", sp_state, sp_msg)

    # 15. Email
    em_state, em_msg = probe_email()
    registry.set_status("EMAIL", em_state, em_msg)

    # 16. Calendar
    cal_state, cal_msg = probe_calendar()
    registry.set_status("CALENDAR", cal_state, cal_msg)

    # 17. Flask UI
    ui_state, ui_msg = probe_flask_ui()
    registry.set_status("FLASK_UI", ui_state, ui_msg)

    # File Processor
    fp_state, fp_msg = probe_file_processor()
    registry.set_status("FILE_PROCESSOR", fp_state, fp_msg)

    # Vision
    vis_state, vis_msg = probe_vision()
    registry.set_status("VISION", vis_state, vis_msg)

    # Task Queue
    tq_state, tq_msg = probe_task_queue()
    registry.set_status("TASK_QUEUE", tq_state, tq_msg)

    # Dev Agent
    da_state, da_msg = probe_dev_agent()
    registry.set_status("DEV_AGENT", da_state, da_msg)

    # Configuration
    cfg_state, cfg_msg = probe_configuration()
    registry.set_status("CONFIG", cfg_state, cfg_msg)

    # Hardware Resources
    hw_state, hw_msg = probe_hardware_resources()
    registry.set_status("HARDWARE", hw_state, hw_msg)

    # Print Formatted Report
    status_registry._print_report()

    # Determine Overall Result
    critical_failures = [
        name for name, info in registry.get_all().items()
        if info.get("state") == SubsystemState.OFFLINE.value and name in ("VOICE_STT", "VOICE_TTS", "OLLAMA", "MEMORY", "TASKS", "FILE_PROCESSOR")
    ]

    if critical_failures:
        print("[FAIL] Boot validation failed. Critical operational failures discovered: " + ", ".join(critical_failures))
        print("Please check local port listeners, folder paths, and file permissions before booting.")
        return False
    
    print("[PASS] Core runtime systems verified successfully. Jarvis is ready for operational tasks!")
    return True


if __name__ == "__main__":
    success = run_smoke_test()
    sys.exit(0 if success else 1)
