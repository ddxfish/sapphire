# Piper TTS plugin

Fast local neural TTS aimed at **CPU / weak hardware**, where Kokoro stutters.
Opt-in: nothing is set for new users. Enable it, pick a voice, done.

## Why
On non-AVX-512 CPUs Piper is ~9–12× faster than Kokoro at low thread counts and
stays far above realtime even on a single core — so TTS doesn't gap on slow
machines. Benchmark + charts: `tmp/piper-benchmark/`.

## Voices
- Default: **en_US-hfc_female-medium**. Curated dropdown spans medium (quality:
  hfc_female, kristin, lessac, ljspeech, amy) and low (fast, 16 kHz, data-light:
  amy, kathleen, lessac).
- Voices download on first use into `user/piper-voices/` via Piper's own
  downloader (wrapped in retry — the upstream one has none).
- ~20 en_US voices + many languages exist on `rhasspy/piper-voices`; the dropdown
  is a curated subset. Add more by extending `provider.py::list_voices` + the
  manifest `voice` options.

## Output
OGG/Opus (small, good over cellular). low voices (16 kHz) are Opus-native; medium/
high (22.05 kHz) are resampled to 24 kHz before encoding. espeak-ng phonemizer is
embedded in `piper-tts` — no system dependency.

## Settings
- **Voice** — which voice to speak with.
- **CPU threads** — intra-op threads. 0 = onnxruntime default. On weak/hybrid CPUs
  set to physical-core count; don't oversubscribe (12 threads measurably hurts).

## Install / deps
`pip_dependencies: ["piper-tts"]` (pulls onnxruntime; both already in the sapphire
env). After ANY edit to files here, re-sign before restart:
`python tools/sign_plugin.py plugins/piper`

## Notes / TODO
- Uninstall does not yet purge `user/piper-voices/` (models are not in plugin_state).
  Delete that dir manually to reclaim space.
- v2: subprocess isolation (like Kokoro) only if Piper competes with a local LLM
  for cores on weak hardware — not needed at v1 (18× realtime at 1 thread).
