import sys
import os
import lifecycle
import jarvis

# Patch startup to add debug prints
original_startup = jarvis.startup

def debug_startup():
    print("[DEBUG] startup() called", flush=True)
    print("[DEBUG] calibrating audio...", flush=True)
    jarvis.listener.calibrate_ambient_noise()
    print("[DEBUG] audio calibrated", flush=True)
    
    print("[DEBUG] init executor...", flush=True)
    jarvis.executor.init(speak_fn=jarvis.speak, ask_fn=jarvis.planner.ask)
    print("[DEBUG] executor init done", flush=True)
    
    print("[DEBUG] registering lifecycle components...", flush=True)
    jarvis._register_lifecycle_components()
    print("[DEBUG] lifecycle components registered", flush=True)
    
    print("[DEBUG] starting lifecycle...", flush=True)
    if not lifecycle.start_all():
        print("[Startup] ERROR: Failed to start one or more components")
        jarvis.speak("Some systems failed to start. Check the console.")
        return
    print("[DEBUG] lifecycle started", flush=True)
    
    print("[DEBUG] running boot scans...", flush=True)
    jarvis._run_boot_syntax_scan()
    jarvis._run_self_awareness_scan()
    print("[DEBUG] boot scans done", flush=True)
    
    print("[DEBUG] speaking 'Systems up.'...", flush=True)
    jarvis.speak("Systems up.")
    print("[DEBUG] spoke", flush=True)
    
    jarvis.memory.log_activity("startup", "Jarvis online")
    jarvis.memory.update_daily_stats("sessions")
    print("[DEBUG] startup complete", flush=True)

jarvis.startup = debug_startup

import lifecycle
lifecycle.setup_lifecycle_signals()

print("[TEST] Calling startup()...", flush=True)
jarvis.startup()
print("[TEST] startup() returned", flush=True)
import time
time.sleep(2)
import status_registry
reg = status_registry.get_registry()
for name, info in reg.get_all().items():
    sys.stdout.write(f'{name} {info["state"]} {info["detail"][:60]}\n')
    sys.stdout.flush()
print('=== SHUTTING DOWN ===', flush=True)
lifecycle.shutdown('Test complete')
print('Shutdown test OK', flush=True)
print("[TEST] Script completed", flush=True)