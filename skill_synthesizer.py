"""Autonomous Skill Synthesizer for J.A.R.V.I.S. — MARK VIII.

Implements open-ended procedural tool synthesis (Voyager paradigm) so J.A.R.V.I.S never refuses an unknown task:
  1. Code Generation: Prompts brain LLM to write self-contained Python skills adhering to `def execute(params: dict) -> dict:`.
  2. Validation Sandbox: Dry-runs generated code in preflight_simulator.py with self-healing retry loop (up to 2 patches).
  3. Dynamic Hot-Reload: Saves verified skills to `skills/custom_<skill_name>.py` and dynamically mounts them at runtime.
  4. Autobiographical Sync: Records evidence in status_registry.py and stores semantic relational nodes in cognitive_graph.py.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import brain
import cognitive_graph
import error_handler
import preflight_simulator
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()
SKILLS_DIR = BASE_DIR / "skills"

MAX_SYNTHESIS_RETRIES = 2


def sanitize_skill_name(name: str) -> str:
    """Normalize task description or custom name into a valid python identifier."""
    if not name:
        return f"skill_{int(time.time())}"
    clean = re.sub(r"[^\w\s]", "", name.lower().strip())
    words = clean.split()[:4]
    slug = "_".join(words) if words else f"skill_{int(time.time())}"
    if not slug.isidentifier():
        slug = f"skill_{slug}"
    return slug


def generate_skill_code(
    task_description: str,
    params: Optional[Dict[str, Any]] = None,
    feedback_error: Optional[str] = None,
    previous_code: Optional[str] = None,
) -> Optional[str]:
    """Prompt brain LLM to write or patch a Python skill module implementing execute(params: dict) -> dict."""
    clean_task = task_description.strip()
    param_hint = json.dumps(params or {}, indent=2)

    if feedback_error and previous_code:
        prompt = f"""You are the SKILL SYNTHESIZER in J.A.R.V.I.S. MARK VIII.
The previous skill code failed validation in the pre-flight sandbox with the following error:

```
{feedback_error}
```

Previous Code:
```python
{previous_code}
```

Task Description: "{clean_task}"
Expected Parameters: {param_hint}

Please write a corrected, self-contained Python module that fixes this error.
Requirements:
1. Must define: `def execute(params: dict) -> dict:`
2. The return dictionary must have `"success": True/False`, `"message": str`, and optionally `"result": Any`.
3. Keep code robust, safe, and return cleanly without throwing unhandled exceptions.
4. Return ONLY valid Python code inside a ```python ``` markdown block."""
    else:
        prompt = f"""You are the SKILL SYNTHESIZER in J.A.R.V.I.S. MARK VIII.
Synthesize an autonomous procedural skill to perform the following task:

Task Description: "{clean_task}"
Sample Parameters: {param_hint}

Requirements:
1. Must define: `def execute(params: dict) -> dict:`
2. The return dictionary must have `"success": True/False`, `"message": str`, and optionally `"result": Any`.
3. Use only standard Python libraries or available modules (e.g. math, datetime, json, os, urllib, re).
4. Return ONLY valid Python code inside a ```python ``` markdown block."""

    try:
        response = brain.ask_llm(prompt, model_type="chat", allow_actions=False)
        if not response:
            return None

        # Extract code from markdown block
        clean = response.strip()
        if "```python" in clean:
            clean = clean.split("```python")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()

        return clean
    except Exception as exc:
        print(f"[SkillSynthesizer] LLM code generation failed: {exc}")
        return None


def validate_skill_in_sandbox(
    skill_code: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 12.0,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Dry-run skill code in preflight_simulator ephemeral sandbox with mock parameters.

    Returns:
        Tuple of (passed: bool, error_or_stdout: str, execution_output: Optional[dict])
    """
    if not skill_code or "def execute" not in skill_code:
        return False, "Code must contain 'def execute(params: dict) -> dict:'", None

    param_json = json.dumps(params or {})

    test_harness = f"""
import json

{skill_code}

if __name__ == "__main__":
    test_params = json.loads('''{param_json}''')
    res = execute(test_params)
    assert isinstance(res, dict), f"Return value must be a dict, got {{type(res)}}"
    print("---OUTPUT_START---")
    print(json.dumps(res))
    print("---OUTPUT_END---")
"""

    sim_res = preflight_simulator.simulate_python_execution(test_harness, timeout=timeout)
    if not sim_res.success or sim_res.exit_code != 0:
        err = sim_res.stderr or sim_res.error or f"Exited with code {sim_res.exit_code}"
        return False, err, None

    # Parse stdout result
    try:
        if "---OUTPUT_START---" in sim_res.stdout and "---OUTPUT_END---" in sim_res.stdout:
            raw_out = sim_res.stdout.split("---OUTPUT_START---")[1].split("---OUTPUT_END---")[0].strip()
            parsed = json.loads(raw_out)
            return True, sim_res.stdout, parsed
    except Exception:
        pass

    return True, sim_res.stdout, {"success": True, "message": "Execution verified"}


def save_and_mount_skill(skill_name: str, skill_code: str) -> Tuple[bool, str, Any]:
    """Write verified skill to skills/custom_<skill_name>.py and dynamically load the module.

    Returns:
        Tuple of (success: bool, file_path: str, loaded_module: Any)
    """
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    clean_name = sanitize_skill_name(skill_name)
    file_name = f"custom_{clean_name}.py"
    file_path = SKILLS_DIR / file_name

    header = f'"""Auto-synthesized skill: {clean_name} for J.A.R.V.I.S. MARK VIII."""\n\n'
    full_content = header + skill_code + "\n"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        # Dynamic import / reload
        module_name = f"skills.custom_{clean_name}"
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
        else:
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            else:
                return False, str(file_path), None

        return True, str(file_path), module

    except Exception as exc:
        print(f"[SkillSynthesizer] Failed mounting skill {clean_name}: {exc}")
        return False, str(file_path), None


def synthesize_skill(
    task_description: str,
    skill_name: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    max_retries: int = MAX_SYNTHESIS_RETRIES,
    fallback_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Full synthesis pipeline: generate code, validate in preflight sandbox with auto-patching, and mount.

    Returns:
        Dictionary with status, skill_name, file_path, code, and module
    """
    registry = get_registry()
    name = skill_name or sanitize_skill_name(task_description)

    current_code = fallback_code or generate_skill_code(task_description, params)
    attempt = 0
    last_error = ""

    while attempt <= max_retries:
        if not current_code:
            break

        passed, out_or_err, _ = validate_skill_in_sandbox(current_code, params)
        if passed:
            # Mount into skills
            mounted, file_path, module = save_and_mount_skill(name, current_code)
            if mounted:
                # Sync status registry and cognitive graph
                cap_id = f"SKILL_{name.upper()}"
                try:
                    registry.set_capability_evidence(
                        "SKILL_SYNTHESIZER",
                        EvidenceLevel.LIVE,
                        f"Synthesized skill: {name} (attempts: {attempt + 1})",
                        source="skill synthesizer",
                    )
                except Exception:
                    pass

                try:
                    cognitive_graph.add_triple(
                        subject=f"Skill:{name}",
                        predicate="synthesized_for_task",
                        obj=task_description[:80],
                        confidence=1.0,
                        source="skill_synthesizer",
                    )
                except Exception:
                    pass

                return {
                    "success": True,
                    "skill_name": name,
                    "file_path": file_path,
                    "code": current_code,
                    "module": module,
                    "retries": attempt,
                }
            else:
                last_error = "Failed to dynamically import module"
                break

        # Validation failed -> auto-patch
        last_error = out_or_err
        print(f"[SkillSynthesizer] Validation attempt {attempt + 1} failed: {last_error[:120]}")
        attempt += 1
        if attempt <= max_retries:
            current_code = generate_skill_code(
                task_description,
                params=params,
                feedback_error=last_error,
                previous_code=current_code,
            )

    registry.set_capability_evidence(
        "SKILL_SYNTHESIZER",
        EvidenceLevel.BROKEN,
        f"Skill synthesis failed for '{name}': {last_error[:80]}",
        source="skill synthesizer",
    )
    return {
        "success": False,
        "skill_name": name,
        "error": last_error or "Synthesis exhausted max retries",
        "retries": attempt,
    }


def synthesize_and_execute_skill(
    task_description: str,
    params: Optional[Dict[str, Any]] = None,
    skill_name: Optional[str] = None,
    fallback_code: Optional[str] = None,
) -> Tuple[bool, str]:
    """Synthesizes an unknown tool on-the-fly and immediately executes it.

    Returns:
        Tuple of (success: bool, response_message: str)
    """
    synth_result = synthesize_skill(
        task_description,
        skill_name=skill_name,
        params=params,
        fallback_code=fallback_code,
    )
    if not synth_result["success"]:
        return False, f"I couldn't synthesize a skill for that task: {synth_result.get('error')}"

    module = synth_result.get("module")
    if not module or not hasattr(module, "execute"):
        return False, "Synthesized skill module missing execute() entry point."

    try:
        res = module.execute(params or {})
        if isinstance(res, dict):
            msg = res.get("message") or str(res.get("result") or "Skill executed successfully.")
            return bool(res.get("success", True)), msg
        return True, str(res)
    except Exception as exc:
        error_handler.log_and_demote(
            "SKILL_SYNTHESIZER",
            exc,
            f"Execution of synthesized skill {synth_result.get('skill_name')}",
            SubsystemState.DEGRADED,
        )
        return False, f"Error executing synthesized skill: {exc}"
