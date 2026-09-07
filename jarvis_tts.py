# jarvis_tts.py — F5-TTS Voice Cloning Subsystem
# CPU-based local voice cloning for J.A.R.V.I.S.
# Fallback-aware and status-registry integrated.

import os
import wave
import numpy as np
import tempfile
from typing import Optional

# Reliability imports
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REF_AUDIO_PATH = os.path.join(BASE_DIR, "Voices", "Jarvis.wav")
REF_TEXT = "Hello Sir, I'm Jarvis. How May I Assist You Today?"

_f5_instance = None
_f5_initialized = False


def _init_f5_tts() -> bool:
    """Lazily load F5-TTS model on CPU to preserve RTX 3050 VRAM for LLMs."""
    global _f5_instance, _f5_initialized
    if _f5_initialized:
        return _f5_instance is not None

    _f5_initialized = True
    registry = get_registry()

    if not os.path.exists(REF_AUDIO_PATH):
        print(f"[TTS] ⚠ Voice reference sample not found at: {REF_AUDIO_PATH}")
        registry.set_status(
            "VOICE_TTS",
            SubsystemState.DEGRADED,
            f"F5-TTS reference sample missing ({REF_AUDIO_PATH})"
        )
        return False

    try:
        from f5_tts.api import F5TTS
        print("[TTS] Loading F5-TTS voice cloning engine on CPU...")
        _f5_instance = F5TTS(device="cpu")
        print("[TTS] ✓ F5-TTS loaded successfully on CPU")
        registry.set_status(
            "VOICE_TTS",
            SubsystemState.READY,
            "F5-TTS CPU voice cloning active"
        )
        return True
    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="VOICE_TTS",
            exception=exc,
            context="Initializing F5-TTS CPU weights",
            demote_to=SubsystemState.DEGRADED
        )
        _f5_instance = None
        return False


def is_f5_ready() -> bool:
    """Check if F5-TTS is loaded and reference sample is present."""
    if _f5_instance is None:
        return _init_f5_tts()
    return True


def generate_speech_wav(text: str, output_wav_path: Optional[str] = None) -> Optional[str]:
    """
    Generate speech using F5-TTS and save to a WAV file.
    Returns the file path or None if failed.
    """
    if not is_f5_ready():
        return None

    if output_wav_path is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        output_wav_path = tmp.name
        tmp.close()

    try:
        _f5_instance.infer(
            gen_text=text,
            ref_file=REF_AUDIO_PATH,
            ref_text=REF_TEXT,
            file_wave_output=output_wav_path
        )
        if os.path.exists(output_wav_path) and os.path.getsize(output_wav_path) > 0:
            return output_wav_path
        return None
    except Exception as exc:
        error_handler.log_and_demote(
            subsystem="VOICE_TTS",
            exception=exc,
            context="F5-TTS speech synthesis inference",
            demote_to=SubsystemState.DEGRADED
        )
        return None


def speak_jarvis(text: str) -> bool:
    """Standalone playback for testing/CLI."""
    wav_path = generate_speech_wav(text)
    if not wav_path:
        return False

    try:
        import sounddevice as sd
        with wave.open(wav_path, "rb") as wf:
            data = wf.readframes(wf.getnframes())
            framerate = wf.getframerate()
            audio_array = np.frombuffer(data, dtype=np.int16)
            sd.play(audio_array, samplerate=framerate)
            sd.wait()
        return True
    except Exception as exc:
        print(f"[TTS] Standalone playback failed: {exc}")
        return False
    finally:
        try:
            if os.path.exists(wav_path):
                os.unlink(wav_path)
        except Exception:
            pass


if __name__ == "__main__":
    speak_jarvis("Systems are fully operational, sir. Voice clone running cleanly.")