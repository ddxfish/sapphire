# Voice — talking to Sapphire and hearing her answer

## What it is

Sapphire has a full voice stack: speech-to-text (STT) for your voice in, text-to-speech (TTS) for her voice out, a wake word so she answers to her name, and a hands-free **Conversation mode** with real interruptions. Everything runs locally by default; cloud providers are opt-in. This guide covers all of it — from holding the mic button to tuning echo cancellation.

## Using it

### The four ways to voice

| Mode | How it starts | Where audio happens | Best for |
|------|--------------|--------------------|----------|
| **Hold-to-record** | Hold the 🎤 button in the composer, speak, release | Your browser's mic; reply plays in the browser | Quick voice messages at the desk |
| **Wake word** | Say the wake word (e.g. "hey sapphire") near the server | Server's mic and speakers | Across-the-room requests |
| **Conversation mode** | Mic flyout → Conversation → Local mic / Browser mic | Server mic *or* browser mic; continuous listening | Real back-and-forth, hands-free |
| **Phone calls** | Call her Twilio number (or she calls you) | The phone line | Voice away from home — see [PHONE-CALLS.md](PHONE-CALLS.md) |

All four share the same STT and TTS providers, the same transcription cleanup, and the same privacy gates.

### Voice input — STT

Pick a provider in **Settings → STT**:

| Provider | Runs | Notes |
|----------|------|-------|
| **Disabled** | — | No voice input; mic button inert |
| **Local (Faster Whisper)** | Your machine | Free, private. Model sizes tiny → large; `.en` variants are English-only and faster |
| **Fireworks Whisper** | Cloud | Needs a Fireworks API key; fast, no local VRAM |
| **Sapphire Router (Managed)** | Cloud | Managed/hosted installs |

Two support layers clean up what the mic hears:

- **Voice activity detection (VAD)** — **Silero VAD** (a tiny ML model, robust to background noise) decides speech vs. silence. If Silero is off or unavailable, a **Classic VAD** fallback uses amplitude percentile thresholding. The STT tab shows a live Silero status badge and a **Test my voice (5s)** button that scores your mic and suggests a threshold.
- **Hallucination filter** — Whisper invents canned phrases on silence ("Thanks for watching", "[music]"...). A shared filter at the provider boundary drops them for every voice lane, so a 3am noise doesn't become a conversation.

Hold-to-record specifics: the browser records while you hold 🎤, releasing sends the audio to the server for transcription, and the transcribed text is sent as your message. Leaving the button or hiding the tab stops the recording safely.

### Voice output — TTS

Pick a provider in **Settings → TTS**:

| Provider | Runs | Streaming | Notes |
|----------|------|-----------|-------|
| **Disabled** | — | — | Silent |
| **Local (Kokoro)** | Your machine (GPU or CPU) | Yes | 28 built-in voices: American/British, male/female. Runs as a local server subprocess |
| **Sapphire Router (Managed)** | Cloud | No | Managed/hosted installs |
| **Piper** (plugin) | Your machine, CPU | Yes | Fast on weak hardware; voices download on first use |
| **ElevenLabs** (plugin) | Cloud | No | Needs an API key |
| **Google Translate** (plugin) | Cloud | No | Free, basic quality |

Plugin providers appear in the dropdown automatically when their plugin is enabled. The **Test TTS** button at the bottom of the tab speaks a test line and reports latency.

**Voice, pitch, and speed are per-chat, not global.** They live in the chat sidebar under the **TTS (Voice)** accordion — a voice dropdown plus Pitch and Speed sliders. Each chat (and each persona) remembers its own; switching chats switches her voice. Defaults: voice Heart (`af_heart`), pitch 0.98, speed 1.3.

- **Speed** is applied at synthesis time (providers clamp to their supported range).
- **Pitch** is native for Kokoro and Piper. For providers without native pitch it's applied by resampling the audio afterward — which also changes duration, so keep it near 1.0 there.

Where her voice comes out:

- **Web chats** — audio plays in your browser. Every assistant message also has a replay action to speak it again; while TTS is playing, the mic button becomes a ⏹ stop button.
- **Wake word turns** — audio plays on the server's speakers.
- **Conversation mode** — server speakers (Local mic) or your browser (Browser mic).

### Streaming TTS

Off by default. With **Streaming** on (Settings → TTS → Streaming section), Sapphire synthesizes and starts speaking each chunk as the LLM finishes it — speech starts a second or two after generation begins instead of after the whole reply. Only streaming-capable providers do this (Kokoro, Piper); others fall back to whole-reply playback automatically, no harm done.

Tuning lives in the same section: split mode (`paragraph` keeps prosody natural, `sentence` gets audio out faster), min/max chunk sizes, pauses after paragraph/sentence boundaries, and how `*stage directions*` and `(parentheticals)` are rendered as pauses.

**Streaming TTS is also what gives Conversation mode its voice** — see below.

### The mic button and flyout

The composer's mic is a split button: **[🎤 | ▾]**.

- **🎤** — hold to record (release to send). If TTS is playing it shows ⏹ and a tap stops playback instead. While Conversation mode is on it shows 🎙 and a tap ends the session.
- **▾** — opens the device flyout: a volume slider + mute for this browser, and the **Conversation** segment with three positions: **Off | Local mic | Browser mic**.

The flyout is *device* settings (this machine's audio); the sidebar is *chat* settings (her voice in this chat). A phone call never lights the mic button — it isn't this device's session.

### Wake word

Turn it on in **Settings → Wakeword**. Sapphire then listens continuously on the server's mic (openWakeWord, ONNX) and answers when she hears the model's phrase.

**Models:**

- **Bundled with Sapphire:** `hey_sapphire` (the default).
- **Built into openWakeWord:** `alexa`, `hey_mycroft`, `hey_jarvis`, `hey_rhasspy`, `timer`, `weather`.
- **Custom:** drop `.onnx` or `.tflite` files into `user/wakeword/models/` — they appear in the dropdown automatically. Community collections exist on GitHub.

Changing the model in Settings hot-swaps the detector — no restart. The **threshold** (default 0.6) sets sensitivity: lower catches more but false-triggers more.

**A wake turn, start to finish:** wake word detected → short acknowledgment tone → she records until you stop talking (or aborts after a few seconds of no speech) → transcribe → voice-command check → the reply streams live into any open web page *and* speaks on the server's speakers. The Stop button on the page works on wake turns too.

Wake word needs a working STT provider — with STT disabled she'll hear her name but can't understand what follows. Detection is suppressed while you're actively using the web UI (recording or mid-chat) and while Conversation mode is running.

### Voice commands

The **voice-commands** plugin (enabled by default) handles instant actions before the LLM ever sees them — the utterance must match exactly:

| Say | Does |
|-----|------|
| "stop", "halt", "be quiet", "shut up" | Stop TTS and cancel generation |
| "reset", "reset chat", "clear chat", "clear history" | Clear the current chat's history |

These are ordinary plugin hooks — other plugins can register their own triggers.

### Conversation mode

The headline voice feature: **continuous listening with real turns and real interruptions**. No wake word per utterance, no button. You talk, she answers, you can talk over her and she stops.

**Prerequisites:**

| Need | Why |
|------|-----|
| An STT provider | She has to understand you |
| A streaming-capable TTS provider (Kokoro or Piper) **with Streaming TTS enabled** | Conversation audio rides the streaming pipeline — with streaming off her replies appear on screen but she stays silent |
| For **Local mic**: wake word enabled and running | Local conversation borrows the wakeword's mic via a fail-safe handoff |
| For **Browser mic**: a mic permission in your browser | The browser is the audio device |

**Starting and stopping:** mic flyout → Conversation → **Local mic** (the server's microphone and speakers) or **Browser mic** (this browser over a WebSocket — works from your phone on the couch). **Off** ends it, as does tapping the green 🎙 mic button. The two sources are mutually exclusive; picking one hands off from the other. The mode is ephemeral — it doesn't survive a restart, and the wake word is restored the moment it ends. If the mic can't be acquired, the mode simply doesn't engage and wakeword listening stays intact — she is never left deaf.

**How a turn works.** Every audio frame is scored by Silero VAD against `CONVERSATION_VAD_THRESHOLD`. When speech starts, she accumulates your utterance; when you've been silent for `CONVERSATION_ENDPOINT_SILENCE_MS` (default 700ms), the utterance ends and goes to STT. Utterances shorter than `CONVERSATION_MIN_SPEECH_MS` (default 200ms) are discarded as blips — a cough doesn't start a turn. Her reply streams to the page and to the speakers as it generates.

**Barge-in.** While she's speaking, sustained speech from you for `CONVERSATION_BARGE_HOLD_MS` (default 90ms) cuts her off — the LLM stream is cancelled, the audio stops, and your new utterance becomes the next turn. Barge-in only *arms* once her audio is actually flowing: talking during her silent thinking phase (reasoning models, tool calls) won't kill the turn, because there's nothing to interrupt yet.

**Start word (optional).** Set `CONVERSATION_START_WORD` (e.g. `hey sapphire, sapphire`) and she only acts on utterances that *begin* with one of those phrases — the phrase is stripped and the rest is the message. Unlike the wake word this is matched on the transcription (fuzzy, so "hey staff fire" still works — tune `CONVERSATION_START_WORD_FUZZY`), and there's no pause: it's a filter, not a trigger. Great for rejecting a TV in the room. Blank = respond to everything.

**Echo — the three audio tiers.** The classic failure is her hearing herself through your speakers and barging in on her own voice:

| Tier | Setting | When |
|------|---------|------|
| Headphone (default) | `CONVERSATION_DTLN` = none | You wear headphones; nothing to cancel |
| Duplex AEC | `CONVERSATION_DTLN` = 256 or 512 | Open speakers on the server: a single duplex audio stream plays her voice *and* uses exactly what it played as the echo reference for DTLN echo cancellation. 512 cancels harder for a little more CPU. Needs the DTLN models in `user/models/dtln/`; falls back to the headphone tier if absent |
| Browser | (Browser mic mode) | The browser's own echo cancellation applies to its mic capture |

Duplex-tier extras (config-only, not in the Settings UI): `CONVERSATION_AEC_DELAY_MS` aligns the echo reference to your speaker round-trip, `CONVERSATION_BARGE_GUARD_MS` ignores barge-ins briefly while DTLN locks onto her voice onset, and `CONVERSATION_BARGE_RMS_FLOOR` rejects quiet residual echo as a false barge.

**Tuning** lives in **Settings → Conversation**. All knobs take effect on the next activation (flip Off/On) — no restart:

- Too many false turns from background noise → raise `CONVERSATION_VAD_THRESHOLD` (0.6–0.7) and/or `CONVERSATION_MIN_SPEECH_MS` (250–400), or set a start word.
- She cuts you off mid-sentence → raise `CONVERSATION_ENDPOINT_SILENCE_MS` (900–1200).
- Keyboard clatter interrupts her → raise `CONVERSATION_BARGE_HOLD_MS` (150–250).
- You can't interrupt her → lower `CONVERSATION_BARGE_HOLD_MS`, and on open speakers check the DTLN tier is actually loaded (log says `using duplex/DTLN-… audio tier`).

**Phone calls** run on the same conversation engine as independent external sessions (up to `CONVERSATION_EXTERNAL_SLOTS` at once) with their own per-call tuning — they never touch your local mic or wake word. See [PHONE-CALLS.md](PHONE-CALLS.md).

### Voice privacy

In a **private chat** (vault open — see [PRIVACY.md](PRIVACY.md)), cloud voice is refused in both directions: cloud STT would ship your voice audio off-machine, cloud TTS would ship her reply text. The gate reads each provider's `is_local` flag and **fails closed** — a provider that can't prove it's local is treated as cloud. Hold-to-record surfaces the refusal as a toast; wake word and conversation turns log it and stay silent. There is deliberately no override toggle: switch to a local provider or type.

### Hosted / managed installs

Managed installs have no server-side audio: the Audio and Wakeword tabs are hidden, local-mic recorder knobs disappear, and STT/TTS run through the Sapphire Router providers. Hold-to-record and browser-mic conversation still work — the browser is the microphone.

## Settings

Per-chat voice, pitch, and speed are in the chat sidebar (**TTS (Voice)** accordion), not below — there are no global keys for them.

### STT (Settings → STT)

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `STT_PROVIDER` | `none` | Voice-input engine (none / faster_whisper / fireworks_whisper / sapphire_router) | STT tab, provider dropdown |
| `STT_MODEL_SIZE` | `base.en` | Faster Whisper model; bigger = more accurate, more RAM/VRAM | STT tab |
| `STT_LANGUAGE` | `en` | ISO 639-1 language for transcription | STT tab |
| `STT_FIREWORKS_API_KEY` | *(empty)* | Fireworks key for cloud Whisper | STT tab (Fireworks) |
| `STT_FIREWORKS_MODEL` | `whisper-v3-turbo` | Fireworks model choice | STT tab (Fireworks) |
| `FASTER_WHISPER_DEVICE` | `cuda` | cpu / cuda / auto | STT tab, advanced |
| `FASTER_WHISPER_COMPUTE_TYPE` | `int8` | Precision vs. speed (int8 / float16 / float32) | STT tab, advanced |
| `FASTER_WHISPER_BEAM_SIZE` | `3` | Decoding beam; higher = slower, slightly better | STT tab, advanced |
| `FASTER_WHISPER_NUM_WORKERS` | `4` | Parallel transcription workers | STT tab, advanced |
| `FASTER_WHISPER_VAD_FILTER` | `true` | Whisper's internal VAD pre-filter | STT tab, advanced |
| `STT_VAD_ENABLED` | `true` | Use Silero VAD (recommended); off = Classic VAD | STT tab, Voice Activity Detection |
| `STT_VAD_SPEECH_THRESHOLD` | `0.5` | Silero speech probability cutoff | STT tab, Silero VAD |
| `RECORDER_SILENCE_DURATION` | `1.0` | Seconds of silence that end a recording | STT tab |
| `RECORDER_MAX_SECONDS` | `30` | Hard cap on one recording | STT tab |
| `RECORDER_NO_SPEECH_TIMEOUT` | `3.0` | Abort a wake-word recording if you never speak | STT tab, advanced |
| `RECORDER_SPEECH_DURATION` | `0.2` | Minimum speech run to count as speech | STT tab, advanced |
| `RECORDER_BEEP_WAIT_TIME` | `0.15` | Gap after the wake tone before recording | STT tab, advanced |
| `RECORDER_SILENCE_THRESHOLD` | `0.0025` | Classic VAD only: amplitude floor | STT tab, Classic VAD |
| `RECORDER_BACKGROUND_PERCENTILE` | `32` | Classic VAD only: background-noise percentile | STT tab, Classic VAD |
| `RECORDER_NOISE_MULTIPLIER` | `1.1` | Classic VAD only: speech-over-background ratio | STT tab, Classic VAD |
| `RECORDER_LEVEL_HISTORY_SIZE` | `15` | Classic VAD only: rolling noise-estimate window | STT tab, Classic VAD |

### TTS (Settings → TTS)

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `TTS_PROVIDER` | `none` | Voice-output engine (none / kokoro / sapphire_router / plugin providers) | TTS tab, provider dropdown |
| `KOKORO_DEVICE` | `cuda` | Kokoro synthesis on GPU or CPU | TTS tab (Kokoro) |
| `KOKORO_CUDA_DEVICE` | *(empty)* | GPU index for multi-GPU setups | TTS tab, advanced |
| `TTS_SERVER_HOST` / `TTS_SERVER_PORT` | `0.0.0.0` / `5012` | The local Kokoro server subprocess bind | TTS tab, advanced |
| `TTS_PRIMARY_SERVER` / `TTS_FALLBACK_SERVER` | localhost URLs | Where the app reaches the TTS server | TTS tab, advanced |
| `TTS_STREAMING_ENABLED` | `false` | Speak sentence-sized chunks as the LLM writes; required for Conversation-mode audio | TTS tab, Streaming |
| `TTS_STREAMING_SPLIT_MODE` | `paragraph` | Chunk on paragraphs (natural) or sentences (faster first audio) | TTS tab, Streaming |
| `TTS_STREAMING_MIN_CHARS` | `15` | Smallest chunk emitted at a boundary | TTS tab, Streaming |
| `TTS_STREAMING_MAX_CHARS` | `750` | Force-split cap | TTS tab, Streaming |
| `TTS_STREAMING_PAUSE_PARAGRAPH_MS` | `80` | Silence after a paragraph chunk | TTS tab, Streaming |
| `TTS_STREAMING_PAUSE_SENTENCE_MS` | `0` | Silence after a sentence chunk (sentence mode) | TTS tab, Streaming |
| `TTS_STREAMING_STAGE_PAUSE_STYLE` | `comma` | How `*stage directions*`/(parens) pause: comma / period / ellipsis / none | TTS tab, Streaming |
| `TTS_ELEVENLABS_API_KEY` / `_MODEL` / `_VOICE_ID` | *(empty)* | ElevenLabs plugin credentials/choices | ElevenLabs plugin settings |

### Wakeword (Settings → Wakeword)

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `WAKE_WORD_ENABLED` | `false` | Continuous wake-word listening on the server mic | Wakeword tab |
| `WAKEWORD_MODEL` | `hey_sapphire` | Which model/phrase to listen for | Wakeword tab, dropdown |
| `WAKEWORD_THRESHOLD` | `0.6` | Detection confidence; lower = more sensitive | Wakeword tab |
| `WAKEWORD_FRAMEWORK` | `onnx` | Inference backend (onnx everywhere; tflite Linux-only) | Wakeword tab, advanced |
| `CHUNK_SIZE` | `1280` | Detector frame size (samples) | Wakeword tab, advanced |
| `WAKE_TONE_DURATION` | `0.15` | Acknowledgment beep length (seconds) | Wakeword tab, advanced |
| `WAKE_TONE_FREQUENCY` | `440` | Acknowledgment beep pitch (Hz) | Wakeword tab, advanced |

### Conversation (Settings → Conversation)

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `CONVERSATION_DTLN` | `none` | Echo tier: none (headphones) / 256 / 512 (open-speaker AEC) | Conversation tab |
| `CONVERSATION_START_WORD` | *(empty)* | Only act on utterances beginning with these phrases (comma-separated); blank = off | Conversation tab |
| `CONVERSATION_START_WORD_FUZZY` | `0.7` | Start-word match looseness (1.0 = exact) | Conversation tab |
| `CONVERSATION_VAD_THRESHOLD` | `0.5` | Speech confidence to count as you talking | Conversation tab |
| `CONVERSATION_BARGE_HOLD_MS` | `90` | Sustained speech needed to interrupt her | Conversation tab |
| `CONVERSATION_MIN_SPEECH_MS` | `200` | Shortest utterance that starts a turn | Conversation tab |
| `CONVERSATION_ENDPOINT_SILENCE_MS` | `700` | Pause that ends your utterance | Conversation tab |
| `CONVERSATION_AEC_DELAY_MS` | `0` | Duplex tier: echo-reference alignment (0 off, <0 auto, >0 manual ms) | *(config only)* |
| `CONVERSATION_BARGE_GUARD_MS` | `300` | Duplex tier: barge-in blackout while AEC locks on | *(config only)* |
| `CONVERSATION_BARGE_RMS_FLOOR` | `0.03` | Duplex tier: minimum loudness for a real barge | *(config only)* |
| `CONVERSATION_EXTERNAL_SLOTS` | `2` | Max simultaneous external sessions (phone calls) | *(config only)* |

### Audio devices (Settings → Audio)

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `AUDIO_INPUT_DEVICE` | auto | Server input device (mic) — pick + 🎙 Test | Audio tab |
| `AUDIO_OUTPUT_DEVICE` | auto | Server output device (speakers) — pick + 🔊 Test | Audio tab |

## Quick Troubleshooting

- **She hears herself and self-interrupts (open speakers)** → conversation echo tier → set `CONVERSATION_DTLN` to 256 or 512 (models must exist in `user/models/dtln/`), or wear headphones; check the log for "using duplex/DTLN … tier" vs. "falling back to headphone tier".
- **Conversation mode replies show on screen but she's silent** → Streaming TTS is off or the provider can't stream → enable **Settings → TTS → Streaming** and use Kokoro or Piper.
- **"Local mic" won't turn on** → wake word off or mic unavailable → enable Wakeword first (local conversation borrows its mic); check Settings → Audio input device and the log's `[CONV]` lines. On failure wakeword is restored automatically.
- **Can't barge in / interrupts don't register** → she may still be "thinking" (barge-in arms only once audio flows) → wait for her voice, then talk over it; if it still won't cut, lower `CONVERSATION_BARGE_HOLD_MS`.
- **Keyboard/cough interrupts her** → raise `CONVERSATION_BARGE_HOLD_MS` (150–250) and `CONVERSATION_MIN_SPEECH_MS`.
- **She cuts you off mid-sentence** → raise `CONVERSATION_ENDPOINT_SILENCE_MS` (900–1200).
- **Wake word misses you** → lower `WAKEWORD_THRESHOLD` (0.4–0.5), check the Audio tab input device with 🎙 Test, and confirm the right model is selected. False triggers → raise the threshold.
- **Wake tone plays but nothing happens** → STT is disabled or still loading → pick a provider in Settings → STT; the mic button tooltip shows "STT loading — downloading speech model" while a model downloads.
- **Mic button stuck "loading" / provider switch seemed to work but voice is dead** → the provider failed to construct (bad key, missing CUDA, model download failure) and fell back to disabled → check the app log for `[stt] Failed to create` / `[tts] Failed to create`, fix the cause, re-select the provider.
- **Pitch sounds wrong / audio speeds up or slows down with pitch** → the provider has no native pitch, so pitch is a resample (changes duration too) → keep pitch near 1.0 on ElevenLabs/gTTS; Kokoro and Piper shift pitch natively.
- **No audio in the browser at all, toast about autoplay** → the browser blocked audio before your first interaction → click anywhere on the page and retry.
- **Voice refused with a "Private chat" message** → the vault gate: the active STT or TTS provider isn't local → switch to a local provider (Faster Whisper / Kokoro / Piper) or type. Note plugin providers must declare themselves local in their manifest, or they're treated as cloud.
- **Silero badge says unavailable** → the VAD model isn't loaded (first-run download or offline) → Classic VAD carries voice detection in the meantime; check the badge again after connectivity.

## See also

- [PHONE-CALLS.md](PHONE-CALLS.md) — telephony: the same conversation engine over a real phone line
- [PRIVACY.md](PRIVACY.md) — the vault, private chats, and why cloud voice gets refused
- [CHATS.md](CHATS.md) — per-chat settings, including the sidebar voice/pitch/speed
- [PERSONAS.md](PERSONAS.md) — bundling a voice with a prompt/toolset as a persona
- [PLUGINS.md](PLUGINS.md) — enabling the Piper / ElevenLabs / gTTS provider plugins
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general diagnosis beyond voice

## Reference for AI

STT: registry core/stt/providers (faster_whisper local, fireworks_whisper cloud, sapphire_router managed, none). `STT_ENABLED`/`TTS_ENABLED` are DERIVED from provider != 'none', not user keys. Silero VAD (core/stt/silero_vad.py) with Classic percentile fallback (RECORDER_* keys, only active when Silero off/unavailable). Hallucination filter at provider boundary (core/stt/hallucination.py) — all lanes inherit. Routes: POST /api/transcribe (browser hold-to-record; 403 on private-chat cloud-STT gate), GET /api/stt/vad-status, POST /api/stt/vad-test.

TTS: registry core/tts/providers (kokoro local subprocess :5012, sapphire_router managed, none) + plugin providers (piper local, elevenlabs, gtts). 28 Kokoro voices (11 AF, 9 AM, 4 BF, 4 BM). supports_streaming: kokoro + piper only. supports_pitch: kokoro + piper (native); others = client-side resample (duration changes). Voice/pitch/speed are PER-CHAT settings (`voice`, `pitch`, `speed` in chat settings; sidebar "TTS (Voice)" accordion; applied on chat switch via tts.set_voice/set_pitch/set_speed). Defaults af_heart / 0.98 / 1.3; pitch clamped 0.5–2.0. Routes: POST /api/tts (play=server speakers, file=browser blob), /api/tts/stream (SSE chunks; 503 if streaming off → client falls back), /api/tts/test, /api/tts/stop, GET /api/tts/status, /api/tts/voices.

STREAMING TTS: core/tts/streaming.py (chunker) + stream_pump.py. Pump enabled iff TTS_ENABLED && TTS_STREAMING_ENABLED && provider.supports_streaming. Web replies auto-speak in browser (SSE tts_chunk); streaming off = whole-blob after generation. Strips think/reasoning/tools blocks + code fences before synth.

WAKEWORD: core/wakeword/ (openWakeWord). Builtin: alexa, hey_mycroft, hey_jarvis, hey_rhasspy, timer, weather; bundled: hey_sapphire (core/wakeword/models); custom: user/wakeword/models/*.onnx|.tflite auto-discovered (GET /api/settings/wakeword-models). WAKEWORD_MODEL change = hot-swap (reload_wakeword_model), no restart. Wake turn: tone → record (RECORDER_NO_SPEECH_TIMEOUT abort) → transcribe → post_stt hook → process_llm_query(voice_turn=True) → streams to page (VOICE_TURN events) + server speakers. Suppressed during web activity (_web_active) and conversation mode. Requires STT.

VOICE COMMANDS: plugins/voice-commands — pre_chat hooks with voice_match, exact match, bypass LLM. stop|halt|be quiet|shut up → stop TTS + cancel; reset|reset chat|clear chat|clear history → clear history.

CONVERSATION MODE ("true speech"): core/conversation/ — engine.py (IDLE/USER_SPEAKING/RESPONDING state machine), driver.py (STT→chat_stream→sink; barge = scoped cancel_generation + sink.stop), manager.py, vad.py (SpeechGate w/ hysteresis: exit threshold = threshold−0.15). Sources: local_source (headphone tier), duplex_source (DTLN-AEC 256/512, one duplex stream = source AND sink, models user/models/dtln/, soft-fallback to headphone), browser_source (WS /ws/conversation). UI: mic flyout conv-seg Off|Local mic|Browser mic (features/convo.js owns transitions; truth via conversation_mode_changed SSE). Routes: PUT/GET /api/runtime/true-speech (local), WS /ws/conversation (browser; refused 4409 if local active). Local requires active wakeword (fail-safe handoff, restored on any failure/exit); browser doesn't. AUDIO REQUIRES streaming TTS + capable provider (driver feeds sink only tts_chunk events). Barge-in arms on first tts_chunk (not content — thinking is uninterruptible by design). Start word = STT-side fuzzy prefix gate (match_start_word), no pause, strips prefix. Tuning read fresh per activation. Settings tab "Conversation" (💬). External sessions (phone) = manager.start_external, CONVERSATION_EXTERNAL_SLOTS cap (default 2), own driver/gate per call, never touches wakeword; one live session per chat. Mode is ephemeral (not persisted).

PRIVACY: core/voice_privacy.py — private chat + non-local provider = refuse (stt_gate_reason/tts_gate_reason). Fail-closed on missing is_local metadata; fail-open only on systemic settings errors. No override toggle by design. Gates: /api/transcribe 403, wake path pre-record, conversation driver per-session, pump first-push, /api/tts file-mode 403. NOTE: piper/gtts/elevenlabs manifests don't declare is_local → all treated as cloud by the gate (piper is factually local — known gap).

MANAGED MODE: Audio + Wakeword settings tabs hidden; recorder keys hidden; sapphire_router STT/TTS providers.

DEVICES: unified device manager; AUDIO_INPUT_DEVICE/AUDIO_OUTPUT_DEVICE null = auto (Linux prefers pipewire/pulse/default). Test buttons on Audio tab.
