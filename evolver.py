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
import datetime
import time
import threading

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
        for act in AVAILABLE_ACTIONS_LIST:
            if act not in usage and act not in ["exit", "run_sequence"]:
                findings.append({
                    "type":       "unused_capability",
                    "action":     act,
                    "suggestion": f"'{act}' has never been used. Consider promoting it in suggestions."
                })
    except:
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
        print(f"[Evolver] Proposal saved → {path}")
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
      4. If sandbox_test fails → auto-rollback and log
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


# ── Main evolution cycle ──
def run_evolution_cycle():
    """
    Full cycle: analyze → propose → sandbox test.
    Runs periodically. NEVER writes to core files.
    """
    print("[Evolver] Running evolution cycle...")
    findings = analyze()

    if not findings:
        print("[Evolver] No issues found.")
        return

    print(f"[Evolver] {len(findings)} finding(s) detected.")
    for finding in findings:
        proposal = generate_proposal(finding)
        safe     = sandbox_test(proposal)
        if safe:
            print(f"[Evolver] Proposal ready for review: {finding['type']} → {finding.get('suggestion','')[:60]}")
        else:
            print(f"[Evolver] Proposal rejected in sandbox: {finding['type']}")


# ── Background runner ──
def start_evolver(interval_hours=6):
    """Runs evolution cycle every N hours in background."""
    def _loop():
        # First run after 5 min delay (let system warm up)
        time.sleep(300)
        while True:
            try:
                run_evolution_cycle()
            except Exception as e:
                print(f"[Evolver] Error: {e}")
            time.sleep(interval_hours * 3600)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print(f"[Evolver] Started — cycle every {interval_hours}h. Proposals → /patches/")


# ── Manual trigger ──
if __name__ == "__main__":
    print("=== Jarvis Evolver — Manual Run ===")
    run_evolution_cycle()
    patches = os.listdir(PATCHES_DIR) if os.path.exists(PATCHES_DIR) else []
    print(f"\n{len(patches)} proposal(s) in /patches/:")
    for p in patches:
        print(f"  → {p}")
    backups = list_backups()
    print(f"\n{len(backups)} backup(s) in /evolver_backups/:")
    for b in backups[:10]:
        print(f"  → {os.path.basename(b)}")