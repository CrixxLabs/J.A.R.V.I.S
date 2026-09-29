"""Agent-style Creative Studio jobs for MARK VII.

Generation runs off the conversation thread. The runtime can continue listening and
talking while NVIDIA works. Completion is announced through executor's speak callback.
"""
from __future__ import annotations
import os, subprocess, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from creative_studio import CreativeStudio

_lock = threading.RLock()
_jobs = {}
_latest_asset: str | None = None
_latest_kind: str | None = None
_counter = 0

@dataclass
class Job:
    id: int
    kind: str
    status: str = "queued"
    prompt: str = ""
    path: str | None = None
    error: str = ""

def _next_id():
    global _counter
    with _lock:
        _counter += 1
        return _counter

def _set_latest(path: str, kind: str):
    global _latest_asset, _latest_kind
    with _lock:
        _latest_asset, _latest_kind = path, kind

def latest_asset():
    with _lock:
        return _latest_asset

def latest_status():
    with _lock:
        active_jobs = [job for job in _jobs.values() if job.status in ("queued", "running")]
        latest = _jobs[max(_jobs)] if _jobs else None
        recent = sorted(_jobs.values(), key=lambda job: job.id, reverse=True)[:5]
        return {
            "active": bool(active_jobs),
            "active_jobs": [job.__dict__.copy() for job in active_jobs],
            "job": latest.__dict__.copy() if latest else None,
            "recent_jobs": [job.__dict__.copy() for job in recent],
            "latest_asset": _latest_asset,
            "latest_kind": _latest_kind,
        }

def cancel(job_id: int | None = None) -> bool:
    """Abandon a queued/running generation. HTTP work may continue until timeout,
    but completion is suppressed and the abandoned result is not promoted to latest."""
    with _lock:
        candidates = [job for job in _jobs.values() if job.status in ("queued", "running")]
        if not candidates:
            return False
        job = next((j for j in candidates if j.id == int(job_id)), None) if job_id is not None else max(candidates, key=lambda j: j.id)
        if not job:
            return False
        job.status = "cancelled"
        job.error = "Generation abandoned by user."
        return True

def _notify(cb: Callable[[str], None] | None, text: str):
    if cb:
        try: cb(text)
        except Exception as exc: print(f"[CreativeAgent] notify failed: {exc}")

def _mark_capability(capability: str, ok: bool, detail: str):
    try:
        from status_registry import get_registry, EvidenceLevel
        get_registry().set_capability_evidence(
            capability, EvidenceLevel.LIVE if ok else EvidenceLevel.BROKEN,
            detail, source="creative studio live job"
        )
    except Exception as exc:
        print(f"[CreativeAgent] capability evidence unavailable: {exc}")

def submit_image(prompt: str, notify=None, auto_open: bool = False) -> Job:
    job = Job(_next_id(), "image", prompt=prompt)
    with _lock: _jobs[job.id] = job

    def worker():
        with _lock:
            if job.status == "cancelled":
                return
            job.status = "running"
        try:
            result = CreativeStudio().generate_image(prompt)
            with _lock:
                if job.status == "cancelled":
                    return
                if result.ok and result.path:
                    job.status, job.path = "done", str(Path(result.path).resolve())
                else:
                    job.status, job.error = "failed", result.message
            if job.status == "done":
                _set_latest(job.path, "image")
                _mark_capability("IMAGE_GENERATION", True, "NVIDIA NIM generated an image successfully")
                if auto_open: open_asset(job.path)
                _notify(notify, "Your image is ready. I've opened it." if auto_open else "Your image is ready. Want me to open it?")
            else:
                _mark_capability("IMAGE_GENERATION", False, job.error)
                _notify(notify, "The image generation didn't complete. I kept the rest of the system running.")
        except Exception as exc:
            with _lock:
                if job.status == "cancelled":
                    return
                job.status, job.error = "failed", str(exc)
            _mark_capability("IMAGE_GENERATION", False, str(exc))
            _notify(notify, "The image generation failed, but the rest of JARVIS is still running.")
    threading.Thread(target=worker, name=f"creative-image-{job.id}", daemon=True).start()
    return job

def submit_animate(image_path: str | None = None, notify=None, auto_open: bool = False) -> Job | None:
    source = image_path or latest_asset()
    if not source or not Path(source).is_file():
        return None
    job = Job(_next_id(), "video", prompt=str(source))
    with _lock: _jobs[job.id] = job

    def worker():
        with _lock:
            if job.status == "cancelled":
                return
            job.status = "running"
        try:
            result = CreativeStudio().animate_image(source)
            with _lock:
                if job.status == "cancelled":
                    return
                if result.ok and result.path:
                    job.status, job.path = "done", str(Path(result.path).resolve())
                else:
                    job.status, job.error = "failed", result.message
            if job.status == "done":
                _set_latest(job.path, "video")
                _mark_capability("VIDEO_GENERATION", True, "NVIDIA NIM generated video from an image successfully")
                if auto_open: open_asset(job.path)
                _notify(notify, "The video is ready. I've opened it." if auto_open else "The video is ready. Want me to open it?")
            else:
                _mark_capability("VIDEO_GENERATION", False, job.error)
                _notify(notify, "The video generation didn't complete.")
        except Exception as exc:
            with _lock:
                if job.status == "cancelled":
                    return
                job.status, job.error = "failed", str(exc)
            _mark_capability("VIDEO_GENERATION", False, str(exc))
            _notify(notify, "The video generation failed, but the rest of JARVIS is still running.")
    threading.Thread(target=worker, name=f"creative-video-{job.id}", daemon=True).start()
    return job

def open_asset(path: str | None = None) -> bool:
    target = path or latest_asset()
    if not target or not Path(target).exists(): return False
    try:
        if os.name == "nt": os.startfile(str(Path(target).resolve()))
        else: subprocess.Popen(["xdg-open", str(Path(target).resolve())])
        return True
    except Exception:
        return False

def open_folder() -> bool:
    target = latest_asset()
    folder = Path(target).resolve().parent if target else (Path.cwd()/"generated_media").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt": os.startfile(str(folder))
        else: subprocess.Popen(["xdg-open", str(folder)])
        return True
    except Exception:
        return False
