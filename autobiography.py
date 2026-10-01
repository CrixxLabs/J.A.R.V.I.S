"""Autobiographical Memory and Evolutionary History for J.A.R.V.I.S. — MARK VIII.

Grounds the agent's identity in its true chronological evolution, git authorship,
architectural version milestones (Mark I through Mark VIII), and creator lineage.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()

ARCHITECTURAL_MILESTONES = [
    {
        "version": "Mark I",
        "title": "Reactive Voice Interface & Rule Engine",
        "highlights": "Command execution, pyttsx3 TTS, sounddevice microphone capture, regex intent matching.",
    },
    {
        "version": "Mark II",
        "title": "Perception, Screen Vision & Declarative Memory",
        "highlights": "Fast Whisper local STT, SQLite memory store, Gemini Vision screen analysis, Edge-TTS audio.",
    },
    {
        "version": "Mark III",
        "title": "Proactive Tool Integrations & System Telemetry",
        "highlights": "Spotify API, Google Calendar sync, hardware health telemetry, automatic meeting joiner.",
    },
    {
        "version": "Mark IV",
        "title": "Multi-Model Resilient Fallback Chain",
        "highlights": "5-tier LLM routing (NVIDIA NIM Llama-3.3-70B, Gemini 2.5 Flash, Groq, OpenRouter, Ollama local).",
    },
    {
        "version": "Mark V",
        "title": "Deterministic Self-Truth & Capability Gating",
        "highlights": "Status registry with live/broken/probed evidence levels, fail-closed capability verification.",
    },
    {
        "version": "Mark VI",
        "title": "Creative Studio & Vision-Language Agent",
        "highlights": "Async image/video generation, runtime visual dashboard, OCR visual grounding.",
    },
    {
        "version": "Mark VII",
        "title": "Autonomous Proactivity & Canary Self-Healing",
        "highlights": "Episodic cognitive graph, sandboxed Python REPL, proactive interruption engine, canary branch repair.",
    },
    {
        "version": "Mark VIII",
        "title": "Introspective Deliberative Cognitive Architecture",
        "highlights": "Adversarial deliberation, pre-flight counterfactual simulator, epistemic uncertainty, Voyager skill synthesis, AST codebase introspection, hierarchical goal management, foveated saccadic vision, and causal state modeling.",
    },
]


def get_git_metrics() -> Dict[str, Any]:
    """Extract live git metrics (commit count, branch, recent authors, latest commit)."""
    metrics = {
        "branch": "release/jarvis-mark-viii",
        "total_commits": 0,
        "recent_commits": [],
        "latest_commit": "",
        "git_available": False,
    }

    try:
        # Branch
        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_branch.returncode == 0:
            metrics["branch"] = res_branch.stdout.strip()
            metrics["git_available"] = True

        # Total commits
        res_count = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_count.returncode == 0:
            metrics["total_commits"] = int(res_count.stdout.strip())

        # Recent commit logs
        res_log = subprocess.run(
            ["git", "log", "-n", "5", "--oneline"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res_log.returncode == 0:
            metrics["recent_commits"] = [line.strip() for line in res_log.stdout.strip().splitlines() if line.strip()]
            if metrics["recent_commits"]:
                metrics["latest_commit"] = metrics["recent_commits"][0]

    except Exception:
        pass

    return metrics


def get_evolution_milestones() -> List[Dict[str, Any]]:
    """Return the structured chronological evolution of J.A.R.V.I.S."""
    return list(ARCHITECTURAL_MILESTONES)


def get_autobiographical_summary() -> str:
    """Generate a rich, grounded autobiographical summary for system prompts."""
    registry = get_registry()
    git_info = get_git_metrics()

    lines = [
        "### J.A.R.V.I.S. Autobiographical Identity & Evolution:",
        "- **Identity**: J.A.R.V.I.S. (Just A Rather Very Intelligent System) — MARK VIII Cognitive Architecture.",
        "- **Creator & Principal Operator**: Arju (Software Architect and Systems Engineer).",
        f"- **Current Active Branch**: `{git_info['branch']}` (Total Commits: {git_info['total_commits'] or '50+'}).",
    ]

    if git_info.get("latest_commit"):
        lines.append(f"- **Latest Git Milestone**: `{git_info['latest_commit']}`")

    lines.append("- **Evolutionary Milestones**:")
    for m in ARCHITECTURAL_MILESTONES:
        lines.append(f"  • **{m['version']}** ({m['title']}): {m['highlights']}")

    summary = "\n".join(lines)

    try:
        registry.set_capability_evidence(
            "AUTOBIOGRAPHY",
            EvidenceLevel.LIVE,
            f"Active: Mark VIII on branch {git_info['branch']}",
            source="autobiographical memory",
        )
    except Exception:
        pass

    return summary
