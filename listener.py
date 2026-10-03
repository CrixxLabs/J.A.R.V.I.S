# listener.py - Iron Man Voice System
# Layer 1: Double clap wake trigger
# Layer 2: Whisper-powered voice capture (works in noise/whispers/low volume)
# Layer 3: Speaker verification (only your voice is accepted)
# Layer 4: Google Speech Recognition fallback
# Layer 5: Interrupt detection during Jarvis speech

import pyaudio
import numpy as np
import time
import speech_recognition as sr
import os
import wave
import tempfile
import threading
from collections import deque

# Reliability imports
import status_registry
from status_registry import SubsystemState, get_registry
import error_handler
import global_workspace

# ── Audio config ──────────────────────────────────────────────────────────────
RATE = 16000
CHUNK = 1024
MIC_INDEX = 1  # Default fallback, resolved dynamically via init_mic()

# ── Clap detection thresholds ─────────────────────────────────────────────────
CLAP_ENERGY_THRESHOLD = 3000
CLAP_MIN_INTERVAL = 0.15
CLAP_MAX_INTERVAL = 0.8

# ── Voice capture thresholds ──────────────────────────────────────────────────
VOICE_ENERGY_MULTIPLIER = 1.3
SILENCE_DURATION = 1.5
PHRASE_TIME_LIMIT = 20

# ── Interrupt detection thresholds ────────────────────────────────────────────
INTERRUPT_ENERGY_MULTIPLIER = 3.0    # Higher bar so speech playback doesn't self-trigger
INTERRUPT_MIN_DURATION = float(os.getenv("JARVIS_INTERRUPT_MIN_DURATION", "0.35"))
INTERRUPT_CLAP_THRESHOLD_MULT = 5.0  # Even higher for clap detection during speech
INTERRUPT_ENERGY_FLOOR = float(os.getenv("JARVIS_INTERRUPT_ENERGY_FLOOR", "500"))
INTERRUPT_START_GRACE = float(os.getenv("JARVIS_INTERRUPT_START_GRACE", "0.35"))

# ── Fallback energy if calibration fails ─────────────────────────────────────
FALLBACK_ENERGY = 300

# ── Calibration ───────────────────────────────────────────────────────────────
_ambient_energy = FALLBACK_ENERGY
_calibrated = False

# ── Iron Man Voice System (loaded lazily) ─────────────────────────────────────
_whisper_model = None
_voice_encoder = None
_user_voice_embedding = None

# ── Interrupt state ───────────────────────────────────────────────────────────
_interrupt_flag = threading.Event()
_interrupt_thread = None
_interrupt_running = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_EMBEDDING_FILE = os.path.join(BASE_DIR, "user_voice.npy")
WHISPER_MODEL_SIZE = "small"
SPEAKER_SIMILARITY_THRESHOLD = 0.55


# ── Configurable / Sane Fallback Mic Selector ─────────────────────────────────

def _get_working_mic_index() -> int | None:
    """
    Find first available working mic index by inspecting PyAudio device indices.
    Priority list:
      1. Env-configured index (JARVIS_MIC_INDEX)
      2. MIC_INDEX = 1 (original code default)
      3. PyAudio default input device info
      4. First device index with input channels > 0
    """
    p = None
    try:
        p = pyaudio.PyAudio()
        device_count = p.get_device_count()
        if device_count == 0:
            return None

        # 1. Check .env setting
        env_mic = os.getenv("JARVIS_MIC_INDEX")
        if env_mic is not None:
            try:
                idx = int(env_mic)
                if 0 <= idx < device_count:
                    info = p.get_device_info_by_index(idx)
                    if info.get('maxInputChannels', 0) > 0:
                        return idx
            except ValueError:
                pass

        # 2. Try the legacy default index (1)
        default_idx = 1
        if default_idx < device_count:
            info = p.get_device_info_by_index(default_idx)
            if info.get('maxInputChannels', 0) > 0:
                return default_idx

        # 3. Try OS system default input device
        try:
            default_info = p.get_default_input_device_info()
            default_idx = default_info.get('index')
            if default_idx is not None:
                idx = int(default_idx)
                if idx < device_count:
                    return idx
        except Exception:
            pass

        # 4. Search for any input-capable hardware device
        for i in range(device_count):
            try:
                info = p.get_device_info_by_index(i)
                if info.get('maxInputChannels', 0) > 0:
                    return i
            except Exception:
                continue

    except Exception as exc:
        print(f"[Listener] PyAudio device querying failed: {exc}")
    finally:
        if p:
            try:
                p.terminate()
            except Exception:
                pass
    return None


_mic_initialized = False


def init_mic():
    """
    Perform mic discovery, configure MIC_INDEX, and set VOICE_STT status.
    Called lazily on first audio utility access.
    """
    global MIC_INDEX, _mic_initialized
    if _mic_initialized:
        return

    registry = get_registry()
    working_idx = _get_working_mic_index()

    if working_idx is not None:
        MIC_INDEX = working_idx
        print(f"[Listener] Verified microphone. Active index set to: {MIC_INDEX}")
        registry.set_status(
            "VOICE_STT",
            SubsystemState.READY,
            f"Mic index {MIC_INDEX} online"
        )
    else:
        print("[Listener] [WARN] Critical: No working input devices found!")
        registry.set_status(
            "VOICE_STT",
            SubsystemState.OFFLINE,
            "No input hardware devices discovered on host system"
        )

    _mic_initialized = True


# ── Original Utility Methods with Integrated Demotions ───────────────────────

def _load_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"[Listener] Loading Whisper ({WHISPER_MODEL_SIZE}) on {device}...")
            _whisper_model = whisper.load_model(WHISPER_MODEL_SIZE, device=device)
            print(f"[Listener] OK Whisper loaded on {device}")
        except Exception as e:
            error_handler.log_and_demote(
                subsystem="VOICE_STT",
                exception=e,
                context="Loading local Whisper model weights",
                demote_to=SubsystemState.DEGRADED
            )
            _whisper_model = False
    return _whisper_model


def _load_voice_encoder():
    global _voice_encoder, _user_voice_embedding
    if _voice_encoder is None:
        try:
            from resemblyzer import VoiceEncoder
            _voice_encoder = VoiceEncoder(verbose=False)
            print("[Listener] OK Voice encoder loaded")

            if os.path.exists(VOICE_EMBEDDING_FILE):
                _user_voice_embedding = np.load(VOICE_EMBEDDING_FILE)
                print("[Listener] OK User voice embedding loaded")
            else:
                print("[Listener] [WARN] No user voice registered - run voice_setup.py first")
        except Exception as e:
            error_handler.log_and_demote(
                subsystem="VOICE_STT",
                exception=e,
                context="Loading speaker verification models",
                demote_to=SubsystemState.DEGRADED
            )
            _voice_encoder = False
    return _voice_encoder


def _verify_speaker(audio_data_np):
    global _user_voice_embedding

    encoder = _load_voice_encoder()
    if not encoder or _user_voice_embedding is None:
        return True

    try:
        from resemblyzer import preprocess_wav
        wav = preprocess_wav(audio_data_np, source_sr=RATE)
        embedding = encoder.embed_utterance(wav)
        similarity = np.inner(embedding, _user_voice_embedding)
        print(f"[Listener] Speaker similarity: {similarity:.3f} (threshold: {SPEAKER_SIMILARITY_THRESHOLD})")
        return similarity >= SPEAKER_SIMILARITY_THRESHOLD
    except Exception as e:
        error_handler.log_and_demote(
            subsystem="VOICE_STT",
            exception=e,
            context="Checking speaker profile authentication token",
            demote_to=SubsystemState.DEGRADED
        )
        return True


def _transcribe_with_whisper(audio_data):
    model = _load_whisper()
    if not model:
        return None

    try:
        import io
        import soundfile as sf

        raw_data = audio_data.get_wav_data(convert_rate=16000, convert_width=2)
        audio_np, sr_rate = sf.read(io.BytesIO(raw_data), dtype='float32')

        if len(audio_np.shape) > 1:
            audio_np = audio_np.mean(axis=1)

        print("[Listener] Whisper transcribing...")
        result = model.transcribe(
            audio_np,
            fp16=False,
            language="en",
            no_speech_threshold=0.6,
            condition_on_previous_text=False,
            temperature=0.0,
        )
        text = result.get("text", "").strip()
        text = _dedupe_phrases(text)
        return text if text else None
    except Exception as e:
        error_handler.log_and_demote(
            subsystem="VOICE_STT",
            exception=e,
            context="Whisper transcription engine",
            demote_to=SubsystemState.DEGRADED
        )
        return None


def _dedupe_phrases(text):
    if not text:
        return text
    words = text.split()
    if len(words) < 4:
        return text
    for phrase_len in range(6, 1, -1):
        if len(words) < phrase_len * 2:
            continue
        first  = " ".join(words[:phrase_len]).lower()
        second = " ".join(words[phrase_len:phrase_len*2]).lower()
        if first == second:
            deduped = words[:phrase_len]
            i = phrase_len
            while i < len(words):
                candidate = " ".join(words[i:i+phrase_len]).lower()
                if candidate == first:
                    i += phrase_len
                else:
                    deduped.extend(words[i:])
                    break
            return " ".join(deduped)
    return text


def _audio_data_to_numpy(audio_data):
    try:
        raw = audio_data.get_raw_data(convert_rate=RATE, convert_width=2)
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return arr
    except Exception as e:
        print(f"[Listener] Audio conversion error: {e}")
        return None


def _get_audio_energy(data):
    try:
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(audio_data) == 0:
            return 0.0
        mean_sq = np.mean(audio_data ** 2)
        if mean_sq <= 0 or np.isnan(mean_sq):
            return 0.0
        return float(np.sqrt(mean_sq))
    except Exception:
        return 0.0


def calibrate_ambient_noise():
    global _ambient_energy, _calibrated
    init_mic()

    print("[Listener] Calibrating to your environment...")
    print("[Listener] Stay quiet for 2 seconds...")

    p = pyaudio.PyAudio()
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            input_device_index=MIC_INDEX,
            frames_per_buffer=CHUNK,
        )

        samples = []
        for _ in range(int(RATE / CHUNK * 2)):
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                energy = _get_audio_energy(data)
                if energy > 0 and not np.isnan(energy):
                    samples.append(energy)
            except Exception:
                continue

        stream.stop_stream()
        stream.close()

        if samples:
            _ambient_energy = float(np.mean(samples))
            if np.isnan(_ambient_energy) or _ambient_energy <= 0:
                _ambient_energy = FALLBACK_ENERGY
        else:
            _ambient_energy = FALLBACK_ENERGY

        _calibrated = True

        voice_threshold = int(_ambient_energy * VOICE_ENERGY_MULTIPLIER)
        print(f"[Listener] OK Calibrated - ambient: {_ambient_energy:.0f} | voice threshold: {voice_threshold}")

    except Exception as e:
        error_handler.log_and_demote(
            subsystem="VOICE_STT",
            exception=e,
            context="Calibrating environment background metrics",
            demote_to=SubsystemState.DEGRADED
        )
        _ambient_energy = FALLBACK_ENERGY
        _calibrated = True
    finally:
        try:
            p.terminate()
        except Exception:
            pass


def detect_double_clap():
    init_mic()
    if not _calibrated:
        calibrate_ambient_noise()

    p = pyaudio.PyAudio()
    clap_threshold = max(CLAP_ENERGY_THRESHOLD, _ambient_energy * 4)

    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            input_device_index=MIC_INDEX,
            frames_per_buffer=CHUNK,
        )

        clap_times = deque(maxlen=2)
        last_clap_time = 0

        while True:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                energy = _get_audio_energy(data)
                current_time = time.time()

                if energy > clap_threshold:
                    if current_time - last_clap_time < CLAP_MIN_INTERVAL:
                        continue

                    clap_times.append(current_time)
                    last_clap_time = current_time
                    print(f"[Listener] Clap detected (energy: {energy:.0f})")

                    if len(clap_times) == 2:
                        interval = clap_times[1] - clap_times[0]
                        if CLAP_MIN_INTERVAL < interval < CLAP_MAX_INTERVAL:
                            print(f"[Listener] OK Double clap! (interval: {interval:.2f}s)")
                            stream.stop_stream()
                            stream.close()
                            return True
                        else:
                            clap_times.clear()
                            clap_times.append(current_time)

            except Exception as e:
                # Dynamic stream issues, avoid crash and log silently
                continue

    except Exception as e:
        error_handler.log_and_demote(
            subsystem="VOICE_STT",
            exception=e,
            context="Clap trigger audio stream",
            demote_to=SubsystemState.OFFLINE
        )
        return False
    finally:
        try:
            p.terminate()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# INTERRUPT DETECTION (Layer 5)
# ══════════════════════════════════════════════════════════════════════════════

def _interrupt_voice_threshold(ambient_energy: float | None = None) -> float:
    """Compute a conservative interruption threshold during TTS playback."""
    ambient = ambient_energy if ambient_energy is not None else _ambient_energy
    try:
        ambient = float(ambient)
    except (TypeError, ValueError):
        ambient = FALLBACK_ENERGY
    if not np.isfinite(ambient) or ambient <= 0:
        ambient = FALLBACK_ENERGY
    return max(INTERRUPT_ENERGY_FLOOR, ambient * INTERRUPT_ENERGY_MULTIPLIER)


def _interrupt_watcher():
    """
    Runs in background thread while Jarvis is speaking.
    Sets _interrupt_flag when:
      - User speaks (sustained voice above threshold)
      - Double clap detected
    """
    global _interrupt_running
    init_mic()

    if not _calibrated:
        calibrate_ambient_noise()

    safe_ambient = _ambient_energy if _ambient_energy > 0 else FALLBACK_ENERGY

    # Conservative threshold while TTS is playing: calibration can occasionally
    # report tiny values (e.g. ambient=1), which previously caused energy=21 to
    # interrupt speech. Never let the speech-interrupt threshold fall below floor.
    voice_threshold = _interrupt_voice_threshold(safe_ambient)
    clap_threshold  = max(CLAP_ENERGY_THRESHOLD, safe_ambient * INTERRUPT_CLAP_THRESHOLD_MULT)

    p = None
    stream = None
    try:
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            input_device_index=MIC_INDEX,
            frames_per_buffer=CHUNK,
        )

        voice_start_time = 0
        clap_times = deque(maxlen=2)
        watcher_started = time.time()
        last_clap_time = 0

        while _interrupt_running:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                energy = _get_audio_energy(data)
                current_time = time.time()

                # ── Clap interrupt detection ──────────────────────────────
                if energy > clap_threshold:
                    if current_time - last_clap_time >= CLAP_MIN_INTERVAL:
                        clap_times.append(current_time)
                        last_clap_time = current_time
                        if len(clap_times) == 2:
                            interval = clap_times[1] - clap_times[0]
                            if CLAP_MIN_INTERVAL < interval < CLAP_MAX_INTERVAL:
                                print("[Interrupt] OK Double clap interrupt!")
                                _interrupt_flag.set()
                                break
                            else:
                                clap_times.clear()
                                clap_times.append(current_time)

                # ── Sustained voice interrupt detection ────────────────────
                if (current_time - watcher_started) < INTERRUPT_START_GRACE:
                    voice_start_time = 0
                    continue
                if energy > voice_threshold:
                    if voice_start_time == 0:
                        voice_start_time = current_time
                    elif (current_time - voice_start_time) >= INTERRUPT_MIN_DURATION:
                        print(f"[Interrupt] OK Voice interrupt (energy: {energy:.0f})")
                        _interrupt_flag.set()
                        try:
                            global_workspace.hook_audio(energy=energy, speech_detected=True)
                        except Exception:
                            pass
                        break
                else:
                    voice_start_time = 0

            except Exception:
                continue

    except Exception as e:
        error_handler.log_and_demote(
            subsystem="VOICE_STT",
            exception=e,
            context="Speech interrupt capture thread",
            demote_to=SubsystemState.DEGRADED
        )
    finally:
        try:
            if stream:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if p:
                p.terminate()
        except Exception:
            pass


def start_interrupt_watcher():
    """
    Start background thread that watches for voice/clap during Jarvis speech.
    Call this at the START of speak().
    """
    global _interrupt_thread, _interrupt_running

    # Stop any existing watcher
    stop_interrupt_watcher()

    _interrupt_flag.clear()
    _interrupt_running = True
    _interrupt_thread = threading.Thread(target=_interrupt_watcher, daemon=True)
    _interrupt_thread.start()


def stop_interrupt_watcher():
    """Stop the interrupt watcher thread. Call at END of speak()."""
    global _interrupt_thread, _interrupt_running
    _interrupt_running = False
    if _interrupt_thread and _interrupt_thread.is_alive():
        _interrupt_thread.join(timeout=0.5)
    _interrupt_thread = None


def check_interrupt() -> bool:
    """Non-blocking check - has user interrupted?"""
    return _interrupt_flag.is_set()


def reset_interrupt():
    """Clear interrupt flag."""
    _interrupt_flag.clear()


# ══════════════════════════════════════════════════════════════════════════════
# UI SOUND
# ══════════════════════════════════════════════════════════════════════════════

def play_jarvis_ui_sound():
    import os

    sound_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_ui.mp3")

    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        if os.path.exists(sound_path):
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
                time.sleep(0.1)

            pygame.mixer.music.load(sound_path)
            pygame.mixer.music.set_volume(0.4)
            pygame.mixer.music.play()

            timeout = time.time() + 3
            while pygame.mixer.music.get_busy():
                if time.time() > timeout:
                    break
                time.sleep(0.05)
        else:
            print(f"[Listener] jarvis_ui.mp3 not found at {sound_path} - using beep")
            _beep_fallback()

    except Exception as e:
        print(f"[Listener] UI sound failed: {e} - using beep")
        _beep_fallback()

    time.sleep(0.4)


def _beep_fallback():
    try:
        import winsound
        winsound.Beep(800, 120)
        time.sleep(0.08)
        winsound.Beep(1000, 80)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# VOICE CAPTURE
# ══════════════════════════════════════════════════════════════════════════════

def listen_for_command(timeout=12, phrase_time_limit=20):
    init_mic()
    if not _calibrated:
        calibrate_ambient_noise()

    time.sleep(0.3)

    recognizer = sr.Recognizer()

    safe_energy = _ambient_energy if (
        _ambient_energy and
        not np.isnan(_ambient_energy) and
        _ambient_energy > 0
    ) else FALLBACK_ENERGY

    energy_threshold = int(safe_energy * 1.3)
    energy_threshold = max(150, min(energy_threshold, 3000))

    recognizer.energy_threshold = energy_threshold
    recognizer.dynamic_energy_threshold = False
    recognizer.pause_threshold = 1.2
    recognizer.phrase_threshold = 0.3
    recognizer.non_speaking_duration = 0.8

    print(f"[Listener] [MIC] Listening now - SPEAK! (threshold: {energy_threshold}, timeout: {timeout}s)")

    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            try:
                audio = recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit,
                )
                print("[Listener] OK Audio captured, processing...")
            except sr.WaitTimeoutError:
                print("[Listener] FAIL Timeout - no voice detected in time")
                return None

    except Exception as e:
        error_handler.log_and_demote(
            subsystem="VOICE_STT",
            exception=e,
            context="Opening hardware Microphone stream inside SpeechRecognizer loop",
            demote_to=SubsystemState.OFFLINE
        )
        return None

    audio_np = _audio_data_to_numpy(audio)
    if audio_np is not None:
        if not _verify_speaker(audio_np):
            print("[Listener] FAIL Voice doesn't match registered user - ignored")
            return None
        else:
            print("[Listener] OK Voice verified as user")

    whisper_text = _transcribe_with_whisper(audio)
    if whisper_text:
        print(f"[Listener] OK Whisper heard: '{whisper_text}'")
        return whisper_text.lower().strip()

    print("[Listener] Whisper empty, trying Google...")
    for language in ("en-IN", "en-US"):
        try:
            text = recognizer.recognize_google(audio, language=language)
            normalized = text.strip()
            if normalized:
                print(f"[Listener] OK Google ({language}): {normalized}")
                return normalized.lower()
        except sr.UnknownValueError:
            continue
        except sr.RequestError as e:
            print(f"[Listener] Google recognition error: {e}")
            break
        except Exception as e:
            print(f"[Listener] Google error: {e}")
            break

    print("[Listener] FAIL Couldn't understand.")
    return None


# ── Test mode ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[Listener Test Mode - Iron Man Voice System]")
    calibrate_ambient_noise()

    print("\nPre-loading Whisper (first time only)...")
    _load_whisper()
    print("Pre-loading voice encoder...")
    _load_voice_encoder()

    print("\nWaiting for double clap...")
    while True:
        if detect_double_clap():
            play_jarvis_ui_sound()
            print("\nOK Activated! Listening for command...")
            command = listen_for_command()
            if command:
                print(f"OK Command: '{command}'")
            else:
                print("FAIL No command heard.")
            print("\nWaiting for double clap again...")