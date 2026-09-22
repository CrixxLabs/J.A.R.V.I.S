"""Cross-process ownership guard for the canonical MARK VII voice runtime."""

from __future__ import annotations

import os
import socket


class DuplicateRuntimeError(RuntimeError):
    pass


class RuntimeInstanceGuard:
    """Hold one OS-owned runtime identity until explicitly released."""

    def __init__(self, name: str = "Local\\JARVIS_MARK_VII_RUNTIME", fallback_port: int = 8764):
        self.name = name
        self.fallback_port = int(fallback_port)
        self._handle = None
        self._socket = None

    def acquire(self):
        if self._handle is not None or self._socket is not None:
            return self
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                raise OSError("CreateMutexW failed for the JARVIS runtime guard")
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                raise DuplicateRuntimeError("Another JARVIS MARK VII runtime already owns this session")
            self._handle = handle
            return self

        guard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            guard_socket.bind(("127.0.0.1", self.fallback_port))
            guard_socket.listen(1)
        except OSError as exc:
            guard_socket.close()
            raise DuplicateRuntimeError("Another JARVIS MARK VII runtime already owns this session") from exc
        self._socket = guard_socket
        return self

    def release(self):
        if self._handle is not None:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback):
        self.release()


def acquire_runtime_guard() -> RuntimeInstanceGuard:
    return RuntimeInstanceGuard().acquire()
