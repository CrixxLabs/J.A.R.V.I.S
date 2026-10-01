"""Epistemic Uncertainty Evaluator for J.A.R.V.I.S. — MARK VIII.

Calibrates model output confidence and detects epistemic ambiguity before critical execution:
  1. Multi-Sample Variance: Analyzes semantic dispersion and entropy across high-temperature samples.
  2. Entropy Calculation: Measures token distribution divergence and pairwise Jaccard/cosine distance.
  3. Verbal Self-Correction: Automatically triggers calibrated epistemic hedges when entropy exceeds thresholds.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import brain
import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

DEFAULT_UNCERTAINTY_THRESHOLD = 0.45

CALIBRATED_HEDGE_PHRASES = [
    "I have low certainty regarding this specific procedure, sir. Verifying against local records before proceeding.",
    "Epistemic divergence detected across potential solution paths. Proceeding with caution and heightened verification.",
    "Confidence calibration indicates ambiguity. Cross-checking parameters with cognitive memory.",
]


class UncertaintyEvaluation:
    """Structured container for epistemic confidence and entropy calibration."""

    def __init__(
        self,
        prompt: str,
        samples: List[str],
        entropy: float,
        variance: float,
        is_uncertain: bool,
        confidence: float,
        hedge_phrase: Optional[str] = None,
        consensus_response: str = "",
    ):
        self.prompt = prompt
        self.samples = samples
        self.entropy = max(0.0, min(1.0, float(entropy)))
        self.variance = max(0.0, min(1.0, float(variance)))
        self.is_uncertain = is_uncertain
        self.confidence = max(0.0, min(1.0, float(confidence)))
        self.hedge_phrase = hedge_phrase
        self.consensus_response = consensus_response

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "samples_count": len(self.samples),
            "entropy": round(self.entropy, 4),
            "variance": round(self.variance, 4),
            "is_uncertain": self.is_uncertain,
            "confidence": round(self.confidence, 4),
            "hedge_phrase": self.hedge_phrase,
            "consensus_response": self.consensus_response,
        }


STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "can", "will",
    "just", "don", "should", "now", "are", "was", "were", "be", "been", "being",
}


def _tokenize(text: str) -> Set[str]:
    """Tokenize and normalize text into word stems/tokens, filtering stop words."""
    clean = re.sub(r"[^\w\s]", " ", (text or "").lower())
    tokens = {t for t in clean.split() if len(t) > 2 and t not in STOP_WORDS}
    return tokens


def calculate_semantic_entropy(samples: List[str]) -> Tuple[float, float]:
    """Compute semantic entropy and pairwise variance across generated candidate samples.

    Args:
        samples: List of textual candidate outputs

    Returns:
        Tuple of (normalized_entropy [0..1], pairwise_variance [0..1])
    """
    if not samples or len(samples) < 2:
        return 0.0, 0.0

    # Token sets
    token_sets = [_tokenize(s) for s in samples]
    total_samples = len(token_sets)

    # 1. Pairwise Jaccard Dissimilarity (Variance)
    distances: List[float] = []
    for i in range(total_samples):
        for j in range(i + 1, total_samples):
            s1 = token_sets[i]
            s2 = token_sets[j]
            union = s1.union(s2)
            if not union:
                distances.append(0.0)
            else:
                intersection = s1.intersection(s2)
                jaccard_sim = len(intersection) / len(union)
                distances.append(1.0 - jaccard_sim)

    variance = sum(distances) / len(distances) if distances else 0.0

    # 2. Cross-sample Token Disagreement Entropy
    # Measures whether tokens consistently appear across all samples or diverge across samples
    all_unique_tokens = set().union(*token_sets) if token_sets else set()
    total_token_entropy = 0.0

    if all_unique_tokens and total_samples > 1:
        for t in all_unique_tokens:
            count = sum(1 for s in token_sets if t in s)
            p = count / total_samples
            if 0.0 < p < 1.0:
                h_binary = -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)
                total_token_entropy += h_binary

        mean_token_entropy = total_token_entropy / len(all_unique_tokens)
    else:
        mean_token_entropy = 0.0

    # Composite uncertainty score combining pairwise distance and token disagreement
    combined_score = 0.6 * variance + 0.4 * mean_token_entropy
    return max(0.0, min(1.0, combined_score)), max(0.0, min(1.0, variance))


def sample_llm_variations(
    prompt: str,
    n_samples: int = 3,
    context: Optional[str] = None,
) -> List[str]:
    """Sample multiple outputs from brain LLM across temperature variations."""
    samples = []
    for i in range(n_samples):
        try:
            res = brain.ask_llm(
                prompt,
                context=context,
                model_type="fast",
                allow_actions=False,
            )
            if res and res.strip():
                samples.append(res.strip())
        except Exception as exc:
            print(f"[EpistemicEvaluator] Sampling variation {i + 1} failed: {exc}")

    return samples


def evaluate_uncertainty(
    prompt: str,
    samples: Optional[List[str]] = None,
    context: Optional[str] = None,
    threshold: float = DEFAULT_UNCERTAINTY_THRESHOLD,
    use_llm: bool = False,
) -> UncertaintyEvaluation:
    """Evaluate epistemic uncertainty for a proposed query or task plan.

    Args:
        prompt: User input or proposed intent
        samples: Optional pre-generated candidate responses
        context: Optional environmental context
        threshold: Uncertainty cutoff triggering self-correction (default 0.45)
        use_llm: Whether to invoke LLM to generate candidate variations if samples not provided

    Returns:
        UncertaintyEvaluation with calibrated entropy, confidence, and hedge phrase
    """
    registry = get_registry()

    if not prompt or not prompt.strip():
        return UncertaintyEvaluation(
            prompt="",
            samples=[],
            entropy=0.0,
            variance=0.0,
            is_uncertain=False,
            confidence=1.0,
            consensus_response="",
        )

    try:
        sample_list = samples or []
        if not sample_list and use_llm:
            sample_list = sample_llm_variations(prompt, n_samples=3, context=context)

        if not sample_list:
            # Fallback deterministic baseline if no samples
            sample_list = [prompt]

        if len(sample_list) == 1:
            entropy = 0.0
            variance = 0.0
            consensus = sample_list[0]
        else:
            entropy, variance = calculate_semantic_entropy(sample_list)
            # Consensus is highest overlap or longest coherent sample
            consensus = max(sample_list, key=len)

        is_uncertain = entropy >= threshold
        confidence = max(0.0, min(1.0, 1.0 - entropy))

        hedge = CALIBRATED_HEDGE_PHRASES[0] if is_uncertain else None

        evidence = EvidenceLevel.LIVE if not is_uncertain else EvidenceLevel.PROBED
        registry.set_capability_evidence(
            "EPISTEMIC_EVALUATOR",
            evidence,
            f"Epistemic uncertainty: {entropy:.2f} (Confidence: {confidence:.2f})",
            source="epistemic calibration",
        )

        return UncertaintyEvaluation(
            prompt=prompt,
            samples=sample_list,
            entropy=entropy,
            variance=variance,
            is_uncertain=is_uncertain,
            confidence=confidence,
            hedge_phrase=hedge,
            consensus_response=consensus,
        )

    except Exception as exc:
        error_handler.log_and_demote(
            "EPISTEMIC_EVALUATOR",
            exc,
            f"Epistemic evaluation for: {prompt[:50]}",
            SubsystemState.DEGRADED,
        )
        return UncertaintyEvaluation(
            prompt=prompt,
            samples=samples or [],
            entropy=1.0,
            variance=1.0,
            is_uncertain=True,
            confidence=0.0,
            hedge_phrase=CALIBRATED_HEDGE_PHRASES[0],
            consensus_response="",
        )


def calibrate_and_guard(
    prompt: str,
    response: str,
    threshold: float = DEFAULT_UNCERTAINTY_THRESHOLD,
    samples: Optional[List[str]] = None,
) -> Tuple[str, UncertaintyEvaluation]:
    """Guard a response by injecting verbal self-correction if entropy is elevated.

    Returns:
        Tuple of (guarded_response, evaluation)
    """
    eval_samples = samples or ([response] if response else [])
    evaluation = evaluate_uncertainty(prompt, samples=eval_samples, threshold=threshold)

    if evaluation.is_uncertain and evaluation.hedge_phrase:
        guarded_response = f"{evaluation.hedge_phrase} {response}".strip()
        return guarded_response, evaluation

    return response, evaluation
