# self_awareness.py — Runtime self-introspection
# =======================================================================
# Jarvis scans its own codebase to know what files exist, what functions
# they expose, and how its abilities have evolved over time.
#
# Unified with self_model.py to provide truthful capability summaries.

import ast
import json
import os
import hashlib
import datetime
from typing import Optional

# ── Reliability imports (Phase 7 Live-State tracking) ──────────────────────────
import status_registry
from status_registry import SubsystemState
import self_model

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "self_awareness_state.json")

# Files that don't count as "core" (utilities, data, tests)
_IGNORE_FILES = {
    "self_awareness_state.json",
    "memory.json",
    "user_profile.json",
    "tasks.json",
    "obligations.json",
    "session_awareness.json",
    "gcal_token.json",
    "credentials.json",
    "__init__.py",
    "self_model.json",
}

_IGNORE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "logs",
    "patches",
    "generated_media",
    "skills",  # scanned separately
    "ui",
    "node_modules",
}

# Curated descriptions for known files (fills in what pure code inspection can't)
_FILE_DESCRIPTIONS = {
    "jarvis.py":               "Main entry point, event loop, user interaction",
    "brain.py":               "LLM router: NVIDIA, then Gemini, then local Ollama",
    "planner.py":              "Decision router — decides local action vs LLM call",
    "core.py":                 "Intent classification, spaCy NLP, Malayalam translation",
    "executor.py":             "Executes actions (open apps, web search, reminders, etc.)",
    "listener.py":             "Voice capture, wake-word, double-clap, interrupt detection",
    "voice_sample.py":         "Captures voice embedding for speaker verification",
    "vision.py":               "Screen capture, OCR via Tesseract",
    "memory.py":               "Facts, activity log, daily stats in memory.json",
    "user_profile.py":         "Personal preferences in user_profile.json",
    "proactive_scheduler.py":  "Scheduled reminders, obligation check-ins",
    "tasks.py":                "Active task engine with reminders",
    "obligations.py":          "Assignments, exams, deadlines tracker",
    "conversation_manager.py": "Rolling chat history + auto-summarization",
    "session_logger.py":       "Structured session event logs",
    "system_monitor.py":       "CPU, GPU, RAM tracking",
    "self_diagnostic.py":      "Internal health checks, boot-time syntax scan",
    "observer.py":             "Active app monitoring, screen state, system observers",
    "file_ops.py":             "Filesystem operations",
    "whatsapp_fetcher.py":     "WhatsApp message scraping",
    "face_recognition_module.py": "Facial recognition authentication",
    "evolver.py":              "Self-patching, proposes code improvements",
    "plugin_loader.py":        "Loads custom skill plugins from /skills/",
    "portal_scan.py":          "Screen deadline scanning",
    "server.py":               "Local web backend for dashboard",
    "jarvis_runtime.py":       "Runtime lifecycle, background states",

    # Iron Man modules
    "app_installer.py":        "Winget-based app installer with real-time progress tracking",
    "credential_vault.py":     "Secure password storage via Windows DPAPI (keyring)",
    "window_watcher.py":       "Real-time window state detection (no fake sleep timing)",
    "self_awareness.py":       "Runtime self-introspection — scans own codebase",
    "auto_login_profiles.py":  "Per-app login flow recipes (Spotify, Discord, etc.)",
    "login_orchestrator.py":   "Coordinates install → open → login → verify sequence",
    "self_model.py":           "Structured self-model tracking operational capabilities",
}


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: FILE SCANNING
# ══════════════════════════════════════════════════════════════════════════════

def _hash_file(filepath: str) -> str:
    """Fast hash of file contents for change detection."""
    try:
        h = hashlib.md5()
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return ""


def _extract_functions(filepath: str) -> list:
    """Parse a Python file and return list of top-level function names."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
        functions = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                functions.append(node.name)
        return functions
    except (SyntaxError, UnicodeDecodeError):
        return []
    except Exception:
        return []


def _scan_core_files() -> dict:
    """
    Scan all top-level .py files in BASE_DIR.
    Returns dict: {filename: {hash, size_bytes, functions, description}}
    """
    result = {}

    try:
        entries = os.listdir(BASE_DIR)
    except Exception:
        return result

    for entry in entries:
        # Skip ignored files
        if entry in _IGNORE_FILES or entry.startswith(("test_", "add_", "fix_", "apply_", "update_")) or entry == "restore_run_smoke_test.py":
            continue

        full_path = os.path.join(BASE_DIR, entry)

        # Skip directories
        if os.path.isdir(full_path):
            continue

        # Only .py files
        if not entry.endswith(".py"):
            continue

        try:
            size = os.path.getsize(full_path)
            file_hash = _hash_file(full_path)
            functions = _extract_functions(full_path)
            description = _FILE_DESCRIPTIONS.get(entry, "")

            result[entry] = {
                "hash":        file_hash,
                "size_bytes":  size,
                "functions":   functions,
                "description": description,
            }
        except Exception as exc:
            print(f"[self_awareness] error scanning {entry}: {exc}")
            continue

    return result


def _scan_skills() -> list:
    """Scan the /skills/ directory for plugin files."""
    skills_dir = os.path.join(BASE_DIR, "skills")
    if not os.path.isdir(skills_dir):
        return []

    skills = []
    try:
        for entry in os.listdir(skills_dir):
            if entry.endswith(".py") and not entry.startswith("_"):
                skills.append(entry[:-3])  # strip .py
    except Exception:
        pass

    return sorted(skills)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: STATE PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════════

def _load_previous_state() -> Optional[dict]:
    """Load the previous scan state from disk."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_state(state: dict):
    """Persist current scan state to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"[self_awareness] error saving state: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# CACHED SCAN STATE
# ══════════════════════════════════════════════════════════════════════════════

_current_scan = None
_scan_timestamp = None


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: SCAN
# ══════════════════════════════════════════════════════════════════════════════

def scan_self(verbose: bool = True) -> dict:
    """
    Perform a full self-scan and compare to previous state.
    """
    global _current_scan, _scan_timestamp

    if verbose:
        print("[self_awareness] scanning codebase...")

    # Scan current state
    core_files = _scan_core_files()
    skills = _scan_skills()

    total_functions  = sum(len(f["functions"]) for f in core_files.values())
    total_size_bytes = sum(f["size_bytes"] for f in core_files.values())

    # Compare against previous scan
    previous = _load_previous_state()
    changes = {"added": [], "removed": [], "modified": []}

    if previous:
        prev_files = previous.get("core_files", {})

        # Added files
        for filename in core_files:
            if filename not in prev_files:
                changes["added"].append(filename)

        # Removed files
        for filename in prev_files:
            if filename not in core_files:
                changes["removed"].append(filename)

        # Modified files
        for filename in core_files:
            if filename in prev_files:
                if core_files[filename]["hash"] != prev_files[filename].get("hash"):
                    changes["modified"].append(filename)

    scan_result = {
        "timestamp":        datetime.datetime.now().isoformat(),
        "core_files":       core_files,
        "skills":           skills,
        "total_files":      len(core_files),
        "total_functions":  total_functions,
        "total_size_bytes": total_size_bytes,
        "changes":          changes,
    }

    # Save new state
    _save_state(scan_result)

    # Cache in memory
    _current_scan = scan_result
    _scan_timestamp = datetime.datetime.now()

    if verbose:
        print(f"[self_awareness] OK scanned {len(core_files)} files, {total_functions} functions, {len(skills)} skills")
        if changes["added"]:
            print(f"[self_awareness] + added since last boot: {', '.join(changes['added'])}")
        if changes["removed"]:
            print(f"[self_awareness] - removed since last boot: {', '.join(changes['removed'])}")
        if changes["modified"]:
            print(f"[self_awareness] ~ modified: {len(changes['modified'])} file(s)")

    return scan_result


def force_rescan() -> dict:
    """Invalidate cache and rescan from scratch."""
    global _current_scan, _scan_timestamp
    _current_scan = None
    _scan_timestamp = None
    return scan_self(verbose=True)


def _get_scan(auto_scan: bool = True) -> dict:
    """Get current scan — auto-scan if not cached yet."""
    global _current_scan
    if _current_scan is None and auto_scan:
        scan_self(verbose=False)
    return _current_scan or {}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: QUERIES & LIVE-STATE INTROSPECTION (RE-WRITTEN WITH SELF-MODEL)
# ══════════════════════════════════════════════════════════════════════════════

def get_live_status_summary() -> str:
    """
    Read operational capability status states from self_model
    and format a dynamic, structured summary of operational capabilities.
    """
    model = self_model.get_model()
    return model.get_self_summary(compact=False)


def get_file_list() -> list:
    """Return sorted list of core .py filenames."""
    scan = _get_scan()
    return sorted(scan.get("core_files", {}).keys())


def get_ability_summary() -> str:
    """
    Return a truthful summary of Jarvis's user-meaningful abilities.
    Queries the central capability model instead of pure raw file mappings.
    """
    model = self_model.get_model()
    caps = model.get_capabilities()
    
    lines = []
    for c in caps:
        status_suffix = f" [Status: {c['status']}]"
        lines.append(f"- {c['name']}: {c['description']}{status_suffix}")
    return "\n".join(lines)


def get_change_summary() -> str:
    """
    Return a human-readable summary of what changed since last boot.
    """
    scan = _get_scan()
    changes = scan.get("changes", {})

    added    = changes.get("added", [])
    removed  = changes.get("removed", [])
    modified = changes.get("modified", [])

    if not any([added, removed, modified]):
        return "No changes since last boot."

    parts = []
    if added:
        parts.append(f"gained {len(added)} new file(s): {', '.join(added)}")
    if removed:
        parts.append(f"lost {len(removed)} file(s): {', '.join(removed)}")
    if modified:
        parts.append(f"{len(modified)} file(s) were modified")

    return "Since last boot, I " + "; ".join(parts) + "."


def get_self_context_for_prompt(compact: bool = False) -> str:
    """
    Build a structured, self-aware capability context block for injection into LLM system prompts.
    Replaces static descriptions with live operational states from self_model.
    """
    scan = _get_scan()
    skills = scan.get("skills", [])
    total_files = scan.get("total_files", 0)
    total_functions = scan.get("total_functions", 0)

    model = self_model.get_model()
    self_summary = model.get_self_summary(compact=True)

    if compact:
        prompt_text = (
            f"You consist of {total_files} core modules with {total_functions} functions. "
            f"Operational Capabilities: {self_summary}."
        )
        return prompt_text

    # Detailed version
    lines = [
        f"You consist of {total_files} core Python modules with {total_functions} functions.",
        "",
        "Operational Capabilities Model State:",
    ]

    for c in model.get_capabilities():
        status_line = f"  * {c['name']} ({c['category']}) — {c['description']} [Status: {c['status']}]"
        if c["failure_count"] > 0:
            status_line += f" (Recent failures: {c['failure_count']})"
        lines.append(status_line)

    if skills:
        lines.append("")
        lines.append(f"Active custom skill plugins: {', '.join(skills)}")

    # Highlight currently failed or degraded subsystems dynamically
    unavailable = model.get_unavailable_capabilities()
    if unavailable:
        lines.append("")
        lines.append("CRITICAL SERVICE DEGRADATIONS (Do not attempt actions relying on these):")
        for u in unavailable:
            lines.append(f"  ! {u['name']} is OFFLINE: {u['status_detail']}")

    changes = scan.get("changes", {})
    added = changes.get("added", [])
    if added:
        lines.append("")
        lines.append(f"Incremental file evolution since last boot: {', '.join(added)}")

    return "\n".join(lines)


def get_scan_metadata() -> dict:
    """Get metadata about the last scan."""
    scan = _get_scan()
    return {
        "timestamp":       scan.get("timestamp", ""),
        "total_files":     scan.get("total_files", 0),
        "total_functions": scan.get("total_functions", 0),
        "total_size_kb":   round(scan.get("total_size_bytes", 0) / 1024, 1),
        "skills":          len(scan.get("skills", [])),
    }


if __name__ == "__main__":
    print("═" * 60)
    print("SELF AWARENESS — Test Mode")
    print("═" * 60)
    scan_self(verbose=True)
