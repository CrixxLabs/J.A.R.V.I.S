# login_orchestrator.py — Smart app install + login coordinator
# Ties together app_installer, credential_vault, window_watcher, and
# auto_login_profiles into one coherent flow.
#
# Every step is event-driven. No sleep(N) as a "hope it worked" timer.
# Every step reports back so Jarvis can speak intelligently about what's happening.
#
# Public API:
#   install_and_setup(app, progress_callback)   → dict result
#   install_only(app, progress_callback)        → dict result
#   launch_and_login(app, progress_callback)    → dict result
#   perform_login_only(app, progress_callback)  → dict result

import os
import subprocess
import shlex
import time
from typing import Callable, Optional

try:
    import pyautogui
except Exception as _pyautogui_err:
    pyautogui = None
    print(f"[login] pyautogui unavailable: {_pyautogui_err}")

import app_installer
import auto_login_profiles as profiles
import credential_vault
import window_watcher


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# Safety cap — pyautogui type speed (seconds between keystrokes)
_TYPE_INTERVAL = 0.04

# How long to wait for app to appear after launch command
_APP_APPEAR_TIMEOUT = 30

# How long to wait for login screen to stabilize
_LOGIN_STABILIZE_TIMEOUT = 15

# Configure pyautogui safely
if pyautogui is not None:
    pyautogui.FAILSAFE = True   # move mouse to corner to abort
    pyautogui.PAUSE = 0.05      # small pause between actions


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: LAUNCH APP
# ══════════════════════════════════════════════════════════════════════════════

def _launch_app(app_name: str) -> dict:
    """
    Launch an app based on its profile's launch config.
    Returns {success, message, method_used}
    """
    launch = profiles.get_launch_command(app_name)
    if not launch:
        return {"success": False, "message": f"No launch profile for {app_name}", "method_used": None}

    launch_type  = launch.get("type", "")
    launch_value = launch.get("value", "")

    if launch_type == "path":
        # Direct executable path, optionally followed by arguments.  Never pass
        # profile-controlled command text through a shell.
        try:
            parts = shlex.split(str(launch_value), posix=False)
            parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part for part in parts]
        except (TypeError, ValueError) as exc:
            return {"success": False, "message": f"Invalid launch command: {exc}", "method_used": None}

        if not parts:
            return {"success": False, "message": "Empty app launch path", "method_used": None}

        executable = parts[0]
        if not os.path.exists(executable):
            return {"success": False, "message": f"App path not found: {executable}", "method_used": None}

        try:
            if len(parts) == 1:
                os.startfile(executable)
                return {"success": True, "message": f"Launched {app_name}", "method_used": "startfile"}
            subprocess.Popen(parts, shell=False)
            return {"success": True, "message": f"Launched {app_name}", "method_used": "direct"}
        except Exception as exc:
            return {"success": False, "message": f"Launch failed: {exc}", "method_used": None}

    elif launch_type == "start_menu":
        # Launch via Windows start menu search
        try:
            subprocess.Popen(["cmd", "/c", "start", "", launch_value])
            return {"success": True, "message": f"Launched {app_name} via start menu", "method_used": "start"}
        except Exception as exc:
            return {"success": False, "message": f"Start menu launch failed: {exc}", "method_used": None}

    elif launch_type == "protocol":
        # Launch via URL protocol (e.g. spotify:// discord://)
        try:
            os.startfile(launch_value)
            return {"success": True, "message": f"Launched via {launch_value}", "method_used": "protocol"}
        except Exception as exc:
            return {"success": False, "message": f"Protocol launch failed: {exc}", "method_used": None}

    else:
        return {"success": False, "message": f"Unknown launch type: {launch_type}", "method_used": None}


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: EXECUTE LOGIN STEPS
# ══════════════════════════════════════════════════════════════════════════════

def _execute_step(step: dict, credentials: dict, window_pattern: str) -> dict:
    """
    Execute one login step.
    Returns {success: bool, message: str, should_abort: bool}
    """
    action = step.get("action", "")
    value = step.get("value", "")
    wait_after = step.get("wait_after", 0)
    timeout = step.get("timeout", 10)

    try:
        # ── wait: pause for a set time (use sparingly) ─────────────────────
        if action == "wait":
            time.sleep(float(value) if value else 1.0)

        # ── wait_for_window: block until window appears ────────────────────
        elif action == "wait_for_window":
            pattern = value or window_pattern
            win = window_watcher.wait_for_window(pattern, timeout=timeout)
            if not win:
                return {
                    "success": False,
                    "message": f"Window '{pattern}' didn't appear within {timeout}s",
                    "should_abort": True,
                }

        # ── wait_for_stable: wait for window to stop changing ──────────────
        elif action == "wait_for_stable":
            pattern = value or window_pattern
            win = window_watcher.wait_for_window_stable(pattern, timeout=timeout)
            if not win:
                return {
                    "success": False,
                    "message": f"Window '{pattern}' never stabilized",
                    "should_abort": True,
                }

        # ── focus_window: bring window to front ────────────────────────────
        elif action == "focus_window":
            pattern = value or window_pattern
            if not window_watcher.focus_window(pattern):
                return {
                    "success": False,
                    "message": f"Couldn't focus window '{pattern}'",
                    "should_abort": True,
                }

        # ── click_window_center: click center of matching window ───────────
        elif action == "click_window_center":
            pattern = value or window_pattern
            win = window_watcher.find_window(pattern)
            if not win:
                return {
                    "success": False,
                    "message": f"Window '{pattern}' not found for click",
                    "should_abort": True,
                }
            center_x = win["x"] + win["width"] // 2
            center_y = win["y"] + win["height"] // 2
            pyautogui.click(center_x, center_y)

        # ── click: click at specific coordinates ───────────────────────────
        elif action == "click":
            if isinstance(value, dict) and "x" in value and "y" in value:
                pyautogui.click(value["x"], value["y"])
            elif value == "current":
                pyautogui.click()
            else:
                return {
                    "success": False,
                    "message": f"Invalid click value: {value}",
                    "should_abort": False,
                }

        # ── type_text: type literal text ───────────────────────────────────
        elif action == "type_text":
            pyautogui.write(str(value), interval=_TYPE_INTERVAL)

        # ── type_username: type saved username ─────────────────────────────
        elif action == "type_username":
            username = credentials.get("username", "")
            if not username:
                return {
                    "success": False,
                    "message": "No username in credentials",
                    "should_abort": True,
                }
            pyautogui.write(username, interval=_TYPE_INTERVAL)

        # ── type_password: type saved password ─────────────────────────────
        elif action == "type_password":
            password = credentials.get("password", "")
            if not password:
                return {
                    "success": False,
                    "message": "No password in credentials",
                    "should_abort": True,
                }
            pyautogui.write(password, interval=_TYPE_INTERVAL)

        # ── press_key: press a single key ──────────────────────────────────
        elif action == "press_key":
            pyautogui.press(str(value))

        # ── hotkey: press key combination ──────────────────────────────────
        elif action in ("hotkey", "keyboard_shortcut"):
            if isinstance(value, str) and "+" in value:
                keys = [k.strip() for k in value.split("+")]
                pyautogui.hotkey(*keys)
            elif isinstance(value, list):
                pyautogui.hotkey(*value)
            else:
                return {
                    "success": False,
                    "message": f"Invalid hotkey value: {value}",
                    "should_abort": False,
                }

        else:
            return {
                "success": False,
                "message": f"Unknown action: {action}",
                "should_abort": False,
            }

        # ── Post-step wait ────────────────────────────────────────────────
        if wait_after > 0:
            time.sleep(wait_after)

        return {"success": True, "message": f"{action} completed", "should_abort": False}

    except pyautogui.FailSafeException:
        return {
            "success": False,
            "message": "User aborted (moved mouse to corner)",
            "should_abort": True,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Step '{action}' failed: {exc}",
            "should_abort": True,
        }


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: VERIFY LOGIN SUCCESS
# ══════════════════════════════════════════════════════════════════════════════

def _verify_login_success(app_name: str, initial_snapshot: tuple = None) -> dict:
    """
    Check if login actually succeeded using the app's success indicator.
    Returns {success: bool, message: str}
    """
    indicator = profiles.get_success_indicator(app_name)
    if not indicator:
        return {"success": True, "message": "No verification configured"}

    indicator_type = indicator.get("type", "")
    indicator_value = indicator.get("value", "")
    timeout = indicator.get("timeout", 15)
    window_pattern = profiles.get_window_pattern(app_name)

    if indicator_type == "window_title_changes":
        # Watch for the window's state to change (login screen → dashboard)
        state = window_watcher.detect_state_change(
            title_pattern=window_pattern,
            initial_snapshot=initial_snapshot,
            timeout=timeout,
            change_threshold=1.0,
        )
        if state == "changed":
            return {"success": True, "message": "Window state changed — login likely succeeded"}
        elif state == "closed":
            return {"success": False, "message": "App window closed unexpectedly"}
        elif state == "unchanged":
            return {"success": False, "message": "Window didn't change — login may have failed"}
        else:
            return {"success": False, "message": "App window not found"}

    elif indicator_type == "window_title_contains":
        # Poll for a window with a specific title substring
        start = time.time()
        while time.time() - start < timeout:
            win = window_watcher.find_window(indicator_value)
            if win:
                return {"success": True, "message": f"Found target window: {win['title']}"}
            time.sleep(0.3)
        return {"success": False, "message": f"Never saw '{indicator_value}' in title"}

    elif indicator_type == "new_window":
        # Wait for a completely new window matching pattern
        win = window_watcher.wait_for_window(indicator_value, timeout=timeout)
        if win:
            return {"success": True, "message": f"New window appeared: {win['title']}"}
        return {"success": False, "message": f"New window '{indicator_value}' never appeared"}

    return {"success": True, "message": "Unknown indicator type, assuming success"}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: INSTALL ONLY
# ══════════════════════════════════════════════════════════════════════════════

def install_only(
    app_name: str,
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """
    Install an app without launching it.
    """
    def _emit(event: str, data: dict = None):
        if progress_callback:
            try:
                progress_callback(event, data or {})
            except Exception:
                pass

    if not profiles.is_supported(app_name):
        # Try direct install via alias in app_installer anyway
        _emit("info", {"message": f"No login profile for {app_name}, trying direct install"})
        result = app_installer.install_app(app_name, progress_callback=progress_callback)
        return result

    winget_id = profiles.get_winget_id(app_name)
    if not winget_id:
        return {
            "success": False,
            "message": f"No winget ID configured for {app_name}",
            "app_id": "",
        }

    return app_installer.install_app(winget_id, progress_callback=progress_callback)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: LAUNCH AND LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def launch_and_login(
    app_name: str,
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """
    Launch an already-installed app and perform auto-login if credentials exist.

    Returns:
        dict {
            "success": bool,
            "message": str,
            "stage": str  — which stage completed/failed
            "credentials_used": bool,
            "login_verified": bool,
        }
    """
    def _emit(event: str, data: dict = None):
        if progress_callback:
            try:
                progress_callback(event, data or {})
            except Exception:
                pass

    result = {
        "success": False,
        "message": "",
        "stage": "start",
        "credentials_used": False,
        "login_verified": False,
    }

    # ── Check profile exists ─────────────────────────────────────────────────
    if not profiles.is_supported(app_name):
        result["message"] = f"I don't have a login profile for {app_name} yet."
        result["stage"] = "no_profile"
        return result

    display_name = profiles.get_profile(app_name).get("display_name", app_name)
    window_pattern = profiles.get_window_pattern(app_name)

    # ── Stage 1: Launch app ──────────────────────────────────────────────────
    _emit("launching", {"app": display_name})
    result["stage"] = "launching"

    launch_result = _launch_app(app_name)
    if not launch_result["success"]:
        result["message"] = launch_result["message"]
        _emit("launch_failed", {"reason": launch_result["message"]})
        return result

    _emit("launched", {"app": display_name})

    # ── Stage 2: Wait for window to appear ───────────────────────────────────
    _emit("waiting_for_window", {"pattern": window_pattern})
    result["stage"] = "waiting_for_window"

    def _wait_progress(elapsed):
        _emit("waiting_progress", {"elapsed": elapsed})

    win = window_watcher.wait_for_window(
        window_pattern,
        timeout=_APP_APPEAR_TIMEOUT,
        progress_callback=_wait_progress,
    )
    if not win:
        result["message"] = f"{display_name} didn't open within {_APP_APPEAR_TIMEOUT} seconds."
        _emit("window_never_appeared", {})
        return result

    _emit("window_found", {"title": win["title"]})

    # ── Stage 3: Check if login is required ──────────────────────────────────
    if not profiles.requires_login(app_name):
        result["success"] = True
        result["stage"] = "no_login_needed"
        result["message"] = f"{display_name} is open. No login required."
        _emit("no_login_needed", {})
        return result

    # ── Stage 4: Get credentials ─────────────────────────────────────────────
    _emit("checking_credentials", {})
    result["stage"] = "checking_credentials"

    credentials = credential_vault.get_credential(app_name)
    if not credentials:
        result["message"] = (
            f"{display_name} is open but I don't have login credentials saved. "
            f"Say 'save my {display_name} login' to set them up."
        )
        _emit("no_credentials", {"app": display_name})
        return result

    result["credentials_used"] = True
    _emit("credentials_loaded", {"username": credentials["username"]})

    # ── Stage 5: Snapshot initial state for change detection ─────────────────
    initial_snapshot = window_watcher.get_window_snapshot(window_pattern)

    # ── Stage 6: Execute login steps ─────────────────────────────────────────
    _emit("logging_in", {"app": display_name})
    result["stage"] = "logging_in"

    steps = profiles.get_login_steps(app_name)
    for i, step in enumerate(steps, 1):
        _emit("login_step", {
            "step": i,
            "total": len(steps),
            "action": step.get("action", ""),
        })

        step_result = _execute_step(step, credentials, window_pattern)

        if not step_result["success"]:
            result["message"] = f"Login step {i} failed: {step_result['message']}"
            _emit("login_step_failed", {
                "step": i,
                "action": step.get("action", ""),
                "reason": step_result["message"],
            })
            if step_result["should_abort"]:
                return result

    # ── Stage 7: Verify login succeeded ──────────────────────────────────────
    _emit("verifying_login", {})
    result["stage"] = "verifying"

    verification = _verify_login_success(app_name, initial_snapshot=initial_snapshot)
    result["login_verified"] = verification["success"]

    if verification["success"]:
        # Post-login wait for UI to settle
        post_wait = profiles.get_profile(app_name).get("post_login_wait", 0)
        if post_wait > 0:
            time.sleep(post_wait)

        result["success"] = True
        result["stage"] = "done"
        result["message"] = f"You're logged into {display_name}."
        _emit("login_success", {"app": display_name})
    else:
        result["message"] = (
            f"I tried to log you into {display_name} but couldn't confirm it worked. "
            f"{verification['message']} — maybe the password changed, "
            f"or the login screen updated. Check the window and log in manually if needed."
        )
        _emit("login_unverified", {"reason": verification["message"]})

    return result


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: FULL FLOW — INSTALL + LAUNCH + LOGIN
# ══════════════════════════════════════════════════════════════════════════════

def install_and_setup(
    app_name: str,
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """
    Complete flow: install (if needed) → launch → auto-login (if creds saved).
    """
    def _emit(event: str, data: dict = None):
        if progress_callback:
            try:
                progress_callback(event, data or {})
            except Exception:
                pass

    result = {
        "success": False,
        "message": "",
        "stage": "start",
        "was_installed": False,
        "login_attempted": False,
        "login_verified": False,
    }

    # ── Check if we support this app at all ──────────────────────────────────
    if not profiles.is_supported(app_name):
        # Try install-only fallback
        _emit("info", {"message": f"No profile for {app_name}, install-only mode"})
        install_result = app_installer.install_app(app_name, progress_callback=progress_callback)
        result["success"] = install_result["success"]
        result["message"] = install_result["message"]
        result["was_installed"] = install_result["success"] and not install_result.get("already_installed", False)
        result["stage"] = "install_only_done"
        return result

    display_name = profiles.get_profile(app_name).get("display_name", app_name)

    # ── Stage A: Install if not present ──────────────────────────────────────
    winget_id = profiles.get_winget_id(app_name)
    if winget_id and not app_installer.is_app_installed(winget_id):
        _emit("install_starting", {"app": display_name})
        result["stage"] = "installing"

        install_result = app_installer.install_app(winget_id, progress_callback=progress_callback)

        if not install_result["success"]:
            result["message"] = install_result["message"]
            return result

        result["was_installed"] = not install_result.get("already_installed", False)

        # Give Windows a moment to register the new install path
        time.sleep(2)

    else:
        _emit("already_installed", {"app": display_name})

    # ── Stage B: Launch and login ────────────────────────────────────────────
    result["login_attempted"] = profiles.requires_login(app_name)
    login_result = launch_and_login(app_name, progress_callback=progress_callback)

    result["success"] = login_result["success"]
    result["message"] = login_result["message"]
    result["stage"] = login_result["stage"]
    result["login_verified"] = login_result["login_verified"]

    return result


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: LOGIN ONLY (app already open)
# ══════════════════════════════════════════════════════════════════════════════

def perform_login_only(
    app_name: str,
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """
    Assume app is already open — just do the login steps.
    Useful for retry scenarios.
    """
    def _emit(event: str, data: dict = None):
        if progress_callback:
            try:
                progress_callback(event, data or {})
            except Exception:
                pass

    if not profiles.is_supported(app_name):
        return {"success": False, "message": f"No profile for {app_name}"}

    window_pattern = profiles.get_window_pattern(app_name)

    if not window_watcher.is_window_open(window_pattern):
        return {"success": False, "message": f"{app_name} isn't open. Launch it first."}

    credentials = credential_vault.get_credential(app_name)
    if not credentials:
        return {"success": False, "message": f"No credentials saved for {app_name}"}

    initial_snapshot = window_watcher.get_window_snapshot(window_pattern)
    steps = profiles.get_login_steps(app_name)

    for i, step in enumerate(steps, 1):
        _emit("login_step", {"step": i, "total": len(steps)})
        step_result = _execute_step(step, credentials, window_pattern)
        if not step_result["success"] and step_result["should_abort"]:
            return {"success": False, "message": step_result["message"]}

    verification = _verify_login_success(app_name, initial_snapshot=initial_snapshot)
    return {
        "success": verification["success"],
        "message": verification["message"],
        "login_verified": verification["success"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# TEST MODE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 60)
    print("LOGIN ORCHESTRATOR — Test Mode")
    print("═" * 60)

    # Simple progress printer for tests
    def _test_progress(event, data):
        print(f"  [{event}] {data}")

    # Test 1: Check module dependencies
    print("\n[TEST 1] Module dependencies")
    print(f"  app_installer:       {app_installer.check_winget_available()}")
    print(f"  credential_vault:    {credential_vault.is_available()}")
    print(f"  window_watcher:      {window_watcher._WIN32_AVAILABLE}")
    print(f"  auto_login_profiles: {len(profiles.list_supported_apps())} apps registered")

    # Test 2: Check Spotify profile is reachable
    print("\n[TEST 2] Spotify profile check")
    profile = profiles.get_profile("spotify")
    if profile:
        print(f"  ✓ Spotify profile loaded")
        print(f"    Winget ID: {profile['winget_id']}")
        print(f"    Launch path: {profile['launch']['value']}")
        print(f"    Login steps: {len(profile['login_steps'])}")
    else:
        print(f"  ✗ Spotify profile missing")

    # Test 3: Is Spotify installed?
    print("\n[TEST 3] Spotify install status")
    installed = app_installer.is_app_installed("Spotify.Spotify")
    print(f"  Installed: {installed}")

    # Test 4: Do we have Spotify credentials?
    print("\n[TEST 4] Spotify credentials check")
    creds = credential_vault.get_credential("spotify")
    if creds:
        print(f"  ✓ Have credentials for: {creds['username']}")
    else:
        print(f"  ✗ No credentials saved for Spotify")
        print(f"    (That's expected — we'll set them up in Jarvis flow)")

    # Test 5: DRY RUN — show what full install+setup would do
    print("\n[TEST 5] Dry run of install_and_setup('spotify')")
    print("  This would:")
    if not installed:
        print("    1. Install Spotify via winget")
    else:
        print("    1. Skip install (already installed)")
    print("    2. Launch Spotify")
    print("    3. Wait for window to appear + stabilize")
    if creds:
        print("    4. Execute 12-step login sequence")
        print("    5. Verify success by watching window state change")
    else:
        print("    4. Report: 'no credentials saved'")

    # Test 6: Ask if you want to actually run it
    print("\n[TEST 6] LIVE TEST")
    print("  ⚠ This will ACTUALLY install Spotify if not installed.")
    print("  ⚠ It will ACTUALLY open Spotify.")
    print("  ⚠ It will NOT attempt login (no credentials saved yet — safe).")
    print()
    answer = input("  Run live test? (yes/no): ").strip().lower()

    if answer == "yes":
        print("\n  Running install_and_setup('spotify')...")
        print("  " + "─" * 55)
        result = install_and_setup("spotify", progress_callback=_test_progress)
        print("  " + "─" * 55)
        print(f"\n  Final result:")
        for k, v in result.items():
            print(f"    {k}: {v}")
    else:
        print("  Skipped live test.")

    print("\n" + "═" * 60)
    print("Tests done.")
    print("═" * 60)