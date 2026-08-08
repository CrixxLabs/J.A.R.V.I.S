# system_monitor.py — Unified System Status (Phase 4)
# Aggregates CPU/RAM/battery (from observer) + GPU stats (pynvml → GPUtil fallback)
# Single entry point: get_system_status(mode="full" | "gpu" | "cpu" | "battery")
# Designed for RTX 3050 but works on any NVIDIA GPU.

import warnings
import psutil

# ── GPU library detection ─────────────────────────────────────────────────────
_GPU_BACKEND = None   # "pynvml" | "gputil" | None
_pynvml = None
_GPUtil = None

# Suppress the pynvml deprecation warning cleanly
warnings.filterwarnings("ignore", category=FutureWarning, module="pynvml")

try:
    import pynvml as _pynvml
    try:
        _pynvml.nvmlInit()
        _GPU_BACKEND = "pynvml"
        print("[SystemMonitor] GPU backend: pynvml")
    except Exception as e:
        print(f"[SystemMonitor] pynvml init failed: {e}")
        _pynvml = None
except ImportError:
    pass

if _GPU_BACKEND is None:
    try:
        import GPUtil as _GPUtil
        _GPU_BACKEND = "gputil"
        print("[SystemMonitor] GPU backend: GPUtil (fallback)")
    except ImportError:
        print("[SystemMonitor] No GPU library available — GPU stats disabled.")


# ── Internal helpers ──────────────────────────────────────────────────────────
def _observer_has_real_data(snap):
    """
    Observer's initial state defaults are all zero/-1.
    Only trust observer if at least one value looks real (non-default).
    """
    if not snap:
        return False
    # CPU and RAM are 0 only during the first 5s before observer fills them
    # If both are exactly 0, observer probably hasn't run yet
    if snap.get("cpu_percent", 0) == 0 and snap.get("ram_percent", 0) == 0:
        return False
    return True


def _get_observer_snapshot():
    """Pull cached CPU/RAM/battery from observer if running with real data, else None."""
    try:
        import observer
        snap = observer.get_state_snapshot()
        if _observer_has_real_data(snap):
            return snap
    except Exception:
        pass
    return None


def _get_cpu_ram_battery_fallback():
    """Direct psutil read — used when observer isn't ready."""
    try:
        cpu = psutil.cpu_percent(interval=0.3)
        ram = psutil.virtual_memory()
        bat = psutil.sensors_battery()
        return {
            "cpu_percent":      cpu,
            "ram_percent":      ram.percent,
            "battery_percent":  bat.percent if bat else -1,
            "battery_charging": bat.power_plugged if bat else False,
        }
    except Exception as e:
        print(f"[SystemMonitor] psutil fallback failed: {e}")
        return {
            "cpu_percent": 0, "ram_percent": 0,
            "battery_percent": -1, "battery_charging": False,
        }


def _get_gpu_stats():
    """
    Returns dict with GPU info, or {} if no GPU library available.
    Keys: name, gpu_percent, mem_used_mb, mem_total_mb, mem_percent, temp_c
    """
    if _GPU_BACKEND == "pynvml":
        try:
            handle = _pynvml.nvmlDeviceGetHandleByIndex(0)
            name = _pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="ignore")
            util = _pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem  = _pynvml.nvmlDeviceGetMemoryInfo(handle)
            temp = _pynvml.nvmlDeviceGetTemperature(handle, _pynvml.NVML_TEMPERATURE_GPU)
            return {
                "name":         name,
                "gpu_percent":  util.gpu,
                "mem_used_mb":  mem.used  / (1024 * 1024),
                "mem_total_mb": mem.total / (1024 * 1024),
                "mem_percent":  (mem.used / mem.total * 100) if mem.total else 0,
                "temp_c":       temp,
            }
        except Exception as e:
            print(f"[SystemMonitor] pynvml read failed: {e}")
            return {}

    if _GPU_BACKEND == "gputil":
        try:
            gpus = _GPUtil.getGPUs()
            if not gpus:
                return {}
            g = gpus[0]
            return {
                "name":         g.name,
                "gpu_percent":  g.load * 100,
                "mem_used_mb":  g.memoryUsed,
                "mem_total_mb": g.memoryTotal,
                "mem_percent":  (g.memoryUsed / g.memoryTotal * 100) if g.memoryTotal else 0,
                "temp_c":       g.temperature,
            }
        except Exception as e:
            print(f"[SystemMonitor] GPUtil read failed: {e}")
            return {}

    return {}


# ── Public API ────────────────────────────────────────────────────────────────
def get_system_status(mode="full", as_dict=False):
    """
    Returns a TTS-friendly string by default, or a dict if as_dict=True.

    Modes:
      "full"    — CPU + RAM + battery + GPU (default)
      "gpu"     — GPU only
      "cpu"     — CPU + RAM only
      "battery" — battery only
    """
    # Get CPU/RAM/battery (prefer observer, fall back to live psutil read)
    snap = _get_observer_snapshot() or _get_cpu_ram_battery_fallback()
    cpu  = snap.get("cpu_percent", 0)
    ram  = snap.get("ram_percent", 0)
    bat  = snap.get("battery_percent", -1)
    chg  = snap.get("battery_charging", False)

    # Get GPU
    gpu = _get_gpu_stats() if mode in ("full", "gpu") else {}

    if as_dict:
        return {
            "cpu_percent":      cpu,
            "ram_percent":      ram,
            "battery_percent":  bat,
            "battery_charging": chg,
            "gpu":              gpu,
            "mode":             mode,
        }

    # Build TTS-friendly string per mode
    if mode == "gpu":
        if not gpu:
            return "No GPU detected or GPU monitoring is unavailable."
        return (
            f"GPU {gpu['name']} at {gpu['gpu_percent']:.0f}% usage. "
            f"VRAM {gpu['mem_used_mb']:.0f} of {gpu['mem_total_mb']:.0f} megabytes used. "
            f"Temperature {gpu['temp_c']}°C."
        )

    if mode == "cpu":
        return f"CPU at {cpu:.0f}%. RAM {ram:.0f}% used."

    if mode == "battery":
        if bat < 0:
            return "Battery info unavailable."
        state = "charging" if chg else "on battery"
        return f"Battery at {bat:.0f}%, {state}."

    # mode == "full"
    parts = [f"CPU {cpu:.0f}%", f"RAM {ram:.0f}%"]
    if bat >= 0:
        state = "charging" if chg else "on battery"
        parts.append(f"battery {bat:.0f}% {state}")
    if gpu:
        parts.append(
            f"GPU {gpu['gpu_percent']:.0f}% at {gpu['temp_c']}°C "
            f"with {gpu['mem_percent']:.0f}% VRAM used"
        )
    return ". ".join(parts) + "."


def shutdown():
    """Clean up GPU handles. Call on app exit (optional)."""
    if _GPU_BACKEND == "pynvml" and _pynvml is not None:
        try:
            _pynvml.nvmlShutdown()
        except Exception:
            pass