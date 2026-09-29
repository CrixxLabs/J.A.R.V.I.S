"""MARK VII Project/Dev Assistant skill: constrained read-only project diagnostics."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SKILL_NAME = "Project Dev Assistant"
ENABLED = True
PRIORITY = 86
TRIGGERS = [
    "project status", "repo status", "git status", "run project tests",
    "run the tests", "run tests", "test the project", "project doctor",
]


def can_handle(user_input):
    text = (user_input or "").lower().strip()
    # Keep routing narrow so ordinary coding questions still go to the brain.
    return any(trigger in text for trigger in TRIGGERS)


def _root():
    return Path(__file__).resolve().parents[1]


def _run(args, timeout=60):
    try:
        cp = subprocess.run(
            args, cwd=str(_root()), capture_output=True, text=True,
            timeout=timeout, shell=False,
        )
        output = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
        return cp.returncode, output
    except subprocess.TimeoutExpired:
        return 124, "command timed out"
    except Exception as exc:
        return 1, str(exc)


def _git_status():
    code, out = _run(["git", "status", "--short"], timeout=10)
    if code != 0:
        return "Git status unavailable: " + (out.splitlines()[-1] if out else "unknown error")
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return "Git working tree is clean."
    preview = ", ".join(line.strip() for line in lines[:6])
    more = f" plus {len(lines)-6} more" if len(lines) > 6 else ""
    return f"Git has {len(lines)} changed path(s): {preview}{more}."


def _run_tests():
    code, out = _run([os.sys.executable, "-m", "pytest", "tests", "-q"], timeout=120)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    summary = ""
    for line in reversed(lines):
        if " passed" in line or " failed" in line or " error" in line:
            summary = line
            break
    if not summary:
        summary = lines[-1] if lines else "no pytest output"
    return f"Tests {'passed' if code == 0 else 'failed'}: {summary}"


def handle(user_input, context=None):
    text = (user_input or "").lower()
    if any(x in text for x in ("run project tests", "run the tests", "run tests", "test the project")):
        return _run_tests()
    return "Project Dev Assistant: " + _git_status()
