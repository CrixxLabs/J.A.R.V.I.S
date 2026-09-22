"""Owned full-GUI launcher for MARK VII.

The WPF executable remains an attach-only client.  This launcher starts the
Python runtime only when no verified visual bridge is already available, and
stops only the runtime process it created when the WPF process exits.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional


ROOT = Path(__file__).resolve().parent
WPF_PROJECT = ROOT / "desktop" / "Jarvis.Desktop.Codex" / "Jarvis.Desktop.csproj"


def visual_bridge_available(url: str, timeout: float = 0.5) -> bool:
    """Return true only for a MARK VII visual-bridge snapshot endpoint."""
    snapshot_url = url.rstrip("/") + "/v1/snapshot"
    try:
        with urllib.request.urlopen(snapshot_url, timeout=timeout) as response:
            payload = json.load(response)
        return isinstance(payload, dict) and "operational_state" in payload
    except Exception:
        return False


class FullGuiLauncher:
    """Own a WPF session and, when necessary, its Python runtime subprocess."""

    def __init__(
        self,
        *,
        root: Path = ROOT,
        python_executable: Optional[str] = None,
        visual_url: Optional[str] = None,
        popen: Callable = subprocess.Popen,
        runtime_probe: Callable[[str], bool] = visual_bridge_available,
        startup_timeout: float = 45.0,
        shutdown_timeout: float = 15.0,
    ):
        self.root = Path(root).resolve()
        self.python_executable = python_executable or os.getenv("JARVIS_PYTHON") or sys.executable
        self.visual_url = visual_url or os.getenv("JARVIS_VISUAL_URL") or "http://127.0.0.1:8765"
        self._popen = popen
        self._runtime_probe = runtime_probe
        self.startup_timeout = float(startup_timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        self.runtime_process = None
        self.ui_process = None
        self.runtime_owned = False

    def start_runtime(self) -> bool:
        """Start one runtime or attach to an existing verified bridge.

        Returns true when this launcher owns the newly created process.
        """
        if self.runtime_process is not None and self.runtime_process.poll() is None:
            return self.runtime_owned
        if self._runtime_probe(self.visual_url):
            print(f"[Launcher] Attaching to external runtime at {self.visual_url}; it will not be stopped.")
            self.runtime_process = None
            self.runtime_owned = False
            return False

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["JARVIS_LAUNCH_OWNER_PID"] = str(os.getpid())
        command = [self.python_executable, str(self.root / "jarvis.py")]
        print(f"[Launcher] Starting owned runtime: {' '.join(command)}")
        self.runtime_process = self._popen(
            command, cwd=str(self.root), env=env, creationflags=creationflags
        )
        self.runtime_owned = True

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.runtime_process.poll() is not None:
                code = self.runtime_process.returncode
                self.runtime_owned = False
                raise RuntimeError(f"JARVIS runtime exited during startup with code {code}")
            if self._runtime_probe(self.visual_url):
                print(f"[Launcher] Runtime bridge verified at {self.visual_url}")
                return True
            time.sleep(0.2)

        self.stop_owned_runtime()
        raise TimeoutError(f"JARVIS runtime did not expose {self.visual_url} within {self.startup_timeout:g}s")

    def start_ui(self, *, no_build: bool = False):
        if self.ui_process is not None and self.ui_process.poll() is None:
            return self.ui_process
        project = self.root / "desktop" / "Jarvis.Desktop.Codex" / "Jarvis.Desktop.csproj"
        command = ["dotnet", "run", "--project", str(project), "--configuration", "Debug"]
        if no_build:
            command.append("--no-build")
        env = os.environ.copy()
        env["JARVIS_VISUAL_URL"] = self.visual_url
        print(f"[Launcher] Starting WPF client: {' '.join(command)}")
        self.ui_process = self._popen(command, cwd=str(self.root), env=env)
        return self.ui_process

    def stop_owned_runtime(self) -> None:
        process = self.runtime_process
        if not self.runtime_owned or process is None:
            return
        self.runtime_owned = False
        if process.poll() is not None:
            return
        print("[Launcher] Requesting clean shutdown of owned runtime...")
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            process.wait(timeout=self.shutdown_timeout)
            return
        except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
            pass
        print("[Launcher] Runtime did not stop in time; terminating owned process.")
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def close_ui(self) -> None:
        process = self.ui_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()

    def run(self, *, no_build: bool = False) -> int:
        try:
            self.start_runtime()
            ui = self.start_ui(no_build=no_build)
            return int(ui.wait())
        except KeyboardInterrupt:
            return 130
        finally:
            self.close_ui()
            self.stop_owned_runtime()


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch MARK VII runtime and native WPF client with explicit ownership.")
    parser.add_argument("--no-build", action="store_true", help="Run the existing WPF build without rebuilding.")
    args = parser.parse_args()
    return FullGuiLauncher().run(no_build=args.no_build)


if __name__ == "__main__":
    raise SystemExit(main())
