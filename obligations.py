# obligations.py — Obligation Engine (Layer 1)
# Single source of truth for assignments, exams, worksheets, Coursera deadlines, etc.
# Separate from tasks.json and memory.json — own schema, own storage.
# Used by: executor.py (voice intake), portal_scan.py (screen read),
#           proactive_scheduler.py (Layer 2 reasoning)

import json
import os
import uuid
import datetime
import threading

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
OBLIGATIONS_FILE  = os.path.join(BASE_DIR, "obligations.json")

# Valid values — enforced on write, never silently coerced
VALID_TYPES    = {"assignment", "exam", "worksheet", "coursera", "other"}
VALID_STATUSES = {"pending", "in_progress", "done", "overdue"}
VALID_SOURCES  = {"manual", "voice", "portal_scan"}

_obligations: list = []
_lock = threading.Lock()


# ── Persistence ───────────────────────────────────────────────────────────────

def _load():
    global _obligations
    if os.path.exists(OBLIGATIONS_FILE):
        try:
            with open(OBLIGATIONS_FILE, "r", encoding="utf-8") as f:
                _obligations = json.load(f)
            print(f"[obligations] Loaded {len(_obligations)} obligation(s).")
        except Exception as e:
            print(f"[obligations] Load error — starting fresh. ({e})")
            _obligations = []
    else:
        _obligations = []
        _save()


def _save():
    try:
        with open(OBLIGATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_obligations, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[obligations] Save error: {e}")


# Load on module import
_load()


# ── Overdue auto-update ───────────────────────────────────────────────────────

def _refresh_overdue():
    """
    Mark any pending/in_progress obligation whose due_date has passed as overdue.
    Called lazily before any read operation — no background thread needed.
    """
    now     = datetime.datetime.now()
    changed = False
    for ob in _obligations:
        if ob.get("status") in ("pending", "in_progress"):
            due_str = ob.get("due_date", "")
            if due_str:
                try:
                    due_dt = datetime.datetime.fromisoformat(due_str)
                    if due_dt < now:
                        ob["status"] = "overdue"
                        changed = True
                except Exception:
                    pass
    if changed:
        _save()


# ── Core API ──────────────────────────────────────────────────────────────────

def add_obligation(
    title: str,
    ob_type: str = "other",
    due_date: str = "",
    status: str = "pending",
    source: str = "manual",
    notes: str = "",
) -> dict:
    """
    Add a new obligation.

    Args:
        title    : Human-readable name, e.g. "ANN assignment"
        ob_type  : One of VALID_TYPES
        due_date : ISO 8601 string or empty. e.g. "2025-07-04T23:59:00"
                   Pass empty string if unknown.
        status   : One of VALID_STATUSES (default "pending")
        source   : One of VALID_SOURCES (default "manual")
        notes    : Free-text extra info

    Returns:
        The newly created obligation dict.
    """
    if not title or not title.strip():
        raise ValueError("Obligation title cannot be empty.")

    ob_type = ob_type.lower().strip() if ob_type else "other"
    if ob_type not in VALID_TYPES:
        ob_type = "other"

    status = status.lower().strip() if status else "pending"
    if status not in VALID_STATUSES:
        status = "pending"

    source = source.lower().strip() if source else "manual"
    if source not in VALID_SOURCES:
        source = "manual"

    # Normalise due_date — try to parse and re-emit as ISO, store empty if unparseable
    normalised_due = ""
    if due_date and due_date.strip():
        try:
            from dateutil import parser as _dp
            parsed = _dp.parse(due_date.strip(), fuzzy=True)
            normalised_due = parsed.isoformat()
        except Exception:
            normalised_due = due_date.strip()   # store as-is if dateutil can't parse

    now_iso = datetime.datetime.now().isoformat()

    obligation = {
        "id":         str(uuid.uuid4())[:8],
        "title":      title.strip(),
        "type":       ob_type,
        "due_date":   normalised_due,
        "status":     status,
        "created_at": now_iso,
        "source":     source,
        "notes":      (notes or "").strip(),
    }

    with _lock:
        _obligations.append(obligation)
        _save()

    print(f"[obligations] Added: '{title}' | type={ob_type} | due={normalised_due or 'unset'} | source={source}")
    return obligation


def update_status(ob_id: str, new_status: str) -> bool:
    """
    Update the status of an obligation by id.
    Returns True if found and updated, False if not found.
    """
    new_status = (new_status or "").lower().strip()
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {VALID_STATUSES}.")

    with _lock:
        for ob in _obligations:
            if ob.get("id") == ob_id:
                ob["status"] = new_status
                _save()
                print(f"[obligations] Updated {ob_id} → status={new_status}")
                return True
    print(f"[obligations] update_status: id '{ob_id}' not found.")
    return False


def mark_done(ob_id: str) -> bool:
    """Convenience wrapper — mark obligation as done."""
    return update_status(ob_id, "done")


def get_all() -> list:
    """Return all obligations (copy), refreshing overdue status first."""
    with _lock:
        _refresh_overdue()
        return list(_obligations)


def get_pending() -> list:
    """Return obligations with status pending or in_progress."""
    with _lock:
        _refresh_overdue()
        return [ob for ob in _obligations if ob.get("status") in ("pending", "in_progress")]


def get_overdue() -> list:
    """Return obligations with status overdue."""
    with _lock:
        _refresh_overdue()
        return [ob for ob in _obligations if ob.get("status") == "overdue"]


def get_due_soon(days: int = 3) -> list:
    """
    Return pending/in_progress obligations due within the next N days.
    Excludes already-overdue ones (those have their own bucket).
    """
    with _lock:
        _refresh_overdue()
        now      = datetime.datetime.now()
        cutoff   = now + datetime.timedelta(days=days)
        result   = []
        for ob in _obligations:
            if ob.get("status") not in ("pending", "in_progress"):
                continue
            due_str = ob.get("due_date", "")
            if not due_str:
                continue
            try:
                due_dt = datetime.datetime.fromisoformat(due_str)
                if now <= due_dt <= cutoff:
                    result.append(ob)
            except Exception:
                pass
        # Sort by due_date ascending
        result.sort(key=lambda x: x.get("due_date", ""))
        return result


def get_by_id(ob_id: str) -> dict | None:
    """Return a single obligation by id, or None."""
    with _lock:
        for ob in _obligations:
            if ob.get("id") == ob_id:
                return dict(ob)
    return None


def delete_obligation(ob_id: str) -> bool:
    """Remove an obligation permanently. Returns True if found and deleted."""
    with _lock:
        before = len(_obligations)
        _obligations[:] = [ob for ob in _obligations if ob.get("id") != ob_id]
        if len(_obligations) < before:
            _save()
            print(f"[obligations] Deleted obligation id={ob_id}")
            return True
    print(f"[obligations] delete: id '{ob_id}' not found.")
    return False


# ── Formatting helpers (used by executor + proactive_scheduler) ───────────────

def format_obligation(ob: dict, include_id: bool = False) -> str:
    """
    Return a single-line human-readable string for one obligation.
    e.g. "ANN assignment — due Fri 04 Jul, pending"
    """
    title   = ob.get("title", "Unnamed")
    ob_type = ob.get("type", "other")
    status  = ob.get("status", "pending")
    due_str = ob.get("due_date", "")

    due_label = ""
    if due_str:
        try:
            due_dt    = datetime.datetime.fromisoformat(due_str)
            due_label = f" — due {due_dt.strftime('%a %d %b %H:%M')}"
        except Exception:
            due_label = f" — due {due_str}"

    id_label = f" [{ob.get('id')}]" if include_id else ""
    return f"{title} ({ob_type}){due_label}, {status}{id_label}"


def format_list(obs: list, include_id: bool = False) -> str:
    """
    Format a list of obligations into a numbered spoken-friendly string.
    Returns a fallback string if list is empty.
    """
    if not obs:
        return "Nothing here."
    lines = [f"{i+1}. {format_obligation(ob, include_id=include_id)}" for i, ob in enumerate(obs)]
    return "\n".join(lines)


def build_context_block() -> str:
    """
    Build a compact context string for LLM prompts (Layer 2).
    Groups by overdue / due soon / pending.
    """
    overdue  = get_overdue()
    due_soon = get_due_soon(days=3)
    pending  = get_pending()

    # due_soon is a subset of pending — remove duplicates for the "other pending" bucket
    due_soon_ids = {ob["id"] for ob in due_soon}
    overdue_ids  = {ob["id"] for ob in overdue}
    other_pending = [
        ob for ob in pending
        if ob["id"] not in due_soon_ids and ob["id"] not in overdue_ids
    ]

    parts = []
    if overdue:
        parts.append("OVERDUE:\n" + "\n".join(f"  - {format_obligation(ob)}" for ob in overdue))
    if due_soon:
        parts.append("DUE SOON (≤3 days):\n" + "\n".join(f"  - {format_obligation(ob)}" for ob in due_soon))
    if other_pending:
        parts.append("PENDING:\n" + "\n".join(f"  - {format_obligation(ob)}" for ob in other_pending))

    if not parts:
        return "No active obligations."

    return "\n\n".join(parts)