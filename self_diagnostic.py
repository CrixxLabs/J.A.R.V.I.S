# self_diagnostic.py — Jarvis Self-Diagnostic / Health Check Module
# Run on-demand via voice ("run a checkup", "health check", etc.)
# or programmatically. Never crashes the caller — all checks are isolated.
#
# run_full_checkup() → {
#   "passed":   [{"check": str, "detail": str}, ...],
#   "failed":   [{"check": str, "detail": str}, ...],
#   "warnings": [{"check": str, "detail": str}, ...],
#   "summary":  str   ← short spoken-ready string
# }

import ast
import datetime
import glob
import importlib
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Result helpers ─────────────────────────────────────────────────────────────

def _passed(check, detail=""):
    return {"check": check, "detail": detail}

def _failed(check, detail=""):
    return {"check": check, "detail": detail}

def _warning(check, detail=""):
    return {"check": check, "detail": detail}


# ══════════════════════════════════════════════════════════════════════════════
# CHECK A — SYNTAX CHECK
# Attempt to compile every .py file in the project directory.
# Uses py_compile in a subprocess so a broken file can't crash this process.
# ══════════════════════════════════════════════════════════════════════════════

def check_syntax():
    """
    Returns (passed_list, failed_list, warning_list) of result dicts.
    Skips __pycache__ and venv folders.
    """
    passed   = []
    failed   = []
    warnings = []

    py_files = []
    for root, dirs, files in os.walk(BASE_DIR):
        # Skip virtual-env / cache folders
        dirs[:] = [
            d for d in dirs
            if d not in ("__pycache__", ".git", "venv", ".venv", "env", "node_modules", "bin", "obj", "build", "dist")
            and not d.startswith(".")
        ]
        for fname in files:
            if fname.endswith(".py"):
                py_files.append(os.path.join(root, fname))

    for fpath in sorted(py_files):
        rel = os.path.relpath(fpath, BASE_DIR)
        try:
            py_compile.compile(fpath, doraise=True)
            passed.append(_passed("syntax", f"{rel} — OK"))
        except py_compile.PyCompileError as exc:
            failed.append(_failed("syntax", f"{rel} — {str(exc)[:120]}"))
        except Exception as exc:
            warnings.append(_warning("syntax", f"{rel} — unexpected error: {str(exc)[:80]}"))

    return passed, failed, warnings


# ══════════════════════════════════════════════════════════════════════════════
# CHECK B — TEST SUITE
# Discover and run all test_*.py files, collect pass/fail counts.
# Each test file is run in an isolated subprocess so a crash doesn't
# take down the checkup.
# ══════════════════════════════════════════════════════════════════════════════

def check_tests():
    passed   = []
    failed   = []
    warnings = []

    test_files = glob.glob(os.path.join(BASE_DIR, "test_*.py"))

    if not test_files:
        warnings.append(_warning("test_suite", "No test_*.py files found in project root."))
        return passed, failed, warnings

    for tfile in sorted(test_files):
        rel = os.path.relpath(tfile, BASE_DIR)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", tfile, "--tb=short", "-q",
                 "--no-header", "--timeout=30"],
                capture_output=True,
                text=True,
                cwd=BASE_DIR,
                timeout=60,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                passed.append(_passed("test_suite", f"{rel} — PASSED\n{output[:300]}"))
            else:
                failed.append(_failed("test_suite", f"{rel} — FAILED\n{output[:300]}"))
        except subprocess.TimeoutExpired:
            warnings.append(_warning("test_suite", f"{rel} — timed out after 60s"))
        except FileNotFoundError:
            # pytest not installed — fall back to unittest discovery
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "unittest", tfile.replace(".py", "").replace(os.sep, "."), "-v"],
                    capture_output=True,
                    text=True,
                    cwd=BASE_DIR,
                    timeout=60,
                )
                output = (result.stdout + result.stderr).strip()
                if result.returncode == 0:
                    passed.append(_passed("test_suite", f"{rel} — PASSED (unittest)\n{output[:300]}"))
                else:
                    failed.append(_failed("test_suite", f"{rel} — FAILED (unittest)\n{output[:300]}"))
            except Exception as exc:
                warnings.append(_warning("test_suite", f"{rel} — couldn't run: {str(exc)[:80]}"))
        except Exception as exc:
            warnings.append(_warning("test_suite", f"{rel} — error: {str(exc)[:80]}"))

    return passed, failed, warnings


# ══════════════════════════════════════════════════════════════════════════════
# CHECK C — DATA INTEGRITY
# Validate that key JSON data files exist, are valid JSON, and contain
# the expected top-level keys.
# ══════════════════════════════════════════════════════════════════════════════

# Expected top-level keys per file (basic presence check only)
_SCHEMA_HINTS = {
    "memory.json": ["facts", "activity_log"],
    "obligations.json": [],          # list or dict both acceptable — just valid JSON
    "tasks.json": [],                # list or dict both acceptable
}

def check_data_integrity():
    passed   = []
    failed   = []
    warnings = []

    for filename, required_keys in _SCHEMA_HINTS.items():
        fpath = os.path.join(BASE_DIR, filename)

        if not os.path.exists(fpath):
            # Not all files are mandatory on a fresh install
            warnings.append(_warning("data_integrity", f"{filename} — not found (may be normal on first run)"))
            continue

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            failed.append(_failed("data_integrity", f"{filename} — invalid JSON: {str(exc)[:100]}"))
            continue
        except Exception as exc:
            failed.append(_failed("data_integrity", f"{filename} — read error: {str(exc)[:80]}"))
            continue

        # Key presence check (only for dict-type files)
        if required_keys and isinstance(data, dict):
            missing = [k for k in required_keys if k not in data]
            if missing:
                warnings.append(_warning(
                    "data_integrity",
                    f"{filename} — valid JSON but missing expected keys: {missing}"
                ))
            else:
                passed.append(_passed("data_integrity", f"{filename} — valid JSON, all expected keys present"))
        else:
            passed.append(_passed("data_integrity", f"{filename} — valid JSON"))

    return passed, failed, warnings


# ══════════════════════════════════════════════════════════════════════════════
# CHECK D — DEPENDENCY / CONNECTIVITY
# Verify Ollama reachability, OpenRouter API key + minimal call,
# Tesseract callable, key Python packages importable.
# ══════════════════════════════════════════════════════════════════════════════

def check_dependencies():
    passed   = []
    failed   = []
    warnings = []

    # ── D1: Required Python packages ──────────────────────────────────────────
    packages = [
        ("edge_tts",          "edge_tts"),
        ("pygame",            "pygame"),
        ("speech_recognition","speech_recognition"),
        ("pyautogui",         "pyautogui"),
        ("psutil",            "psutil"),
        ("requests",          "requests"),
        ("dotenv",            "dotenv"),
        ("spacy",             "spacy"),
        ("cv2",               "cv2"),
        ("numpy",             "numpy"),
        ("bs4",               "bs4"),
        ("pytesseract",       "pytesseract"),
        ("pyperclip",         "pyperclip"),
        ("mss",               "mss"),
    ]

    for display_name, import_name in packages:
        try:
            importlib.import_module(import_name)
            passed.append(_passed("dependency", f"{display_name} — importable"))
        except ImportError:
            failed.append(_failed("dependency", f"{display_name} — NOT importable (pip install {import_name})"))
        except Exception as exc:
            warnings.append(_warning("dependency", f"{display_name} — import error: {str(exc)[:60]}"))

    # ── D2: spaCy model loaded ─────────────────────────────────────────────────
    try:
        import spacy
        nlp = spacy.load("en_core_web_sm")
        passed.append(_passed("dependency", "spaCy model en_core_web_sm — loaded"))
    except OSError:
        failed.append(_failed("dependency", "spaCy model en_core_web_sm — NOT found (run: python -m spacy download en_core_web_sm)"))
    except Exception as exc:
        warnings.append(_warning("dependency", f"spaCy model check error: {str(exc)[:80]}"))

    # ── D3: Tesseract callable ─────────────────────────────────────────────────
    try:
        result = subprocess.run(
            [r"C:\Program Files\Tesseract-OCR\tesseract.exe", "--version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode == 0:
            version_line = result.stdout.splitlines()[0] if result.stdout else "unknown version"
            passed.append(_passed("dependency", f"Tesseract — {version_line}"))
        else:
            failed.append(_failed("dependency", f"Tesseract — returned code {result.returncode}"))
    except FileNotFoundError:
        failed.append(_failed("dependency", "Tesseract — not found at expected path C:\\Program Files\\Tesseract-OCR\\tesseract.exe"))
    except subprocess.TimeoutExpired:
        warnings.append(_warning("dependency", "Tesseract — timed out on version check"))
    except Exception as exc:
        warnings.append(_warning("dependency", f"Tesseract check error: {str(exc)[:80]}"))

    # ── D4: OpenRouter API key present + minimal connectivity test ─────────────
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY", "")

    if not api_key:
        failed.append(_failed("connectivity", "OpenRouter — OPENROUTER_API_KEY not set in .env"))
    else:
        passed.append(_passed("connectivity", "OpenRouter — API key present"))
        # Minimal connectivity test: hit /models endpoint (no token cost)
        try:
            import requests as _requests
            resp = _requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                passed.append(_passed("connectivity", "OpenRouter — API reachable (models endpoint responded)"))
            elif resp.status_code == 401:
                failed.append(_failed("connectivity", "OpenRouter — API key invalid (401 Unauthorized)"))
            else:
                warnings.append(_warning("connectivity", f"OpenRouter — unexpected status {resp.status_code} on models endpoint"))
        except Exception as exc:
            warnings.append(_warning("connectivity", f"OpenRouter — connectivity test failed: {str(exc)[:80]}"))

    # ── D5: Ollama reachability (optional — only warn if not reachable) ─────────
    try:
        import requests as _requests
        resp = _requests.get("http://localhost:11434/api/tags", timeout=4)
        if resp.status_code == 200:
            passed.append(_passed("connectivity", "Ollama — reachable at localhost:11434"))
        else:
            warnings.append(_warning("connectivity", f"Ollama — responded with status {resp.status_code}"))
    except Exception:
        # Ollama is optional — warn, don't fail
        warnings.append(_warning("connectivity", "Ollama — not reachable (localhost:11434). OK if not using local models."))

    return passed, failed, warnings


# ══════════════════════════════════════════════════════════════════════════════
# CHECK E — BACKUP STATUS
# Verify evolver_backups/ exists and has at least one recent entry.
# ══════════════════════════════════════════════════════════════════════════════

def check_backup_status():
    passed   = []
    failed   = []
    warnings = []

    backups_dir = os.path.join(BASE_DIR, "evolver_backups")
    patches_dir = os.path.join(BASE_DIR, "patches")

    # ── E1: Backup folder exists ───────────────────────────────────────────────
    if not os.path.exists(backups_dir):
        failed.append(_failed("backup_status", "evolver_backups/ folder does not exist — rollback safety net missing"))
    else:
        passed.append(_passed("backup_status", "evolver_backups/ folder exists"))

        # ── E2: Has at least one backup ────────────────────────────────────────
        bak_files = [
            f for f in os.listdir(backups_dir)
            if f.endswith(".bak")
        ]

        if not bak_files:
            warnings.append(_warning(
                "backup_status",
                "evolver_backups/ is empty — no backups yet. "
                "This is normal on a fresh install. Evolver creates backups on first patch."
            ))
        else:
            # Find most recent backup
            bak_paths = [os.path.join(backups_dir, f) for f in bak_files]
            bak_paths.sort(key=os.path.getmtime, reverse=True)
            newest = bak_paths[0]
            newest_age_hours = (
                datetime.datetime.now().timestamp() - os.path.getmtime(newest)
            ) / 3600

            if newest_age_hours > 168:  # older than 7 days
                warnings.append(_warning(
                    "backup_status",
                    f"Most recent backup is {newest_age_hours:.0f}h old ({os.path.basename(newest)}). "
                    "Consider running an evolution cycle."
                ))
            else:
                passed.append(_passed(
                    "backup_status",
                    f"{len(bak_files)} backup(s) found. Most recent: "
                    f"{os.path.basename(newest)} ({newest_age_hours:.1f}h ago)"
                ))

    # ── E3: Patches folder ─────────────────────────────────────────────────────
    if not os.path.exists(patches_dir):
        warnings.append(_warning("backup_status", "patches/ folder not found — evolver hasn't run yet"))
    else:
        patch_files = os.listdir(patches_dir)
        passed.append(_passed("backup_status", f"patches/ folder exists with {len(patch_files)} proposal(s)"))

    return passed, failed, warnings


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def run_full_checkup() -> dict:
    """
    Run all five diagnostic checks.
    Returns:
        {
            "passed":   [{"check": str, "detail": str}, ...],
            "failed":   [{"check": str, "detail": str}, ...],
            "warnings": [{"check": str, "detail": str}, ...],
            "summary":  str   ← short spoken-ready string
        }
    Never raises — all failures are caught and reported inside the result dict.
    """
    all_passed   = []
    all_failed   = []
    all_warnings = []

    checks = [
        ("Syntax check",       check_syntax),
        ("Test suite",         check_tests),
        ("Data integrity",     check_data_integrity),
        ("Dependencies",       check_dependencies),
        ("Backup status",      check_backup_status),
    ]

    for check_name, check_fn in checks:
        print(f"[Diagnostic] Running: {check_name}...")
        try:
            p, f, w = check_fn()
            all_passed.extend(p)
            all_failed.extend(f)
            all_warnings.extend(w)
        except Exception as exc:
            # A check function itself crashed — report it as a warning
            all_warnings.append(_warning(
                "diagnostic_error",
                f"{check_name} check crashed unexpectedly: {str(exc)[:100]}"
            ))

    # ── Build spoken summary ───────────────────────────────────────────────────
    n_failed   = len(all_failed)
    n_warnings = len(all_warnings)
    n_passed   = len(all_passed)

    if n_failed == 0 and n_warnings == 0:
        summary = f"All systems healthy. {n_passed} checks passed."
    elif n_failed == 0:
        summary = (
            f"Mostly healthy. {n_passed} passed, {n_warnings} warning(s). "
            f"First warning: {all_warnings[0]['detail'][:80]}."
        )
    else:
        # List up to 2 failures concisely
        failure_snippets = [f['detail'][:60] for f in all_failed[:2]]
        summary = (
            f"{n_failed} issue(s) found, {n_warnings} warning(s). "
            + " | ".join(failure_snippets)
            + ("..." if n_failed > 2 else "")
        )

    report = {
        "passed":    all_passed,
        "failed":    all_failed,
        "warnings":  all_warnings,
        "summary":   summary,
        "timestamp": datetime.datetime.now().isoformat(),
    }

    print(f"[Diagnostic] Complete — {n_passed} passed, {n_failed} failed, {n_warnings} warnings")
    return report
