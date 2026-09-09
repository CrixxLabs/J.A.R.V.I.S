with open(r'D:\J.A.R.V.I.S\boot_check.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find the MAIN EXECUTOR section
marker = '# ==============================================================================\n# MAIN EXECUTOR\n# =============================================================================='
idx = content.find(marker)
if idx >= 0:
    new_probes = '''

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


'''
    content = content[:idx] + new_probes + content[idx]

with open(r'D:\J.A.R.V.I.S\boot_check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Probe functions added')