# auto_login_profiles.py — Per-app login flow recipes
# Each app has its own quirks — different login screens, field positions,
# button labels. This file stores the "recipe" for each supported app.
#
# Design philosophy:
# - Never hardcode timing (use window_watcher for state detection)
# - Every step returns success/failure so orchestrator can adapt
# - Profiles are data, not code — easy to add new apps
#
# Public API:
#   get_profile(app_name)               → profile dict or None
#   is_supported(app_name)              → bool
#   list_supported_apps()               → list of app names
#   get_launch_command(app_name)        → path/command to launch app
#   get_window_pattern(app_name)        → pattern for window_watcher
#   get_login_steps(app_name)           → list of login steps
#   get_success_indicator(app_name)     → how to verify login succeeded

import os
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
# PROFILE SCHEMA
# ══════════════════════════════════════════════════════════════════════════════
#
# Each profile is a dict with these keys:
#
# {
#   "display_name":   str        — user-friendly name ("Spotify")
#   "winget_id":      str        — winget package ID for auto-install
#   "launch":         dict       — how to launch the app
#     "type":  "exe" | "start_menu" | "protocol" | "path"
#     "value": str               — the command/path
#   "window_pattern": str        — pattern to match app window (window_watcher.py syntax)
#   "login_detection": dict      — how to detect if login screen is showing
#     "type":  "window_title" | "wait_time" | "always"
#     "value": str/int
#   "login_steps":    list       — ordered steps to complete login
#     Each step: {"action": str, "value": str, "wait_after": float}
#   "success_indicator": dict    — how to verify login succeeded
#     "type":  "window_title_contains" | "window_title_changes" | "new_window"
#     "value": str
#   "post_login_wait": float     — seconds to wait after login for UI to settle
#   "notes":          str        — human notes about this app's quirks
# }
#
# Available actions in login_steps:
#   - "wait":            wait N seconds (use sparingly, prefer window_watcher)
#   - "wait_for_window": wait until a window matching pattern appears
#   - "focus_window":    bring app window to foreground
#   - "click":           click at coords {x, y} or "current"
#   - "type_text":       type literal text (use for username)
#   - "type_username":   type saved username from vault
#   - "type_password":   type saved password from vault
#   - "press_key":       press a specific key (tab, enter, escape, etc.)
#   - "hotkey":          press key combo like ctrl+a
#   - "keyboard_shortcut": alias for hotkey
#
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# SPOTIFY PROFILE
# ══════════════════════════════════════════════════════════════════════════════

_SPOTIFY = {
    "display_name": "Spotify",
    "winget_id":    "Spotify.Spotify",

    "launch": {
        "type":  "path",
        # Spotify installs to per-user AppData
        "value": os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
    },

    # Spotify window title is just "Spotify" when logged out,
    # becomes "Spotify Premium" or "Spotify Free" when logged in
    "window_pattern": "Spotify",

    "login_detection": {
        "type":  "window_title",
        "value": "Spotify",   # if title is exactly "Spotify", likely login screen
    },

    # Spotify login flow (as of 2025):
    # 1. Wait for window to appear + stabilize
    # 2. Focus the window
    # 3. Small wait for login form to render
    # 4. Tab a few times to skip "Continue with Google/Facebook/Apple" buttons
    #    (varies — safer to click the email field if we know coords, but tabbing works)
    # 5. Actually — Spotify's login is browser-embedded, so we need to be careful
    #    The safest approach: click into the email field, then tab to password
    #
    # For first attempt, we use keyboard-only navigation:
    "login_steps": [
        {
            "action":     "wait_for_window",
            "value":      "Spotify",
            "timeout":    30,
            "wait_after": 0,
        },
        {
            "action":     "wait_for_stable",
            "value":      "Spotify",
            "timeout":    15,
            "wait_after": 1.5,   # extra time for login form to fully render
        },
        {
            "action":     "focus_window",
            "value":      "Spotify",
            "wait_after": 0.5,
        },
        # Spotify sometimes needs a click to activate the email field
        # We'll click near the center of the window to focus the login area
        {
            "action":     "click_window_center",
            "value":      "Spotify",
            "wait_after": 0.5,
        },
        # Now tab to the first input field (email/username)
        # Spotify's login has: [Google btn] [Facebook btn] [Apple btn] [email input] [password input] [Login btn]
        # We need enough tabs to reach email — usually 4-5 tabs depending on layout
        {
            "action":     "press_key",
            "value":      "tab",
            "wait_after": 0.15,
        },
        {
            "action":     "press_key",
            "value":      "tab",
            "wait_after": 0.15,
        },
        {
            "action":     "press_key",
            "value":      "tab",
            "wait_after": 0.15,
        },
        {
            "action":     "press_key",
            "value":      "tab",
            "wait_after": 0.15,
        },
        {
            "action":     "type_username",
            "value":      "",     # pulled from vault
            "wait_after": 0.3,
        },
        {
            "action":     "press_key",
            "value":      "tab",
            "wait_after": 0.2,
        },
        {
            "action":     "type_password",
            "value":      "",     # pulled from vault
            "wait_after": 0.3,
        },
        {
            "action":     "press_key",
            "value":      "enter",
            "wait_after": 0.5,
        },
    ],

    "success_indicator": {
        # After login, Spotify's title usually changes to include "Premium" or "Free"
        # or shows a song name / "Spotify - <song>"
        "type":  "window_title_changes",
        "value": "Spotify",   # base title before login
        "timeout": 15,
    },

    "post_login_wait": 2.0,

    "notes": (
        "Spotify login is embedded webview — tab navigation can be fragile "
        "if Spotify updates their login UI. If login fails consistently, "
        "the tab count might need adjustment. Manual login fallback recommended."
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# FUTURE PROFILES (skeleton — to be filled in Session 3)
# ══════════════════════════════════════════════════════════════════════════════

_DISCORD = {
    "display_name": "Discord",
    "winget_id":    "Discord.Discord",
    "launch": {
        "type":  "path",
        "value": os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe --processStart Discord.exe"),
    },
    "window_pattern": "Discord",
    "login_detection": {
        "type":  "window_title",
        "value": "Discord",
    },
    "login_steps": [
        # TODO: Add Discord login flow
        # Discord has: email field → password field → login button
        # Sometimes shows captcha (must skip auto-login if detected)
    ],
    "success_indicator": {
        "type":  "window_title_changes",
        "value": "Discord",
        "timeout": 20,
    },
    "post_login_wait": 3.0,
    "notes": "Discord uses captcha frequently — auto-login may need manual completion.",
}


_VSCODE = {
    "display_name": "Visual Studio Code",
    "winget_id":    "Microsoft.VisualStudioCode",
    "launch": {
        "type":  "path",
        "value": os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
    },
    "window_pattern": "Visual Studio Code",
    "login_detection": {
        # VS Code doesn't require login — sign-in is optional for sync
        "type":  "always",
        "value": False,
    },
    "login_steps": [],
    "success_indicator": {
        "type":  "window_title_contains",
        "value": "Visual Studio Code",
        "timeout": 10,
    },
    "post_login_wait": 0,
    "notes": "VS Code doesn't require login. Sign-in for sync is optional and uses browser OAuth.",
}


# ══════════════════════════════════════════════════════════════════════════════
# PROFILE REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

_PROFILES = {
    "spotify": _SPOTIFY,
    "discord": _DISCORD,
    "vscode":  _VSCODE,
    "vs code": _VSCODE,   # alias
    "visual studio code": _VSCODE,   # alias
}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def _normalize(app_name: str) -> str:
    return (app_name or "").strip().lower()


def is_supported(app_name: str) -> bool:
    """Check if we have a login profile for this app."""
    return _normalize(app_name) in _PROFILES


def list_supported_apps() -> list:
    """List all apps we have login profiles for."""
    # Dedupe display names (since we have aliases)
    seen = set()
    result = []
    for profile in _PROFILES.values():
        name = profile.get("display_name", "")
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return sorted(result)


def get_profile(app_name: str) -> Optional[dict]:
    """Get the full profile dict for an app."""
    return _PROFILES.get(_normalize(app_name))


def get_launch_command(app_name: str) -> Optional[dict]:
    """Get the launch command info for an app."""
    profile = get_profile(app_name)
    return profile.get("launch") if profile else None


def get_window_pattern(app_name: str) -> Optional[str]:
    """Get the window title pattern for an app."""
    profile = get_profile(app_name)
    return profile.get("window_pattern") if profile else None


def get_login_steps(app_name: str) -> list:
    """Get the ordered login steps for an app."""
    profile = get_profile(app_name)
    return profile.get("login_steps", []) if profile else []


def get_success_indicator(app_name: str) -> Optional[dict]:
    """Get how to verify login succeeded."""
    profile = get_profile(app_name)
    return profile.get("success_indicator") if profile else None


def requires_login(app_name: str) -> bool:
    """Check if this app actually needs login (some don't)."""
    profile = get_profile(app_name)
    if not profile:
        return False
    detection = profile.get("login_detection", {})
    if detection.get("type") == "always" and detection.get("value") is False:
        return False
    steps = profile.get("login_steps", [])
    return len(steps) > 0


def get_winget_id(app_name: str) -> Optional[str]:
    """Get winget ID for auto-install."""
    profile = get_profile(app_name)
    return profile.get("winget_id") if profile else None


def get_notes(app_name: str) -> str:
    """Get human notes about this app's login quirks."""
    profile = get_profile(app_name)
    return profile.get("notes", "") if profile else ""


# ══════════════════════════════════════════════════════════════════════════════
# TEST MODE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 60)
    print("AUTO LOGIN PROFILES — Test Mode")
    print("═" * 60)

    # Test 1: List supported apps
    print("\n[TEST 1] Supported apps")
    apps = list_supported_apps()
    for app in apps:
        print(f"  - {app}")

    # Test 2: Check specific apps
    print("\n[TEST 2] Support checks")
    for name in ["spotify", "Spotify", "SPOTIFY", "discord", "vs code", "chrome", "unknown"]:
        supported = is_supported(name)
        marker = "✓" if supported else "✗"
        print(f"  {marker} '{name}' → {supported}")

    # Test 3: Get Spotify profile details
    print("\n[TEST 3] Spotify profile details")
    profile = get_profile("spotify")
    if profile:
        print(f"  Display name:  {profile['display_name']}")
        print(f"  Winget ID:     {profile['winget_id']}")
        print(f"  Launch type:   {profile['launch']['type']}")
        print(f"  Launch path:   {profile['launch']['value']}")
        print(f"  Window match:  {profile['window_pattern']}")
        print(f"  Login steps:   {len(profile['login_steps'])} steps")
        print(f"  Requires login: {requires_login('spotify')}")

    # Test 4: Show Spotify login sequence
    print("\n[TEST 4] Spotify login sequence (first 8 steps)")
    steps = get_login_steps("spotify")
    for i, step in enumerate(steps[:8], 1):
        action = step.get("action", "?")
        value = step.get("value", "")
        wait = step.get("wait_after", 0)
        print(f"  {i}. {action}(value='{value}', wait={wait}s)")
    if len(steps) > 8:
        print(f"  ... {len(steps) - 8} more steps")

    # Test 5: Success indicator
    print("\n[TEST 5] Spotify success indicator")
    indicator = get_success_indicator("spotify")
    if indicator:
        for k, v in indicator.items():
            print(f"  {k}: {v}")

    # Test 6: Check VS Code (doesn't require login)
    print("\n[TEST 6] VS Code login requirement")
    print(f"  requires_login('vscode'): {requires_login('vscode')}")
    print(f"  Notes: {get_notes('vscode')}")

    # Test 7: Alias resolution
    print("\n[TEST 7] Alias resolution")
    for name in ["vscode", "vs code", "visual studio code", "VS CODE"]:
        profile = get_profile(name)
        found = profile["display_name"] if profile else "not found"
        print(f"  '{name}' → {found}")

    # Test 8: Winget IDs
    print("\n[TEST 8] Winget IDs for install")
    for app in ["spotify", "discord", "vscode", "unknown"]:
        wid = get_winget_id(app)
        print(f"  {app}: {wid}")

    # Test 9: Spotify launch path exists?
    print("\n[TEST 9] Does Spotify launch path currently exist on this system?")
    launch = get_launch_command("spotify")
    if launch:
        exists = os.path.exists(launch["value"])
        marker = "✓" if exists else "✗"
        print(f"  {marker} {launch['value']}")
        print(f"  {'Spotify is installed' if exists else 'Spotify not installed yet (that is fine — we will install it)'}")

    print("\n" + "═" * 60)
    print("Tests done.")
    print("═" * 60)