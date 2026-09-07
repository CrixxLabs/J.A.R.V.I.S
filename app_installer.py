# app_installer.py — Real-time Windows app installer
# Uses winget (built into Windows 11) to install apps intelligently.
# Reports progress via callback so Jarvis can speak about what's happening.
#
# Public API:
#   check_winget_available()           → bool
#   search_app(query)                  → list of matching apps
#   is_app_installed(app_id_or_name)   → bool
#   install_app(app_id, progress_cb)   → dict {success, message, app_id}
#   uninstall_app(app_id)              → dict {success, message}
#
# Design principles:
# - Never fake progress with sleep()
# - Read winget output line-by-line for real events
# - Report every meaningful state change via callback
# - Fail loudly with helpful error messages

import subprocess
import re
import threading
import time
from typing import Callable, Optional


# ══════════════════════════════════════════════════════════════════════════════
# WINGET AVAILABILITY CHECK
# ══════════════════════════════════════════════════════════════════════════════

_winget_checked = False
_winget_available = False


def check_winget_available() -> bool:
    """Check if winget is installed on this system. Cached after first call."""
    global _winget_checked, _winget_available

    if _winget_checked:
        return _winget_available

    _winget_checked = True

    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            _winget_available = True
            print(f"[app_installer] ✓ winget available: {result.stdout.strip()}")
        else:
            print(f"[app_installer] ✗ winget returned non-zero")
    except FileNotFoundError:
        print(f"[app_installer] ✗ winget not found on this system")
    except subprocess.TimeoutExpired:
        print(f"[app_installer] ✗ winget check timed out")
    except Exception as exc:
        print(f"[app_installer] ✗ winget check error: {exc}")

    return _winget_available


# ══════════════════════════════════════════════════════════════════════════════
# APP SEARCH
# ══════════════════════════════════════════════════════════════════════════════

# Known app aliases → winget IDs (for common apps users ask by name)
_APP_ALIASES = {
    "spotify":        "Spotify.Spotify",
    "discord":        "Discord.Discord",
    "chrome":         "Google.Chrome",
    "google chrome":  "Google.Chrome",
    "firefox":        "Mozilla.Firefox",
    "brave":          "Brave.Brave",
    "vscode":         "Microsoft.VisualStudioCode",
    "vs code":        "Microsoft.VisualStudioCode",
    "visual studio code": "Microsoft.VisualStudioCode",
    "notion":         "Notion.Notion",
    "slack":          "SlackTechnologies.Slack",
    "zoom":           "Zoom.Zoom",
    "obs":            "OBSProject.OBSStudio",
    "obs studio":     "OBSProject.OBSStudio",
    "vlc":            "VideoLAN.VLC",
    "7zip":           "7zip.7zip",
    "7 zip":          "7zip.7zip",
    "notepad++":      "Notepad++.Notepad++",
    "notepadplusplus":"Notepad++.Notepad++",
    "git":            "Git.Git",
    "python":         "Python.Python.3.12",
    "node":           "OpenJS.NodeJS",
    "nodejs":         "OpenJS.NodeJS",
    "node.js":        "OpenJS.NodeJS",
    "postman":        "Postman.Postman",
    "figma":          "Figma.Figma",
    "gimp":           "GIMP.GIMP",
    "blender":        "BlenderFoundation.Blender",
    "audacity":       "Audacity.Audacity",
    "steam":          "Valve.Steam",
    "epic games":     "EpicGames.EpicGamesLauncher",
    "epic":           "EpicGames.EpicGamesLauncher",
    "telegram":       "Telegram.TelegramDesktop",
    "whatsapp":       "9NKSQGP7F2NH",   # Store ID for WhatsApp
    "microsoft edge": "Microsoft.Edge",
    "edge":           "Microsoft.Edge",
    "powertoys":      "Microsoft.PowerToys",
}


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def resolve_app_id(query: str) -> Optional[str]:
    """
    Try to resolve a friendly name to a winget ID via known aliases.
    Returns None if no alias found (caller should try search_app).
    """
    normalized = _normalize_name(query)
    return _APP_ALIASES.get(normalized)


def search_app(query: str, max_results: int = 5) -> list:
    """
    Search winget for apps matching query.
    Returns list of dicts: [{name, id, version, source}, ...]
    Empty list if none found or search fails.
    """
    if not check_winget_available():
        return []

    if not (query or "").strip():
        return []

    try:
        result = subprocess.run(
            ["winget", "search", query.strip(), "--accept-source-agreements"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        print(f"[app_installer] search timed out for: {query}")
        return []
    except Exception as exc:
        print(f"[app_installer] search error: {exc}")
        return []

    if result.returncode != 0:
        # winget returns non-zero when no matches found
        return []

    output = result.stdout or ""
    lines = output.splitlines()

    # Find the header line (contains "Name" and "Id")
    header_idx = -1
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line and "Version" in line:
            header_idx = i
            break

    if header_idx == -1:
        return []

    # Get column positions from the separator line
    if header_idx + 1 >= len(lines):
        return []

    header       = lines[header_idx]
    name_col     = header.index("Name")
    id_col       = header.index("Id")
    version_col  = header.index("Version")
    source_col   = header.index("Source") if "Source" in header else len(header)

    results = []
    for line in lines[header_idx + 2:]:  # skip header + separator
        if not line.strip():
            continue
        if len(line) < id_col + 1:
            continue

        try:
            name    = line[name_col:id_col].strip()
            app_id  = line[id_col:version_col].strip()
            version = line[version_col:source_col].strip()
            source  = line[source_col:].strip() if source_col < len(line) else ""

            if name and app_id:
                results.append({
                    "name":    name,
                    "id":      app_id,
                    "version": version,
                    "source":  source,
                })

            if len(results) >= max_results:
                break
        except Exception:
            continue

    return results


# ══════════════════════════════════════════════════════════════════════════════
# INSTALL STATUS CHECK
# ══════════════════════════════════════════════════════════════════════════════

def is_app_installed(app_id_or_name: str) -> bool:
    """
    Check if an app is already installed via winget.
    Accepts either winget ID or friendly name.
    """
    if not check_winget_available():
        return False

    query = app_id_or_name.strip()

    # Try to resolve alias to ID
    resolved = resolve_app_id(query)
    if resolved:
        query = resolved

    try:
        result = subprocess.run(
            ["winget", "list", "--id", query, "--exact"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # winget list returns 0 if found, non-zero if not
        if result.returncode == 0 and query.lower() in (result.stdout or "").lower():
            return True

        # Fallback: search without --exact
        result = subprocess.run(
            ["winget", "list", query],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            output = (result.stdout or "").lower()
            # Look for the app name/id in output (skip header lines)
            lines = output.splitlines()
            for line in lines:
                if query.lower() in line and "no installed package" not in line:
                    return True

        return False

    except Exception as exc:
        print(f"[app_installer] is_app_installed error: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# INSTALL WITH REAL-TIME PROGRESS
# ══════════════════════════════════════════════════════════════════════════════

# Winget output patterns to detect state changes
_PATTERN_DOWNLOAD_START = re.compile(r"downloading", re.IGNORECASE)
_PATTERN_DOWNLOAD_PROGRESS = re.compile(r"(\d+)\s*%")
_PATTERN_INSTALL_START = re.compile(r"installing|starting package install", re.IGNORECASE)
_PATTERN_SUCCESS = re.compile(r"successfully installed", re.IGNORECASE)
_PATTERN_ALREADY_INSTALLED = re.compile(r"already installed|no available upgrade", re.IGNORECASE)
_PATTERN_NOT_FOUND = re.compile(r"no package found|no application found", re.IGNORECASE)
_PATTERN_LICENSE = re.compile(r"license.*must be accepted|agreement", re.IGNORECASE)
_PATTERN_ERROR = re.compile(r"failed|error|abandoned", re.IGNORECASE)


def install_app(
    app_id_or_name: str,
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """
    Install an app via winget with real-time progress reporting.

    Args:
        app_id_or_name: winget ID (e.g. "Spotify.Spotify") or friendly name (e.g. "spotify")
        progress_callback: function called with (event_type, data) as install progresses
            event_type is one of:
              "resolving"    → looking up the app
              "starting"     → beginning install
              "downloading"  → download in progress (data has "percent")
              "installing"   → install in progress
              "success"      → successfully installed
              "failed"       → install failed (data has "reason")
              "already"      → already installed

    Returns:
        dict: {
            "success": bool,
            "message": str (human-readable),
            "app_id":  str (resolved winget ID),
            "already_installed": bool,
        }
    """
    def _emit(event: str, data: dict = None):
        if progress_callback:
            try:
                progress_callback(event, data or {})
            except Exception as cb_exc:
                print(f"[app_installer] callback error: {cb_exc}")

    if not check_winget_available():
        return {
            "success": False,
            "message": "Windows app installer (winget) isn't available on this system.",
            "app_id":  "",
            "already_installed": False,
        }

    query = (app_id_or_name or "").strip()
    if not query:
        return {
            "success": False,
            "message": "No app name provided.",
            "app_id":  "",
            "already_installed": False,
        }

    # ── Step 1: Resolve name to winget ID ────────────────────────────────────
    _emit("resolving", {"query": query})

    resolved_id = resolve_app_id(query)
    if not resolved_id:
        # Try search to find best match
        search_results = search_app(query, max_results=3)
        if search_results:
            # Take the first match if it looks reasonable
            resolved_id = search_results[0]["id"]
            print(f"[app_installer] search matched '{query}' → {resolved_id}")
        else:
            return {
                "success": False,
                "message": f"Couldn't find an app called '{query}' in Windows' app catalog.",
                "app_id":  "",
                "already_installed": False,
            }

    # ── Step 2: Check if already installed ───────────────────────────────────
    if is_app_installed(resolved_id):
        _emit("already", {"app_id": resolved_id})
        return {
            "success": True,
            "message": f"{query} is already installed.",
            "app_id":  resolved_id,
            "already_installed": True,
        }

    # ── Step 3: Run winget install with streaming output ─────────────────────
    _emit("starting", {"app_id": resolved_id})

    cmd = [
        "winget", "install",
        "--id", resolved_id,
        "--exact",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--silent",
        "--disable-interactivity",
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        _emit("failed", {"reason": str(exc)})
        return {
            "success": False,
            "message": f"Couldn't start the installer: {exc}",
            "app_id":  resolved_id,
            "already_installed": False,
        }

    # ── Step 4: Stream output, detect state changes ──────────────────────────
    last_state       = "starting"
    last_percent     = -1
    last_progress_at = time.time()
    all_output       = []
    detected_error   = None
    stall_timeout    = 120   # kill if no output for 2 minutes

    try:
        while True:
            line = process.stdout.readline()

            if not line:
                # Process may have exited — check
                if process.poll() is not None:
                    break
                # Check for stall
                if time.time() - last_progress_at > stall_timeout:
                    print(f"[app_installer] install stalled — killing process")
                    process.kill()
                    _emit("failed", {"reason": "Install stalled (no progress for 2 minutes)"})
                    return {
                        "success": False,
                        "message": f"The {query} installer got stuck. Try again later.",
                        "app_id":  resolved_id,
                        "already_installed": False,
                    }
                continue

            line = line.strip()
            if not line:
                continue

            all_output.append(line)
            last_progress_at = time.time()

            # ── Detect state changes ──────────────────────────────────────────

            # Download progress
            if _PATTERN_DOWNLOAD_START.search(line) and last_state != "downloading":
                last_state = "downloading"
                _emit("downloading", {"percent": 0})
                print(f"[app_installer] state → downloading")

            # Percentage update (only report every 25% jump to avoid spam)
            percent_match = _PATTERN_DOWNLOAD_PROGRESS.search(line)
            if percent_match:
                try:
                    percent = int(percent_match.group(1))
                    if percent - last_percent >= 25 or percent == 100:
                        last_percent = percent
                        _emit("downloading", {"percent": percent})
                except Exception:
                    pass

            # Install phase
            if _PATTERN_INSTALL_START.search(line) and last_state != "installing":
                last_state = "installing"
                _emit("installing", {})
                print(f"[app_installer] state → installing")

            # Success
            if _PATTERN_SUCCESS.search(line):
                last_state = "success"

            # Already installed
            if _PATTERN_ALREADY_INSTALLED.search(line):
                last_state = "already"

            # Errors
            if _PATTERN_ERROR.search(line) and not _PATTERN_SUCCESS.search(line):
                detected_error = line

    except Exception as exc:
        print(f"[app_installer] stream read error: {exc}")

    # ── Step 5: Wait for process to finish ───────────────────────────────────
    try:
        return_code = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        return_code = -1

    full_output = "\n".join(all_output)

    # ── Step 6: Interpret final result ───────────────────────────────────────

    if last_state == "already":
        _emit("already", {"app_id": resolved_id})
        return {
            "success": True,
            "message": f"{query} was already installed.",
            "app_id":  resolved_id,
            "already_installed": True,
        }

    if return_code == 0 and last_state == "success":
        _emit("success", {"app_id": resolved_id})
        return {
            "success": True,
            "message": f"{query} installed successfully.",
            "app_id":  resolved_id,
            "already_installed": False,
        }

    # Something went wrong
    error_msg = detected_error or "Install failed for an unknown reason."

    # Try to give useful error messages
    if _PATTERN_LICENSE.search(full_output):
        error_msg = f"{query} requires you to accept a license agreement. Try installing it manually."
    elif _PATTERN_NOT_FOUND.search(full_output):
        error_msg = f"Couldn't find {query} in the app catalog."
    elif "administrator" in full_output.lower() or "elevat" in full_output.lower():
        error_msg = f"{query} needs admin rights to install. Right-click Jarvis and run as administrator."
    elif "network" in full_output.lower() or "internet" in full_output.lower():
        error_msg = f"Couldn't download {query} — check your internet connection."
    elif return_code != 0:
        error_msg = f"Install failed with error code {return_code}. Check the console for details."

    _emit("failed", {"reason": error_msg, "output": full_output[-500:]})

    print(f"[app_installer] install failed. Full output:\n{full_output[-1000:]}")

    return {
        "success": False,
        "message": error_msg,
        "app_id":  resolved_id,
        "already_installed": False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# UNINSTALL
# ══════════════════════════════════════════════════════════════════════════════

def uninstall_app(app_id_or_name: str) -> dict:
    """Uninstall an app via winget. Simple version — no progress tracking."""
    if not check_winget_available():
        return {"success": False, "message": "winget not available."}

    query = (app_id_or_name or "").strip()
    resolved_id = resolve_app_id(query) or query

    try:
        result = subprocess.run(
            ["winget", "uninstall", "--id", resolved_id, "--exact", "--silent"],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return {"success": True, "message": f"Uninstalled {query}."}
        return {"success": False, "message": f"Uninstall failed: {result.stderr.strip()[:200]}"}
    except Exception as exc:
        return {"success": False, "message": f"Uninstall error: {exc}"}


# ══════════════════════════════════════════════════════════════════════════════
# TEST MODE (run this file directly to test)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 60)
    print("APP INSTALLER — Test Mode")
    print("═" * 60)

    # Test 1: winget available?
    print("\n[TEST 1] winget availability")
    print(f"  Result: {check_winget_available()}")

    # Test 2: search for spotify
    print("\n[TEST 2] search for 'spotify'")
    results = search_app("spotify", max_results=3)
    for r in results:
        print(f"  {r['name']} | {r['id']} | v{r['version']}")

    # Test 3: is spotify installed?
    print("\n[TEST 3] is Spotify installed?")
    installed = is_app_installed("Spotify.Spotify")
    print(f"  Result: {installed}")

    # Test 4: alias resolution
    print("\n[TEST 4] alias resolution")
    for name in ["spotify", "vs code", "chrome", "unknown_app"]:
        resolved = resolve_app_id(name)
        print(f"  '{name}' → {resolved}")

    # Test 5: install a small safe app (optional — uncomment to test)
    # print("\n[TEST 5] install test (7zip is small and safe)")
    # def _progress(event, data):
    #     print(f"  [{event}] {data}")
    # result = install_app("7zip", progress_callback=_progress)
    # print(f"  Final: {result}")

    print("\n" + "═" * 60)
    print("Tests done. If everything shows results, we're good.")
    print("═" * 60)