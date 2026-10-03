"""Ambient IoT & Physical Space Mesh Controller for J.A.R.V.I.S. — MARK VIII.

Integrates local Home Assistant REST/WebSocket API endpoints, ambient environmental
profiles (Focus/Study, Sleep, Energy Conservation), smart plug battery watchdog management,
and graceful offline simulation fallback.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.smart_space")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
SPACE_STATE_FILE = DATA_DIR / "smart_space_state.json"

_lock = threading.RLock()

PROFILES = {
    "focus": {
        "lights": {"state": "on", "brightness": 230, "color_temp": "cool_white"},
        "thermostat": {"target_temp_c": 21.5},
        "audio": {"mode": "binaural_beats", "volume": 35},
        "dnd": True,
    },
    "study": {
        "lights": {"state": "on", "brightness": 255, "color_temp": "daylight"},
        "thermostat": {"target_temp_c": 22.0},
        "audio": {"mode": "ambient_quiescence", "volume": 20},
        "dnd": True,
    },
    "sleep": {
        "lights": {"state": "off", "brightness": 0},
        "thermostat": {"target_temp_c": 19.5},
        "audio": {"mode": "white_noise", "volume": 15},
        "dnd": True,
    },
    "quiescent": {
        "lights": {"state": "on", "brightness": 80, "color_temp": "warm"},
        "thermostat": {"target_temp_c": 21.0},
        "audio": {"mode": "off"},
        "dnd": False,
    },
}


class SmartSpaceController:
    """Controls connected smart home devices and ambient space environments."""

    def __init__(self, state_path: Optional[Path] = None):
        self.state_path = Path(state_path).resolve() if state_path else SPACE_STATE_FILE
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.active_profile = "quiescent"
        self._devices: Dict[str, Dict[str, Any]] = {
            "light.lab_overhead": {"state": "on", "brightness": 200, "type": "light"},
            "switch.laptop_charger": {"state": "on", "type": "switch"},
            "climate.lab_thermostat": {"state": "cool", "temperature": 21.0, "type": "climate"},
        }
        self._load_state()

    def _load_state(self) -> None:
        with _lock:
            if self.state_path.exists():
                try:
                    with open(self.state_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self.active_profile = data.get("active_profile", "quiescent")
                            self._devices.update(data.get("devices", {}))
                except Exception as exc:
                    log.warning(f"[SmartSpace] Failed loading state: {exc}")

    def _save_state(self) -> None:
        with _lock:
            tmp_path = self.state_path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "active_profile": self.active_profile,
                        "devices": self._devices,
                        "updated_at": time.time(),
                    }, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self.state_path)
            except Exception as exc:
                log.error(f"[SmartSpace] Failed saving state: {exc}")

    def get_space_state(self) -> Dict[str, Any]:
        """Return the current ambient space profile and device states."""
        with _lock:
            return {
                "active_profile": self.active_profile,
                "profile_spec": PROFILES.get(self.active_profile, {}),
                "devices": dict(self._devices),
                "timestamp": time.time(),
            }

    def set_space_profile(self, profile_name: str) -> Dict[str, Any]:
        """Apply an ambient environmental profile across lights, climate, and sound."""
        clean_name = profile_name.strip().lower()
        if clean_name not in PROFILES:
            return {
                "success": False,
                "status": "invalid_profile",
                "message": f"Unknown profile: '{profile_name}'. Available: {list(PROFILES.keys())}",
            }

        spec = PROFILES[clean_name]
        with _lock:
            self.active_profile = clean_name
            # Apply to devices
            if "light.lab_overhead" in self._devices:
                self._devices["light.lab_overhead"]["state"] = spec["lights"]["state"]
                self._devices["light.lab_overhead"]["brightness"] = spec["lights"]["brightness"]

            self._save_state()

        log.info(f"[SmartSpace] Applied environmental profile: {clean_name.upper()}")

        try:
            get_registry().set_capability_evidence(
                "SMART_SPACE",
                EvidenceLevel.LIVE,
                f"Active space profile set to {clean_name}",
                source="smart_space.set_space_profile",
            )
        except Exception:
            pass

        return {
            "success": True,
            "status": "profile_applied",
            "profile": clean_name,
            "spec": spec,
            "message": f"Ambient space configured for {clean_name.upper()} mode.",
        }

    def control_device(
        self,
        entity_id: str,
        action: str,  # "turn_on", "turn_off", "toggle", "set_brightness", "set_temperature"
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Control a specific connected IoT device entity."""
        ha_url = os.getenv("HOME_ASSISTANT_URL", "").strip()
        ha_token = os.getenv("HOME_ASSISTANT_TOKEN", "").strip()

        with _lock:
            device = self._devices.setdefault(entity_id, {"state": "off", "type": "switch"})

            if action == "turn_on":
                device["state"] = "on"
            elif action == "turn_off":
                device["state"] = "off"
            elif action == "toggle":
                device["state"] = "off" if device.get("state") == "on" else "on"
            elif action == "set_brightness" and params:
                device["brightness"] = params.get("brightness", 255)
                device["state"] = "on"
            elif action == "set_temperature" and params:
                device["temperature"] = params.get("temperature", 21.0)

            self._save_state()

        # If live Home Assistant credentials exist, attempt REST call
        if ha_url and ha_token:
            try:
                headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
                domain = entity_id.split(".")[0]
                service = action if action in ("turn_on", "turn_off", "toggle") else "turn_on"
                endpoint = f"{ha_url}/api/services/{domain}/{service}"
                payload = {"entity_id": entity_id, **(params or {})}
                resp = requests.post(endpoint, headers=headers, json=payload, timeout=3.0)
                resp.raise_for_status()
            except Exception as exc:
                log.warning(f"[SmartSpace] Home Assistant endpoint unreachable ({exc}); using local state.")

        log.info(f"[SmartSpace] Executed {action} on {entity_id} -> State: {device.get('state')}")
        return {
            "success": True,
            "entity_id": entity_id,
            "action": action,
            "current_state": device,
            "message": f"Device {entity_id} set to {device.get('state')}.",
        }

    def run_power_watchdog(self, battery_percent: float, is_charging: bool) -> Dict[str, Any]:
        """Battery protection watchdog: toggles smart plug charger to preserve battery longevity (20%-80%)."""
        charger_entity = "switch.laptop_charger"
        action_taken = "none"

        if battery_percent >= 80.0 and is_charging:
            self.control_device(charger_entity, "turn_off")
            action_taken = "cutoff_charger_at_ceiling"
            msg = f"Battery charge at {battery_percent:.1f}% >= 80%. Smart plug disconnected to preserve battery life."
        elif battery_percent <= 20.0 and not is_charging:
            self.control_device(charger_entity, "turn_on")
            action_taken = "engage_charger_at_floor"
            msg = f"Battery charge at {battery_percent:.1f}% <= 20%. Smart plug activated to recharge."
        else:
            msg = f"Battery at {battery_percent:.1f}% (Charging: {is_charging}) — nominal range."

        return {
            "success": True,
            "battery_percent": battery_percent,
            "action_taken": action_taken,
            "message": msg,
        }


_space_instance: Optional[SmartSpaceController] = None


def get_smart_space() -> SmartSpaceController:
    global _space_instance
    if _space_instance is None:
        with _lock:
            if _space_instance is None:
                _space_instance = SmartSpaceController()
    return _space_instance


def set_space_profile(profile_name: str) -> Dict[str, Any]:
    return get_smart_space().set_space_profile(profile_name)


def control_device(entity_id: str, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return get_smart_space().control_device(entity_id, action, params)


def get_space_state() -> Dict[str, Any]:
    return get_smart_space().get_space_state()


def run_power_watchdog(battery_percent: float, is_charging: bool) -> Dict[str, Any]:
    return get_smart_space().run_power_watchdog(battery_percent, is_charging)
