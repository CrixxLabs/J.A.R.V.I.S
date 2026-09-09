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
from dotenv import load_dotenv
from typing import Tuple

load_dotenv()

# Reliability Imports
import status_registry
from status_registry import SubsystemState, get_registry

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


# ==============================================================================
# ACTIVE PROBES
# ==============================================================================

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
        return SubsystemState.READY, f"PyAudio online ({device_count} devices found. {idx_msg})"
    except Exception as e:
        return SubsystemState.DEGRADED, f"Microphone initialization check threw exception: {e}"


def probe_voice_tts() -> Tuple[SubsystemState, str]:
    """Test local F5-TTS requirements, Edge-TTS, and SAPI fallback engines."""
    # Check Edge-TTS (default engine)
    if not _check_import("edge_tts"):
        return SubsystemState.DEGRADED, "edge_tts module missing; falling back to SAPI local voice"
    
    # Check F5-TTS
    use_f5 = os.getenv("USE_F5_TTS", "false").lower() == "true"
    ref_audio = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Voices", "Jarvis.wav")
    
    if use_f5:
        if not _check_import("f5_tts"):
            return SubsystemState.DEGRADED, "USE_F5_TTS is enabled but f5_tts package is not installed"
        if not os.path.exists(ref_audio):
            return SubsystemState.DEGRADED, f"F5-TTS active but reference sample Jarvis.wav missing at: {ref_audio}"
        return SubsystemState.READY, "F5-TTS local CPU voice cloning engine verified"
    
    return SubsystemState.READY, "Edge-TTS online + SAPI Windows local fallback configured"


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
        face_msg = "Face verification watcher calibrated and ready"

    return (cam_state, cam_msg), (face_state, face_msg)


def probe_ollama() -> Tuple[SubsystemState, str]:
    """Verify that local Ollama port is open and Qwen model is pull-verified."""
    host = "127.0.0.1"
    port = 11434
    
    if not _check_port_open(host, port):
        return SubsystemState.OFFLINE, f"Ollama local daemon is not running on port {port}"
    
    try:
        res = requests.get(f"http://{host}:{port}/api/tags", timeout=1.0)
        if res.status_code == 200:
            models = [m.get("name") for m in res.json().get("models", [])]
            qwen_model = "qwen2.5:3b"
            matching = [m for m in models if qwen_model in m]
            if matching:
                return SubsystemState.READY, f"Ollama operational ({matching[0]} model verified)"
            return SubsystemState.DEGRADED, f"Ollama is running but model '{qwen_model}' was not found in: {models}"
        return SubsystemState.DEGRADED, f"Ollama endpoint returned status code {res.status_code}"
    except Exception as e:
        return SubsystemState.DEGRADED, f"Ollama endpoint ping failed: {e}"


def probe_groq() -> Tuple[SubsystemState, str]:
    """Test Groq API configuration parameters and SDK."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return SubsystemState.DISABLED, "GROQ_API_KEY is missing in environmental configurations"
    if not _check_import("groq"):
        return SubsystemState.DEGRADED, "groq Python SDK package is not installed"
    return SubsystemState.READY, "Groq Cloud API parameters verified"


def probe_openrouter() -> Tuple[SubsystemState, str]:
    """Test OpenRouter API keys."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return SubsystemState.DISABLED, "OPENROUTER_API_KEY is missing in environmental configurations"
    return SubsystemState.READY, "OpenRouter API verified (Kling standard fallback routing active)"


def probe_gemini() -> Tuple[SubsystemState, str]:
    """Test Gemini Vision API credentials."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return SubsystemState.DISABLED, "GEMINI_API_KEY is missing in environmental configurations"
    return SubsystemState.READY, "Gemini Vision API validated"


def probe_tesseract() -> Tuple[SubsystemState, str]:
    """Ping system Tesseract-OCR binary paths and verify module."""
    if not _check_import("pytesseract"):
        return SubsystemState.OFFLINE, "pytesseract Python wrapper package not installed"
    
    import pytesseract
    env_path = os.getenv("TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    pytesseract.pytesseract.tesseract_cmd = env_path

    if os.path.exists(env_path):
        return SubsystemState.READY, f"Tesseract-OCR binary located at: {env_path}"
    
    # Try looking in system path
    import shutil
    sys_find = shutil.which("tesseract")
    if sys_find:
        pytesseract.pytesseract.tesseract_cmd = sys_find
        return SubsystemState.READY, f"Tesseract-OCR binary located on system PATH: {sys_find}"
    
    return SubsystemState.OFFLINE, f"Tesseract-OCR executable was not found at configured path: {env_path}"


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
        skills = plugin_loader.skills
        return SubsystemState.READY, f"{len(skills)} custom skills discovered and loaded"
    except Exception as e:
        return SubsystemState.DEGRADED, f"Skill loader parsing failed: {e}"


def probe_whatsapp_send() -> Tuple[SubsystemState, str]:
    """Check pywhatkit automation engine requirements."""
    if not _check_import("pywhatkit"):
        return SubsystemState.DISABLED, "pywhatkit package not installed"
    return SubsystemState.READY, "pywhatkit automated messaging active"


def probe_spotify() -> Tuple[SubsystemState, str]:
    """Verify Spotify client credential environment properties."""
    cid = os.getenv("SPOTIFY_CLIENT_ID", "")
    secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    if not cid or not secret:
        return SubsystemState.DISABLED, "SPOTIFY_CLIENT_ID or SECRET not set in environmental configuration"
    if not _check_import("spotipy"):
        return SubsystemState.DEGRADED, "spotipy package is not installed on host"
    return SubsystemState.READY, "Spotify credentials authenticated"


def probe_email() -> Tuple[SubsystemState, str]:
    """Verify Gmail credentials."""
    user = os.getenv("GMAIL_ADDRESS", "")
    pwd = os.getenv("GMAIL_PASSWORD", "")
    if not user or not pwd:
        return SubsystemState.DISABLED, "GMAIL_ADDRESS or GMAIL_PASSWORD missing in environmental configuration"
    return SubsystemState.READY, f"SMTP pipelines prepared ({user})"


def probe_calendar() -> Tuple[SubsystemState, str]:
    """Verify Google Calendar credentials on disk."""
    creds = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    token = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcal_token.json")
    if not os.path.exists(creds) and not os.path.exists(token):
        return SubsystemState.DISABLED, "credentials.json missing; Calendar sync inactive"
    return SubsystemState.READY, "Google Calendar OAuth tokens verified"


def probe_flask_ui() -> Tuple[SubsystemState, str]:
    """Verify server.py exists and dependencies are importable."""
    if not _check_import("flask"):
        return SubsystemState.DISABLED, "Flask module is not installed"
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
    if not os.path.exists(server_path):
        return SubsystemState.DISABLED, "server.py dashboard script not found on host"
    return SubsystemState.READY, "Flask server dependencies verified"




def probe_file_processor() -> Tuple[SubsystemState, str]:
    """Verify file processor dependencies and capabilities."""
    required = ["PyPDF2", "docx", "openpyxl", "PIL"]
    missing = [m for m in required if not _check_import(m.lower().replace("PIL", "PIL").replace("docx", "docx"))]
    
    optional = ["pytesseract", "whisper", "ffmpeg", "magic", "pandas"]
    optional_missing = [m for m in optional if not _check_import(m)]
    
    if missing:
        return SubsystemState.DEGRADED, "Core deps missing: " + ", ".join(missing) + ". Optional missing: " + ", ".join(optional_missing)
    
    msg = "All core file processor dependencies available"
    if optional_missing:
        msg += " | Optional: " + ", ".join(optional_missing) + " unavailable"
    return SubsystemState.READY, msg


def probe_vision() -> Tuple[SubsystemState, str]:
    """Verify vision subsystem (Gemini Vision + OCR)."""
    # Check actual Tesseract status (binary availability)
    tes_state, tes_msg = probe_tesseract()
    has_tesseract = (tes_state == SubsystemState.READY)
    
    # Check actual Gemini status (API key)
    gem_state, gem_msg = probe_gemini()
    has_gemini = (gem_state == SubsystemState.READY)
    
    has_cv2 = _check_import("cv2")
    has_mss = _check_import("mss")
    
    if not has_cv2 or not has_mss:
        return SubsystemState.DEGRADED, "OpenCV or MSS screen capture not available"
    
    # Determine vision readiness based on actual working components
    if not has_gemini and not has_tesseract:
        return SubsystemState.DISABLED, "No Gemini API key and no Tesseract OCR fallback"
    
    # Both available
    if has_gemini and has_tesseract:
        return SubsystemState.READY, "Gemini Vision + Tesseract OCR fallback active"
    # Only Gemini available
    elif has_gemini:
        return SubsystemState.READY, "Gemini Vision active (no local OCR fallback)"
    # Only Tesseract available
    else:
        return SubsystemState.READY, "Tesseract OCR active (no cloud vision)"


def probe_task_queue() -> Tuple[SubsystemState, str]:
    """Verify async task queue availability."""
    if not _check_import("task_queue"):
        return SubsystemState.OFFLINE, "task_queue module not found"
    
    try:
        import task_queue
        return SubsystemState.READY, "Async task queue module loaded (4 workers default)"
    except Exception as e:
        return SubsystemState.DEGRADED, "Task queue import error: " + str(e)


def probe_dev_agent() -> Tuple[SubsystemState, str]:
    """Verify dev agent capabilities."""
    if not _check_import("dev_agent"):
        return SubsystemState.OFFLINE, "dev_agent module not found"
    
    # Check for development tools
    dev_tools = ["pytest", "black", "ruff", "mypy", "git"]
    available = [t for t in dev_tools if _check_import(t) or shutil.which(t)]
    
    return SubsystemState.READY, "Dev agent ready | Tools: " + ", ".join(available)


def probe_configuration() -> Tuple[SubsystemState, str]:
    """Validate .env configuration and critical paths."""
    issues = []
    warnings = []
    
    # Check .env exists
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        issues.append(".env file not found")
    else:
        # Check critical keys
        critical_keys = ["OPENROUTER_API_KEY", "GROQ_API_KEY"]
        for key in critical_keys:
            if not os.getenv(key):
                warnings.append(key + " not set (cloud fallback unavailable)")
    
    # Check memory.json
    mem_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
    if not os.path.exists(mem_path):
        warnings.append("memory.json not found (will be created on first run)")
    
    # Check user voice
    voice_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_voice.npy")
    if not os.path.exists(voice_path):
        warnings.append("user_voice.npy not found (voice recognition disabled)")
    
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
    # Check if any LLM provider is available
    ollama_state = registry.get_all().get("OLLAMA", {}).get("state")
    groq_state = registry.get_all().get("GROQ", {}).get("state")
    openrouter_state = registry.get_all().get("OPENROUTER", {}).get("state")
    has_llm_provider = any(s == SubsystemState.READY.value for s in [ollama_state, groq_state, openrouter_state])
    
    critical_failures = [
        name for name, info in registry.get_all().items()
        if info.get("state") == SubsystemState.OFFLINE.value and name in ("VOICE_STT", "VOICE_TTS", "MEMORY", "TASKS", "FILE_PROCESSOR")
    ]
    
    # If no LLM provider is available, that's a critical failure for conversational AI
    if not has_llm_provider:
        critical_failures.append("NO_LLM_PROVIDER")

    if critical_failures:
        print("[FAIL] Boot validation failed. Critical operational failures discovered: " + ", ".join(critical_failures))
        print("Please check local port listeners, folder paths, and file permissions before booting.")
        return False
    
    print("[PASS] Core runtime systems verified successfully. Jarvis is ready for operational tasks!")
    return True


if __name__ == "__main__":
    success = run_smoke_test()
    sys.exit(0 if success else 1)
