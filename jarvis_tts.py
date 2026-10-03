# jarvis_tts.py — Pocket-TTS Persistent Voice Subsystem
# Keeps Pocket-TTS isolated in its dedicated CUDA environment and preserves
# JARVIS's central speak() interface / Edge-TTS / SAPI fallbacks.

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

import error_handler
from speech_cleaner import clean_speech_text
from status_registry import SubsystemState, get_registry

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

_pocket_python = os.getenv("JARVIS_POCKET_TTS_PYTHON")
POCKET_PYTHON = Path(_pocket_python) if _pocket_python else None

_pocket_voice = os.getenv("JARVIS_POCKET_TTS_VOICE")
if not _pocket_voice:
    _pocket_voice = str(BASE_DIR / "Voices" / "Jarvis.wav")
POCKET_VOICE = Path(_pocket_voice) if _pocket_voice else None
POCKET_DEVICE = os.getenv("JARVIS_POCKET_TTS_DEVICE", "cuda").strip() or "cuda"
POCKET_HOST = "127.0.0.1"
POCKET_PORT = int(os.getenv("JARVIS_POCKET_TTS_PORT", "18777"))
WORKER_PATH = BASE_DIR / "pocket_tts_worker.py"

_worker: Optional[subprocess.Popen] = None
_worker_lock = threading.RLock()
_request_lock = threading.Lock()
_ready = False


def ensure_compatible_voice_wav(wav_path: Path, target_sr: int = 24000) -> Path:
    """Inspect and auto-convert voice reference WAV to 24kHz 16-bit mono PCM."""
    if not wav_path.exists():
        raise FileNotFoundError(f"[TTS][ERROR] Voice WAV not found: {wav_path}")
    try:
        import soundfile as sf
        import scipy.signal
        import scipy.io.wavfile as wavfile
        import numpy as np

        data, sr = sf.read(str(wav_path), dtype="float32")
        channels = 1 if data.ndim == 1 else data.shape[1]
        needs_mono = channels > 1
        needs_resample = sr != target_sr

        if not needs_mono and not needs_resample:
            return wav_path

        print(f"[TTS] Voice WAV mismatch ({sr}Hz, {channels}ch) -> Auto-converting {wav_path.name} to {target_sr}Hz mono 16-bit PCM...")
        if needs_mono:
            data = data.mean(axis=1)
        if needs_resample:
            num_samples = int(round(len(data) * target_sr / sr))
            data = scipy.signal.resample(data, num_samples)

        max_val = np.max(np.abs(data))
        if max_val > 0:
            data = data / max_val * 0.95
        data_int16 = (data * 32767.0).astype(np.int16)
        wavfile.write(str(wav_path), target_sr, data_int16)
        print(f"[TTS] Converted {wav_path.name} to {target_sr}Hz mono 16-bit PCM successfully.")
        return wav_path
    except Exception as exc:
        print(f"[TTS][ERROR] Auto-conversion of voice WAV {wav_path} failed: {exc}")
        return wav_path


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ConnectionError("Pocket-TTS worker closed the connection")
        chunks.extend(part)
    return bytes(chunks)


def _send_json(sock: socket.socket, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def _recv_json(sock: socket.socket) -> dict:
    size = struct.unpack("!I", _recv_exact(sock, 4))[0]
    if size > 1_000_000:
        raise RuntimeError("Pocket-TTS worker sent an invalid control frame")
    return json.loads(_recv_exact(sock, size).decode("utf-8"))


def _probe_worker(timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((POCKET_HOST, POCKET_PORT), timeout=timeout) as sock:
            sock.settimeout(timeout)
            _send_json(sock, {"op": "ping"})
            reply = _recv_json(sock)
            return bool(reply.get("ok") and reply.get("ready"))
    except Exception:
        return False


def prerequisites() -> tuple[bool, str]:
    # Kill stale worker on port 18777 pre-flight
    try:
        import psutil
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            cmdline = " ".join(str(p) for p in (proc.info.get("cmdline") or []))
            if "pocket_tts_worker" in cmdline:
                try:
                    p = psutil.Process(proc.info["pid"])
                    p.kill()
                    print(f"[TTS] Killed stale pocket_tts_worker PID={proc.info['pid']}")
                except Exception:
                    pass
    except Exception:
        pass

    if POCKET_PYTHON is None:
        return False, "JARVIS_POCKET_TTS_PYTHON not set in .env — see MARK_VII_SETUP.md"
    if POCKET_VOICE is None:
        return False, "JARVIS_POCKET_TTS_VOICE not set in .env — see MARK_VII_SETUP.md"
    if not POCKET_PYTHON.exists():
        return False, f"Pocket-TTS Python missing: {POCKET_PYTHON}"
    if not POCKET_VOICE.exists():
        # Fall back to canonical default if configured path is missing
        fallback = str(BASE_DIR / "Voices" / "Jarvis.wav")
        if Path(fallback).exists():
            print(f"[TTS] Configured voice missing; falling back to {fallback}")
            POCKET_VOICE = Path(fallback)
        else:
            return False, f"Pocket-TTS voice state missing: {POCKET_VOICE}"
    if not WORKER_PATH.exists():
        return False, f"Pocket-TTS worker missing: {WORKER_PATH}"

    # Validate and auto-convert voice reference audio if necessary
    try:
        ensure_compatible_voice_wav(POCKET_VOICE)
    except Exception as e:
        print(f"[TTS][ERROR] Voice audio integrity check failed: {e}")
        return False, f"Voice WAV format invalid: {e}"

    return True, f"Pocket-TTS configured for {POCKET_DEVICE}; voice={POCKET_VOICE.name}"


def start_engine(timeout: float = 45.0) -> bool:
    """Start the persistent Pocket-TTS CUDA worker once and wait until it is ready."""
    global _worker, _ready

    with _worker_lock:
        if _probe_worker():
            _ready = True
            get_registry().set_status(
                "VOICE_TTS", SubsystemState.READY,
                f"Pocket-TTS resident on {POCKET_DEVICE}",
            )
            return True

        ok, detail = prerequisites()
        if not ok:
            _ready = False
            get_registry().set_status("VOICE_TTS", SubsystemState.DEGRADED, detail)
            print(f"[TTS][ERROR] Prerequisites failed: {detail}")
            return False

        if _worker is not None and _worker.poll() is None:
            try:
                _worker.terminate()
                _worker.wait(timeout=2)
            except Exception:
                try:
                    _worker.kill()
                except Exception:
                    pass

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        env = os.environ.copy()
        env["JARVIS_POCKET_TTS_HOST"] = POCKET_HOST
        env["JARVIS_POCKET_TTS_PORT"] = str(POCKET_PORT)
        env["JARVIS_POCKET_TTS_VOICE"] = str(POCKET_VOICE)
        # VRAM-pressure fallback: switch worker to CPU if CUDA device requested but unavailable / contested
        device_for_env = POCKET_DEVICE
        try:
            import torch
            if POCKET_DEVICE == "cuda" and (not torch.cuda.is_available() or torch.cuda.memory_allocated() > 4 * 1024**3):
                device_for_env = "cpu"
                print("[TTS] VRAM pressure detected — falling back Pocket-TTS worker to CPU")
        except Exception:
            pass
        env["JARVIS_POCKET_TTS_DEVICE"] = device_for_env

        print(f"[TTS] Starting persistent Pocket-TTS worker on {POCKET_DEVICE} (port {POCKET_PORT})...")
        _worker = subprocess.Popen(
            [str(POCKET_PYTHON), str(WORKER_PATH)],
            cwd=str(BASE_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            creationflags=creationflags,
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            poll_code = _worker.poll()
            if poll_code is not None:
                print(f"[TTS][ERROR] Pocket-TTS worker process exited prematurely with exit code {poll_code}")
                break
            if _probe_worker(timeout=0.4):
                _ready = True
                print("[TTS] Pocket-TTS ready — model + OG JARVIS voice resident.")
                get_registry().set_status(
                    "VOICE_TTS", SubsystemState.READY,
                    f"Pocket-TTS CUDA resident ({POCKET_DEVICE})",
                )
                return True
            time.sleep(0.15)

        _ready = False
        detail = f"Pocket-TTS worker failed to become ready on port {POCKET_PORT}"
        get_registry().set_status("VOICE_TTS", SubsystemState.DEGRADED, detail)
        print(f"[TTS][ERROR] {detail}; Edge-TTS/SAPI fallback remains available.")
        return False


def is_ready() -> bool:
    global _ready
    if _ready and _probe_worker():
        return True
    _ready = _probe_worker()
    return _ready


# Backwards-compatible name so old callers/tests do not explode during migration.
def is_f5_ready() -> bool:
    return is_ready()


def generate_speech_wav(text: str, output_wav_path: Optional[str] = None) -> Optional[str]:
    """Compatibility/non-streaming path. Uses the resident worker, never the Pocket CLI."""
    text = clean_speech_text(text)
    if not text or not text.strip():
        return None
    if not is_ready() and not start_engine():
        print("[TTS][ERROR] Engine start failed during generate_speech_wav request")
        return None

    if output_wav_path is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        output_wav_path = tmp.name
        tmp.close()

    try:
        with _request_lock, socket.create_connection((POCKET_HOST, POCKET_PORT), timeout=3.0) as sock:
            sock.settimeout(120.0)
            _send_json(sock, {"op": "wav", "text": text, "path": output_wav_path})
            reply = _recv_json(sock)
            if reply.get("ok") and os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 44:
                return output_wav_path
            err = reply.get("error") or "Pocket-TTS WAV generation failed"
            print(f"[TTS][ERROR] WAV synthesis failed: {err}")
            raise RuntimeError(err)
    except Exception as exc:
        print(f"[TTS][ERROR] Pocket-TTS WAV generation exception: {exc}")
        error_handler.log_and_demote(
            subsystem="VOICE_TTS",
            exception=exc,
            context="Pocket-TTS resident WAV synthesis",
            demote_to=SubsystemState.DEGRADED,
        )
        try:
            if output_wav_path and os.path.exists(output_wav_path):
                os.unlink(output_wav_path)
        except Exception:
            pass
        return None


def stream_speech(
    text: str,
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    on_playback_start: Optional[Callable[[], None]] = None,
) -> bool:
    """
    Stream Pocket-TTS PCM chunks directly to the sound device.

    The model/voice stay resident in the worker. The first decoded PCM chunks
    are sent immediately; playback begins before the complete utterance exists.
    """
    text = clean_speech_text(text)
    if not text or not text.strip():
        return False
    if not is_ready() and not start_engine():
        print("[TTS][ERROR] Engine start failed during stream_speech request")
        return False

    try:
        import sounddevice as sd
    except Exception as exc:
        print(f"[TTS][ERROR] sounddevice unavailable for streaming: {exc}")
        return False

    started_at = time.perf_counter()
    first_audio_at = None
    playback_started = False

    try:
        with _request_lock, socket.create_connection((POCKET_HOST, POCKET_PORT), timeout=3.0) as sock:
            sock.settimeout(120.0)
            _send_json(sock, {"op": "stream", "text": text})
            header = _recv_json(sock)
            if not header.get("ok"):
                err = header.get("error") or "Pocket-TTS stream rejected"
                print(f"[TTS][ERROR] Stream rejected by worker: {err}")
                raise RuntimeError(err)

            sample_rate = int(header["sample_rate"])
            channels = int(header.get("channels", 1))

            # Use a reasonable blocksize to prevent buffer underruns on Windows audio drivers.
            # 0 means "no buffering" which can cause fast playback/stuttering on some hardware.
            chunk_size = 2048  # ~85ms at 24kHz, good balance between latency and stability

            with sd.RawOutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                blocksize=chunk_size,
                latency="low",
            ) as output:
                while True:
                    if should_stop and should_stop():
                        try:
                            _send_json(sock, {"op": "cancel"})
                        except Exception:
                            pass
                        print("[Speak] Interrupted by user")
                        return True

                    frame_len = struct.unpack("!I", _recv_exact(sock, 4))[0]
                    if frame_len == 0:
                        break
                    if frame_len > 16 * 1024 * 1024:
                        raise RuntimeError("Pocket-TTS sent an invalid audio frame")

                    pcm = _recv_exact(sock, frame_len)
                    if first_audio_at is None:
                        first_audio_at = time.perf_counter()
                        print(f"[TTS] request -> first PCM: {(first_audio_at-started_at)*1000:.0f} ms")

                    if not playback_started:
                        if on_playback_start:
                            on_playback_start()
                        playback_started = True
                        print(f"[TTS] request -> playback: {(time.perf_counter()-started_at)*1000:.0f} ms")

                    try:
                        output.write(pcm)
                    except Exception as playback_exc:
                        # Windows can invalidate an already-open MME endpoint when
                        # headphones/Bluetooth/default output changes.  Let the
                        # caller fall back cleanly instead of pretending synthesis failed.
                        print(f"[TTS][ERROR] playback device became unavailable: {playback_exc}")
                        try:
                            sd._terminate()
                            sd._initialize()
                        except Exception:
                            pass
                        raise

            total = (time.perf_counter() - started_at) * 1000
            print(f"[TTS] stream complete: {total:.0f} ms")
            get_registry().set_status(
                "VOICE_TTS", SubsystemState.READY,
                f"Pocket-TTS streaming on {POCKET_DEVICE}",
            )
            return True

    except Exception as exc:
        print(f"[TTS][ERROR] Pocket-TTS streaming failed: {exc}")
        error_handler.log_and_demote(
            subsystem="VOICE_TTS",
            exception=exc,
            context="Pocket-TTS streaming synthesis/playback",
            demote_to=SubsystemState.DEGRADED,
        )
        return False


def shutdown_engine(timeout: float = 5.0) -> None:
    """Stop the owned worker and release its CUDA model/voice state."""
    global _worker, _ready

    with _worker_lock:
        try:
            if _probe_worker():
                with socket.create_connection((POCKET_HOST, POCKET_PORT), timeout=1.0) as sock:
                    sock.settimeout(1.0)
                    _send_json(sock, {"op": "shutdown"})
                    try:
                        _recv_json(sock)
                    except Exception:
                        pass
        except Exception:
            pass

        if _worker is not None and _worker.poll() is None:
            try:
                _worker.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _worker.terminate()
                try:
                    _worker.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _worker.kill()

        _worker = None
        _ready = False


def speak_jarvis(text: str) -> bool:
    """Standalone compatibility helper."""
    return stream_speech(text)


if __name__ == "__main__":
    if start_engine():
        try:
            speak_jarvis("Systems are fully operational, sir. Pocket TTS is online.")
        finally:
            shutdown_engine()
