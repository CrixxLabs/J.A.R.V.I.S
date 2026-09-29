"""MARK VII PC Doctor skill: read-only local diagnostics."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

SKILL_NAME = "PC Doctor"
ENABLED = True
PRIORITY = 88
TRIGGERS = [
    "pc doctor", "diagnose my pc", "diagnose my computer", "system health",
    "why is my pc slow", "why is my computer slow", "check my pc",
]


def can_handle(user_input):
    text = (user_input or "").lower().strip()
    return any(trigger in text for trigger in TRIGGERS)


def _disk_line():
    try:
        root = Path(os.environ.get("SystemDrive", "C:")) / "\\"
        total, used, free = shutil.disk_usage(root)
        gb = 1024 ** 3
        pct = (used / total * 100) if total else 0
        return f"Disk {pct:.0f}% used, {free / gb:.1f} GB free"
    except Exception:
        return "disk info unavailable"


def _top_processes(psutil):
    rows = []
    try:
        for proc in psutil.process_iter(["name", "memory_percent"]):
            try:
                rows.append((float(proc.info.get("memory_percent") or 0), proc.info.get("name") or "unknown"))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return ""
    rows.sort(reverse=True)
    names = [name for _mem, name in rows[:3] if name]
    return ", ".join(names)


def handle(user_input, context=None):
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.25)
        ram = psutil.virtual_memory()
        bat = psutil.sensors_battery()
        parts = [f"CPU {cpu:.0f}%", f"RAM {ram.percent:.0f}%", _disk_line()]
        if bat is not None:
            parts.append(f"battery {bat.percent:.0f}%{' charging' if bat.power_plugged else ''}")

        try:
            import system_monitor
            gpu = system_monitor.get_system_status(mode="gpu", as_dict=True).get("gpu") or {}
            if gpu:
                parts.append(
                    f"GPU {gpu.get('gpu_percent', 0):.0f}% at {gpu.get('temp_c', 0):.0f}°C, "
                    f"VRAM {gpu.get('mem_percent', 0):.0f}%"
                )
        except Exception:
            pass

        warnings = []
        if cpu >= 90: warnings.append("CPU load is very high")
        if ram.percent >= 90: warnings.append("RAM usage is very high")
        try:
            _total, used, total_free = shutil.disk_usage(Path(os.environ.get("SystemDrive", "C:")) / "\\")
            if total_free / (1024 ** 3) < 15: warnings.append("system drive is low on free space")
        except Exception:
            pass

        top = _top_processes(psutil)
        result = "PC Doctor: " + ". ".join(parts) + "."
        if warnings:
            result += " Attention: " + "; ".join(warnings) + "."
        elif cpu < 85 and ram.percent < 85:
            result += " Nothing critical stands out in this quick health check."
        if top:
            result += f" Highest-memory processes include {top}."
        return result
    except Exception as exc:
        return f"PC Doctor couldn't complete the health check: {exc}"
