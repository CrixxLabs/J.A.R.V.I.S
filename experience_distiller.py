"""Experience Distillation Engine for J.A.R.V.I.S. — MARK VIII.

Harvests verified reasoning traces, deliberations, and skill executions into
standardized fine-tuning datasets (Alpaca & ShareGPT JSONL formats) for offline model training.
"""
from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import error_handler
from status_registry import EvidenceLevel, SubsystemState, get_registry

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DISTILLED_FILE = DATA_DIR / "distilled_memories.jsonl"

_lock = threading.RLock()


def distill_execution_sample(
    instruction: str,
    response: str,
    context: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    output_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Distill a verified execution or deliberation turn into fine-tuning datasets."""
    out_path = Path(output_file).resolve() if output_file else DISTILLED_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = metadata or {}
    meta.setdefault("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat())
    meta.setdefault("verification_score", 1.0)
    meta.setdefault("capability", "general_reasoning")

    sample_record = {
        "format": "dual_standard",
        "alpaca": {
            "instruction": instruction.strip(),
            "input": (context or "").strip(),
            "output": response.strip(),
        },
        "sharegpt": {
            "conversations": [
                {"from": "human", "value": f"{instruction}\n\nContext:\n{context}" if context else instruction},
                {"from": "gpt", "value": response.strip()},
            ]
        },
        "metadata": meta,
    }

    with _lock:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(sample_record, ensure_ascii=False) + "\n")

    get_registry().set_capability_evidence(
        "EXPERIENCE_DISTILLER",
        EvidenceLevel.LIVE,
        f"Distilled sample: '{instruction[:40]}' -> {out_path.name}",
        source="experience distiller",
    )
    return sample_record


def export_dataset(
    output_path: Optional[str] = None,
    format_type: str = "alpaca",
    source_file: Optional[Path] = None,
) -> int:
    """Export distilled memories into pure Alpaca or ShareGPT JSON / JSONL files.

    Args:
        output_path: Destination file path
        format_type: 'alpaca' or 'sharegpt'
        source_file: Source jsonl file

    Returns:
        Number of exported samples
    """
    src = Path(source_file).resolve() if source_file else DISTILLED_FILE
    if not src.exists():
        return 0

    samples = []
    with _lock:
        with open(src, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        if format_type.lower() == "sharegpt":
                            samples.append(record.get("sharegpt", {}))
                        else:
                            samples.append(record.get("alpaca", {}))
                    except Exception:
                        continue

    if output_path:
        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            if out.suffix == ".json":
                json.dump(samples, f, indent=2, ensure_ascii=False)
            else:
                for s in samples:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")

    return len(samples)
