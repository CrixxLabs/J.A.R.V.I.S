"""Heterogeneous Symbolic Verifier for J.A.R.V.I.S. — MARK VIII.

Deterministic postcondition and safety verification (replacing heuristic LLM self-grading):
  1. AST-level safety analysis (banned imports, infinite loops without breaks, dynamic eval).
  2. State Diff Predicate Verification (SHA-256 integrity, file mutations, return invariants).
  3. Temporal Interval & Numeric Boundary Verification.
  4. Mutation Testing / Test Tampering Detection (catches weakened test assertions).
"""
from __future__ import annotations

import ast
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.symbolic_verifier")

_lock = threading.RLock()

# Banned dangerous modules and functions in generated scripts
BANNED_IMPORTS: Set[str] = {
    "pty",
    "ctypes",
    "winreg",
}

DANGEROUS_CALLS: Set[str] = {
    "eval",
    "exec",
    "__import__",
}


# ── 1. AST SAFETY ANALYSIS ──────────────────────────────────────────────────
class ASTSafetyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.violations: List[str] = []
        self._in_loop = False
        self._loop_has_exit = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base_mod = alias.name.split(".")[0]
            if base_mod in BANNED_IMPORTS:
                self.violations.append(f"Banned import detected: '{alias.name}' at line {node.lineno}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base_mod = node.module.split(".")[0]
            if base_mod in BANNED_IMPORTS:
                self.violations.append(f"Banned from-import detected: '{node.module}' at line {node.lineno}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in DANGEROUS_CALLS:
            self.violations.append(f"Dangerous call detected: '{func_name}()' at line {node.lineno}")

        # Check os.system without sandboxing
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "os" and node.func.attr == "system":
                self.violations.append(f"Direct 'os.system()' call detected at line {node.lineno}")

        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        # Check for while True without break/return
        is_infinite_test = False
        if isinstance(node.test, ast.Constant) and bool(node.test.value) is True:
            is_infinite_test = True
        elif isinstance(node.test, ast.NameConstant) and node.test.value is True:
            is_infinite_test = True

        if is_infinite_test:
            has_exit = any(
                isinstance(n, (ast.Break, ast.Return, ast.Raise))
                for n in ast.walk(node)
            )
            if not has_exit:
                self.violations.append(f"Infinite loop without exit (break/return) detected at line {node.lineno}")

        self.generic_visit(node)


def verify_code_safety(code_or_ast: Union[str, ast.AST]) -> Dict[str, Any]:
    """Perform deterministic AST-level safety verification on Python code."""
    try:
        if isinstance(code_or_ast, str):
            tree = ast.parse(code_or_ast)
        else:
            tree = code_or_ast

        visitor = ASTSafetyVisitor()
        visitor.visit(tree)

        is_safe = len(visitor.violations) == 0
        return {
            "verified": is_safe,
            "status": "safe" if is_safe else "safety_violations_detected",
            "violations": visitor.violations,
        }
    except SyntaxError as exc:
        return {
            "verified": False,
            "status": "syntax_error",
            "violations": [f"Syntax error at line {exc.lineno}: {exc.msg}"],
        }
    except Exception as exc:
        return {
            "verified": False,
            "status": "parse_failure",
            "violations": [f"AST parsing exception: {exc}"],
        }


# ── 2. STATE DIFF PREDICATE VERIFICATION ────────────────────────────────────
def verify_postcondition(
    pre_state: Dict[str, Any],
    post_state: Dict[str, Any],
    predicates: List[Callable[[Dict[str, Any], Dict[str, Any]], Tuple[bool, str]]],
) -> Dict[str, Any]:
    """Evaluate deterministic state diff predicates against observed state transitions."""
    failures: List[str] = []
    passed: List[str] = []

    for pred in predicates:
        try:
            ok, desc = pred(pre_state, post_state)
            if ok:
                passed.append(desc)
            else:
                failures.append(desc)
        except Exception as exc:
            failures.append(f"Predicate crashed with error: {exc}")

    is_valid = len(failures) == 0

    try:
        get_registry().set_capability_evidence(
            "SYMBOLIC_VERIFIER",
            EvidenceLevel.LIVE,
            f"Verified postcondition predicates ({len(passed)} passed, {len(failures)} failed)",
            source="symbolic_verifier.verify_postcondition",
        )
    except Exception:
        pass

    return {
        "verified": is_valid,
        "passed_predicates": passed,
        "failed_predicates": failures,
    }


def compute_sha256(data: Union[str, bytes]) -> str:
    """Compute deterministic SHA-256 hash string."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


# ── 3. TEMPORAL INTERVAL & NUMERIC BOUNDARY VERIFICATION ────────────────────
def verify_temporal_bounds(
    start_epoch: float,
    end_epoch: float,
    min_duration: float = 0.0,
    max_duration: float = 86400.0,
) -> Dict[str, Any]:
    """Verify validity of temporal scheduling intervals."""
    if end_epoch < start_epoch:
        return {
            "verified": False,
            "reason": f"Temporal inversion: end_epoch ({end_epoch}) < start_epoch ({start_epoch})",
        }

    duration = end_epoch - start_epoch
    if duration < min_duration:
        return {
            "verified": False,
            "reason": f"Duration ({duration:.1f}s) under minimum threshold ({min_duration:.1f}s)",
        }
    if duration > max_duration:
        return {
            "verified": False,
            "reason": f"Duration ({duration:.1f}s) exceeds maximum threshold ({max_duration:.1f}s)",
        }

    return {"verified": True, "duration": duration}


def verify_intervals_non_overlapping(intervals: List[Tuple[float, float, str]]) -> Dict[str, Any]:
    """Verify that a set of (start, end, label) intervals do not have conflicting collisions."""
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    conflicts: List[str] = []

    for i in range(len(sorted_intervals) - 1):
        s1, e1, l1 = sorted_intervals[i]
        s2, e2, l2 = sorted_intervals[i + 1]

        if s2 < e1:
            overlap = e1 - s2
            conflicts.append(f"Collision between '{l1}' and '{l2}' (overlap of {overlap:.1f}s)")

    return {
        "verified": len(conflicts) == 0,
        "conflicts": conflicts,
        "interval_count": len(intervals),
    }


# ── 4. MUTATION TEST SAMPLING & TAMPERING DETECTION ─────────────────────────
def detect_test_mutation(original_test_code: str, candidate_test_code: str) -> Dict[str, Any]:
    """Detect test assertion weakening, tampering, or removed checks."""
    orig_asserts = re.findall(r"^\s*assert\s+(.+)$", original_test_code, flags=re.MULTILINE)
    cand_asserts = re.findall(r"^\s*assert\s+(.+)$", candidate_test_code, flags=re.MULTILINE)

    tampering_flags: List[str] = []

    # 1. Check for reduced assertion count
    if len(cand_asserts) < len(orig_asserts):
        tampering_flags.append(
            f"Assertion count decreased: {len(orig_asserts)} -> {len(cand_asserts)}"
        )

    # 2. Check for trivialized assertions (e.g. `assert True`, `assert 1`)
    trivial_pattern = re.compile(r"^\s*assert\s+(True|1|1\s*==\s*1)\s*$", re.IGNORECASE)
    for line in candidate_test_code.splitlines():
        if trivial_pattern.match(line):
            tampering_flags.append(f"Trivialized assertion detected: '{line.strip()}'")

    # 3. Check for empty pass in test functions
    if re.search(r"def\s+test_\w+\s*\([^)]*\)\s*:\s*\n\s*pass\b", candidate_test_code):
        tampering_flags.append("Empty test function containing only 'pass' detected")

    is_clean = len(tampering_flags) == 0

    return {
        "tampering_detected": not is_clean,
        "is_valid_test": is_clean,
        "original_assertion_count": len(orig_asserts),
        "candidate_assertion_count": len(cand_asserts),
        "tampering_flags": tampering_flags,
    }
