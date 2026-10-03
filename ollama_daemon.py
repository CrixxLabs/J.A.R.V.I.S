"""Autonomous Ollama Process & VRAM Lifecycle Controller for J.A.R.V.I.S. — MARK VIII.

Manages silent background spawning of the local Ollama daemon only when cloud
connectivity (APInex) is interrupted, and cleanly terminates Ollama to reclaim
system RAM and VRAM (enforcing the <= 5.0GB RTX 3050 hardware ceiling) once
cloud connectivity recovers.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

import requests

log = logging.getLogger("jarvis.ollama_daemon")

DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


class CloudRecoveryWatchdog(threading.Thread):
    """Background watchdog thread that periodically probes cloud health during local fallback."""

    def __init__(
        self,
        ping_url: str,
        api_key: str = "",
        check_interval: float = 30.0,
        on_cloud_recovered: Optional[Callable[[], None]] = None,
    ):
        super().__init__(daemon=True, name="CloudRecoveryWatchdog")
        self.ping_url = ping_url
        self.api_key = api_key
        self.check_interval = max(0.01, check_interval)
        self.on_cloud_recovered = on_cloud_recovered
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        log.info(f"[CloudRecoveryWatchdog] Started monitoring {self.ping_url} (interval={self.check_interval}s)")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        while not self._stop_event.is_set():
            # Wait for interval or stop event
            if self._stop_event.wait(timeout=self.check_interval):
                break

            try:
                # Lightweight probe to models or health endpoint
                resp = requests.get(self.ping_url, headers=headers, timeout=(1.5, 3.0))
                if resp.status_code == 200:
                    log.info("[CloudRecoveryWatchdog] Cloud connectivity restored! Signaling recovery.")
                    if self.on_cloud_recovered:
                        try:
                            self.on_cloud_recovered()
                        except Exception as e:
                            log.error(f"[CloudRecoveryWatchdog] Recovery callback error: {e}")
                    break
            except Exception as exc:
                log.debug(f"[CloudRecoveryWatchdog] Cloud probe failed ({type(exc).__name__}); remaining in local fallback.")


class OllamaLifecycleManager:
    """Autonomous manager for local Ollama process lifecycle and VRAM reclamation."""

    def __init__(self, host: str = DEFAULT_OLLAMA_HOST):
        self._host = host.rstrip("/")
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._spawned_by_us: bool = False
        self._fallback_active: bool = False
        self._watchdog: Optional[CloudRecoveryWatchdog] = None

    @property
    def is_fallback_active(self) -> bool:
        return self._fallback_active

    def is_responsive(self, timeout: float = 1.0) -> bool:
        """Check if local Ollama daemon is currently accepting HTTP connections."""
        try:
            r = requests.get(f"{self._host}/api/tags", timeout=(0.5, timeout))
            return r.status_code == 200
        except Exception:
            return False

    def ensure_running(self, timeout: float = 5.0) -> bool:
        """Ensure local Ollama daemon is running, spawning it silently if inactive."""
        with self._lock:
            if self.is_responsive(timeout=0.8):
                return True

            log.info("[OllamaLifecycleManager] Local Ollama daemon inactive. Spawning silent background process...")
            try:
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                self._process = subprocess.Popen(
                    ["ollama", "serve"],
                    creationflags=flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._spawned_by_us = True
            except Exception as exc:
                log.error(f"[OllamaLifecycleManager] Failed to launch 'ollama serve': {exc}")
                return False

            # Poll for readiness
            start = time.time()
            while time.time() - start < timeout:
                if self.is_responsive(timeout=0.4):
                    elapsed = time.time() - start
                    log.info(f"[OllamaLifecycleManager] Local Ollama ready in {elapsed:.2f}s.")
                    return True
                time.sleep(0.2)

            log.warning(f"[OllamaLifecycleManager] Ollama failed to respond within {timeout}s.")
            return False

    def stop(self, force: bool = True) -> bool:
        """Terminate local Ollama process to reclaim RAM and VRAM."""
        with self._lock:
            self.stop_watchdog()
            self._fallback_active = False

            if self._process is not None:
                try:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                except Exception as e:
                    log.debug(f"[OllamaLifecycleManager] Process terminate exception: {e}")
                finally:
                    self._process = None

            if force and sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/IM", "ollama.exe", "/T"],
                        capture_output=True,
                        check=False,
                    )
                    subprocess.run(
                        ["taskkill", "/F", "/IM", "ollama_app.exe", "/T"],
                        capture_output=True,
                        check=False,
                    )
                except Exception as e:
                    log.debug(f"[OllamaLifecycleManager] taskkill exception: {e}")

            self._spawned_by_us = False
            log.info("[OllamaLifecycleManager] Ollama daemon terminated. VRAM and RAM reclaimed.")
            return True

    def enter_fallback_mode(
        self,
        ping_url: str,
        api_key: str = "",
        check_interval: float = 30.0,
        on_cloud_recovered: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Trigger local offline fallback: ensures Ollama is running and arms cloud watchdog."""
        with self._lock:
            self._fallback_active = True
            ready = self.ensure_running()

            def _handle_recovery():
                with self._lock:
                    log.info("[OllamaLifecycleManager] Executing cloud recovery sequence.")
                    self._fallback_active = False
                    self.stop(force=True)
                    if on_cloud_recovered:
                        on_cloud_recovered()

            if self._watchdog is None or not self._watchdog.is_alive():
                self._watchdog = CloudRecoveryWatchdog(
                    ping_url=ping_url,
                    api_key=api_key,
                    check_interval=check_interval,
                    on_cloud_recovered=_handle_recovery,
                )
                self._watchdog.start()

            return ready

    def exit_fallback_mode(self) -> None:
        """Manually exit fallback mode and reclaim hardware resources."""
        with self._lock:
            self.stop_watchdog()
            self.stop(force=True)

    def stop_watchdog(self) -> None:
        """Stop the background cloud watchdog thread."""
        with self._lock:
            if self._watchdog is not None:
                self._watchdog.stop()
                self._watchdog = None


# ---------------------------------------------------------------------------
# Module-level singleton and helpers
# ---------------------------------------------------------------------------

_manager_lock = threading.RLock()
_manager_instance: Optional[OllamaLifecycleManager] = None


def get_ollama_manager() -> OllamaLifecycleManager:
    """Retrieve the global OllamaLifecycleManager singleton."""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = OllamaLifecycleManager()
    return _manager_instance


def ensure_ollama_running(timeout: float = 5.0) -> bool:
    return get_ollama_manager().ensure_running(timeout=timeout)


def stop_ollama(force: bool = True) -> bool:
    return get_ollama_manager().stop(force=force)


def is_ollama_running() -> bool:
    return get_ollama_manager().is_responsive()
