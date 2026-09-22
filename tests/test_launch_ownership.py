import subprocess

from launch_mark_vii import FullGuiLauncher


class FakeProcess:
    def __init__(self, *, ui=False):
        self.ui = ui
        self.returncode = None
        self.signals = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.ui:
            self.returncode = 0
            return 0
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = 0

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9


class ProcessFactory:
    def __init__(self):
        self.calls = []
        self.processes = []

    def __call__(self, command, **kwargs):
        is_ui = command[0] == "dotnet"
        process = FakeProcess(ui=is_ui)
        self.calls.append((command, kwargs))
        self.processes.append(process)
        return process


def test_external_runtime_is_attached_and_not_killed(tmp_path):
    factory = ProcessFactory()
    launcher = FullGuiLauncher(root=tmp_path, popen=factory, runtime_probe=lambda _url: True)

    assert launcher.run(no_build=True) == 0
    assert launcher.runtime_owned is False
    assert len(factory.processes) == 1
    assert factory.processes[0].ui is True


def test_owned_runtime_is_stopped_when_ui_exits(tmp_path):
    factory = ProcessFactory()
    probes = iter((False, True))
    launcher = FullGuiLauncher(
        root=tmp_path,
        popen=factory,
        runtime_probe=lambda _url: next(probes),
        startup_timeout=1,
    )

    assert launcher.run(no_build=True) == 0
    runtime, ui = factory.processes
    assert runtime.ui is False and ui.ui is True
    assert runtime.signals
    assert runtime.terminated is False


def test_duplicate_runtime_start_reuses_owned_process(tmp_path):
    factory = ProcessFactory()
    probes = iter((False, True))
    launcher = FullGuiLauncher(
        root=tmp_path,
        popen=factory,
        runtime_probe=lambda _url: next(probes),
        startup_timeout=1,
    )

    assert launcher.start_runtime() is True
    assert launcher.start_runtime() is True
    assert len(factory.processes) == 1
    launcher.stop_owned_runtime()
