# listener.py — Clap Detection + Personalized Voice Capture
# Layer 1: Double clap wake trigger
# Layer 2: Voice capture tuned to your volume
# Layer 3: Smart silence detection

import pyaudio
import numpy as np
import time
import speech_recognition as sr
from collections import deque

# ── Audio config ──────────────────────────────────────────────────────────────
RATE = 16000
CHUNK = 1024
MIC_INDEX = 1  # Microphone (Realtek(R) Audio) from your list

# ── Clap detection thresholds ─────────────────────────────────────────────────
CLAP_ENERGY_THRESHOLD = 3000     # Base threshold for clap detection
CLAP_MIN_INTERVAL = 0.15         # Min time between claps (seconds)
CLAP_MAX_INTERVAL = 0.8          # Max time between claps (seconds)

# ── Voice capture thresholds ──────────────────────────────────────────────────
VOICE_ENERGY_MULTIPLIER = 1.8    # How much louder than ambient = speech
SILENCE_DURATION = 1.5           # Seconds of silence = done talking
PHRASE_TIME_LIMIT = 20           # Max recording time

# ── Fallback energy if calibration fails ─────────────────────────────────────
FALLBACK_ENERGY = 300

# ── Calibration ───────────────────────────────────────────────────────────────
_ambient_energy = FALLBACK_ENERGY
_calibrated = False


def _get_audio_energy(data):
    """Calculate RMS energy of audio chunk — NaN safe."""
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
    """Auto-calibrate to your room's ambient noise level."""
    global _ambient_energy, _calibrated

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
        for _ in range(int(RATE / CHUNK * 2)):  # 2 seconds
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                energy = _get_audio_energy(data)
                # Only keep valid non-zero samples
                if energy > 0 and not np.isnan(energy):
                    samples.append(energy)
            except Exception:
                continue

        stream.stop_stream()
        stream.close()

        if samples:
            _ambient_energy = float(np.mean(samples))
            # Sanity check — if still NaN or 0, use fallback
            if np.isnan(_ambient_energy) or _ambient_energy <= 0:
                print("[Listener] ⚠ Calibration returned invalid value — using fallback")
                _ambient_energy = FALLBACK_ENERGY
        else:
            print("[Listener] ⚠ No valid audio samples — using fallback")
            _ambient_energy = FALLBACK_ENERGY

        _calibrated = True

        voice_threshold = int(_ambient_energy * VOICE_ENERGY_MULTIPLIER)
        print(f"[Listener] ✓ Calibrated — ambient: {_ambient_energy:.0f} | voice threshold: {voice_threshold}")

    except Exception as e:
        print(f"[Listener] Calibration failed: {e} — using fallback")
        _ambient_energy = FALLBACK_ENERGY
        _calibrated = True
    finally:
        try:
            p.terminate()
        except Exception:
            pass


def detect_double_clap():
    """
    Listen for double clap pattern.
    Returns True if detected, False otherwise.
    Works with both hand claps and desk taps.
    """
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

                # Detect sharp sound spike (clap)
                if energy > clap_threshold:
                    # Ignore if too soon after last clap (debounce)
                    if current_time - last_clap_time < CLAP_MIN_INTERVAL:
                        continue

                    clap_times.append(current_time)
                    last_clap_time = current_time
                    print(f"[Listener] Clap detected (energy: {energy:.0f})")

                    # Check if we have 2 claps within the time window
                    if len(clap_times) == 2:
                        interval = clap_times[1] - clap_times[0]
                        if CLAP_MIN_INTERVAL < interval < CLAP_MAX_INTERVAL:
                            print(f"[Listener] ✓ Double clap! (interval: {interval:.2f}s)")
                            stream.stop_stream()
                            stream.close()
                            return True
                        else:
                            # Interval too long — reset, treat latest as first clap
                            clap_times.clear()
                            clap_times.append(current_time)

            except Exception as e:
                print(f"[Listener] Clap read error: {e}")
                continue

    except Exception as e:
        print(f"[Listener] Stream error: {e}")
        return False
    finally:
        try:
            p.terminate()
        except Exception:
            pass


def play_jarvis_ui_sound():
    """
    Play JARVIS activation sound.
    Uses pygame safely — reinitializes mixer if needed.
    Falls back to winsound beep if anything fails.
    """
    import os

    sound_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_ui.mp3")

    try:
        import pygame

        # Safely reinitialize mixer if not active
        if not pygame.mixer.get_init():
            pygame.mixer.init()

        if os.path.exists(sound_path):
            # Stop anything currently playing
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.stop()
                time.sleep(0.1)

            pygame.mixer.music.load(sound_path)
            pygame.mixer.music.set_volume(0.4)
            pygame.mixer.music.play()

            # Wait for sound to finish
            timeout = time.time() + 3
            while pygame.mixer.music.get_busy():
                if time.time() > timeout:
                    break
                time.sleep(0.05)
        else:
            print(f"[Listener] jarvis_ui.mp3 not found at {sound_path} — using beep")
            _beep_fallback()

    except Exception as e:
        print(f"[Listener] UI sound failed: {e} — using beep")
        _beep_fallback()


def _beep_fallback():
    """System beep fallback if pygame fails."""
    try:
        import winsound
        winsound.Beep(800, 120)
        time.sleep(0.08)
        winsound.Beep(1000, 80)
    except Exception:
        pass


def listen_for_command(timeout=12, phrase_time_limit=20):
    """
    Listen for voice command — personalized to YOUR voice level.
    Auto-tuned energy threshold based on calibration.
    Returns recognized text or None.
    """
    if not _calibrated:
        calibrate_ambient_noise()

    recognizer = sr.Recognizer()

    # Safe energy threshold — always a valid int
    safe_energy = _ambient_energy if (
        _ambient_energy and
        not np.isnan(_ambient_energy) and
        _ambient_energy > 0
    ) else FALLBACK_ENERGY

    energy_threshold = int(safe_energy * VOICE_ENERGY_MULTIPLIER)
    energy_threshold = max(200, min(energy_threshold, 4000))  # Clamp to safe range

    recognizer.energy_threshold = energy_threshold
    recognizer.pause_threshold = 1.2
    recognizer.phrase_threshold = 0.4
    recognizer.non_speaking_duration = 0.8

    print(f"[Listener]  Listening (threshold: {energy_threshold})")

    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)

            try:
                audio = recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit,
                )
            except sr.WaitTimeoutError:
                print("[Listener] Timeout — nothing heard.")
                return None

    except Exception as e:
        print(f"[Listener] Mic error: {e}")
        return None

    # Try multi-language recognition
    for language in ("en-IN", "ml-IN", "en-US"):
        try:
            text = recognizer.recognize_google(audio, language=language)
            normalized = text.strip()
            if normalized:
                print(f"[Listener] ✓ Heard: {normalized}")
                return normalized.lower()
        except sr.UnknownValueError:
            continue
        except sr.RequestError as e:
            print(f"[Listener] Recognition error: {e}")
            return None
        except Exception as e:
            print(f"[Listener] Error: {e}")
            return None

    print("[Listener] Couldn't understand.")
    return None


# ── Test mode ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[Listener Test Mode]")
    calibrate_ambient_noise()

    print("\nWaiting for double clap...")
    while True:
        if detect_double_clap():
            play_jarvis_ui_sound()
            print("\n✓ Activated! Listening for command...")
            command = listen_for_command()
            if command:
                print(f"✓ Command: '{command}'")
            else:
                print("✗ No command heard.")
            print("\nWaiting for double clap again...")