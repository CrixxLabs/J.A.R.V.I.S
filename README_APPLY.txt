JARVIS MARK VII — Pocket-TTS surgical patch

Copy these four files into D:\J.A.R.V.I.S:
  REPLACE jarvis.py
  REPLACE jarvis_tts.py
  REPLACE boot_check.py
  ADD     pocket_tts_worker.py

Expected existing paths:
  C:\Users\sonur\JarvisTTS\.venv\Scripts\python.exe
  C:\Users\sonur\JarvisVoice\jarvis.safetensors

Then:
  cd D:\J.A.R.V.I.S
  python -m pytest -q tests

STOP if the existing 190/190 record is not preserved.
If 190/190:
  python .\MARK_VII_RELEASE_ACCEPTANCE.py

If acceptance PASS:
  python .\launch_mark_vii.py --no-build

Pocket fast path:
  persistent worker -> CUDA once -> jarvis.safetensors once ->
  generate_audio_stream() -> float32 PCM chunks -> sounddevice playback

Fallback:
  Pocket-TTS -> Edge-TTS -> Windows SAPI

Expected runtime metrics:
  [TTS] request -> first PCM: XXX ms
  [TTS] request -> playback: XXX ms
  [TTS] stream complete: XXXX ms
