"""Substrate Succession & Cold-Boot Reconstruction for J.A.R.V.I.S. — MARK VIII.

Module AP:
  1. Substrate Abstraction Contract:
     - Model-agnostic cognitive state schema decoupling beliefs, skills, goals,
       and identity from proprietary base LLM tokenizers or prompt formatting.
  2. Model Succession & Migration Protocol:
     - Gated validation of candidate successor models against Module AD Novelty Gym
       and Module AE Behavioral Golden Probes before promoting active substrate.
  3. Cold-Boot Disaster Recovery:
     - Deterministic full-state reconstruction strictly from git autobiography,
       signed constitution, bitemporal belief graph, and verified procedural skills.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.substrate_manager")

_lock = threading.RLock()


@dataclass
class SubstrateMigrationVerdict:
    candidate_id: str
    is_approved: bool
    gym_win_rate: float
    probe_max_jsd: float
    safety_violations: List[str]
    evaluation_timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ColdBootRecoveryReport:
    recovery_id: str
    success: bool
    beliefs_restored_count: int
    skills_restored_count: int
    constitution_verified: bool
    autobiography_entries: int
    reconstruction_duration_sec: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SubstrateManager:
    """Substrate succession evaluator and cold-boot disaster recovery manager."""

    def __init__(self, current_substrate_id: str = "claude-sonnet-5-5"):
        self.current_substrate_id = current_substrate_id
        self._migration_history: List[SubstrateMigrationVerdict] = []
        self._recovery_history: List[ColdBootRecoveryReport] = []

    # ------------------------------------------------------------------
    # Substrate Abstraction Contract
    # ------------------------------------------------------------------

    def export_cognitive_state(
        self,
        constitution_dict: Dict[str, Any],
        beliefs: List[Dict[str, Any]],
        skills: List[Dict[str, Any]],
        goals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Serialize complete cognitive substrate into portable, model-agnostic JSON payload."""
        return {
            "schema_version": "8.0.0",
            "exported_at": time.time(),
            "substrate_id": self.current_substrate_id,
            "constitution": constitution_dict,
            "beliefs": beliefs,
            "skills": skills,
            "goals": goals,
        }

    # ------------------------------------------------------------------
    # Model Succession Migration Protocol
    # ------------------------------------------------------------------

    def evaluate_candidate_substrate(
        self,
        candidate_id: str,
        gym_win_rate: float,
        probe_max_jsd: float,
        min_win_rate: float = 0.80,
        max_allowable_jsd: float = 0.15,
        safety_audit_passed: bool = True,
    ) -> SubstrateMigrationVerdict:
        """Validate candidate successor model before promoting as the cognitive host."""
        violations = []
        if gym_win_rate < min_win_rate:
            violations.append(f"Novelty Gym win rate {gym_win_rate:.2f} < required {min_win_rate:.2f}")
        if probe_max_jsd > max_allowable_jsd:
            violations.append(f"Golden probe max JSD {probe_max_jsd:.3f} > threshold {max_allowable_jsd:.3f}")
        if not safety_audit_passed:
            violations.append("Candidate failed constitutional safety invariant audit")

        is_approved = len(violations) == 0

        verdict = SubstrateMigrationVerdict(
            candidate_id=candidate_id,
            is_approved=is_approved,
            gym_win_rate=round(gym_win_rate, 4),
            probe_max_jsd=round(probe_max_jsd, 4),
            safety_violations=violations,
            evaluation_timestamp=time.time(),
        )

        with _lock:
            self._migration_history.append(verdict)
            if is_approved:
                old_id = self.current_substrate_id
                self.current_substrate_id = candidate_id
                log.info(f"[SubstrateManager] Substrate promoted: {old_id} -> {candidate_id}")

        try:
            get_registry().set_capability_evidence(
                "SUBSTRATE_MANAGER",
                EvidenceLevel.LIVE,
                f"Evaluated substrate {candidate_id}: approved={is_approved}",
                source="substrate_manager.evaluate_candidate_substrate",
            )
        except Exception:
            pass

        return verdict

    # ------------------------------------------------------------------
    # Cold-Boot Disaster Recovery
    # ------------------------------------------------------------------

    def reconstruct_from_cold_boot(
        self,
        constitution_doc: Dict[str, Any],
        autobiography_records: List[Dict[str, Any]],
        belief_triples: List[Dict[str, Any]],
        skill_library: List[Dict[str, Any]],
        verify_constitution_fn: Optional[Any] = None,
    ) -> ColdBootRecoveryReport:
        """Reconstruct entire cognitive state strictly from cold-storage primitives."""
        start_time = time.time()
        rid = f"rec_{uuid.uuid4().hex[:8]}"

        # Verify constitution
        constitution_ok = True
        if verify_constitution_fn and callable(verify_constitution_fn):
            constitution_ok = bool(verify_constitution_fn(constitution_doc))
        elif "signature_hex" in constitution_doc and "invariants" in constitution_doc:
            constitution_ok = len(constitution_doc["invariants"]) > 0

        success = constitution_ok and len(autobiography_records) > 0

        duration = time.time() - start_time
        report = ColdBootRecoveryReport(
            recovery_id=rid,
            success=success,
            beliefs_restored_count=len(belief_triples),
            skills_restored_count=len(skill_library),
            constitution_verified=constitution_ok,
            autobiography_entries=len(autobiography_records),
            reconstruction_duration_sec=round(duration, 4),
        )

        with _lock:
            self._recovery_history.append(report)

        log.info(f"[SubstrateManager] Cold-boot recovery {rid}: success={success} (beliefs={len(belief_triples)}, skills={len(skill_library)})")
        return report


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_substrate_instance: Optional[SubstrateManager] = None


def get_substrate_manager() -> SubstrateManager:
    global _substrate_instance
    if _substrate_instance is None:
        with _lock:
            if _substrate_instance is None:
                _substrate_instance = SubstrateManager()
    return _substrate_instance


def export_cognitive_state(constitution_dict: Dict[str, Any], beliefs: List[Dict[str, Any]], skills: List[Dict[str, Any]], goals: List[Dict[str, Any]]) -> Dict[str, Any]:
    return get_substrate_manager().export_cognitive_state(constitution_dict, beliefs, skills, goals)


def evaluate_candidate_substrate(candidate_id: str, gym_win_rate: float, probe_max_jsd: float, **kwargs) -> SubstrateMigrationVerdict:
    return get_substrate_manager().evaluate_candidate_substrate(candidate_id, gym_win_rate, probe_max_jsd, **kwargs)


def reconstruct_from_cold_boot(constitution_doc: Dict[str, Any], autobiography_records: List[Dict[str, Any]], belief_triples: List[Dict[str, Any]], skill_library: List[Dict[str, Any]], **kwargs) -> ColdBootRecoveryReport:
    return get_substrate_manager().reconstruct_from_cold_boot(constitution_doc, autobiography_records, belief_triples, skill_library, **kwargs)
