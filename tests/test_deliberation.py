"""Test suite for deliberation.py adversarial deliberation engine."""
import pytest
from unittest.mock import patch, MagicMock

import deliberation


class TestDeliberationEngine:
    """Test cases for Architect, Red-Team Sceptic, and Judge Arbiter personas."""

    def test_heuristic_red_team_audit_clean(self):
        """Test heuristic audit with benign code/command."""
        score, risks = deliberation._heuristic_red_team_audit("print('Hello World')")
        assert score == 0.0
        assert len(risks) == 0

    def test_heuristic_red_team_audit_destructive_rm(self):
        """Test heuristic audit detects dangerous rm -rf."""
        score, risks = deliberation._heuristic_red_team_audit("rm -rf /var/data")
        assert score >= 0.8
        assert any("Dangerous system command" in r for r in risks)

    def test_heuristic_red_team_audit_sensitive_file(self):
        """Test heuristic audit detects access to .env file."""
        score, risks = deliberation._heuristic_red_team_audit("read .env credentials")
        assert score >= 0.7
        assert any("sensitive file" in r for r in risks)

    def test_heuristic_red_team_audit_path_traversal(self):
        """Test heuristic audit detects path traversal."""
        score, risks = deliberation._heuristic_red_team_audit("../../etc/passwd")
        assert score >= 0.8
        assert any("Path traversal" in r for r in risks)

    def test_propose_plan_no_llm(self):
        """Test Architect fallback plan without LLM."""
        plan = deliberation.propose_plan("Refactor math module", use_llm=False)
        assert plan["goal"] == "Refactor math module"
        assert len(plan["steps"]) >= 1
        assert plan["steps"][0]["sandboxed"] is True

    @patch('deliberation.brain.ask_llm')
    def test_propose_plan_with_llm(self, mock_brain):
        """Test Architect plan generation with LLM."""
        mock_brain.return_value = """```json
{
  "goal": "Optimize query",
  "complexity": "high",
  "steps": [
    {"step": 1, "action": "profile_db", "detail": "Run explain", "sandboxed": true},
    {"step": 2, "action": "add_index", "detail": "Create index", "sandboxed": true}
  ]
}
```"""
        plan = deliberation.propose_plan("Optimize query", use_llm=True)
        assert plan["goal"] == "Optimize query"
        assert len(plan["steps"]) == 2
        assert plan["steps"][0]["action"] == "profile_db"

    def test_critique_plan_no_llm_benign(self):
        """Test Red-Team critique on benign plan without LLM."""
        plan = {"goal": "Calculate fibonacci", "steps": [{"action": "compute"}]}
        critique = deliberation.critique_plan(plan, use_llm=False)
        assert critique["risk_score"] == 0.0

    def test_critique_plan_no_llm_hazardous(self):
        """Test Red-Team critique on hazardous command without LLM."""
        plan = {"goal": "Cleanup", "steps": [{"action": "run", "cmd": "del /f /q C:\\*"}]}
        critique = deliberation.critique_plan(plan, use_llm=False)
        assert critique["risk_score"] >= 0.8
        assert len(critique["critical_risks"]) >= 1

    @patch('deliberation.brain.ask_llm')
    def test_critique_plan_with_llm(self, mock_brain):
        """Test Red-Team critique with LLM."""
        mock_brain.return_value = """{
  "risk_score": 0.4,
  "critical_risks": [],
  "warnings": ["Potential memory allocation spike"],
  "failure_modes": ["Out of memory on large inputs"]
}"""
        plan = {"goal": "Large matrix multiply", "steps": [{"action": "matmul"}]}
        critique = deliberation.critique_plan(plan, use_llm=True)
        assert critique["risk_score"] == 0.4
        assert "Potential memory allocation spike" in critique["warnings"]

    def test_synthesize_decision_approved(self):
        """Test Judge approves low-risk benign plan."""
        plan = {"goal": "Inspect memory", "steps": [{"action": "read"}]}
        critique = {"risk_score": 0.1, "critical_risks": [], "warnings": []}

        decision = deliberation.synthesize_decision("Inspect memory", plan, critique)
        assert decision.approved is True
        assert decision.verdict == "APPROVED"
        assert decision.risk_score == 0.1

    def test_synthesize_decision_guardrails(self):
        """Test Judge applies guardrails for moderate risk."""
        plan = {"goal": "Modify config", "steps": [{"action": "edit_file", "target": "settings.json"}]}
        critique = {"risk_score": 0.5, "critical_risks": [], "warnings": ["Config corruption possible"]}

        decision = deliberation.synthesize_decision("Modify config", plan, critique)
        assert decision.approved is True
        assert decision.verdict == "MODIFIED_WITH_GUARDRAILS"
        assert len(decision.modifications) >= 1
        assert decision.final_plan[0]["sandboxed"] is True
        assert decision.final_plan[0]["require_backup"] is True

    def test_synthesize_decision_rejected(self):
        """Test Judge rejects hazardous plan."""
        plan = {"goal": "Format drive", "steps": [{"action": "format"}]}
        critique = {"risk_score": 0.95, "critical_risks": ["Destructive drive format"], "warnings": []}

        decision = deliberation.synthesize_decision("Format drive", plan, critique)
        assert decision.approved is False
        assert decision.verdict == "REJECTED"
        assert decision.risk_score == 0.95
        assert len(decision.final_plan) == 0

    def test_deliberate_end_to_end_benign(self):
        """Test full deliberation pipeline for benign request."""
        decision = deliberation.deliberate("Explain python decorators", use_llm=False)
        assert decision.approved is True
        assert decision.verdict in ("APPROVED", "MODIFIED_WITH_GUARDRAILS")

    def test_deliberate_end_to_end_hazardous(self):
        """Test full deliberation pipeline for hazardous request."""
        decision = deliberation.deliberate("rm -rf / --no-preserve-root", use_llm=False)
        assert decision.approved is False
        assert decision.verdict == "REJECTED"

    def test_deliberate_empty_intent(self):
        """Test deliberation rejects empty intent."""
        decision = deliberation.deliberate("")
        assert decision.approved is False
        assert decision.verdict == "REJECTED"

    def test_deliberation_result_to_dict(self):
        """Test DeliberationResult serializes cleanly to dict."""
        result = deliberation.DeliberationResult(
            approved=True,
            verdict="APPROVED",
            risk_score=0.15,
            final_plan=[{"step": 1}],
            critique="Looks safe",
            modifications=[],
            reason="All checks passed"
        )
        data = result.to_dict()
        assert data["approved"] is True
        assert data["verdict"] == "APPROVED"
        assert data["risk_score"] == 0.15
        assert data["reason"] == "All checks passed"
