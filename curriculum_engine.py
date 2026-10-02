"""Autonomous Curriculum Loop (Voyager Phase 2) for J.A.R.V.I.S. — MARK VIII.

Drives self-directed capability acquisition during quiescent periods (> 25 min idle) by:
  1. Gap Identification: Probing missing action intents, unverified status registry capabilities,
     and common OS automation patterns.
  2. Safe Hypothesis & Task Formulation: Generating structured skill proposals under strict safety guardrails.
  3. Execution & Verification: Dispatching proposals to skill_synthesizer.py and preflight_simulator.py.
  4. Knowledge Assimilation: Registering verified skills in `skills/` and recording relational milestones in cognitive_graph.py.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import cognitive_graph
import error_handler
import psutil
import skill_synthesizer
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.curriculum_engine")

BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_IDLE_QUIESCENCE_SECONDS = 1500.0  # 25 minutes
DEFAULT_MAX_CPU_PERCENT = 25.0

# ── DESTRUCTIVE SYSTEM PATTERNS (GUARDRAILS) ─────────────────────────────────
DESTRUCTIVE_PATTERNS = [
    r"\bformat\s+[a-zA-Z]:",
    r"\brmdir\s+/[sq]",
    r"\brm\s+-rf\b",
    r"\bdel\s+/[fsq]",
    r"\bbootrec\b",
    r"\bbcdedit\b",
    r"\breg\s+delete\b",
    r"\bdiskpart\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"/dev/sd[a-z]",
    r"\bshutdown\s+/[sr]",
    r"\breboot\b",
    r"system32",
    r"windows[\\/]system",
]

# ── BUILT-IN OS AUTOMATION & UTILITY CURRICULUM CATALOG ───────────────────────
BUILTIN_CURRICULUM_TASKS: List[Dict[str, Any]] = [
    {
        "name": "deduplicate_directory",
        "description": "Find and report duplicate files in a directory using SHA256 checksums.",
        "rationale": "Automates disk cleanup audits without destructive deletion.",
        "category": "os_automation",
        "sample_params": {"directory_path": "data/temp"},
        "safety_score": 0.95,
    },
    {
        "name": "extract_pdf_text",
        "description": "Extract plain text and metadata from PDF documents safely.",
        "rationale": "Enables local document ingestion without external APIs.",
        "category": "document_intelligence",
        "sample_params": {"file_path": "data/sample.pdf"},
        "safety_score": 0.98,
    },
    {
        "name": "network_ping_audit",
        "description": "Measure ICMP latency and reachability for diagnostic host targets.",
        "rationale": "Provides autonomous network telemetry and offline diagnosis.",
        "category": "diagnostics",
        "sample_params": {"host": "127.0.0.1", "count": 2},
        "safety_score": 0.95,
    },
    {
        "name": "calculate_directory_checksums",
        "description": "Generate a SHA256 integrity manifest for all files in a directory.",
        "rationale": "Enables codebase verification and file tamper detection.",
        "category": "security",
        "sample_params": {"directory_path": "skills"},
        "safety_score": 0.99,
    },
    {
        "name": "check_local_open_ports",
        "description": "Scan standard local loopback ports to verify active daemon endpoints.",
        "rationale": "Validates local background service availability.",
        "category": "diagnostics",
        "sample_params": {"ports": [18777, 11434, 8000]},
        "safety_score": 0.95,
    },
    {
        "name": "audit_large_temp_files",
        "description": "Scan temporary directories and report files exceeding size threshold.",
        "rationale": "Prevents storage exhaustion during long autonomous sessions.",
        "category": "maintenance",
        "sample_params": {"min_size_mb": 50},
        "safety_score": 0.95,
    },
    {
        "name": "audit_running_subprocesses",
        "description": "Enumerate running Python child processes and their CPU/RAM metrics.",
        "rationale": "Provides process supervision and leak detection.",
        "category": "telemetry",
        "sample_params": {"filter_name": "python"},
        "safety_score": 0.98,
    },
    {
        "name": "normalize_csv_dataset",
        "description": "Parse a CSV file, strip whitespace from headers, and report missing values.",
        "rationale": "Prepares tabular telemetry and logs for statistical analysis.",
        "category": "data_processing",
        "sample_params": {"file_path": "data/telemetry.csv"},
        "safety_score": 0.99,
    },
]


class CurriculumEngine:
    """Voyager Phase 2: Autonomous Curriculum Loop for self-directed skill synthesis."""

    def __init__(
        self,
        idle_threshold: float = DEFAULT_IDLE_QUIESCENCE_SECONDS,
        max_cpu_percent: float = DEFAULT_MAX_CPU_PERCENT,
    ):
        self.idle_threshold = idle_threshold
        self.max_cpu_percent = max_cpu_percent
        self._lock = threading.RLock()
        self._last_user_activity = time.time()
        self._experiments_in_current_window = 0
        self._acquired_skills: Set[str] = set()
        self._history: List[Dict[str, Any]] = []

    def mark_user_activity(self) -> None:
        """Signal that user or active agent task has occurred; resets idle window cap."""
        with self._lock:
            self._last_user_activity = time.time()
            self._experiments_in_current_window = 0

    def get_idle_duration(self) -> float:
        """Get elapsed seconds since last user interaction."""
        with self._lock:
            return time.time() - self._last_user_activity

    def is_curriculum_eligible(self) -> Tuple[bool, str]:
        """Check whether system conditions permit autonomous curriculum execution."""
        idle = self.get_idle_duration()
        if idle < self.idle_threshold:
            return False, f"Recent user activity ({idle:.1f}s < {self.idle_threshold:.1f}s)"

        with self._lock:
            if self._experiments_in_current_window >= 1:
                return False, f"Experiment cap reached for current idle window (max 1)"

        try:
            cpu = psutil.cpu_percent(interval=None)
            if cpu > self.max_cpu_percent:
                return False, f"CPU load exceeds threshold ({cpu:.1f}% > {self.max_cpu_percent:.1f}%)"
        except Exception:
            pass

        return True, "Eligible for autonomous curriculum synthesis"

    def is_safe_proposal(self, task_description: str, params: Optional[dict] = None) -> Tuple[bool, str]:
        """Guardrail: verify proposal contains no destructive OS commands or paths."""
        text_to_check = f"{task_description} {str(params or '')}".lower()

        for pattern in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, text_to_check, re.IGNORECASE):
                return False, f"Destructive pattern rejected by safety guardrail: '{pattern}'"

        return True, "Safe"

    def probe_learning_gaps(self) -> List[Dict[str, Any]]:
        """Identify capability gaps from status_registry, errors, and catalog."""
        gaps: List[Dict[str, Any]] = []
        registry = get_registry()

        # Check existing installed skills in skills/ directory
        skills_dir = BASE_DIR / "skills"
        existing_skills = set()
        if skills_dir.exists():
            for f in skills_dir.glob("custom_*.py"):
                name = f.stem.replace("custom_", "")
                existing_skills.add(name)

        with self._lock:
            known_mastered = self._acquired_skills | existing_skills

        # 1. Probe unmastered builtin tasks
        for task in BUILTIN_CURRICULUM_TASKS:
            if task["name"] not in known_mastered:
                gaps.append({
                    "name": task["name"],
                    "description": task["description"],
                    "rationale": task["rationale"],
                    "category": task["category"],
                    "sample_params": task["sample_params"],
                    "safety_score": task["safety_score"],
                    "source": "curriculum_catalog",
                })

        # 2. Probe unverified capabilities in status registry
        try:
            capabilities = registry.get_capabilities()
            for cap_name, cap_info in capabilities.items():
                evidence = cap_info.get("evidence", EvidenceLevel.NONE)
                if evidence in (EvidenceLevel.NONE, EvidenceLevel.BLOCKED) and cap_name.lower() not in known_mastered:
                    clean_name = cap_name.lower()
                    gaps.append({
                        "name": f"verify_{clean_name}",
                        "description": f"Autonomous diagnostic probe to verify {cap_name} capability.",
                        "rationale": f"Capability {cap_name} currently has evidence level {evidence}.",
                        "category": "system_probe",
                        "sample_params": {},
                        "safety_score": 0.95,
                        "source": "status_registry_gap",
                    })
        except Exception:
            pass

        return gaps

    def propose_curriculum_task(self) -> Optional[Dict[str, Any]]:
        """Select and formulate the highest priority valid curriculum proposal."""
        gaps = self.probe_learning_gaps()
        if not gaps:
            return None

        for candidate in gaps:
            safe, reason = self.is_safe_proposal(candidate["description"], candidate.get("sample_params"))
            if safe:
                return candidate
            else:
                log.warning(f"[CurriculumEngine] Skipping unsafe gap: {candidate.get('name')} ({reason})")

        return None

    def run_curriculum_experiment(self, proposal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute autonomous synthesis for a target proposal and update knowledge graph."""
        # 1. Check eligibility
        eligible, reason = self.is_curriculum_eligible()
        if not eligible and proposal is None:
            return {
                "success": False,
                "status": "ineligible",
                "reason": reason,
            }

        # 2. Get proposal
        target = proposal or self.propose_curriculum_task()
        if not target:
            return {
                "success": False,
                "status": "no_gaps",
                "message": "No unmastered curriculum gaps available.",
            }

        task_name = target["name"]
        description = target["description"]
        params = target.get("sample_params", {})

        # 3. Guardrail safety check
        safe, safe_reason = self.is_safe_proposal(description, params)
        if not safe:
            log.error(f"[CurriculumEngine] Safety violation: {safe_reason}")
            return {
                "success": False,
                "status": "safety_violation",
                "message": safe_reason,
            }

        log.info(f"[CurriculumEngine] Starting experiment on target skill: {task_name}")

        # 4. Dispatch to skill synthesizer
        try:
            synth_res = skill_synthesizer.synthesize_skill(
                task_description=description,
                skill_name=task_name,
                params=params,
            )
        except Exception as exc:
            error_handler.log_and_demote(
                subsystem="CURRICULUM_ENGINE",
                exception=exc,
                context=f"Autonomous skill synthesis for {task_name}",
                demote_to=SubsystemState.DEGRADED,
            )
            return {
                "success": False,
                "status": "synthesis_error",
                "message": f"Synthesis exception: {exc}",
            }

        success = bool(synth_res.get("success", False))

        with self._lock:
            self._experiments_in_current_window += 1
            if success:
                self._acquired_skills.add(task_name)

            record = {
                "task_name": task_name,
                "success": success,
                "timestamp": time.time(),
                "file_path": synth_res.get("file_path"),
                "status": synth_res.get("status"),
            }
            self._history.append(record)

        # 5. Record knowledge graph triple on success
        if success:
            try:
                cognitive_graph.add_triple(
                    subject="JARVIS",
                    predicate="acquired_skill",
                    object=task_name,
                    confidence=1.0,
                    source="curriculum_engine",
                )
            except Exception as graph_exc:
                log.warning(f"[CurriculumEngine] Failed writing triple to cognitive graph: {graph_exc}")

            try:
                get_registry().set_capability_evidence(
                    "CURRICULUM_ENGINE",
                    EvidenceLevel.LIVE,
                    f"Successfully synthesized and verified skill '{task_name}'",
                    source="curriculum_engine",
                )
            except Exception:
                pass

        return {
            "success": success,
            "status": "completed" if success else "failed",
            "skill_name": task_name,
            "details": synth_res.get("message", "Synthesis completed"),
            "file_path": synth_res.get("file_path"),
        }


# ── SINGLETON & CONVENIENCE FUNCTIONS ───────────────────────────────────────
_curriculum_engine = CurriculumEngine()


def get_curriculum_engine() -> CurriculumEngine:
    """Get the singleton curriculum engine."""
    return _curriculum_engine


def propose_curriculum_task() -> Optional[Dict[str, Any]]:
    """Propose the next autonomous learning goal."""
    return _curriculum_engine.propose_curriculum_task()


def run_curriculum_experiment(proposal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run an autonomous curriculum experiment."""
    return _curriculum_engine.run_curriculum_experiment(proposal)


def mark_user_activity() -> None:
    """Reset the curriculum idle window cap."""
    _curriculum_engine.mark_user_activity()
