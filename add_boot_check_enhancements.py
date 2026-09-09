with open(r'D:\J.A.R.V.I.S\boot_check.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Add new probe functions before MAIN EXECUTOR section
new_probes = '''

def probe_file_processor() -> Tuple[SubsystemState, str]:
    """Verify file processor dependencies and capabilities."""
    required = ["PyPDF2", "docx", "openpyxl", "PIL"]
    missing = [m for m in required if not _check_import(m.lower().replace("PIL", "PIL").replace("docx", "docx"))]
    
    optional = ["pytesseract", "whisper", "ffmpeg", "magic", "pandas"]
    optional_missing = [m for m in optional if not _check_import(m)]
    
    if missing:
        return SubsystemState.DEGRADED, f"Core deps missing: {', '.join(missing)}. Optional missing: {', '.join(optional_missing)}"
    
    msg = "All core file processor dependencies available"
    if optional_missing:
        msg += f" | Optional: {', '.join(optional_missing)} unavailable"
    return SubsystemState.READY, msg


def probe_vision() -> Tuple[SubsystemState, str]:
    """Verify vision subsystem (Gemini Vision + OCR)."""
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    has_tesseract = _check_import("pytesseract")
    has_cv2 = _check_import("cv2")
    has_mss = _check_import("mss")
    
    if not has_cv2 or not has_mss:
        return SubsystemState.DEGRADED, "OpenCV or MSS screen capture not available"
    
    if not gemini_key and not has_tesseract:
        return SubsystemState.DISABLED, "No Gemini API key and no Tesseract OCR fallback"
    
    if gemini_key and has_tesseract:
        return SubsystemState.READY, "Gemini Vision + Tesseract OCR fallback active"
    elif gemini_key:
        return SubsystemState.READY, "Gemini Vision active (no local OCR fallback)"
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
        return SubsystemState.DEGRADED, f"Task queue import error: {e}"


def probe_dev_agent() -> Tuple[SubsystemState, str]:
    """Verify dev agent capabilities."""
    if not _check_import("dev_agent"):
        return SubsystemState.OFFLINE, "dev_agent module not found"
    
    # Check for development tools
    dev_tools = ["pytest", "black", "ruff", "mypy", "git"]
    available = [t for t in dev_tools if _check_import(t) or shutil.which(t)]
    
    return SubsystemState.READY, f"Dev agent ready | Tools: {', '.join(available)}"


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
                warnings.append(f"{key} not set (cloud fallback unavailable)")
    
    # Check memory.json
    mem_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
    if not os.path.exists(mem_path):
        warnings.append("memory.json not found (will be created on first run)")
    
    # Check user voice
    voice_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_voice.npy")
    if not os.path.exists(voice_path):
        warnings.append("user_voice.npy not found (voice recognition disabled)")
    
    if issues:
        return SubsystemState.DEGRADED, f"Config issues: {', '.join(issues)}" + (f" | Warnings: {', '.join(warnings)}" if warnings else "")
    elif warnings:
        return SubsystemState.READY, f"Config OK with warnings: {', '.join(warnings)}"
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
                gpu_info = f"CUDA ({torch.cuda.get_device_name(0)})"
        except:
            pass
        
        status = SubsystemState.READY
        if free_gb < 5:
            status = SubsystemState.DEGRADED
            msg = f"Low disk: {free_gb:.1f}GB free"
        elif mem_free_gb < 2:
            status = SubsystemState.DEGRADED
            msg = f"Low memory: {mem_free_gb:.1f}GB available"
        else:
            msg = f"Disk: {free_gb:.1f}GB free | RAM: {mem_free_gb:.1f}GB avail | CPU: {cpu_count} cores | GPU: {gpu_info}"
        
        return status, msg
    except Exception as e:
        return SubsystemState.DEGRADED, f"Hardware check error: {e}"


'''

# Insert before MAIN EXECUTOR
marker = '# ═══════════════════════════════════════════════════════════════════════════════\n# MAIN EXECUTOR\n# ═══════════════════════════════════════════════════════════════════════════════'
idx = content.find(marker)
if idx >= 0:
    content = content[:idx] + new_probes + content[idx]

# Now update run_smoke_test to include new probes
run_smoke_marker = 'def run_smoke_test() -> bool:'
idx = content.find(run_smoke_marker)
if idx >= 0:
    # Find the end of the probe registration section (before "Print Formatted Report")
    report_marker = '# Print Formatted Report'
    idx2 = content.find(report_marker, idx)
    
    # Add new probe calls before the report
    new_probe_calls = '''
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

'''
    content = content[:idx2] + new_probe_calls + content[idx2]

# Also update critical_failures list
critical_marker = 'critical_failures = ['
idx = content.find(critical_marker)
if idx >= 0:
    idx2 = content.find(']', idx)
    if idx2 >= 0:
        new_critical = '''critical_failures = [
        name for name, info in registry.get_all().items()
        if info.get("state") == SubsystemState.OFFLINE.value and name in ("VOICE_STT", "VOICE_TTS", "OLLAMA", "MEMORY", "TASKS", "FILE_PROCESSOR")
    ]'''
        content = content[:idx] + new_critical + content[idx2+1:]

# Add import shutil at top
import_marker = 'import importlib.util'
idx = content.find(import_marker)
if idx >= 0:
    idx_end = content.find('\n', idx) + 1
    content = content[:idx_end] + '\nimport shutil' + content[idx_end:]

with open(r'D:\J.A.R.V.I.S\boot_check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Boot check enhanced')