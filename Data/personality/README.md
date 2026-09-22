# Arju / JARVIS Personality Seed v0.1

Purpose: give MARK VII a model-independent personality and user-adaptation layer.

Design:
- NVIDIA, Gemini, and Ollama should all feel like the same JARVIS.
- Retrieve only relevant profile entries for each interaction.
- Explicit corrections override weaker inferred/default preferences.
- Sensitive personal material is intentionally excluded from this seed.
- Start with retrieval + feedback learning; do not fine-tune a foundation model yet.

Recommended flow:
1. Review/edit the JSONL seed.
2. Let Codex build profile loading, relevance retrieval, privacy filtering, and prompt assembly.
3. Add correction capture to `corrections.jsonl`.
4. Test personality consistency across NVIDIA/Gemini/Ollama.
5. Accumulate real corrections before considering fine-tuning.
