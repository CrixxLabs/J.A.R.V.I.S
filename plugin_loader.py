# plugin_loader.py — Skill / Plugin Loader
# Phase 1: Auto-loads skills from /skills directory with priority and context support

import os
import importlib.util
import traceback
from session_logger import log_event

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(BASE_DIR, "skills")

# Registry of loaded skills with priority
_loaded_skills = {}   # skill_name -> {"module", "triggers", "priority", "name"}


def load_skills():
    """
    Scan the /skills directory and load all valid skill modules.
    Skills can now have:
        SKILL_NAME     : str
        TRIGGERS       : list of keywords
        PRIORITY       : int (higher = runs first, default 50)
        handle(text, context=None) : main function
        can_handle(text) : optional intelligent check
    """
    global _loaded_skills
    _loaded_skills = {}
    
    os.makedirs(SKILLS_DIR, exist_ok=True)

    skill_files = [
        f for f in os.listdir(SKILLS_DIR)
        if f.endswith(".py") and not f.startswith("_")
    ]

    for filename in skill_files:
        skill_path = os.path.join(SKILLS_DIR, filename)
        skill_name = filename[:-3]

        try:
            spec   = importlib.util.spec_from_file_location(skill_name, skill_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, "SKILL_NAME") or not hasattr(module, "TRIGGERS"):
                print(f"[PluginLoader] Skipping {filename} — missing SKILL_NAME or TRIGGERS")
                continue

            priority = getattr(module, "PRIORITY", 50)

            _loaded_skills[module.SKILL_NAME] = {
                "module":    module,
                "triggers":  [t.lower() for t in module.TRIGGERS],
                "priority":  priority,
                "name":      module.SKILL_NAME,
                "can_handle": getattr(module, "can_handle", None)
            }
            print(f"[PluginLoader] Loaded skill: {module.SKILL_NAME} (priority={priority})")
            log_event("skill_loaded", {"name": module.SKILL_NAME, "priority": priority}, module="plugin_loader")

        except Exception as e:
            print(f"[PluginLoader] Failed to load {filename}: {e}")
            traceback.print_exc()
            log_event("skill_load_error", {"file": filename, "error": str(e)}, severity="error", module="plugin_loader")

    # Sort by priority (higher first)
    sorted_skills = dict(sorted(_loaded_skills.items(), key=lambda x: x[1]["priority"], reverse=True))
    _loaded_skills = sorted_skills

    print(f"[PluginLoader] {len(_loaded_skills)} skill(s) loaded.")
    log_event("skills_loaded", {"count": len(_loaded_skills)}, module="plugin_loader")
    return _loaded_skills


def reload_skills():
    """Useful during development"""
    print("[PluginLoader] Reloading all skills...")
    return load_skills()


def get_loaded_skills():
    """Returns the registry of loaded skills."""
    return _loaded_skills


def route_to_skill(user_input):
    """
    Check skills in priority order.
    First skill that returns a response wins.
    """
    if not _loaded_skills:
        load_skills()

    text = user_input.lower().strip()
    context = None
    try:
        import conversation_manager
        context = conversation_manager.get_context_block()
    except:
        pass

    for skill_name, skill in _loaded_skills.items():
        # Use intelligent can_handle() if available
        if skill.get("can_handle"):
            try:
                if skill["can_handle"](user_input):
                    response = skill["module"].handle(user_input, context)
                    if response:
                        log_event("skill_triggered", {"skill": skill_name, "input": user_input[:50]}, module="plugin_loader")
                        return skill_name, response
            except Exception as e:
                print(f"[PluginLoader] Error in can_handle() of '{skill_name}': {e}")
                continue

        # Fallback to trigger words
        for trigger in skill["triggers"]:
            if trigger in text:
                try:
                    response = skill["module"].handle(user_input, context)
                    if response:
                        log_event("skill_triggered", {"skill": skill_name, "input": user_input[:50]}, module="plugin_loader")
                        return skill_name, response
                except Exception as e:
                    print(f"[PluginLoader] Error in skill '{skill_name}': {e}")
                    traceback.print_exc()
                break

    return None, None


def list_skills():
    """Returns a list of loaded skill names and their triggers."""
    result = []
    for name, skill in _loaded_skills.items():
        result.append({
            "name":     name,
            "priority": skill["priority"],
            "triggers": skill["triggers"]
        })
    return result