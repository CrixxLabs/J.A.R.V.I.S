# voice_setup.py — One-time voice sample recorder
# Run this ONCE to teach Jarvis your voice

import pyaudio
import numpy as np
import time
import os
import wave

MIC_INDEX = 1
RATE = 16000
CHUNK = 1024
DURATION = 10  # seconds
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_FILE = os.path.join(BASE_DIR, "user_voice.npy")
SAMPLE_WAV = os.path.join(BASE_DIR, "user_voice_sample.wav")


def record_voice():
    print("=" * 60)
    print("JARVIS VOICE REGISTRATION")
    print("=" * 60)
    print()
    print("Speak naturally for 10 seconds.")
    print("Read this passage (or say anything you want):")
    print()
    print("  \"Hi Jarvis, this is my voice. I am the only one you")
    print("   should listen to. Please remember how I sound so you")
    print("   can recognize me even in noisy places. Let's build")
    print("   something amazing together.\"")
    print()
    input("Press ENTER when ready to record...")
    print()

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        input=True,
        input_device_index=MIC_INDEX,
        frames_per_buffer=CHUNK,
    )

    print("🎙  Recording... (speak now)")
    frames = []
    for i in range(int(RATE / CHUNK * DURATION)):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        # Progress indicator
        if i % 30 == 0:
            remaining = DURATION - int(i / (RATE / CHUNK))
            print(f"   ...{remaining}s left")

    stream.stop_stream()
    stream.close()
    p.terminate()

    print("✓ Recording complete!")
    print()

    # Save WAV for reference
    wf = wave.open(SAMPLE_WAV, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()

    # Generate embedding
    print("Creating voice fingerprint...")
    audio_np = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0

    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        encoder = VoiceEncoder(verbose=False)
        wav = preprocess_wav(audio_np, source_sr=RATE)
        embedding = encoder.embed_utterance(wav)
        np.save(VOICE_FILE, embedding)
        print(f"✓ Voice fingerprint saved to: {VOICE_FILE}")
        print()
        print("=" * 60)
        print("SUCCESS! Jarvis will now recognize only YOUR voice.")
        print("=" * 60)
    except Exception as e:
        print(f"✗ Failed to create fingerprint: {e}")
        print("  Check that resemblyzer is installed correctly.")


if __name__ == "__main__":
    record_voice()