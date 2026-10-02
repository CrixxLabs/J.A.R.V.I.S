# pocket_tts_worker.py — Dedicated persistent Pocket-TTS CUDA worker for JARVIS
# Run with the Python interpreter specified in JARVIS_POCKET_TTS_PYTHON environment variable.

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import traceback
import wave
from pathlib import Path

import numpy as np
import torch
from pocket_tts import TTSModel

HOST = os.getenv("JARVIS_POCKET_TTS_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_POCKET_TTS_PORT", "18777"))
_voice_path = os.getenv("JARVIS_POCKET_TTS_VOICE")
if not _voice_path:
    # Hard fallback to the canonical Jarvis voice
    _voice_path = str(Path(__file__).parent / "Voices" / "Jarvis.wav")
VOICE = Path(_voice_path)
if not VOICE.exists():
    raise EnvironmentError(
        f"[TTS][ERROR] JARVIS_POCKET_TTS_VOICE not found: {VOICE}. "
        "Set JARVIS_POCKET_TTS_VOICE in .env or place Jarvis.wav in D:\\J.A.R.V.I.S\\Voices\\"
    )
DEVICE = os.getenv("JARVIS_POCKET_TTS_DEVICE", "cuda")

_shutdown = threading.Event()
_generation_lock = threading.Lock()


def ensure_compatible_voice_wav(wav_path: Path, target_sr: int = 24000) -> Path:
    """Inspect and auto-convert voice reference WAV to 24kHz 16-bit mono PCM."""
    if not wav_path.exists():
        raise FileNotFoundError(f"[TTS][ERROR] Voice WAV not found: {wav_path}")
    try:
        import soundfile as sf
        import scipy.signal
        import scipy.io.wavfile as wavfile

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
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise ConnectionError("client disconnected")
        data.extend(part)
    return bytes(data)


def _send_json(sock: socket.socket, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def _recv_json(sock: socket.socket) -> dict:
    size = struct.unpack("!I", _recv_exact(sock, 4))[0]
    if size > 1_000_000:
        raise ValueError("invalid control frame")
    return json.loads(_recv_exact(sock, size).decode("utf-8"))


def _as_float32_bytes(chunk: torch.Tensor) -> bytes:
    audio = chunk.detach().float().cpu().contiguous().reshape(-1).numpy()
    return audio.astype(np.float32, copy=False).tobytes()


def _as_int16(chunk: torch.Tensor) -> np.ndarray:
    audio = chunk.detach().float().cpu().contiguous().reshape(-1).numpy()
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767.0).astype(np.int16)


print(f"[PocketWorker] Loading Pocket-TTS model...")
try:
    model = TTSModel.load_model()
    model.to(DEVICE)
    if DEVICE.startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but torch.cuda.is_available() is False")
        print(f"[PocketWorker] CUDA: {torch.cuda.get_device_name(0)}")

    VOICE = ensure_compatible_voice_wav(VOICE)
    print(f"[PocketWorker] Loading precomputed voice: {VOICE}")
    voice_state = model.get_state_for_audio_prompt(str(VOICE))
    sample_rate = int(model.sample_rate)
    print(f"[PocketWorker] READY @ {sample_rate} Hz on {model.device}")
except Exception as init_exc:
    print(f"[TTS][ERROR] Pocket-TTS worker initialization failed: {init_exc}")
    traceback.print_exc()
    raise


def _handle(conn: socket.socket) -> None:
    try:
        req = _recv_json(conn)
        op = req.get("op")

        if op == "ping":
            _send_json(conn, {
                "ok": True,
                "ready": True,
                "device": str(model.device),
                "sample_rate": sample_rate,
            })
            return

        if op == "shutdown":
            _send_json(conn, {"ok": True})
            _shutdown.set()
            return

        text = str(req.get("text") or "").strip()
        if not text:
            _send_json(conn, {"ok": False, "error": "empty text"})
            return

        if op == "stream":
            with _generation_lock:
                _send_json(conn, {
                    "ok": True,
                    "sample_rate": sample_rate,
                    "channels": 1,
                    "dtype": "float32",
                })
                try:
                    for chunk in model.generate_audio_stream(
                        model_state=voice_state,
                        text_to_generate=text,
                        copy_state=True,
                    ):
                        pcm = _as_float32_bytes(chunk)
                        conn.sendall(struct.pack("!I", len(pcm)) + pcm)
                    conn.sendall(struct.pack("!I", 0))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Client interruption: stop consuming the generator. Model stays resident.
                    return
                except Exception as stream_exc:
                    print(f"[TTS][ERROR] Pocket-TTS stream generation failed: {stream_exc}")
                    raise
            return

        if op == "wav":
            path = str(req.get("path") or "").strip()
            if not path:
                _send_json(conn, {"ok": False, "error": "missing output path"})
                return

            with _generation_lock:
                chunks = []
                try:
                    for chunk in model.generate_audio_stream(
                        model_state=voice_state,
                        text_to_generate=text,
                        copy_state=True,
                    ):
                        chunks.append(_as_int16(chunk))

                    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
                    with wave.open(path, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(sample_rate)
                        wf.writeframes(audio.tobytes())
                except Exception as wav_exc:
                    print(f"[TTS][ERROR] Pocket-TTS WAV synthesis failed: {wav_exc}")
                    raise

            _send_json(conn, {"ok": True, "path": path})
            return

        _send_json(conn, {"ok": False, "error": f"unknown op: {op}"})

    except Exception as exc:
        print(f"[TTS][ERROR] Request handler encountered exception: {exc}")
        try:
            _send_json(conn, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> None:
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(8)
        server.settimeout(0.5)
        print(f"[TTS] Pocket-TTS server listening on {HOST}:{PORT}")
    except OSError as bind_exc:
        print(f"[TTS][ERROR] Failed to bind Pocket-TTS server on {HOST}:{PORT}: {bind_exc}")
        return

    with server:
        while not _shutdown.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            # Requests are handled in their own thread so ping/shutdown remain responsive.
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()

    # Best-effort CUDA release on clean shutdown.
    try:
        del globals()["voice_state"]
        del globals()["model"]
    except Exception:
        pass
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print("[PocketWorker] Shutdown complete.")


if __name__ == "__main__":
    main()
