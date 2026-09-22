"""
lifecycle.py — Centralized lifecycle management for JARVIS.

Provides:
- Coordinated startup/shutdown of all subsystems
- Signal handling (SIGINT, SIGTERM)
- Resource cleanup (mic, camera, threads, connections)
- Status registry integration
"""

import signal
import threading
import time
from typing import Callable, List, Optional
from contextlib import contextmanager

import status_registry
from status_registry import EvidenceLevel, get_registry


class LifecycleManager:
    """Manages the complete JARVIS lifecycle."""

    def __init__(self, *, component_stop_timeout: float = 5.0,
                 total_shutdown_timeout: float = 30.0):
        self._shutdown_event = threading.Event()
        self._shutdown_complete = threading.Event()
        self._startup_complete = False
        self._shutdown_in_progress = False
        self._components: List[dict] = []  # {name, start_fn, stop_fn, priority}
        self._lock = threading.RLock()
        self._component_stop_timeout = max(0.01, float(component_stop_timeout))
        self._total_shutdown_timeout = max(0.01, float(total_shutdown_timeout))
        self._original_handlers = {}

    def register(self, name: str, start_fn: Callable, stop_fn: Callable,
                 priority: int = 50, *, owned: bool = True,
                 already_started: bool = False, stop_timeout: Optional[float] = None):
        """Register a component with start/stop functions.

        Lower priority = started earlier, stopped later.
        """
        with self._lock:
            if any(component["name"] == name for component in self._components):
                raise ValueError(f"Lifecycle component already registered: {name}")
            self._components.append({
                "name": name,
                "start_fn": start_fn,
                "stop_fn": stop_fn,
                "priority": priority,
                "owned": bool(owned),
                "started": bool(already_started),
                "stop_timeout": self._component_stop_timeout if stop_timeout is None else max(0.01, float(stop_timeout)),
            })
            # Keep sorted by priority
            self._components.sort(key=lambda c: c["priority"])

    def start_all(self) -> bool:
        """Start all registered components in priority order."""
        with self._lock:
            if self._startup_complete:
                return True
            if self._shutdown_event.is_set():
                return False
        registry = get_registry()
        print("[Lifecycle] Starting all components...")

        for comp in self._components:
            if self._shutdown_event.is_set():
                break
            try:
                if comp["started"]:
                    continue
                print(f"[Lifecycle] Starting {comp['name']}...")
                comp["start_fn"]()
                comp["started"] = True
                print(f"[Lifecycle] OK {comp['name']} started")
            except Exception as exc:
                print(f"[Lifecycle] FAIL {comp['name']} failed to start: {exc}")
                registry.set_evidence("LIFECYCLE", EvidenceLevel.BROKEN,
                                      f"Startup failure in {comp['name']}: {exc}",
                                      source="lifecycle startup")
                self.shutdown(f"Startup failure in {comp['name']}")
                return False

        self._startup_complete = True
        registry.set_evidence("LIFECYCLE", EvidenceLevel.PROBED, "All owned components started",
                              source="lifecycle")
        print("[Lifecycle] All components started successfully")
        return True

    def _stop_component(self, comp: dict, timeout: float) -> tuple[bool, Optional[BaseException]]:
        error = []

        def invoke():
            try:
                comp["stop_fn"]()
            except BaseException as exc:  # cleanup must report and continue
                error.append(exc)

        worker = threading.Thread(target=invoke, name=f"stop-{comp['name']}", daemon=True)
        worker.start()
        worker.join(timeout=max(0.0, timeout))
        return not worker.is_alive(), error[0] if error else None

    def shutdown(self, reason: str = "Requested") -> bool:
        """Stop owned, started components in reverse order within a fixed deadline."""
        with self._lock:
            if self._shutdown_complete.is_set():
                return True
            if self._shutdown_in_progress:
                waiter = True
            else:
                self._shutdown_in_progress = True
                self._shutdown_event.set()
                waiter = False

        if waiter:
            return self._shutdown_complete.wait(self._total_shutdown_timeout)

        print(f"[Lifecycle] Shutdown initiated: {reason}")
        registry = get_registry()
        registry.set_evidence("LIFECYCLE", EvidenceLevel.PROBED, f"Shutdown requested: {reason}",
                              source="lifecycle")
        deadline = time.monotonic() + self._total_shutdown_timeout
        clean = True

        for comp in reversed(self._components):
            if not comp["started"] or not comp["owned"]:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                clean = False
                print(f"[Lifecycle] TIMEOUT before stopping {comp['name']}")
                continue
            try:
                print(f"[Lifecycle] Stopping {comp['name']}...")
                finished, error = self._stop_component(
                    comp, min(comp["stop_timeout"], remaining)
                )
                comp["started"] = False
                if not finished:
                    clean = False
                    print(f"[Lifecycle] TIMEOUT stopping {comp['name']}")
                elif error is not None:
                    clean = False
                    print(f"[Lifecycle] FAIL {comp['name']} failed to stop cleanly: {error}")
                else:
                    print(f"[Lifecycle] OK {comp['name']} stopped")
            except BaseException as exc:
                clean = False
                print(f"[Lifecycle] FAIL {comp['name']} cleanup bookkeeping failed: {exc}")

        self._startup_complete = False
        registry.set_evidence("LIFECYCLE", EvidenceLevel.UNKNOWN,
                              "Shutdown complete" if clean else "Shutdown complete with cleanup errors",
                              source="lifecycle")
        self._shutdown_complete.set()
        print("[Lifecycle] Shutdown complete")
        return clean

    def wait_for_shutdown(self, timeout: Optional[float] = None):
        """Block until shutdown is signaled."""
        self._shutdown_event.wait(timeout)

    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    def is_started(self) -> bool:
        return self._startup_complete

    def is_shutdown_complete(self) -> bool:
        return self._shutdown_complete.is_set()

    def component_snapshot(self) -> List[dict]:
        with self._lock:
            return [{key: value for key, value in component.items()
                     if key not in ("start_fn", "stop_fn")}
                    for component in self._components]

    def setup_signal_handlers(self):
        """Install signal handlers for graceful shutdown."""
        if threading.current_thread() is not threading.main_thread():
            return

        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            print(f"[Lifecycle] Received {sig_name}")
            self.shutdown(f"Signal {sig_name}")
            raise KeyboardInterrupt

        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            signals.append(signal.SIGBREAK)
        for signum in signals:
            if signum not in self._original_handlers:
                self._original_handlers[signum] = signal.signal(signum, _signal_handler)

    def restore_signal_handlers(self):
        """Restore original signal handlers."""
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, handler in self._original_handlers.items():
            signal.signal(signum, handler)
        self._original_handlers.clear()


# Global lifecycle manager instance
_lifecycle: Optional[LifecycleManager] = None
_lifecycle_lock = threading.Lock()


def get_lifecycle() -> LifecycleManager:
    """Get the global lifecycle manager."""
    global _lifecycle
    with _lifecycle_lock:
        if _lifecycle is None or _lifecycle.is_shutdown_complete():
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
