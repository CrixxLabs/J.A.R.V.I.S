# window_watcher.py — Real-time Windows state detector
# Watches windows appear, close, and change state so Jarvis knows what's
# actually happening — no fake sleep() timing.
#
# Public API:
#   list_windows()                                    → list of window dicts
#   find_window(title_pattern)                        → window dict or None
#   wait_for_window(title_pattern, timeout=30)        → window dict or None
#   wait_for_window_close(title_pattern, timeout=30)  → bool
#   is_window_open(title_pattern)                     → bool
#   focus_window(title_pattern)                       → bool
#   get_active_window()                               → window dict or None
#   wait_for_window_stable(title_pattern, ...)        → window dict or None
#   detect_state_change(title_pattern, ...)           → str state
#
# Uses pygetwindow (simple) + win32gui (advanced) as fallback.

import time
import re
from typing import Optional, Callable

try:
    import pygetwindow as gw
    _PYGETWINDOW_AVAILABLE = True
except ImportError:
    gw = None
    _PYGETWINDOW_AVAILABLE = False
    print("[window_watcher] ⚠ pygetwindow not installed — run: pip install pygetwindow")

try:
    import win32gui
    import win32process
    import win32con
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False
    print("[window_watcher] ⚠ pywin32 not installed — run: pip install pywin32")


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# Poll interval — how often to check window state (seconds)
_POLL_INTERVAL = 0.3

# How long a window must stay unchanged to be considered "stable"
_STABILITY_DURATION = 1.5


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW ENUMERATION
# ══════════════════════════════════════════════════════════════════════════════

def _get_window_info(hwnd) -> dict:
    """Extract useful info from a Windows HWND."""
    try:
        title = win32gui.GetWindowText(hwnd)
        rect  = win32gui.GetWindowRect(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return {
            "hwnd":    hwnd,
            "title":   title,
            "pid":     pid,
            "x":       rect[0],
            "y":       rect[1],
            "width":   rect[2] - rect[0],
            "height":  rect[3] - rect[1],
            "visible": win32gui.IsWindowVisible(hwnd),
        }
    except Exception:
        return {}


def list_windows(visible_only: bool = True) -> list:
    """
    Get all top-level windows.
    Returns list of dicts with hwnd, title, pid, position, size, visible.
    """
    if not _WIN32_AVAILABLE:
        return []

    windows = []

    def _enum_callback(hwnd, results):
        info = _get_window_info(hwnd)
        if not info:
            return True
        if visible_only and not info.get("visible"):
            return True
        if visible_only and not info.get("title"):
            return True
        results.append(info)
        return True

    try:
        win32gui.EnumWindows(_enum_callback, windows)
    except Exception as exc:
        print(f"[window_watcher] enum error: {exc}")

    return windows


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW FINDING
# ══════════════════════════════════════════════════════════════════════════════

def _match_title(title: str, pattern: str) -> bool:
    """
    Match a window title against a pattern.
    Pattern can be:
      - Plain substring (case-insensitive)
      - Regex if wrapped in / /  (e.g. "/spotify.*/i")
    """
    if not title or not pattern:
        return False

    # Check if pattern is regex format /pattern/flags
    regex_match = re.match(r"^/(.+)/([gimsx]*)$", pattern)
    if regex_match:
        try:
            regex_pat = regex_match.group(1)
            flags = 0
            if "i" in regex_match.group(2):
                flags |= re.IGNORECASE
            return bool(re.search(regex_pat, title, flags))
        except re.error:
            pass

    # Plain substring match (case-insensitive)
    return pattern.lower() in title.lower()


def find_window(title_pattern: str) -> Optional[dict]:
    """
    Find the first window matching the title pattern.
    Returns window dict or None.
    """
    windows = list_windows(visible_only=True)
    for win in windows:
        if _match_title(win.get("title", ""), title_pattern):
            return win
    return None


def find_all_windows(title_pattern: str) -> list:
    """Find all windows matching the title pattern."""
    windows = list_windows(visible_only=True)
    return [w for w in windows if _match_title(w.get("title", ""), title_pattern)]


def is_window_open(title_pattern: str) -> bool:
    """Quick check: is a matching window currently open?"""
    return find_window(title_pattern) is not None


# ══════════════════════════════════════════════════════════════════════════════
# WAIT FOR WINDOW APPEAR
# ══════════════════════════════════════════════════════════════════════════════

def wait_for_window(
    title_pattern: str,
    timeout: float = 30.0,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> Optional[dict]:
    """
    Block until a window matching the pattern appears, or timeout.

    Args:
        title_pattern:     substring or /regex/i to match window title
        timeout:           seconds to wait before giving up
        progress_callback: optional function called with elapsed seconds each poll

    Returns:
        window dict if found, None if timed out
    """
    if not _WIN32_AVAILABLE:
        print("[window_watcher] can't wait — win32 not available")
        return None

    start = time.time()
    last_callback = 0

    while time.time() - start < timeout:
        win = find_window(title_pattern)
        if win:
            elapsed = time.time() - start
            print(f"[window_watcher] ✓ found '{win['title'][:40]}' after {elapsed:.1f}s")
            return win

        elapsed = time.time() - start
        if progress_callback and (elapsed - last_callback) >= 1.0:
            last_callback = elapsed
            try:
                progress_callback(elapsed)
            except Exception:
                pass

        time.sleep(_POLL_INTERVAL)

    print(f"[window_watcher] ✗ '{title_pattern}' didn't appear within {timeout}s")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# WAIT FOR WINDOW CLOSE
# ══════════════════════════════════════════════════════════════════════════════

def wait_for_window_close(title_pattern: str, timeout: float = 30.0) -> bool:
    """
    Block until a matching window closes, or timeout.
    Returns True if window closed, False if timed out.
    """
    if not _WIN32_AVAILABLE:
        return False

    start = time.time()

    while time.time() - start < timeout:
        if not is_window_open(title_pattern):
            print(f"[window_watcher] ✓ '{title_pattern}' closed")
            return True
        time.sleep(_POLL_INTERVAL)

    return False


# ══════════════════════════════════════════════════════════════════════════════
# WAIT FOR WINDOW STABILITY (window opens AND stops changing)
# ══════════════════════════════════════════════════════════════════════════════

def wait_for_window_stable(
    title_pattern: str,
    timeout: float = 30.0,
    stability_duration: float = None,
) -> Optional[dict]:
    """
    Wait for a window to appear AND remain stable (unchanged) for a period.

    This is critical for login automation: an app might show a splash screen,
    then a loading screen, then the actual login screen. We want to wait until
    the window is DONE changing before interacting with it.

    Args:
        title_pattern:      pattern to match
        timeout:            max seconds to wait for window to appear + stabilize
        stability_duration: seconds window must stay unchanged (default: 1.5s)

    Returns:
        window dict when stable, None if timeout
    """
    if stability_duration is None:
        stability_duration = _STABILITY_DURATION

    # Step 1: Wait for window to appear
    win = wait_for_window(title_pattern, timeout=timeout)
    if not win:
        return None

    # Step 2: Wait for it to stabilize
    start = time.time()
    remaining = timeout - (start - (start - _POLL_INTERVAL))

    last_snapshot = _snapshot_window(win["hwnd"])
    stable_since  = time.time()

    while time.time() - start < remaining:
        current = _snapshot_window(win["hwnd"])

        if current is None:
            # Window vanished — fail
            print(f"[window_watcher] ✗ window vanished while stabilizing")
            return None

        if current != last_snapshot:
            # Window changed — reset stability timer
            last_snapshot = current
            stable_since = time.time()
        elif time.time() - stable_since >= stability_duration:
            # Been stable long enough
            print(f"[window_watcher] ✓ window stabilized after {time.time() - start:.1f}s")
            # Return fresh info
            return _get_window_info(win["hwnd"])

        time.sleep(_POLL_INTERVAL)

    # Timed out but window still open — return what we have
    print(f"[window_watcher] ⚠ window never fully stabilized, returning anyway")
    return _get_window_info(win["hwnd"])


def _snapshot_window(hwnd) -> Optional[tuple]:
    """
    Take a snapshot of a window's state for comparison.
    Returns (title, x, y, width, height) tuple or None if window gone.
    """
    try:
        if not win32gui.IsWindow(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd)
        rect  = win32gui.GetWindowRect(hwnd)
        return (title, rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE WINDOW / FOCUS
# ══════════════════════════════════════════════════════════════════════════════

def get_active_window() -> Optional[dict]:
    """Get the currently focused window."""
    if not _WIN32_AVAILABLE:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            return _get_window_info(hwnd)
    except Exception:
        pass
    return None


def focus_window(title_pattern: str) -> bool:
    """
    Bring a window to foreground.
    Returns True if successful, False otherwise.
    """
    if not _WIN32_AVAILABLE:
        return False

    win = find_window(title_pattern)
    if not win:
        return False

    try:
        hwnd = win["hwnd"]
        # If minimized, restore
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Bring to front
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)  # small delay for focus to take effect
        return True
    except Exception as exc:
        print(f"[window_watcher] focus error: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# STATE CHANGE DETECTION (for login screen → dashboard transitions)
# ══════════════════════════════════════════════════════════════════════════════

def detect_state_change(
    title_pattern: str,
    initial_snapshot: tuple = None,
    timeout: float = 15.0,
    change_threshold: float = 1.0,
) -> str:
    """
    Watch a window for state changes.

    Useful for detecting when Spotify goes from login screen → main dashboard,
    which usually involves a title change or significant resize.

    Args:
        title_pattern:    window to watch
        initial_snapshot: tuple from _snapshot_window() to compare against
        timeout:          max seconds to watch
        change_threshold: seconds change must persist to count as real

    Returns:
        "changed"    if window state changed and persisted
        "closed"     if window closed
        "unchanged"  if no significant change within timeout
        "not_found"  if window not found
    """
    win = find_window(title_pattern)
    if not win:
        return "not_found"

    hwnd = win["hwnd"]

    if initial_snapshot is None:
        initial_snapshot = _snapshot_window(hwnd)
        if initial_snapshot is None:
            return "not_found"

    start           = time.time()
    change_detected = None

    while time.time() - start < timeout:
        current = _snapshot_window(hwnd)

        if current is None:
            return "closed"

        if current != initial_snapshot:
            if change_detected is None:
                change_detected = time.time()
            elif time.time() - change_detected >= change_threshold:
                return "changed"
        else:
            change_detected = None

        time.sleep(_POLL_INTERVAL)

    return "unchanged"


def get_window_snapshot(title_pattern: str) -> Optional[tuple]:
    """
    Get a snapshot of a window's current state.
    Use with detect_state_change() to compare states over time.
    """
    win = find_window(title_pattern)
    if not win:
        return None
    return _snapshot_window(win["hwnd"])


# ══════════════════════════════════════════════════════════════════════════════
# TEST MODE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 60)
    print("WINDOW WATCHER — Test Mode")
    print("═" * 60)

    # Test 1: Backend availability
    print("\n[TEST 1] Backend availability")
    print(f"  pygetwindow: {_PYGETWINDOW_AVAILABLE}")
    print(f"  win32:       {_WIN32_AVAILABLE}")

    if not _WIN32_AVAILABLE:
        print("  ⚠ Fix pywin32 first — stopping tests")
        exit(1)

    # Test 2: List all windows
    print("\n[TEST 2] List visible windows (first 10)")
    windows = list_windows(visible_only=True)
    print(f"  Total visible: {len(windows)}")
    for i, win in enumerate(windows[:10]):
        print(f"  {i+1}. [{win['pid']}] {win['title'][:60]}")

    # Test 3: Get active window
    print("\n[TEST 3] Currently active window")
    active = get_active_window()
    if active:
        print(f"  Title: {active['title']}")
        print(f"  PID:   {active['pid']}")
        print(f"  Size:  {active['width']}x{active['height']}")

    # Test 4: Find a specific window (try common ones)
    print("\n[TEST 4] Find common windows")
    for pattern in ["notepad", "chrome", "brave", "vs code", "visual studio", "explorer", "task manager"]:
        found = find_window(pattern)
        marker = "✓" if found else "✗"
        title = found["title"][:50] if found else "not found"
        print(f"  {marker} '{pattern}' → {title}")

    # Test 5: Regex matching
    print("\n[TEST 5] Regex title matching")
    found = find_window("/.*\\.py.*/")
    if found:
        print(f"  Regex '/.*\\.py.*/': {found['title'][:60]}")
    else:
        print(f"  Regex '/.*\\.py.*/': no match")

    # Test 6: Interactive test — open notepad
    print("\n[TEST 6] LIVE TEST — open Notepad")
    print("  Opening notepad in 2 seconds...")
    time.sleep(2)

    import subprocess
    subprocess.Popen(["notepad.exe"])

    print("  Waiting for notepad window (max 10s)...")
    def _progress(elapsed):
        print(f"    ... still waiting ({elapsed:.1f}s)")

    notepad = wait_for_window("notepad", timeout=10, progress_callback=_progress)
    if notepad:
        print(f"  ✓ Notepad appeared: {notepad['title']}")

        # Test stability wait
        print("\n[TEST 7] Wait for notepad to stabilize")
        stable = wait_for_window_stable("notepad", timeout=5)
        if stable:
            print(f"  ✓ Stabilized")

        # Test focus
        print("\n[TEST 8] Focus notepad")
        if focus_window("notepad"):
            print("  ✓ Focused")
        active_after = get_active_window()
        if active_after:
            print(f"  Now active: {active_after['title'][:50]}")

        # Test close detection
        print("\n[TEST 9] Close notepad manually within 15s to test close detection")
        print("  (waiting for you to close it...)")
        closed = wait_for_window_close("notepad", timeout=15)
        if closed:
            print("  ✓ Detected notepad close")
        else:
            print("  ⚠ Timeout — didn't close it in time")

    else:
        print(f"  ✗ Notepad didn't appear")

    print("\n" + "═" * 60)
    print("Tests done.")
    print("═" * 60)