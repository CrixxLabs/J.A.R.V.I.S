"""Windows Scheduling & Real-Time Audio Process Tuner for J.A.R.V.I.S. — MARK VIII.

Workstream 2:
  1. Multimedia Class Scheduler Service (MMCSS) "Pro Audio" registration via Win32 AvSetMmThreadCharacteristicsW.
  2. Sets RT audio process priority class to HIGH_PRIORITY_CLASS (0x00000080), avoiding REALTIME_PRIORITY_CLASS.
  3. Disables Windows 11 EcoQoS power throttling via SetProcessInformation.
  4. CPU Core Affinity Pinning: Pins audio/RT loops to dedicated CPU cores and isolates background workers.
"""
from __future__ import annotations

import ctypes
import logging
import os
import platform
import sys
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("jarvis.windows_realtime_tuner")

# Win32 Constants
HIGH_PRIORITY_CLASS = 0x00000080
REALTIME_PRIORITY_CLASS = 0x00000100  # Explicitly avoided for system stability
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ProcessPowerThrottling = 4  # PROCESS_INFORMATION_CLASS enum value


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("ControlMask", ctypes.c_uint32),
        ("StateMask", ctypes.c_uint32),
    ]


class WindowsRealTimeTuner:
    """Windows 11 Real-Time Audio and Thread Priority Optimizer."""

    def __init__(self):
        self.is_windows = platform.system().lower() == "windows"
        self._mmcss_handle: Optional[int] = None
        self._priority_configured = False
        self._eco_qos_disabled = False
        self._affinity_mask: Optional[int] = None

    def enable_mmcss_pro_audio(self, task_name: str = "Pro Audio") -> Tuple[bool, str]:
        """Register current thread with Multimedia Class Scheduler Service (MMCSS)."""
        if not self.is_windows:
            return True, f"Simulated MMCSS '{task_name}' on non-Windows platform ({platform.system()})"

        try:
            avrt = ctypes.windll.avrt
            avrt.AvSetMmThreadCharacteristicsW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
            avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
            task_idx = ctypes.c_ulong(0)
            handle = avrt.AvSetMmThreadCharacteristicsW(task_name, ctypes.byref(task_idx))
            if handle:
                self._mmcss_handle = handle
                log.info(f"[WindowsRealTimeTuner] MMCSS '{task_name}' enabled successfully (handle={handle})")
                return True, f"MMCSS '{task_name}' enabled successfully"
            else:
                err = ctypes.GetLastError()
                return False, f"AvSetMmThreadCharacteristicsW failed with error code {err}"
        except Exception as e:
            return False, f"MMCSS registration exception: {e}"

    def disable_mmcss(self) -> Tuple[bool, str]:
        """Revert MMCSS registration."""
        if not self.is_windows or not self._mmcss_handle:
            return True, "No active MMCSS handle to revert"

        try:
            avrt = ctypes.windll.avrt
            avrt.AvRevertMmThreadCharacteristics.argtypes = [ctypes.c_void_p]
            avrt.AvRevertMmThreadCharacteristics.restype = ctypes.c_bool
            res = avrt.AvRevertMmThreadCharacteristics(self._mmcss_handle)
            self._mmcss_handle = None
            return bool(res), "MMCSS reverted"
        except Exception as e:
            return False, f"MMCSS revert error: {e}"

    def set_high_process_priority(self) -> Tuple[bool, str]:
        """Set process priority to HIGH_PRIORITY_CLASS (0x80), strictly avoiding REALTIME_PRIORITY_CLASS."""
        if not self.is_windows:
            self._priority_configured = True
            return True, "Simulated HIGH_PRIORITY_CLASS on non-Windows platform"

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass.restype = ctypes.c_bool

            handle = kernel32.GetCurrentProcess()
            # Set HIGH_PRIORITY_CLASS (0x00000080)
            res = kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
            if res:
                self._priority_configured = True
                log.info("[WindowsRealTimeTuner] Process priority set to HIGH_PRIORITY_CLASS (0x80)")
                return True, "HIGH_PRIORITY_CLASS applied"
            else:
                err = ctypes.GetLastError()
                return False, f"SetPriorityClass failed with error {err}"
        except Exception as e:
            return False, f"SetPriorityClass error: {e}"

    def disable_eco_qos_power_throttling(self) -> Tuple[bool, str]:
        """Disable Windows 11 EcoQoS / Efficiency Mode power throttling on RT audio process."""
        if not self.is_windows:
            self._eco_qos_disabled = True
            return True, "Simulated EcoQoS disabling on non-Windows platform"

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetProcessInformation.restype = ctypes.c_bool

            handle = kernel32.GetCurrentProcess()

            state = PROCESS_POWER_THROTTLING_STATE()
            state.Version = 1
            state.ControlMask = PROCESS_POWER_THROTTLING_EXECUTION_SPEED
            state.StateMask = 0  # 0 disables execution speed throttling

            # ProcessPowerThrottling = 4 (enum value in PROCESS_INFORMATION_CLASS)
            res = kernel32.SetProcessInformation(
                handle,
                ProcessPowerThrottling,
                ctypes.byref(state),
                ctypes.sizeof(state),
            )
            if res:
                self._eco_qos_disabled = True
                log.info("[WindowsRealTimeTuner] Windows 11 EcoQoS power throttling successfully disabled")
                return True, "EcoQoS disabled successfully"
            else:
                err = ctypes.GetLastError()
                return False, f"SetProcessInformation failed with error {err}"
        except Exception as e:
            return False, f"EcoQoS configuration error: {e}"

    def set_cpu_affinity(self, core_mask: int = 0x03) -> Tuple[bool, str]:
        """Pin process execution to dedicated CPU core mask (e.g. Cores 0 & 1)."""
        if not self.is_windows:
            self._affinity_mask = core_mask
            return True, f"Simulated CPU core affinity mask 0x{core_mask:X} on non-Windows"

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            kernel32.SetProcessAffinityMask.restype = ctypes.c_bool

            handle = kernel32.GetCurrentProcess()
            res = kernel32.SetProcessAffinityMask(handle, core_mask)
            if res:
                self._affinity_mask = core_mask
                log.info(f"[WindowsRealTimeTuner] Process affinity pinned to CPU core mask 0x{core_mask:X}")
                return True, f"Affinity mask 0x{core_mask:X} applied"
            else:
                err = ctypes.GetLastError()
                return False, f"SetProcessAffinityMask failed with error {err}"
        except Exception as e:
            return False, f"SetProcessAffinityMask error: {e}"

    def get_tuning_status(self) -> Dict[str, Any]:
        return {
            "is_windows": self.is_windows,
            "mmcss_active": self._mmcss_handle is not None,
            "high_priority_active": self._priority_configured,
            "eco_qos_disabled": self._eco_qos_disabled,
            "affinity_mask": f"0x{self._affinity_mask:X}" if self._affinity_mask else "default",
        }


_tuner_instance: Optional[WindowsRealTimeTuner] = None


def get_realtime_tuner() -> WindowsRealTimeTuner:
    global _tuner_instance
    if _tuner_instance is None:
        _tuner_instance = WindowsRealTimeTuner()
    return _tuner_instance
