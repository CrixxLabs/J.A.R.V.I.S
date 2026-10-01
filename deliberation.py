"""Adversarial Deliberation Engine for J.A.R.V.I.S. — MARK VIII.

Implements a multi-persona cognitive deliberation loop for high-complexity, destructive,
or ambiguous plans before execution:
  1. Architect Persona: Formulates structured multi-step execution plans.
  2. Red-Team Sceptic Persona: Adversarially audits for filesystem collisions,
     destructive operations, credential exposure, rate limits, and security vulnerabilities.
  3. Judge Arbiter: Evaluates critiques, modifies plans with guardrails, or rejects unsafe steps.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import brain
import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

# Dangerous patterns monitored by deterministic heuristic audits
DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bdel\s+/[fFqsSQ\s]+",
    r"\bformat\s+[a-zA-Z]:",
    r"\bdrop\s+database\b",
    r"\bdrop\s+table\b",
    r"\bshutdown\b",
    r"\brestart\b",
    r":\(\)\{\s*:\|:&\s*\};:",  # Fork bomb
    r"\bos\.system\(.*rm\s+-rf.*\)",
    r"\bshutil\.rmtree\(",
]

SENSITIVE_FILE_PATTERNS = [
    r"\.env",
    r"id_rsa",
    r"\.pem$",
    r"\.key$",
    r"credentials\.json",
    r"token\.json",
]


class DeliberationResult:
    """Outcome of the 3-persona adversarial deliberation."""

    def __init__(
        self,
        approved: bool,
        verdict: str,
        risk_score: float,
        final_plan: List[Dict[str, Any]],
        critique: str = "",
        modifications: Optional[List[str]] = None,
        reason: str = "",
    ):
        self.approved = approved
        self.verdict = verdict  # "APPROVED", "MODIFIED_WITH_GUARDRAILS", "REJECTED"
        self.risk_score = max(0.0, min(1.0, float(risk_score)))
        self.final_plan = final_plan
        self.critique = critique
        self.modifications = modifications or []
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "verdict": self.verdict,
            "risk_score": round(self.risk_score, 3),
            "final_plan": self.final_plan,
            "critique": self.critique,
            "modifications": self.modifications,
            "reason": self.reason,
        }


def _heuristic_red_team_audit(text: str) -> Tuple[float, List[str]]:
    """Fast deterministic safety audit for obvious hazardous patterns."""
    risks = []
    score = 0.0

    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            risks.append(f"Dangerous system command or destructive operation pattern: {pattern}")
            score = max(score, 0.9)

    for pattern in SENSITIVE_FILE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            risks.append(f"Direct access/exposure risk for sensitive file pattern: {pattern}")
            score = max(score, 0.7)

    if re.search(r"\.\./\.\./", text):
        risks.append("Path traversal attempt detected")
        score = max(score, 0.85)

    return score, risks


def propose_plan(intent: str, context: Optional[str] = None, use_llm: bool = True) -> Dict[str, Any]:
    """Architect Persona: Generates a structured multi-step execution proposal.

    Args:
        intent: User request or high-complexity problem description
        context: Optional environmental or state snapshot context
        use_llm: Whether to invoke brain LLM or fallback to fast structured plan

    Returns:
        Dict representing the proposed execution plan with steps
    """
    if not intent or not intent.strip():
        return {"goal": "", "steps": [], "complexity": "low"}

    if not use_llm:
        return {
            "goal": intent.strip(),
            "steps": [{"step": 1, "action": "execute_task", "target": intent.strip(), "sandboxed": True}],
            "complexity": "moderate",
        }

    prompt = f"""You are the ARCHITECT persona in the J.A.R.V.I.S. deliberation engine.
Formulate a structured execution plan for this goal.

User Intent: "{intent}"
Context: "{context or 'Standard desktop workstation'}"

Format as JSON:
{{
  "goal": "{intent}",
  "complexity": "high",
  "steps": [
    {{"step": 1, "action": "inspect_target", "detail": "...", "sandboxed": true}},
    {{"step": 2, "action": "execute_operation", "detail": "...", "sandboxed": true}}
  ],
  "requires_filesystem": true,
  "requires_network": false
}}
Return ONLY valid JSON."""

    try:
        response = brain.ask_llm(prompt, model_type="fast", allow_actions=False)
        if response:
            clean = response.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and "steps" in parsed:
                return parsed
    except Exception as exc:
        print(f"[Deliberation][Architect] Proposal LLM failed: {exc}")

    return {
        "goal": intent.strip(),
        "steps": [{"step": 1, "action": "execute_task", "detail": intent.strip(), "sandboxed": True}],
        "complexity": "moderate",
    }


def critique_plan(plan: Dict[str, Any], context: Optional[str] = None, use_llm: bool = True) -> Dict[str, Any]:
    """Red-Team Sceptic Persona: Audits the plan for failure modes and security risks.

    Args:
        plan: Proposed plan from Architect
        context: Environmental context
        use_llm: Whether to invoke LLM adversarial audit

    Returns:
        Dict with risk_score, critical_risks, warnings, and failure_modes
    """
    plan_str = json.dumps(plan, ensure_ascii=False)
    heuristic_score, heuristic_risks = _heuristic_red_team_audit(plan_str)

    if not use_llm:
        return {
            "risk_score": heuristic_score,
            "critical_risks": heuristic_risks,
            "warnings": ["Heuristic evaluation only"],
            "failure_modes": ["Unverified execution path"],
        }

    prompt = f"""You are the RED-TEAM SCEPTIC persona in the J.A.R.V.I.S. deliberation engine.
Adversarially audit this proposed execution plan for failure modes, destructive operations,
unverified dependencies, irreversible overwrites, credential leaks, and filesystem hazards.

Proposed Plan:
{plan_str}

Format as JSON:
{{
  "risk_score": 0.2,
  "critical_risks": ["..."],
  "warnings": ["..."],
  "failure_modes": ["..."]
}}
Risk score must be between 0.0 (completely benign) and 1.0 (extremely hazardous).
Return ONLY valid JSON."""

    try:
        response = brain.ask_llm(prompt, model_type="fast", allow_actions=False)
        if response:
            clean = response.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            parsed = json.loads(clean)
            if isinstance(parsed, dict) and "risk_score" in parsed:
                # Combine heuristic risks with LLM findings
                llm_score = float(parsed.get("risk_score", 0.0))
                final_score = max(heuristic_score, llm_score)
                crit_risks = list(set(parsed.get("critical_risks", []) + heuristic_risks))
                return {
                    "risk_score": final_score,
                    "critical_risks": crit_risks,
                    "warnings": parsed.get("warnings", []),
                    "failure_modes": parsed.get("failure_modes", []),
                }
    except Exception as exc:
        print(f"[Deliberation][RedTeam] Critique LLM failed: {exc}")

    return {
        "risk_score": heuristic_score,
        "critical_risks": heuristic_risks,
        "warnings": [],
        "failure_modes": [],
    }


def synthesize_decision(
    intent: str, proposed_plan: Dict[str, Any], critique: Dict[str, Any]
) -> DeliberationResult:
    """Judge Arbiter: Evaluates critique, applies guardrails, or rejects hazardous steps.

    Args:
        intent: Original goal
        proposed_plan: Plan from Architect
        critique: Critique from Red-Team

    Returns:
        DeliberationResult with final verdict and safeguarded plan
    """
    risk_score = float(critique.get("risk_score", 0.0))
    critical_risks = critique.get("critical_risks", [])
    steps = proposed_plan.get("steps", [])

    # Case 1: Extreme Hazard -> REJECT
    if risk_score >= 0.85 or any("destructive" in r.lower() or "traversal" in r.lower() for r in critical_risks):
        reason = f"Plan rejected due to critical risks: {'; '.join(critical_risks)}"
        return DeliberationResult(
            approved=False,
            verdict="REJECTED",
            risk_score=risk_score,
            final_plan=[],
            critique=str(critique),
            modifications=["Execution aborted to preserve system stability."],
            reason=reason,
        )

    # Case 2: Moderate Risk -> ADD GUARDRAILS
    if risk_score > 0.3 or critical_risks or critique.get("warnings"):
        modifications = [
            "Enforce strict ephemeral sandbox execution",
            "Shadow-copy target files before modification",
            "Enable pre-flight dry-run simulation",
            "Cap execution timeout at 15 seconds",
        ]
        guarded_steps = []
        for s in steps:
            guarded = dict(s)
            guarded["sandboxed"] = True
            guarded["require_backup"] = True
            guarded["timeout"] = 15.0
            guarded_steps.append(guarded)

        return DeliberationResult(
            approved=True,
            verdict="MODIFIED_WITH_GUARDRAILS",
            risk_score=risk_score,
            final_plan=guarded_steps,
            critique=str(critique),
            modifications=modifications,
            reason="Guardrails and pre-flight sandbox constraints injected.",
        )

    # Case 3: Low Risk -> APPROVE
    return DeliberationResult(
        approved=True,
        verdict="APPROVED",
        risk_score=risk_score,
        final_plan=steps,
        critique=str(critique),
        modifications=[],
        reason="Plan validated and confirmed safe for execution.",
    )


def deliberate(
    intent: str,
    proposed_action: Optional[Dict[str, Any]] = None,
    context: Optional[str] = None,
    use_llm: bool = True,
) -> DeliberationResult:
    """Full 3-persona adversarial deliberation pipeline.

    Args:
        intent: User query or planned task
        proposed_action: Optional initial action dict
        context: System or conversation context
        use_llm: Whether to invoke brain LLM personas

    Returns:
        DeliberationResult with synthesized verdict
    """
    registry = get_registry()

    if not intent or not intent.strip():
        return DeliberationResult(
            approved=False,
            verdict="REJECTED",
            risk_score=1.0,
            final_plan=[],
            reason="Empty intent payload",
        )

    try:
        # Phase 1: Architect proposes plan
        plan = propose_plan(intent, context=context, use_llm=use_llm)
        if proposed_action:
            plan.setdefault("steps", []).insert(0, proposed_action)

        # Phase 2: Red-Team audits plan
        critique = critique_plan(plan, context=context, use_llm=use_llm)

        # Phase 3: Judge synthesizes decision
        decision = synthesize_decision(intent, plan, critique)

        evidence_level = EvidenceLevel.LIVE if decision.approved else EvidenceLevel.BLOCKED
        registry.set_capability_evidence(
            "DELIBERATION_ENGINE",
            evidence_level,
            f"Deliberation verdict: {decision.verdict} (Risk: {decision.risk_score:.2f})",
            source="adversarial deliberation",
        )

        return decision

    except Exception as exc:
        error_handler.log_and_demote(
            "DELIBERATION_ENGINE",
            exc,
            f"Deliberation for: {intent[:60]}",
            SubsystemState.DEGRADED,
        )
        # Safe fallback: reject or strictly guard
        return DeliberationResult(
            approved=False,
            verdict="REJECTED",
            risk_score=1.0,
            final_plan=[],
            reason=f"Deliberation internal error: {exc}",
        )
