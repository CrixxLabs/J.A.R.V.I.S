with open(r'D:\J.A.R.V.I.S\boot_check.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find the end of probe_hardware_resources function
idx = content.rfind('def probe_hardware_resources')
if idx >= 0:
    # Find the end of this function (next def or double newline)
    idx2 = content.find('\ndef ', idx + 1)
    if idx2 == -1:
        idx2 = content.find('\n\n', idx)
    if idx2 == -1:
        idx2 = len(content)

run_smoke_test = '''

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
'''

content = content[:idx2] + run_smoke_test

with open(r'D:\J.A.R.V.I.S\boot_check.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('run_smoke_test restored')