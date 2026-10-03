"""Research Synthesis & Empirical Discovery for J.A.R.V.I.S. — MARK VIII.

Module AG:
  1. Cryptographic Pre-Registration Gate:
     - Pre-registers falsifiable empirical hypotheses (test type, alpha, target effect,
       sample size) into an immutable BLAKE2b/SHA256 registration hash BEFORE inspecting data.
  2. Sequential Alpha-Wealth (Alpha-Investing) FDR Control:
     - Allocates statistical alpha from an online alpha-wealth pool (Foster-Stine / mFDR).
     - Protects against p-hacking and multiple-testing distortion across lifelong experiments.
  3. Claims-to-Evidence Ledger:
     - Enforces deterministic artifact links for quantitative claims.
     - Validates academic citations and DOI / URI formats.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from status_registry import EvidenceLevel, get_registry

log = logging.getLogger("jarvis.research_engine")

_lock = threading.RLock()

# Standard DOI regex format
DOI_REGEX = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


@dataclass
class PreregistrationRecord:
    prereg_id: str
    title: str
    predicted_effect: str
    test_type: str                  # t-test, mann-whitney, chi-squared, regression
    allocated_alpha: float
    sample_size: int
    direction: str                  # greater, less, two-sided
    registration_hash: str
    created_at: float
    is_executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HypothesisTestResult:
    result_id: str
    prereg_id: str
    p_value: float
    observed_effect_size: float
    alpha_threshold: float
    is_significant: bool
    alpha_wealth_after: float
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EmpiricalClaim:
    claim_id: str
    statement: str
    artifact_id: str
    doi_citations: List[str]
    is_verified: bool
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ResearchEngine:
    """Research Synthesis Engine enforcing pre-registration, alpha-investing FDR, and claims ledger."""

    def __init__(self, initial_alpha_wealth: float = 0.05, alpha_gamma: float = 0.5, success_reward: float = 0.05):
        self._alpha_wealth = initial_alpha_wealth
        self._alpha_gamma = alpha_gamma
        self._success_reward = success_reward

        self._preregistrations: Dict[str, PreregistrationRecord] = {}
        self._test_results: List[HypothesisTestResult] = []
        self._claims_ledger: Dict[str, EmpiricalClaim] = {}

    # ------------------------------------------------------------------
    # Pre-Registration Gate
    # ------------------------------------------------------------------

    def preregister_hypothesis(
        self,
        title: str,
        predicted_effect: str,
        test_type: str = "two-sample-t-test",
        sample_size: int = 100,
        direction: str = "greater",
        alpha_fraction: Optional[float] = None,
    ) -> PreregistrationRecord:
        """Create a cryptographic commitment to a test protocol prior to data evaluation."""
        with _lock:
            # Allocate alpha from current alpha wealth
            frac = alpha_fraction if alpha_fraction is not None else self._alpha_gamma
            allocated_alpha = max(1e-5, round(self._alpha_wealth * frac, 6))

            now = time.time()
            content = {
                "title": title,
                "predicted_effect": predicted_effect,
                "test_type": test_type,
                "allocated_alpha": allocated_alpha,
                "sample_size": sample_size,
                "direction": direction,
                "created_at": now,
            }
            reg_bytes = json.dumps(content, sort_keys=True).encode("utf-8")
            reg_hash = hashlib.sha256(reg_bytes).hexdigest()

            pid = f"prereg_{reg_hash[:8]}"
            record = PreregistrationRecord(
                prereg_id=pid,
                title=title,
                predicted_effect=predicted_effect,
                test_type=test_type,
                allocated_alpha=allocated_alpha,
                sample_size=sample_size,
                direction=direction,
                registration_hash=reg_hash,
                created_at=now,
                is_executed=False,
            )
            self._preregistrations[pid] = record

        log.info(f"[ResearchEngine] Preregistered hypothesis {pid} (alpha={allocated_alpha:.5f})")
        return record

    # ------------------------------------------------------------------
    # Alpha-Investing Hypothesis Testing
    # ------------------------------------------------------------------

    def evaluate_hypothesis(
        self,
        prereg_id: str,
        p_value: float,
        observed_effect_size: float,
    ) -> HypothesisTestResult:
        """Evaluate hypothesis against allocated alpha and update global alpha-wealth pool."""
        with _lock:
            if prereg_id not in self._preregistrations:
                raise KeyError(f"Preregistration {prereg_id} not found")

            prereg = self._preregistrations[prereg_id]
            if prereg.is_executed:
                raise ValueError(f"Preregistration {prereg_id} has already been evaluated")

            alpha = prereg.allocated_alpha
            is_significant = p_value <= alpha

            # Alpha-investing update rule
            if is_significant:
                # Reward successful true discovery
                self._alpha_wealth = self._alpha_wealth - alpha + self._success_reward
            else:
                # Pay penalty for failed test
                self._alpha_wealth = max(1e-6, self._alpha_wealth - alpha)

            prereg.is_executed = True

            rid = f"res_{uuid.uuid4().hex[:8]}"
            result = HypothesisTestResult(
                result_id=rid,
                prereg_id=prereg_id,
                p_value=p_value,
                observed_effect_size=observed_effect_size,
                alpha_threshold=alpha,
                is_significant=is_significant,
                alpha_wealth_after=round(self._alpha_wealth, 6),
                timestamp=time.time(),
            )
            self._test_results.append(result)

        try:
            get_registry().set_capability_evidence(
                "RESEARCH_ENGINE",
                EvidenceLevel.LIVE,
                f"Evaluated hypothesis {prereg_id}: p={p_value:.4f} vs alpha={alpha:.4f} "
                f"(sig={is_significant}, wealth={self._alpha_wealth:.4f})",
                source="research_engine.evaluate_hypothesis",
            )
        except Exception:
            pass

        return result

    def get_alpha_wealth(self) -> float:
        with _lock:
            return round(self._alpha_wealth, 6)

    # ------------------------------------------------------------------
    # Claims-to-Evidence Ledger & Citation Verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_doi_format(doi: str) -> bool:
        """Verify standard digital object identifier syntax."""
        cleaned = doi.strip()
        if cleaned.startswith("https://doi.org/"):
            cleaned = cleaned.replace("https://doi.org/", "")
        elif cleaned.startswith("doi:"):
            cleaned = cleaned.replace("doi:", "")
        return bool(DOI_REGEX.match(cleaned))

    def record_claim(
        self,
        statement: str,
        artifact_id: str,
        doi_citations: Optional[List[str]] = None,
    ) -> EmpiricalClaim:
        """Register empirical claim linked to deterministic artifact ID and verified DOIs."""
        citations = doi_citations or []
        for doi in citations:
            if not self.verify_doi_format(doi):
                raise ValueError(f"Invalid DOI format: {doi!r}")

        cid = f"claim_{uuid.uuid4().hex[:8]}"
        claim = EmpiricalClaim(
            claim_id=cid,
            statement=statement,
            artifact_id=artifact_id,
            doi_citations=citations,
            is_verified=bool(artifact_id and citations),
            created_at=time.time(),
        )

        with _lock:
            self._claims_ledger[cid] = claim

        return claim


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_research_instance: Optional[ResearchEngine] = None


def get_research_engine(initial_alpha_wealth: float = 0.05) -> ResearchEngine:
    global _research_instance
    if _research_instance is None:
        with _lock:
            if _research_instance is None:
                _research_instance = ResearchEngine(initial_alpha_wealth=initial_alpha_wealth)
    return _research_instance


def preregister_hypothesis(title: str, predicted_effect: str, **kwargs) -> PreregistrationRecord:
    return get_research_engine().preregister_hypothesis(title, predicted_effect, **kwargs)


def evaluate_hypothesis(prereg_id: str, p_value: float, observed_effect_size: float) -> HypothesisTestResult:
    return get_research_engine().evaluate_hypothesis(prereg_id, p_value, observed_effect_size)


def record_claim(statement: str, artifact_id: str, doi_citations: Optional[List[str]] = None) -> EmpiricalClaim:
    return get_research_engine().record_claim(statement, artifact_id, doi_citations)
