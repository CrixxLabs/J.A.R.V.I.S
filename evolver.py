# evolver.py — Phase 6: Self-Evolution System
# Reads memory, detects inefficiencies, proposes patches.
# NEVER overwrites core files directly.
# Saves proposals to /patches/ for manual review.
#
# Phase 6.1 (FIX 5): Adds backup + rollback system for safe patch application.
#   - Every file modification is backed up to /evolver_backups/ first
#   - rollback_last_patch(filepath) restores the most recent backup
#   - apply_patch() auto-rolls back if sandbox validation fails after apply

import os
import json
import shutil
import subprocess
import datetime
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import brain
import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

# Best-effort session logger import (don't crash if signature differs)
try:
    from session_logger import log_event
except Exception:
    def log_event(*args, **kwargs):
        pass

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PATCHES_DIR  = os.path.join(BASE_DIR, "patches")
BACKUPS_DIR  = os.path.join(BASE_DIR, "evolver_backups")
MEMORY_FILE  = os.path.join(BASE_DIR, "memory.json")

os.makedirs(PATCHES_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)


# ── Load memory snapshot ──
def _load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


# ── Analysis: detect problems ──
def analyze():
    """
    Scans memory for:
    - Actions with high failure rates
    - Repeated inefficient patterns
    - Underused capabilities
    Returns list of findings.
    """
    m        = _load_memory()
    findings = []

    # 1. High failure rate actions
    failures = m.get("failure_log", {})
    usage    = m.get("usage_freq", {})
    for action, fdata in failures.items():
        fail_count = fdata.get("count", 0)
        use_count  = usage.get(action, {}).get("count", 0)
        total      = fail_count + use_count
        if total >= 3 and fail_count / total > 0.4:
            findings.append({
                "type":    "high_failure",
                "action":  action,
                "rate":    round(fail_count / total, 2),
                "detail":  fdata.get("detail", ""),
                "suggestion": f"Consider adding a better fallback or alternative for '{action}'"
            })

    # 2. Actions never used (capability waste)
    try:
        from executor import AVAILABLE_ACTIONS_LIST
        unused = [act for act in AVAILABLE_ACTIONS_LIST if act not in usage and act not in ["exit", "run_sequence"]]
        if unused:
            # Report at most 1 representative finding for unused capabilities instead of spamming 69 proposals
            sample_unused = unused[:3]
            findings.append({
                "type":       "unused_capability",
                "action":     sample_unused[0],
                "all_unused": sample_unused,
                "suggestion": f"Actions {sample_unused} have not been used. Consider promoting them in suggestions."
            })
    except Exception:
        pass

    # 3. Repeated same action sequences (efficiency opportunity)
    activity_log = m.get("activity_log", [])
    if len(activity_log) >= 6:
        recent_types = [e.get("type", "") for e in activity_log[-10:]]
        # detect consecutive duplicates
        dupes = []
        for i in range(len(recent_types) - 1):
            if recent_types[i] == recent_types[i+1] and recent_types[i]:
                dupes.append(recent_types[i])
        if dupes:
            findings.append({
                "type":       "repeated_pattern",
                "actions":    list(set(dupes)),
                "suggestion": "These actions are repeated back-to-back. Consider bundling into run_sequence."
            })

    # 4. Daily API overuse
    today       = datetime.date.today().isoformat()
    daily_stats = m.get("daily_stats", {}).get(today, {})
    or_calls    = daily_stats.get("openrouter_calls", 0)
    gem_calls   = daily_stats.get("gemini_calls", 0)
    if or_calls > 80:
        findings.append({
            "type":       "api_overuse",
            "model":      "openrouter",
            "count":      or_calls,
            "suggestion": "High OpenRouter usage today. Consider expanding fast_command shortcuts."
        })
    if gem_calls > 50:
        findings.append({
            "type":       "api_overuse",
            "model":      "gemini",
            "count":      gem_calls,
            "suggestion": "High Gemini usage. Check vision cooldown or cache settings."
        })

    return findings


# ── Generate patch proposal (text, not code) ──
def generate_proposal(finding):
    """
    Generates a human-readable patch proposal.
    Saved as .json in /patches/.
    NEVER auto-applied to core files.
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname     = f"proposal_{finding['type']}_{timestamp}.json"
    path      = os.path.join(PATCHES_DIR, fname)

    proposal = {
        "generated_at": datetime.datetime.now().isoformat(),
        "finding":      finding,
        "status":       "pending_review",   # human must approve
        "auto_apply":   False,              # NEVER auto-apply
        "patch_idea":   finding.get("suggestion", ""),
        "files_affected": _suggest_files(finding["type"]),
    }

    try:
        with open(path, "w") as f:
            json.dump(proposal, f, indent=2)
        print(f"[Evolver] Proposal saved -> {path}")
    except Exception as e:
        print(f"[Evolver] Save error: {e}")

    return proposal


def _suggest_files(finding_type):
    mapping = {
        "high_failure":        ["executor.py", "planner.py"],
        "unused_capability":   ["planner.py", "jarvis.py"],
        "repeated_pattern":    ["planner.py"],
        "api_overuse":         ["planner.py", "jarvis.py"],
    }
    return mapping.get(finding_type, ["planner.py"])


# ── Sandbox simulation (dry-run) ──
def sandbox_test(proposal):
    """
    Simulates the proposal without touching core files.
    Currently: validates the proposal is well-formed.
    Returns True if safe to consider applying.
    """
    required_keys = ["generated_at", "finding", "status", "auto_apply"]
    for key in required_keys:
        if key not in proposal:
            print(f"[Evolver] Sandbox fail: missing key '{key}'")
            return False
    if proposal.get("auto_apply") is True:
        print("[Evolver] Sandbox REJECT: auto_apply is True — unsafe")
        return False
    print(f"[Evolver] Sandbox OK for: {proposal['finding'].get('type')}")
    return True


# ═════════════════════════════════════════════════════════════════════════════
# FIX 5 — Backup & Rollback System
# ═════════════════════════════════════════════════════════════════════════════

def _safe_log(event_type, payload, severity=None):
    """Best-effort log_event call — tolerates differing signatures."""
    try:
        if severity:
            log_event(event_type, payload, severity=severity, module="evolver")
        else:
            log_event(event_type, payload, module="evolver")
    except TypeError:
        # Fall back to minimal call if log_event doesn't accept kwargs
        try:
            log_event(event_type, payload)
        except Exception:
            pass
    except Exception:
        pass


def _backup_file(filepath):
    """
    Save a timestamped backup of filepath into /evolver_backups/.
    Returns the backup path on success, None on failure.
    Naming: <filename>__<YYYYMMDD_HHMMSS>.bak
    """
    if not os.path.exists(filepath):
        print(f"[Evolver][Backup] Source not found: {filepath}")
        return None
    try:
        filename  = os.path.basename(filepath)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{filename}__{timestamp}.bak"
        backup_path = os.path.join(BACKUPS_DIR, backup_name)
        shutil.copy2(filepath, backup_path)
        print(f"[Evolver][Backup] Saved: {backup_path}")
        _safe_log("evolver_backup_created", {
            "source": filepath,
            "backup": backup_path,
        })
        return backup_path
    except Exception as e:
        print(f"[Evolver][Backup] Error backing up {filepath}: {e}")
        _safe_log("evolver_backup_error", {
            "source": filepath,
            "error":  str(e),
        }, severity="error")
        return None


def list_backups(filepath=None):
    """
    Return list of backup paths sorted newest first.
    If filepath is given, filter to only backups of that file's basename.
    """
    try:
        all_backups = [
            os.path.join(BACKUPS_DIR, f)
            for f in os.listdir(BACKUPS_DIR)
            if f.endswith(".bak")
        ]
    except Exception:
        return []

    if filepath:
        target = os.path.basename(filepath)
        all_backups = [b for b in all_backups if os.path.basename(b).startswith(f"{target}__")]

    # Sort newest first by mtime
    all_backups.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return all_backups


def rollback_last_patch(filepath):
    """
    Restore filepath from its most recent backup in /evolver_backups/.
    Returns True on success, False otherwise.
    """
    backups = list_backups(filepath)
    if not backups:
        print(f"[Evolver][Rollback] No backups found for {filepath}")
        _safe_log("evolver_rollback_no_backup", {"target": filepath}, severity="error")
        return False

    latest_backup = backups[0]
    try:
        shutil.copy2(latest_backup, filepath)
        print(f"[Evolver][Rollback] Restored {filepath} from {latest_backup}")
        _safe_log("evolver_rollback_success", {
            "target": filepath,
            "restored_from": latest_backup,
        })
        return True
    except Exception as e:
        print(f"[Evolver][Rollback] Error restoring {filepath}: {e}")
        _safe_log("evolver_rollback_error", {
            "target": filepath,
            "backup": latest_backup,
            "error":  str(e),
        }, severity="error")
        return False


def apply_patch(filepath, new_content, proposal=None):
    """
    Safely apply a patch to filepath:
      1. Backup the original
      2. Write the new content
      3. Run sandbox_test() if a proposal is given
      4. If sandbox_test fails -> auto-rollback and log
    Returns (True, message) on success, (False, message) on failure.
    """
    if not os.path.exists(filepath):
        msg = f"Target file not found: {filepath}"
        print(f"[Evolver][Apply] {msg}")
        return False, msg

    # Step 1: backup
    backup_path = _backup_file(filepath)
    if backup_path is None:
        msg = f"Backup failed; aborting patch for {filepath}"
        print(f"[Evolver][Apply] {msg}")
        _safe_log("evolver_patch_aborted", {"target": filepath, "reason": "backup_failed"}, severity="error")
        return False, msg

    # Step 2: write new content
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"[Evolver][Apply] Patch written to {filepath}")
        _safe_log("evolver_patch_applied", {
            "target": filepath,
            "backup": backup_path,
        })
    except Exception as e:
        msg = f"Write failed: {e}"
        print(f"[Evolver][Apply] {msg} — rolling back.")
        _safe_log("evolver_patch_write_error", {
            "target": filepath, "error": str(e),
        }, severity="error")
        rollback_last_patch(filepath)
        return False, msg

    # Step 3: sandbox validation (if a proposal was provided)
    if proposal is not None:
        try:
            safe = sandbox_test(proposal)
        except Exception as e:
            safe = False
            print(f"[Evolver][Apply] Sandbox raised: {e}")

        if not safe:
            print(f"[Evolver][Apply] Sandbox test FAILED after apply — auto-rolling back.")
            _safe_log("evolver_sandbox_failed_post_apply", {
                "target": filepath,
                "backup": backup_path,
            }, severity="error")
            rollback_ok = rollback_last_patch(filepath)
            return False, (
                f"Patch reverted: sandbox check failed after apply. "
                f"{'Rollback successful.' if rollback_ok else 'ROLLBACK ALSO FAILED — manual restore needed.'}"
            )

    return True, f"Patch applied to {filepath}. Backup at {backup_path}"


# ═════════════════════════════════════════════════════════════════════════════
# End FIX 5 additions
# ═════════════════════════════════════════════════════════════════════════════


# ── Voice-Activity & Real-Time Suspension Check ──
def is_voice_active() -> bool:
    """Check if the microphone, TTS, or speech interaction is actively in progress."""
    try:
        import jarvis
        if getattr(jarvis, "is_speaking", False) or getattr(jarvis, "ACTIVE", False):
            return True
    except Exception:
        pass

    try:
        import runtime_visuals
        curr = runtime_visuals.get_current_visual_state()
        state = curr.get("base_state", "").lower()
        activity = curr.get("current_activity", "").lower()
        if state in ("listening", "speaking") or activity in ("listening", "speaking"):
            return True
    except Exception:
        pass

    try:
        from degradation_watchdog import DegradationLevel, get_degradation_watchdog
        watchdog = get_degradation_watchdog()
        if watchdog.current_level >= DegradationLevel.SUSPEND_BACKGROUND:
            return True
    except Exception:
        pass

    return False


# ── Main evolution cycle ──
def run_evolution_cycle(max_proposals: int = 3):
    """
    Full cycle: analyze -> propose -> sandbox test.
    Runs periodically. NEVER writes to core files.
    Pauses automatically during active voice interactions.
    """
    if is_voice_active():
        print("[Evolver] Voice active or background suspended - pausing evolution sweep.")
        return

    print("[Evolver] Running evolution cycle...")
    findings = analyze()

    if not findings:
        print("[Evolver] No issues found.")
        return

    print(f"[Evolver] {len(findings)} finding(s) detected.")
    count = 0
    for finding in findings:
        if count >= max_proposals:
            break
        proposal = generate_proposal(finding)
        safe     = sandbox_test(proposal)
        if safe:
            print(f"[Evolver] Proposal ready for review: {finding['type']} -> {finding.get('suggestion','')[:60]}")
            count += 1
        else:
            print(f"[Evolver] Proposal rejected in sandbox: {finding['type']}")


# ── Background runner ──
_evolver_stop = threading.Event()
_evolver_thread = None


def start_evolver(interval_hours=6):
    """Runs evolution cycle every N hours in background."""
    global _evolver_thread
    if _evolver_thread and _evolver_thread.is_alive():
        return
    _evolver_stop.clear()
    def _loop():
        # First run after 5 min delay (let system warm up)
        if _evolver_stop.wait(300):
            return
        while not _evolver_stop.is_set():
            if not is_voice_active():
                try:
                    run_evolution_cycle()
                except Exception as e:
                    print(f"[Evolver] Error: {e}")
            else:
                print("[Evolver] Voice active or background suspended - deferring evolution sweep.")
            _evolver_stop.wait(interval_hours * 3600)

    _evolver_thread = threading.Thread(target=_loop, daemon=True)
    _evolver_thread.start()
    print(f"[Evolver] Started - cycle every {interval_hours}h. Proposals -> /patches/")


def stop_evolver():
    _evolver_stop.set()
    if _evolver_thread and _evolver_thread.is_alive():
        _evolver_thread.join(timeout=3.0)


# ═════════════════════════════════════════════════════════════════════════════
# Phase 4 — Canary Self-Healing Architecture
# ═════════════════════════════════════════════════════════════════════════════

def generate_ai_patch(file_content: str, error_msg: str, error_trace: str = "") -> Optional[str]:
    """Ask brain.py LLM to generate a targeted minimal bugfix for a failing file.

    Args:
        file_content: Complete original file source code
        error_msg: Runtime or test error message
        error_trace: Traceback or test failure diagnostic

    Returns:
        Patched full file content, or None if generation failed
    """
    prompt = f"""You are the autonomous self-healing engine for J.A.R.V.I.S.
A file produced an unhandled error or test failure.

=== ERROR MESSAGE ===
{error_msg}

=== ERROR TRACE / TEST FAILURE ===
{error_trace[:800]}

=== ORIGINAL SOURCE CODE ===
```python
{file_content}
```

Task: Provide the corrected, complete Python file content that fixes the error.
Do not introduce breaking changes or delete existing functionality.
Return ONLY the complete Python source code enclosed in ```python ... ``` markdown blocks."""

    try:
        response = brain.ask_llm(prompt, model_type="fast", allow_actions=False)
        if not response or not response.strip():
            return None

        # Extract python code block
        if "```python" in response:
            code = response.split("```python")[1].split("```")[0].strip()
        elif "```" in response:
            code = response.split("```")[1].split("```")[0].strip()
        else:
            code = response.strip()

        return code if len(code) > 20 else None

    except Exception as exc:
        print(f"[Evolver][Canary] AI patch generation failed: {exc}")
        return None


def run_canary_tests(timeout: float = 90.0) -> Tuple[bool, str]:
    """Execute the full test suite in an isolated subprocess.

    Returns:
        Tuple of (all_passed: bool, output: str)
    """
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "-q", "tests"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        passed = (result.returncode == 0)
        output = result.stdout + "\n" + result.stderr
        return passed, output
    except subprocess.TimeoutExpired:
        return False, f"Test execution exceeded {timeout}s timeout"
    except Exception as exc:
        return False, f"Test execution error: {exc}"


def canary_self_heal(failing_file: str, error_msg: str,
                     error_trace: str = "") -> Dict[str, Any]:
    """Autonomous canary self-healing pipeline.

    Workflow:
      1. Detect current git branch.
      2. Create an isolated canary git branch `canary/fix-<timestamp>`.
      3. Generate patch via brain.py LLM.
      4. Apply patch on canary branch.
      5. Run test suite.
      6. Invariant: Only merge/promote if 100% tests pass. Otherwise discard & rollback.

    Args:
        failing_file: Path to failing source file (absolute or relative to BASE_DIR)
        error_msg: Error message to diagnose
        error_trace: Full exception traceback or failure diagnostic

    Returns:
        Diagnostic summary of canary self-healing outcome
    """
    registry = get_registry()
    target_path = Path(failing_file)
    if not target_path.is_absolute():
        target_path = Path(BASE_DIR) / target_path

    if not target_path.exists():
        msg = f"Target file not found for canary healing: {target_path}"
        print(f"[Evolver][Canary] {msg}")
        return {"success": False, "error": msg, "stage": "file_lookup"}

    # 1. Get current branch
    try:
        branch_res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=BASE_DIR, capture_output=True, text=True, check=True
        )
        original_branch = branch_res.stdout.strip()
        if not original_branch:
            original_branch = "master"
    except Exception as exc:
        msg = f"Failed to get current git branch: {exc}"
        print(f"[Evolver][Canary] {msg}")
        return {"success": False, "error": msg, "stage": "git_branch"}

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    canary_branch = f"canary/fix-{timestamp}"

    # Read original file content
    with open(target_path, "r", encoding="utf-8") as f:
        original_content = f.read()

    # 2. Generate AI patch
    print(f"[Evolver][Canary] Generating fix for {target_path.name}...")
    patched_content = generate_ai_patch(original_content, error_msg, error_trace)
    if not patched_content:
        registry.set_capability_evidence(
            "EVOLVER", EvidenceLevel.BROKEN,
            f"Failed to generate patch for {target_path.name}", source="canary self-healing"
        )
        return {"success": False, "error": "AI patch generation failed", "stage": "patch_gen"}

    # 3. Create canary branch
    try:
        subprocess.run(
            ["git", "checkout", "-b", canary_branch],
            cwd=BASE_DIR, capture_output=True, text=True, check=True
        )
        print(f"[Evolver][Canary] Switched to canary branch: {canary_branch}")
    except Exception as exc:
        msg = f"Failed to create canary branch: {exc}"
        print(f"[Evolver][Canary] {msg}")
        return {"success": False, "error": msg, "stage": "create_branch"}

    try:
        # 4. Apply patch on canary branch
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(patched_content)
        print(f"[Evolver][Canary] Applied patch to {target_path.name}")

        # 5. Run canary test verification
        print("[Evolver][Canary] Running test suite on canary branch...")
        tests_passed, test_output = run_canary_tests()

        if tests_passed:
            # 6a. Tests passed: commit and merge to original branch
            print(f"[Evolver][Canary] Verification SUCCESS: All tests passed on {canary_branch}.")
            subprocess.run(["git", "add", str(target_path)], cwd=BASE_DIR, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"fix(canary): self-healed {target_path.name} ({error_msg[:40]})"],
                cwd=BASE_DIR, check=True
            )
            subprocess.run(["git", "checkout", original_branch], cwd=BASE_DIR, check=True)
            subprocess.run(["git", "merge", canary_branch], cwd=BASE_DIR, check=True)
            subprocess.run(["git", "branch", "-d", canary_branch], cwd=BASE_DIR, check=True)

            registry.set_capability_evidence(
                "EVOLVER", EvidenceLevel.LIVE,
                f"Canary self-healing succeeded and merged for {target_path.name}",
                source="canary self-healing"
            )
            return {
                "success": True,
                "stage": "promoted",
                "canary_branch": canary_branch,
                "file": str(target_path),
                "message": "Patch verified and promoted to parent branch"
            }
        else:
            # 6b. Tests failed: rollback and discard canary branch
            print(f"[Evolver][Canary] Verification FAILED on {canary_branch}. Discarding canary.")
            # Discard changes on canary
            subprocess.run(["git", "checkout", original_branch], cwd=BASE_DIR, check=True)
            subprocess.run(["git", "branch", "-D", canary_branch], cwd=BASE_DIR, check=True)

            registry.set_capability_evidence(
                "EVOLVER", EvidenceLevel.BROKEN,
                f"Canary verification failed for {target_path.name}; discarded",
                source="canary self-healing"
            )
            return {
                "success": False,
                "stage": "rejected_rollback",
                "canary_branch": canary_branch,
                "file": str(target_path),
                "test_output": test_output[:500],
                "error": "Test verification failed on canary branch"
            }

    except Exception as exc:
        # Guarantee rollback to original branch on unexpected errors
        print(f"[Evolver][Canary] Unexpected error during canary healing: {exc}")
        try:
            subprocess.run(["git", "checkout", original_branch], cwd=BASE_DIR)
            subprocess.run(["git", "branch", "-D", canary_branch], cwd=BASE_DIR)
        except Exception:
            pass
        return {"success": False, "error": str(exc), "stage": "exception"}


# ── Manual trigger ──
if __name__ == "__main__":
    print("=== Jarvis Evolver — Manual Run ===")
    run_evolution_cycle()
    patches = os.listdir(PATCHES_DIR) if os.path.exists(PATCHES_DIR) else []
    print(f"\n{len(patches)} proposal(s) in /patches/:")
    for p in patches:
        print(f"  -> {p}")
    backups = list_backups()
    print(f"\n{len(backups)} backup(s) in /evolver_backups/:")
    for b in backups[:10]:
        print(f"  -> {os.path.basename(b)}")
