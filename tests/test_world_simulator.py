"""Unit tests for Predictive World Model & Counterfactual Replay Engine (Module T)."""
from pathlib import Path
import pytest

from world_simulator import (
    WorldSimulator,
    ActionCandidate,
    ACTION_ACT,
    ACTION_PROBE,
    ACTION_ASK,
    ACTION_WAIT,
)


@pytest.fixture
def simulator(tmp_path):
    traces_file = tmp_path / "test_traces.json"
    return WorldSimulator(traces_path=traces_file)


def test_active_inference_ranking(simulator):
    # Candidate 1: Blind ACT (high risk, moderate utility)
    c1 = ActionCandidate(
        action_id="blind_deploy",
        action_type=ACTION_ACT,
        target_goal="deploy_service",
        expected_pragmatic_utility=0.7,
        epistemic_information_gain=0.0,
        execution_cost=0.3,
        risk_penalty=0.4,
    )

    # Candidate 2: PROBE (low risk, high information gain)
    c2 = ActionCandidate(
        action_id="probe_health_endpoint",
        action_type=ACTION_PROBE,
        target_goal="inspect_readiness",
        expected_pragmatic_utility=0.2,
        epistemic_information_gain=0.9,
        execution_cost=0.05,
        risk_penalty=0.0,
    )

    res = simulator.evaluate_active_inference(
        candidates=[c1, c2],
        epistemic_weight=1.2,
        pragmatic_weight=1.0,
    )
    assert res["selected_action"]["action_id"] == "probe_health_endpoint"
    assert res["free_energy_score"] > 0


def test_counterfactual_replay_failure_turnaround(simulator):
    # Historical Trace: Failed because auth_token was missing
    tid = simulator.record_event_trace(
        trace_id="trace_api_failure_001",
        initial_state={"auth_token": None, "network": "online"},
        actions_taken=[{"action": "send_request", "params": {"endpoint": "/api/v1/data"}}],
        final_outcome={"success": False, "status_code": 401},
        error_logs=["Unauthorized: missing auth token"],
    )

    # Alternative Policy: First acquire token, then send request
    alt_policy = [
        {
            "action": "refresh_auth",
            "state_mutations": {"auth_token": "valid_bearer_token"},
        },
        {
            "action": "send_request",
            "requires_precondition": {"auth_token": "valid_bearer_token"},
            "state_mutations": {"response_received": True},
        },
    ]

    replay_res = simulator.replay_counterfactual(tid, alt_policy)
    assert replay_res["success"] is True
    assert replay_res["original_success"] is False
    assert replay_res["counterfactual_success"] is True
    assert replay_res["delta_improvement"] == 1.0
    assert replay_res["counterfactual_regret"] > 0
    assert replay_res["simulated_final_state"]["auth_token"] == "valid_bearer_token"


def test_dry_run_sandboxed_rehearsal(simulator, tmp_path):
    sandbox = tmp_path / "sandbox_env"

    # 1. Virtual file creation & diff check
    file_res = simulator.dry_run_action(
        action_type="write_file",
        params={"path": "config/settings.json", "content": '{"port": 8080, "ssl": true}'},
        sandbox_dir=sandbox,
    )
    assert file_res["safe"] is True
    assert file_res["sha256"] is not None
    assert "port" in file_res["diff"]

    # 2. Dangerous command block
    cmd_res = simulator.dry_run_action(
        action_type="shell_exec",
        params={"command": "rm -rf /"},
    )
    assert cmd_res["safe"] is False
    assert cmd_res["status"] == "unsafe_command_blocked"
