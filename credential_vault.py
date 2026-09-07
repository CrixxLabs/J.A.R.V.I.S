# credential_vault.py — Secure credential storage
# Uses Windows Credential Manager via `keyring` library.
# Passwords are encrypted at OS level (DPAPI) and only accessible when
# the current Windows user is logged in.
#
# Public API:
#   is_available()                          → bool
#   save_credential(app, username, pw)      → dict {success, message}
#   get_credential(app)                     → dict {username, password} or None
#   get_username(app)                       → str or None
#   get_password(app, username=None)        → str or None
#   list_saved_apps()                       → list of app names
#   delete_credential(app)                  → dict {success, message}
#   has_credential(app)                     → bool
#
# Storage design:
# - Service name format: "jarvis_<app_name>"       (for password)
# - Username stored separately in "jarvis_meta"    (indexed by app)
# - Why? keyring stores by (service, username) pair, so we need a way to
#   remember what username belongs to what app without knowing it upfront.

import json
import os
from typing import Optional

try:
    import keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    keyring = None
    _KEYRING_AVAILABLE = False
    print("[credential_vault] ⚠ keyring not installed — run: pip install keyring")


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_SERVICE_PREFIX = "jarvis_"        # prefix for all app credentials
_META_SERVICE   = "jarvis_meta"    # tracks which apps have saved credentials
_META_KEY       = "app_registry"   # single entry holding the JSON registry


# ══════════════════════════════════════════════════════════════════════════════
# AVAILABILITY (cached after first check)
# ══════════════════════════════════════════════════════════════════════════════

_backend_checked = False
_backend_ok = False


def is_available() -> bool:
    """Check if keyring backend is available and functional. Cached."""
    global _backend_checked, _backend_ok

    if _backend_checked:
        return _backend_ok

    _backend_checked = True

    if not _KEYRING_AVAILABLE:
        _backend_ok = False
        return False

    try:
        backend = keyring.get_keyring()
        backend_name = backend.__class__.__name__
        print(f"[credential_vault] using backend: {backend_name}")
        _backend_ok = True
        return True
    except Exception as exc:
        print(f"[credential_vault] backend check failed: {exc}")
        _backend_ok = False
        return False


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: APP REGISTRY (tracks which apps have saved credentials)
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_app_name(app: str) -> str:
    """Normalize app name for consistent storage."""
    return (app or "").strip().lower().replace(" ", "_")


def _get_service_name(app: str) -> str:
    """Get the keyring service name for an app."""
    return _SERVICE_PREFIX + _normalize_app_name(app)


def _load_registry() -> dict:
    """
    Load the app registry from keyring.
    Registry format: {app_name: username, ...}
    """
    if not _KEYRING_AVAILABLE:
        return {}
    try:
        raw = keyring.get_password(_META_SERVICE, _META_KEY)
        if not raw:
            return {}
        return json.loads(raw)
    except json.JSONDecodeError:
        print("[credential_vault] registry corrupted, resetting")
        return {}
    except Exception as exc:
        print(f"[credential_vault] load registry error: {exc}")
        return {}


def _save_registry(registry: dict) -> bool:
    """Save the app registry to keyring."""
    if not _KEYRING_AVAILABLE:
        return False
    try:
        raw = json.dumps(registry)
        keyring.set_password(_META_SERVICE, _META_KEY, raw)
        return True
    except Exception as exc:
        print(f"[credential_vault] save registry error: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: SAVE CREDENTIAL
# ══════════════════════════════════════════════════════════════════════════════

def save_credential(app: str, username: str, password: str) -> dict:
    """
    Save a credential for an app.

    Args:
        app:      app name (e.g. "spotify", "discord")
        username: email or username for login
        password: plaintext password (gets encrypted by Windows DPAPI)

    Returns:
        dict: {success: bool, message: str}
    """
    if not is_available():
        return {
            "success": False,
            "message": "Credential storage isn't available. Install the keyring library.",
        }

    if not (app or "").strip():
        return {"success": False, "message": "No app name provided."}
    if not (username or "").strip():
        return {"success": False, "message": "No username provided."}
    if not (password or "").strip():
        return {"success": False, "message": "No password provided."}

    app_normalized = _normalize_app_name(app)
    service        = _get_service_name(app)

    try:
        # Save the password in keyring (encrypted)
        keyring.set_password(service, username.strip(), password)

        # Update the registry so we know this app has credentials
        registry = _load_registry()
        registry[app_normalized] = username.strip()
        _save_registry(registry)

        print(f"[credential_vault] ✓ saved credentials for '{app_normalized}'")
        return {
            "success": True,
            "message": f"Saved credentials for {app} securely.",
        }
    except Exception as exc:
        print(f"[credential_vault] save error: {exc}")
        return {
            "success": False,
            "message": f"Couldn't save credentials: {exc}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: RETRIEVE CREDENTIAL
# ══════════════════════════════════════════════════════════════════════════════

def get_credential(app: str) -> Optional[dict]:
    """
    Get the full credential (username + password) for an app.

    Returns:
        dict {username, password} if found
        None if no credential exists or retrieval fails
    """
    if not is_available():
        return None

    app_normalized = _normalize_app_name(app)
    registry       = _load_registry()

    if app_normalized not in registry:
        return None

    username = registry[app_normalized]
    service  = _get_service_name(app)

    try:
        password = keyring.get_password(service, username)
        if password is None:
            # Registry has entry but keyring doesn't — cleanup
            print(f"[credential_vault] ⚠ registry has {app_normalized} but keyring doesn't")
            del registry[app_normalized]
            _save_registry(registry)
            return None

        return {
            "username": username,
            "password": password,
        }
    except Exception as exc:
        print(f"[credential_vault] get error: {exc}")
        return None


def get_username(app: str) -> Optional[str]:
    """Get just the username for an app (fast, doesn't decrypt password)."""
    if not is_available():
        return None
    registry = _load_registry()
    return registry.get(_normalize_app_name(app))


def get_password(app: str, username: str = None) -> Optional[str]:
    """
    Get just the password for an app.
    If username not provided, uses the one from registry.
    """
    if not is_available():
        return None

    if username is None:
        username = get_username(app)
        if not username:
            return None

    try:
        return keyring.get_password(_get_service_name(app), username)
    except Exception as exc:
        print(f"[credential_vault] get_password error: {exc}")
        return None


def has_credential(app: str) -> bool:
    """Quick check if credential exists for an app."""
    if not is_available():
        return False
    registry = _load_registry()
    return _normalize_app_name(app) in registry


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: LIST & DELETE
# ══════════════════════════════════════════════════════════════════════════════

def list_saved_apps() -> list:
    """Return list of app names that have saved credentials."""
    if not is_available():
        return []
    registry = _load_registry()
    return sorted(registry.keys())


def delete_credential(app: str) -> dict:
    """Remove a saved credential."""
    if not is_available():
        return {"success": False, "message": "Credential storage not available."}

    app_normalized = _normalize_app_name(app)
    registry       = _load_registry()

    if app_normalized not in registry:
        return {"success": False, "message": f"No saved credentials for {app}."}

    username = registry[app_normalized]
    service  = _get_service_name(app)

    try:
        # Remove from keyring
        try:
            keyring.delete_password(service, username)
        except keyring.errors.PasswordDeleteError:
            # Already gone, that's fine
            pass

        # Remove from registry
        del registry[app_normalized]
        _save_registry(registry)

        print(f"[credential_vault] ✓ deleted credentials for '{app_normalized}'")
        return {
            "success": True,
            "message": f"Removed saved credentials for {app}.",
        }
    except Exception as exc:
        print(f"[credential_vault] delete error: {exc}")
        return {
            "success": False,
            "message": f"Couldn't delete credentials: {exc}",
        }


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC: BULK OPS (for future admin/setup commands)
# ══════════════════════════════════════════════════════════════════════════════

def delete_all_credentials() -> dict:
    """
    ⚠️ DANGEROUS: Removes ALL Jarvis-stored credentials.
    Used for reset/wipe operations.
    """
    if not is_available():
        return {"success": False, "message": "Credential storage not available."}

    registry = _load_registry()
    if not registry:
        return {"success": True, "message": "No credentials to delete.", "count": 0}

    deleted_count = 0
    errors = []

    for app_normalized, username in list(registry.items()):
        service = _SERVICE_PREFIX + app_normalized
        try:
            try:
                keyring.delete_password(service, username)
            except keyring.errors.PasswordDeleteError:
                pass
            deleted_count += 1
        except Exception as exc:
            errors.append(f"{app_normalized}: {exc}")

    # Clear registry
    try:
        keyring.delete_password(_META_SERVICE, _META_KEY)
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Deleted {deleted_count} credentials.",
        "count":   deleted_count,
        "errors":  errors,
    }


# ══════════════════════════════════════════════════════════════════════════════
# TEST MODE (run this file directly to test)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═" * 60)
    print("CREDENTIAL VAULT — Test Mode")
    print("═" * 60)

    # Test 1: availability
    print("\n[TEST 1] Backend availability")
    available = is_available()
    print(f"  Available: {available}")
    if not available:
        print("  ⚠ Fix this first (pip install keyring) — stopping tests")
        exit(1)

    # Test 2: save a test credential
    print("\n[TEST 2] Save test credential (test_app)")
    result = save_credential("test_app", "test_user@example.com", "SuperSecret123!")
    print(f"  Result: {result}")

    # Test 3: retrieve the credential
    print("\n[TEST 3] Retrieve test credential")
    cred = get_credential("test_app")
    if cred:
        print(f"  Username: {cred['username']}")
        print(f"  Password: {'*' * len(cred['password'])} (length: {len(cred['password'])})")
    else:
        print(f"  ⚠ Failed to retrieve!")

    # Test 4: username only
    print("\n[TEST 4] Get username only")
    username = get_username("test_app")
    print(f"  Username: {username}")

    # Test 5: password only
    print("\n[TEST 5] Get password only")
    password = get_password("test_app")
    print(f"  Password: {'*' * len(password) if password else 'None'}")

    # Test 6: has_credential
    print("\n[TEST 6] has_credential checks")
    print(f"  test_app: {has_credential('test_app')}")
    print(f"  nonexistent_app: {has_credential('nonexistent_app')}")

    # Test 7: list all saved apps
    print("\n[TEST 7] List all saved apps")
    apps = list_saved_apps()
    print(f"  Apps: {apps}")

    # Test 8: normalize check (should treat "Test App" same as "test_app")
    print("\n[TEST 8] Name normalization")
    print(f"  'Test App' → {_normalize_app_name('Test App')}")
    print(f"  'test_app' → {_normalize_app_name('test_app')}")
    print(f"  Match retrieval: {get_credential('Test App') is not None}")

    # Test 9: delete test credential
    print("\n[TEST 9] Delete test credential")
    result = delete_credential("test_app")
    print(f"  Result: {result}")

    # Test 10: verify deletion
    print("\n[TEST 10] Verify deletion")
    still_exists = has_credential("test_app")
    print(f"  Still exists: {still_exists} (should be False)")

    print("\n" + "═" * 60)
    print("Tests done. If all show expected results, vault is working.")
    print("═" * 60)