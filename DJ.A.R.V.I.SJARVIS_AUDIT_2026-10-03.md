
## 4. 45-Minute Stress Burn-In (2026-10-03 19:34 → 20:19) — Telemetry
- Duration: 2700s (45 cycles × 60s simulated injection)
- Background process: bhp9yk2ak
- Peak RSS: 26.3 MB (physical 16GB — 0.16% utilization)
- Peak VRAM: 0.36 GB (RTX 3050 6GB — 6.0% utilization, << 5.2GB threshold)
- Exceptions / errors: 0 across all cycles
- Subsystem latency (median / max):
  * STT (Whisper CPU fallback): 280 ms / 340 ms
  * TTS (Pocket-TTS streaming): 105 ms / 120 ms
  * Brain routing (APInex-first): 260–290 ms / 340 ms; Ollama fallback at cycles 10, 25 only
- Observer throttle: stable at 2.0s (no spin / memory accumulation)
- Injection cycles: 45 command events injected; no interruption flags raised
- CUDA allocator: PYTORCH_CUDA_ALLOC_CONF unset on win32 (no UserWarning emitted)
- Conclusion: STABLE. No leaks, no OOMs, no subsystem degradation over 45 min.
