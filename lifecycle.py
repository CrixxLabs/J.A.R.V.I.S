"""
lifecycle.py — Centralized lifecycle management for JARVIS.

Provides:
- Coordinated startup/shutdown of all subsystems
- Signal handling (SIGINT, SIGTERM)
- Resource cleanup (mic, camera, threads, connections)
- Status registry integration
"""

import sys
import signal
import threading
import atexit
import time
from typing import Callable, List, Optional
from contextlib import contextmanager

import status_registry
from status_registry import SubsystemState, get_registry


class LifecycleManager:
    """Manages the complete JARVIS lifecycle."""

    def __init__(self):
        self._shutdown_event = threading.Event()
        self._startup_complete = False
        self._shutdown_in_progress = False
        self._components: List[dict] = []  # {name, start_fn, stop_fn, priority}
        self._lock = threading.Lock()
        self._original_sigint = None
        self._original_sigterm = None

    def register(self, name: str, start_fn: Callable, stop_fn: Callable, priority: int = 50):
        """Register a component with start/stop functions.

        Lower priority = started earlier, stopped later.
        """
        with self._lock:
            self._components.append({
                "name": name,
                "start_fn": start_fn,
                "stop_fn": stop_fn,
                "priority": priority
            })
            # Keep sorted by priority
            self._components.sort(key=lambda c: c["priority"])

    def start_all(self) -> bool:
        """Start all registered components in priority order."""
        registry = get_registry()
        print("[Lifecycle] Starting all components...")

        for comp in self._components:
            if self._shutdown_event.is_set():
                break
            try:
                print(f"[Lifecycle] Starting {comp['name']}...")
                registry.set_status(comp["name"].upper(), SubsystemState.READY, "Starting...")
                comp["start_fn"]()
                registry.set_status(comp["name"].upper(), SubsystemState.READY, "Running")
                print(f"[Lifecycle] OK {comp['name']} started")
            except Exception as exc:
                print(f"[Lifecycle] FAIL {comp['name']} failed to start: {exc}")
                registry.set_status(comp["name"].upper(), SubsystemState.OFFLINE, str(exc))
                return False

        self._startup_complete = True
        registry.set_status("LIFECYCLE", SubsystemState.READY, "All components started")
        print("[Lifecycle] All components started successfully")
        return True

    def shutdown(self, reason: str = "Requested"):
        """Shutdown all components in reverse priority order."""
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True

        print(f"[Lifecycle] Shutdown initiated: {reason}")
        self._shutdown_event.set()

        registry = get_registry()
        registry.set_status("LIFECYCLE", SubsystemState.DEGRADED, f"Shutting down: {reason}")

        # Stop in reverse priority order
        for comp in reversed(self._components):
            try:
                print(f"[Lifecycle] Stopping {comp['name']}...")
                registry.set_status(comp["name"].upper(), SubsystemState.DEGRADED, "Stopping...")
                comp["stop_fn"]()
                registry.set_status(comp["name"].upper(), SubsystemState.DISABLED, "Stopped")
                print(f"[Lifecycle] OK {comp['name']} stopped")
            except Exception as exc:
                print(f"[Lifecycle] FAIL {comp['name']} failed to stop cleanly: {exc}")
                registry.set_status(comp["name"].upper(), SubsystemState.OFFLINE, f"Stop failed: {exc}")

        registry.set_status("LIFECYCLE", SubsystemState.DISABLED, "Shutdown complete")
        print("[Lifecycle] Shutdown complete")

    def wait_for_shutdown(self, timeout: Optional[float] = None):
        """Block until shutdown is signaled."""
        self._shutdown_event.wait(timeout)

    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    def is_started(self) -> bool:
        return self._startup_complete

    def setup_signal_handlers(self):
        """Install signal handlers for graceful shutdown."""
        def _signal_handler(signum, frame):
            sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            print(f"[Lifecycle] Received {sig_name}")
            self.shutdown(f"Signal {sig_name}")
            # Give time for graceful shutdown, then force exit
            threading.Timer(10.0, lambda: sys.exit(1)).start()

        self._original_sigint = signal.signal(signal.SIGINT, _signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, _signal_handler)

    def restore_signal_handlers(self):
        """Restore original signal handlers."""
        if self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm:
            signal.signal(signal.SIGTERM, self._original_sigterm)


# Global lifecycle manager instance
_lifecycle: Optional[LifecycleManager] = None
_lifecycle_lock = threading.Lock()


def get_lifecycle() -> LifecycleManager:
    """Get the global lifecycle manager."""
    global _lifecycle
    with _lifecycle_lock:
        if _lifecycle is None:
            _lifecycle = LifecycleManager()
        return _lifecycle


def register_component(name: str, start_fn: Callable, stop_fn: Callable, priority: int = 50):
    """Convenience function to register a component."""
    get_lifecycle().register(name, start_fn, stop_fn, priority)


def start_all() -> bool:
    """Start all registered components."""
    return get_lifecycle().start_all()


def shutdown(reason: str = "Requested"):
    """Initiate graceful shutdown."""
    get_lifecycle().shutdown(reason)


def wait_for_shutdown(timeout: Optional[float] = None):
    """Wait for shutdown signal."""
    get_lifecycle().wait_for_shutdown(timeout)


def setup_lifecycle_signals():
    """Setup signal handlers for graceful shutdown."""
    get_lifecycle().setup_signal_handlers()


@contextmanager
def managed_lifecycle():
    """Context manager for automatic lifecycle management."""
    lm = get_lifecycle()
    lm.setup_signal_handlers()
    try:
        if lm.start_all():
            yield lm
        else:
            raise RuntimeError("Failed to start components")
    finally:
        lm.shutdown("Context exit")
        lm.restore_signal_handlers()