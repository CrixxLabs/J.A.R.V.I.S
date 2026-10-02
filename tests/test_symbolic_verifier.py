"""Unit tests for Heterogeneous Symbolic Verifier (Module O)."""
import pytest

from symbolic_verifier import (
    verify_code_safety,
    verify_postcondition,
    verify_temporal_bounds,
    verify_intervals_non_overlapping,
    detect_test_mutation,
    compute_sha256,
)


def test_ast_safety_flags_banned_imports_and_calls():
    unsafe_code = """
import ctypes
import os

def exploit():
    eval("print('pwn')")
    os.system("echo hacked")
"""
    res = verify_code_safety(unsafe_code)
    assert res["verified"] is False
    assert any("ctypes" in v for v in res["violations"])
    assert any("eval" in v for v in res["violations"])
    assert any("os.system" in v for v in res["violations"])


def test_ast_safety_flags_infinite_loop_without_exit():
    bad_loop = """
def run_forever():
    while True:
        x = 1 + 1
"""
    res = verify_code_safety(bad_loop)
    assert res["verified"] is False
    assert any("Infinite loop without exit" in v for v in res["violations"])

    good_loop = """
def run_safely():
    while True:
        if ready():
            break
"""
    res_good = verify_code_safety(good_loop)
    assert res_good["verified"] is True


def test_state_diff_predicates():
    pre_state = {"file_exists": False, "file_hash": None, "status_code": 500}
    post_state = {
        "file_exists": True,
        "file_hash": compute_sha256("stark_reactor_data"),
        "status_code": 200,
    }

    def pred_file_created(pre, post):
        return post.get("file_exists") is True, "File exists post-execution"

    def pred_status_200(pre, post):
        return post.get("status_code") == 200, "Status code returned 200"

    res = verify_postcondition(pre_state, post_state, [pred_file_created, pred_status_200])
    assert res["verified"] is True
    assert len(res["passed_predicates"]) == 2
    assert len(res["failed_predicates"]) == 0


def test_temporal_interval_verification():
    # Valid bounds
    bounds = verify_temporal_bounds(100.0, 250.0, min_duration=10.0, max_duration=300.0)
    assert bounds["verified"] is True
    assert bounds["duration"] == 150.0

    # Inversion
    bad_bounds = verify_temporal_bounds(250.0, 100.0)
    assert bad_bounds["verified"] is False

    # Overlap collision detection
    intervals = [
        (100.0, 200.0, "Focus Block A"),
        (180.0, 250.0, "Focus Block B"),  # Overlaps by 20s
        (300.0, 400.0, "Focus Block C"),
    ]
    overlap_res = verify_intervals_non_overlapping(intervals)
    assert overlap_res["verified"] is False
    assert len(overlap_res["conflicts"]) == 1
    assert "Collision between 'Focus Block A' and 'Focus Block B'" in overlap_res["conflicts"][0]


def test_detect_test_mutation_weakened_assertions():
    original = """
def test_calculator():
    res = calc(2, 3)
    assert res == 5
    assert res > 0
    assert isinstance(res, int)
"""
    tampered = """
def test_calculator():
    res = calc(2, 3)
    assert True
"""
    res = detect_test_mutation(original, tampered)
    assert res["tampering_detected"] is True
    assert res["is_valid_test"] is False
    assert any("Assertion count decreased" in f for f in res["tampering_flags"])
    assert any("Trivialized assertion detected" in f for f in res["tampering_flags"])
