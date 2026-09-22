"""Non-destructive MARK VII release preflight."""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
failures = []
warnings = []

def run(label, cmd):
    print(f"\n=== {label} ===")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode:
        failures.append(f"{label} exited with {result.returncode}")
    return result.returncode

print("MARK VII RELEASE PREFLIGHT")
print(f"Python: {sys.version.split()[0]}")

if shutil.which("ffmpeg"):
    print("FFmpeg: available")
else:
    warnings.append("FFmpeg executable is not on PATH; FFmpeg-dependent media processing is degraded.")
    print("FFmpeg: NOT ON PATH (optional media capability degraded)")

if shutil.which("ollama"):
    result = subprocess.run(["ollama", "list"], cwd=ROOT, capture_output=True, text=True)
    model_text = (result.stdout or "") + (result.stderr or "")
    if result.returncode or "jarvis:latest" not in model_text:
        failures.append("Ollama jarvis:latest was not confirmed by `ollama list`.")
    else:
        print("Ollama jarvis:latest: available")
else:
    failures.append("Ollama executable is not on PATH.")

run("PYTHON REGRESSION", [sys.executable, "-m", "pytest", "tests", "-q"])

project = ROOT / "desktop" / "Jarvis.Desktop.Codex" / "Jarvis.Desktop.csproj"
if shutil.which("dotnet") and project.exists():
    run("WPF BUILD", ["dotnet", "build", str(project), "--configuration", "Debug", "--nologo"])
else:
    failures.append("dotnet or WPF project is unavailable.")

print("\n=== RESULT ===")
for item in warnings:
    print(f"WARNING: {item}")
if failures:
    for item in failures:
        print(f"FAIL: {item}")
    raise SystemExit(1)
print("PASS: MARK VII core release preflight is green.")
if warnings:
    print("PASS WITH OPTIONAL CAPABILITY WARNINGS.")
