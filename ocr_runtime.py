"""Deterministic, bounded local Tesseract discovery and OCR execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from status_registry import EvidenceLevel, get_registry

try:
    import pytesseract
except ImportError:  # optional at runtime; callers degrade cleanly
    pytesseract = None


TESSERACT_PROBE_TIMEOUT = float(os.getenv("TESSERACT_PROBE_TIMEOUT", "5.0"))
TESSERACT_OCR_TIMEOUT = float(os.getenv("TESSERACT_OCR_TIMEOUT", "15.0"))
TESSERACT_DISCOVERY_TTL = float(os.getenv("TESSERACT_DISCOVERY_TTL", "60.0"))


class OCRUnavailableError(RuntimeError):
    pass


class OCRTimeoutError(RuntimeError):
    pass


class OCREmptyResultError(RuntimeError):
    pass


class OCRExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TesseractStatus:
    state: str
    path: str | None
    method: str | None
    detail: str
    version: str | None = None

    @property
    def available(self) -> bool:
        return self.state == "AVAILABLE"


_cache: tuple[float, TesseractStatus] | None = None
_cache_lock = threading.RLock()


def reset_tesseract_state() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _candidate_paths() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    configured = os.getenv("TESSERACT_PATH", "").strip().strip('"')
    if configured:
        candidates.append((configured, "TESSERACT_PATH"))
    on_path = shutil.which("tesseract")
    if on_path:
        candidates.append((on_path, "PATH"))
    candidates.extend([
        (r"C:\Program Files\Tesseract-OCR\tesseract.exe", "standard Windows path"),
        (r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe", "standard Windows path"),
        (os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"), "standard Windows path"),
    ])
    return candidates


def discover_tesseract() -> TesseractStatus:
    """Resolve the executable without scanning any directory tree."""
    if pytesseract is None:
        return TesseractStatus("UNAVAILABLE", None, None,
                               "pytesseract Python wrapper is not installed")
    seen = set()
    for candidate, method in _candidate_paths():
        normalized = os.path.normcase(os.path.abspath(os.path.expanduser(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        path = Path(candidate).expanduser()
        if path.is_file():
            return TesseractStatus("DISCOVERED", str(path.resolve()), method,
                                   f"Tesseract executable discovered via {method}")
    return TesseractStatus("UNAVAILABLE", None, None,
                           "Tesseract executable was not found in configured, PATH, or standard locations")


def _publish_probe(status: TesseractStatus) -> None:
    registry = get_registry()
    evidence = {
        "AVAILABLE": EvidenceLevel.PROBED,
        "UNAVAILABLE": EvidenceLevel.BLOCKED,
        "BROKEN": EvidenceLevel.BROKEN,
        "DISCOVERED": EvidenceLevel.CONFIGURED,
    }[status.state]
    registry.set_evidence("TESSERACT_OCR", evidence, status.detail,
                          source="bounded Tesseract probe", ttl=TESSERACT_DISCOVERY_TTL)
    capability_evidence = (EvidenceLevel.CONFIGURED if status.available
                           else EvidenceLevel.BLOCKED if status.state == "UNAVAILABLE"
                           else EvidenceLevel.BROKEN)
    registry.set_capability_evidence("OCR", capability_evidence, status.detail,
                                     source="Tesseract prerequisite probe",
                                     ttl=TESSERACT_DISCOVERY_TTL)


def probe_tesseract(*, force: bool = False) -> TesseractStatus:
    """Discover and execute ``tesseract --version`` within a fixed timeout."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if not force and _cache and now - _cache[0] < TESSERACT_DISCOVERY_TTL:
            return _cache[1]

    discovered = discover_tesseract()
    if discovered.state != "DISCOVERED":
        result = discovered
    else:
        try:
            completed = subprocess.run(
                [discovered.path, "--version"], capture_output=True, text=True,
                timeout=TESSERACT_PROBE_TIMEOUT, check=False,
            )
            output = getattr(completed, "stdout", "") or getattr(completed, "stderr", "") or ""
            version = (output.splitlines() or [""])[0].strip()
            if completed.returncode == 0:
                result = TesseractStatus(
                    "AVAILABLE", discovered.path, discovered.method,
                    f"Tesseract probe succeeded via {discovered.method}: {version or 'version reported'}",
                    version or None,
                )
            else:
                result = TesseractStatus(
                    "BROKEN", discovered.path, discovered.method,
                    f"Tesseract probe exited with code {completed.returncode}", version or None,
                )
        except subprocess.TimeoutExpired:
            result = TesseractStatus("BROKEN", discovered.path, discovered.method,
                                     "Tesseract version probe timed out")
        except OSError as exc:
            result = TesseractStatus("BROKEN", discovered.path, discovered.method,
                                     f"Tesseract version probe failed ({type(exc).__name__})")

    with _cache_lock:
        _cache = (time.monotonic(), result)
    _publish_probe(result)
    return result


def extract_image_text(image: Any, *, timeout: float | None = None) -> str:
    """Run bounded OCR and return meaningful text only.

    The caller owns ``image`` and remains responsible for closing it. Hidden
    temporary files used by pytesseract are managed by pytesseract itself.
    """
    status = probe_tesseract()
    if not status.available or pytesseract is None:
        raise OCRUnavailableError(status.detail)
    pytesseract.pytesseract.tesseract_cmd = status.path
    limit = TESSERACT_OCR_TIMEOUT if timeout is None else max(0.1, float(timeout))
    try:
        text = pytesseract.image_to_string(image, timeout=limit).strip()
    except RuntimeError as exc:
        detail = str(exc).lower()
        if "timeout" in detail or "terminated" in detail:
            _publish_ocr_failure("OCR execution timed out", EvidenceLevel.BROKEN)
            raise OCRTimeoutError("OCR execution timed out") from exc
        _publish_ocr_failure(f"OCR execution failed ({type(exc).__name__})", EvidenceLevel.BROKEN)
        raise OCRExecutionError(f"OCR execution failed ({type(exc).__name__})") from exc
    except Exception as exc:
        _publish_ocr_failure(f"OCR execution failed ({type(exc).__name__})", EvidenceLevel.BROKEN)
        raise OCRExecutionError(f"OCR execution failed ({type(exc).__name__})") from exc

    registry = get_registry()
    if not text:
        detail = "OCR executed successfully but found no meaningful text"
        registry.set_evidence("TESSERACT_OCR", EvidenceLevel.PROBED, detail,
                              source="OCR extraction", ttl=TESSERACT_DISCOVERY_TTL)
        registry.set_capability_evidence("OCR", EvidenceLevel.PROBED, detail,
                                         source="OCR extraction", ttl=TESSERACT_DISCOVERY_TTL)
        raise OCREmptyResultError(detail)
    detail = f"OCR extracted {len(text)} characters"
    registry.set_evidence("TESSERACT_OCR", EvidenceLevel.LIVE, detail,
                          source="OCR extraction", ttl=TESSERACT_DISCOVERY_TTL)
    registry.set_capability_evidence("OCR", EvidenceLevel.LIVE, detail,
                                     source="OCR extraction", ttl=TESSERACT_DISCOVERY_TTL)
    return text


def _publish_ocr_failure(detail: str, evidence: EvidenceLevel) -> None:
    registry = get_registry()
    registry.set_evidence("TESSERACT_OCR", evidence, detail,
                          source="OCR extraction", ttl=TESSERACT_DISCOVERY_TTL)
    registry.set_capability_evidence("OCR", evidence, detail,
                                     source="OCR extraction", ttl=TESSERACT_DISCOVERY_TTL)
