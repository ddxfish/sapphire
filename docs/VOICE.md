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
| **Local (Faster Whisper)** | Your machine, in its own process | Free, private. Model sizes tiny → large; `.en` variants are English-only and faster. Runs on the CPU when it can't use the card, and the bell says why. On Linux the card needs **Install Whisper GPU support** in Settings › STT |
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
| **Local (Kokoro)** | Your machine (GPU or CPU) | Yes | 28 built-in voices: American/British, male/female. Runs as a local server subprocess. First run downloads the model (about 330 MB) under a progress row in the notification center |
| **Sapphire Router (Managed)** | Cloud | No | Managed/hosted installs |
| **Piper** (plugin) | Your machine, CPU | Yes | Fast on weak hardware; voices download on first use |
| **ElevenLabs** (plugin) | Cloud | No | Needs an API key |
| **Google Translate** (plugin) | Cloud | No | Free, basic quality |

Plugin providers appear among the provider cards automatically when their plugin is enabled. The **Test TTS** button at the bottom of the tab makes a test line (it does not play it), reports latency, and names the Kokoro server that answered (primary, or the fallback and why the primary didn't).

A provider switch runs as a job under its own row in the notification center, so Save returns at once even while a model downloads. If the new provider fails to start, the previous one comes back when it can be rebuilt, otherwise voice goes off; the row says which.

**Kokoro servers** (Settings → TTS → Kokoro, advanced): `TTS_PRIMARY_SERVER` is where speech is made, a URL or a pairing string from another computer's shared Kokoro. `TTS_FALLBACK_SERVER` is tried when the primary doesn't answer within `TTS_FALLBACK_TIMEOUT` (0.2 s); blank means no fallback. Both are read on every sentence, so an edit reaches her next one. To share this computer's Kokoro, turn on `TTS_SHARE_ENABLED`: a second door with a pinned self-signed certificate and a key per computer; add computers in the panel under the Test button and paste each one's pairing string as its primary. The Kokoro worker guards itself against leaks: it measures its own size after her first word and restarts once quiet for 5 s when it has grown 2 GB past that, or after 500 requests. It never restarts mid-sentence.

**Voice, pitch, and speed are per-chat, not global.** They live in the chat sidebar under the **TTS (Voice)** accordion — a voice picker plus Pitch and Speed sliders. Each chat (and each persona) remembers its own; switching chats switches her voice. Defaults: voice Heart (`af_heart`), pitch 0.98, speed 1.3.

- **Speed** is applied at synthesis time (providers clamp to their supported range).
- **Pitch** is a resample on every voice (Kokoro and Piper do it before the audio is encoded, the others after), so it also changes the length a little: keep it near 1.0.

Where her voice comes out:

- **Web chats** — audio plays in your browser. Every assistant message also has a replay action to speak it again; while TTS is playing, the mic button becomes a ⏹ stop button.
- **Wake word turns** — audio plays on the server's speakers.
- **Conversation mode** — server speakers (Local mic) or your browser (Browser mic).

**Play speech here** (Settings → TTS, `TTS_LOCAL_PLAYBACK`, on by default) is the switch for all of the above. Off: speech is still made and every TTS hook still fires, but nothing plays on this computer or in its browser. That is the switch for sending her voice to an avatar or robot plugin instead ([plugin-author/voice-routing.md](plugin-author/voice-routing.md)). With no such plugin on, a reply that would be voiced whole (Stream speech off, or a voice with no streaming such as ElevenLabs) is not made at all: nothing is billed for speech nobody hears. It doesn't govern satellites, phone calls, Discord, Telegram voice notes, the voice preview, or the chime.

### Streaming TTS

On by default. With **Streaming** on (Settings → TTS → **Streaming** section), Sapphire synthesizes and starts speaking each chunk as the LLM finishes it — speech starts a second or two after generation begins instead of after the whole reply. Only streaming-capable providers do this (Kokoro, Piper); others fall back to whole-reply playback automatically, no harm done.

Tuning lives in the same section: split mode (`auto`, the default, starts with the first sentence and then grows the pieces as fast as the voice engine keeps ahead; `whole_paragraph` makes each paragraph whole for the smoothest voice and the slowest start; `sentence` cuts at every sentence), min/max chunk sizes, pauses after paragraph/sentence boundaries, and how `*stage directions*` and `(parentheticals)` are rendered as pauses.

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
- **Custom:** add them in the **Library** on the Wakeword tab, or train your own with the Wakeword Maker ([WAKEWORD-MAKER.md](WAKEWORD-MAKER.md)). This computer listens with openWakeWord `.onnx` models only. A `.tflite` is a microWakeWord model for an ESP32 satellite and is never offered to this computer's detector.

Changing the model in Settings hot-swaps the detector — no restart. The **threshold** (default 0.6) sets sensitivity: lower catches more but false-triggers more. A model change brings the model's own threshold along (from its `.json` sidecar, else 0.5 for an `.onnx`); a threshold you set in the same save wins.

**The Library** (Settings → Wakeword → Library) lists every phrase Sapphire can listen for, one row per phrase. The PC column holds openWakeWord `.onnx` models (this computer and a Pi), the Satellite column microWakeWord `.tflite` + `.json` manifest pairs (ESP32 boards). **Add a PC wake word** takes a `.onnx` of 4 KB to 8 MB with an optional manifest; **Add a satellite wake word** takes a `.tflite` of 4 KB to 1.5 MB plus its manifest, which is required. × removes one. Your files live in `user/wakeword/models/` and win over a shipped model of the same name; only yours can be removed. A file dropped there by hand that fails the checks shows as **unusable** (hover for why).

**Who listens for what.** Each listener has its own switch: this computer is the `WAKEWORD_MODEL` dropdown, each satellite is its board's **Wake word** tab in Devices (a library model or the board's own built-in word). A board restarts on the change; one that is offline keeps the request and gets it when next seen. The switch applies the model's own threshold.

**A wake turn, start to finish:** wake word detected → short acknowledgment tone → she records until you stop talking (or aborts after a few seconds of no speech) → transcribe → voice-command check → the reply streams live into any open web page *and* speaks on the server's speakers. The Stop button on the page works on wake turns too.

**The wake word while she is speaking is the stop button.** If her voice is playing on the server's speakers when she hears the wake word, she goes quiet and nothing is recorded: no tone, no turn. Say the wake word again to talk. This also means her own voice tripping the detector can only cut her short; it can never make her record herself and answer. A phone call's voice is not stopped this way.

Wake word needs a working STT provider — with STT disabled she'll hear her name but can't understand what follows. Detection is suppressed while you're actively using the web UI (recording or mid-chat) and while Conversation mode is running.

### Voice commands

The **voice-commands** plugin (enabled by default) handles instant actions before the LLM ever sees them — the utterance must match exactly (case and surrounding spaces aside):

| Say | Does |
|-----|------|
| "stop", "halt", "be quiet", "shut up" | Stop TTS and cancel generation (a phone caller's "stop" cancels only their call) |
| "reset", "reset chat", "clear chat", "clear history" | Clear the current chat's history |

These are ordinary plugin hooks — other plugins can register their own triggers.

### Conversation mode

The headline voice feature: **continuous listening with real turns and real interruptions**. No wake word per utterance, no button. You talk, she answers, you can talk over her and she stops.

**Prerequisites:**

| Need | Why |
|------|-----|
| An STT provider | She has to understand you |
| A TTS provider, ideally a streaming one (Kokoro or Piper) **with Streaming TTS enabled** | Conversation audio rides the streaming pipeline — with streaming off she speaks only once the whole reply is written and voiced |
| For **Local mic**: wake word enabled and running | Local conversation borrows the wakeword's mic via a fail-safe handoff |
| For **Browser mic**: a mic permission in your browser | The browser is the audio device. **Settings > Audio > Browser Mic** tests it and picks which mic this browser uses (Settings > STT on hosted installs) |

**Starting and stopping:** mic flyout → Conversation → **Local mic** (the server's microphone and speakers) or **Browser mic** (this browser over a WebSocket — works from your phone on the couch). **Off** ends it, as does tapping the green 🎙 mic button. The two sources are mutually exclusive; picking one hands off from the other. The switch is remembered across restarts: Local mic resumes at boot when the wake word is on and the mic is free (otherwise it tries again next boot); Browser mic resumes in the tab that chose it, when that browser still holds mic permission (otherwise a toast says to pick Browser to resume it). The wake word is restored the moment the mode ends. If the mic can't be acquired, the mode simply doesn't engage and wakeword listening stays intact — she is never left deaf.

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
| `STT_PROVIDER` | `none` | Voice-input engine (none / faster_whisper / fireworks_whisper / sapphire_router) | STT tab, provider cards |
| `STT_MODEL_SIZE` | `base.en` | Faster Whisper model; bigger = more accurate, more RAM/VRAM | STT tab |
| `STT_LANGUAGE` | `en` | ISO 639-1 language for transcription | STT tab |
| `STT_FIREWORKS_API_KEY` | *(empty)* | Fireworks key for cloud Whisper | STT tab (Fireworks) |
| `STT_FIREWORKS_MODEL` | `whisper-v3-turbo` | Fireworks model choice | STT tab (Fireworks) |
| `FASTER_WHISPER_DEVICE` | `cuda` | cpu / cuda / auto | STT tab, advanced |
| `FASTER_WHISPER_CUDA_DEVICE` | `0` | GPU index for multi-GPU setups | STT tab, advanced |
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
| `TTS_PROVIDER` | `none` | Voice-output engine (none / kokoro / sapphire_router / plugin providers) | TTS tab, provider cards |
| `KOKORO_DEVICE` | `cuda` | Kokoro synthesis on GPU or CPU | TTS tab (Kokoro) |
| `KOKORO_CUDA_DEVICE` | *(empty)* | GPU index for multi-GPU setups | TTS tab, advanced |
| `TTS_SERVER_HOST` / `TTS_SERVER_PORT` | `127.0.0.1` / `5012` | The local Kokoro server subprocess bind | TTS tab, advanced |
| `TTS_LOCAL_PLAYBACK` | `true` | **Play speech here**: off = speech is still made and hooks fire, nothing plays on this computer or its browser | TTS tab |
| `TTS_PRIMARY_SERVER` / `TTS_FALLBACK_SERVER` | localhost URLs | Where the app reaches the TTS server; the primary may be a pairing string from another computer's shared Kokoro. Fallback is tried per sentence; blank = no fallback | TTS tab, advanced |
| `TTS_FALLBACK_TIMEOUT` | `0.2` | Seconds the primary gets to answer before the fallback is tried | TTS tab, advanced |
| `TTS_SHARE_ENABLED` / `TTS_SHARE_PORT` | `false` / `5013` | Share this Kokoro: a second door, TLS with a pinned self-signed cert and a key per computer (`core/worker_door.py`, keys in `user/worker_door/kokoro/`); add computers in the panel under the Test button, paste each one's pairing string as its primary. Her words are never logged | TTS tab, advanced |
| `TTS_STREAMING_ENABLED` | `true` | Speak sentence-sized chunks as the LLM writes. Off: the reply is voiced whole after it is written (same hooks; an avatar's face gets the GPU after the voice is made) | TTS tab, Streaming |
| `TTS_STREAMING_SPLIT_MODE` | `auto` | `auto` (first sentence early, then as much as the engine keeps ahead), `whole_paragraph` (smoothest, slowest start) or `sentence`; a stored `paragraph` means `auto` | TTS tab, Streaming |
| `TTS_STREAMING_MIN_CHARS` | `15` | Smallest chunk emitted at a boundary | TTS tab, Streaming |
| `TTS_STREAMING_MAX_CHARS` | `750` | Force-split cap | TTS tab, Streaming |
| `TTS_STREAMING_PAUSE_PARAGRAPH_MS` | `80` | Silence after a paragraph chunk | TTS tab, Streaming |
| `TTS_STREAMING_PAUSE_SENTENCE_MS` | `0` | Silence after a sentence chunk (sentence mode) | TTS tab, Streaming |
| `TTS_STREAMING_STAGE_PAUSE_STYLE` | `comma` | How `*stage directions*`/(parens) pause: comma / period / ellipsis / none | TTS tab, Streaming |

ElevenLabs has no page of its own under Plugins: turn its plugin on, pick ElevenLabs in Settings → TTS, and its fields appear on that tab: **API Key**, **Model** (default Turbo v2.5, `eleven_turbo_v2_5`), **Voice ID** (blank = default voice). The old `TTS_ELEVENLABS_*` keys are read only when the plugin setting is blank.

### Wakeword (Settings → Wakeword)

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `WAKE_WORD_ENABLED` | `false` | Continuous wake-word listening on the server mic | Wakeword tab |
| `WAKEWORD_MODEL` | `hey_sapphire` | Which model/phrase to listen for | Wakeword tab, dropdown |
| `WAKEWORD_THRESHOLD` | `0.6` | Detection confidence; lower = more sensitive. Reset to the model's own on a model change | Wakeword tab |
| `WAKEWORD_FRAMEWORK` | `onnx` | Inference backend. Leave it on onnx: `tflite` makes this computer look for library `.tflite` files, which are microWakeWord (ESP32) models it can't load | Wakeword tab, advanced |
| `CHUNK_SIZE` | `1280` | Size of each block read from the wake word's microphone (samples); the detector itself always works on 1280-sample frames | Wakeword tab, advanced |
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
| `AUDIO_SAMPLE_RATES` | `[44100, 48000, 16000, 8000]` | Fallback sample rates tried, in order, when a device won't open at the wanted rate or its default | Audio tab, advanced |
| `AUDIO_BLOCKSIZE_FALLBACKS` | `[1024, 512, 2048, 4096]` | Block sizes tried in order | Audio tab, advanced |
| `AUDIO_PREFERRED_DEVICES_LINUX` | `["pipewire", "pulse", "default"]` | Device names auto picks first on Linux | Audio tab, advanced |
| `AUDIO_PREFERRED_DEVICES_WINDOWS` | `["default", "microsoft sound mapper", "microphone"]` | Device names auto picks first on Windows | Audio tab, advanced |

## Quick Troubleshooting

- **She hears herself and self-interrupts (open speakers)** → conversation echo tier → set `CONVERSATION_DTLN` to 256 or 512 (models must exist in `user/models/dtln/`), or wear headphones; check the log for "using duplex/DTLN … tier" vs. "falling back to headphone tier".
- **Conversation mode replies show on screen long before she speaks** → Streaming TTS is off or the provider can't stream (the reply is voiced whole) → enable **Settings → TTS → Streaming** and use Kokoro or Piper.
- **"Local mic" won't turn on** → wake word off or mic unavailable → enable Wakeword first (local conversation borrows its mic); check Settings → Audio input device and the log's `[CONV]` lines. On failure wakeword is restored automatically.
- **Can't barge in / interrupts don't register** → she may still be "thinking" (barge-in arms only once audio flows) → wait for her voice, then talk over it; if it still won't cut, lower `CONVERSATION_BARGE_HOLD_MS`.
- **Keyboard/cough interrupts her** → raise `CONVERSATION_BARGE_HOLD_MS` (150–250) and `CONVERSATION_MIN_SPEECH_MS`.
- **She cuts you off mid-sentence** → raise `CONVERSATION_ENDPOINT_SILENCE_MS` (900–1200).
- **Wake word misses you** → lower `WAKEWORD_THRESHOLD` (0.4–0.5), check the Audio tab input device with 🎙 Test, and confirm the right model is selected. False triggers → raise the threshold.
- **Wake tone plays but nothing happens** → STT is disabled or still loading → pick a provider in Settings → STT; the mic button tooltip shows "STT enabled but not initialized — loading speech model" while a model loads.
- **Mic button stuck "loading" / provider switch seemed to work but voice is dead** → the provider failed to start (bad key, missing CUDA, model download failure) → its row in the notification center says why, and the previous provider was put back (or voice turned off) → fix the cause, re-select the provider. The app log has `[stt] Failed to create` / `[tts] Failed to create`.
- **TTS test says "The voice engine answered, but couldn't make speech (…)"** → the server answers but can't make audio; the reason is shown and is in `user/logs/kokoro.log`. A voice never used before downloads from Hugging Face on first use (one reach, then cached for good); a box that can't reach it says so in that log at boot.
- **Pitch sounds wrong / audio speeds up or slows down with pitch** → pitch is a resample on every voice (Kokoro and Piper do it before encoding, the others after), so it always changes the length a little → keep it near 1.0.
- **No audio in the browser at all, toast about autoplay** → the browser blocked audio before your first interaction → click anywhere on the page and retry.
- **Voice refused with a "Private chat" message** → the vault gate: the active STT or TTS provider isn't local → switch to a local provider (Faster Whisper / Kokoro / Piper) or type. Note plugin providers must declare themselves local in their manifest, or they're treated as cloud.
- **Silero badge says unavailable** → the VAD model isn't loaded (first-run download or offline) → Classic VAD carries voice detection in the meantime; check the badge again after connectivity.

## See also

- [PHONE-CALLS.md](PHONE-CALLS.md) — telephony: the same conversation engine over a real phone line
- [WAKEWORD-MAKER.md](WAKEWORD-MAKER.md) — train your own wake word for this computer and the satellites
- [DEVICES.md](DEVICES.md) — satellites: boards that hear and speak for her in other rooms
- [plugin-author/voice-routing.md](plugin-author/voice-routing.md) — sending her voice to an avatar, robot or bot
- [PRIVACY.md](PRIVACY.md) — the vault, private chats, and why cloud voice gets refused
- [CHATS.md](CHATS.md) — per-chat settings, including the sidebar voice/pitch/speed
- [PERSONAS.md](PERSONAS.md) — bundling a voice with a prompt/toolset as a persona
- [PLUGINS.md](PLUGINS.md) — enabling the Piper / ElevenLabs / gTTS provider plugins
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general diagnosis beyond voice

## Reference for AI

YOUR TOOL: set_voice(name?, speed?, pitch?) — no arguments = the voice list and what is set now. Voice, pitch and speed are PER CHAT (sidebar "TTS (Voice)"); defaults af_heart / 0.98 / 1.3; pitch 0.5–2.0; loading a persona overwrites them.

STT (Settings > STT, provider cards): faster_whisper local · fireworks_whisper cloud · sapphire_router managed · none. Silero VAD with a Classic percentile fallback (RECORDER_* keys). A hallucination filter runs on every lane. STT_ENABLED / TTS_ENABLED are derived from provider != 'none': set the provider, never those.

TTS (Settings > TTS, provider cards): kokoro local (28 voices: 11 AF, 9 AM, 4 BF, 4 BM) · sapphire_router managed · none · plugins piper (local), elevenlabs and gtts (cloud; ElevenLabs's key, model and voice id show on the TTS tab once it is picked). Only kokoro and piper stream. Pitch is a resample on every provider, so it changes length a little: keep it near 1.0. Test TTS makes a line and reports timings; it plays nothing. Kokoro restarts itself when quiet after heavy use (a leak guard), never mid-sentence.

SWITCHING a provider (STT, TTS, embedding, wake word) is a background job under its own notification row; the setting lands only when the new one answers. A failure reads "Switching … failed. Kept <old>." (or "Still off."), and a failed model download offers Try again, which keeps the pick.

STREAMING: with TTS_STREAMING_ENABLED off, or a voice that can't stream, the reply is made WHOLE after the LLM finishes and then spoken (same events and plugin hooks). Think/reasoning/tool blocks and code fences are never spoken.

PLAY SPEECH HERE (TTS_LOCAL_PLAYBACK, Settings > TTS, default on): off = speech is still made for satellites, phone, Discord and Telegram voice notes, but nothing plays on this computer or in the browser. Plugin side: plugin-author/voice-routing.

WAKEWORD: openWakeWord. Built in: alexa, hey_mycroft, hey_jarvis, hey_rhasspy, timer, weather, hey_sapphire. Custom: user/wakeword/models/<name>.onnx (this computer and a Pi); <name>.tflite + <name>.json are ESP32 satellite models, never the desktop's. Per-listener switch: Settings > Wakeword > Library. Changing WAKEWORD_MODEL swaps live and takes that model's own threshold. Leave WAKEWORD_FRAMEWORK on onnx. Needs STT. Training: WAKEWORD-MAKER.md. A wake turn: tone → record → transcribe → the reply on the page and the server's speakers; the wake word stands down while the web UI is in use and in conversation mode.

VOICE COMMANDS (plugin voice-commands; exact words, end punctuation ignored, no LLM): stop | halt | be quiet | shut up → stops her voice and the reply (a phone caller's stop ends only that call's); reset | reset chat | clear chat | clear history → clears history.

CONVERSATION MODE: mic button flyout → Off | Local mic | Browser mic; tuning on the Settings tab "Conversation". Local needs the wake word on (it borrows that mic); browser does not. Echo: headphones, or CONVERSATION_DTLN 256/512 (models in user/models/dtln/). She can be interrupted once her first words are spoken, not while she thinks. The mode is remembered across restarts (CONVERSATION_LAST_MODE). Phone and Discord calls are sessions of their own (CONVERSATION_EXTERNAL_SLOTS, default 2), one live session per chat.

PRIVATE CHATS: a cloud STT or TTS provider is refused there (a short notice in place of speech), no override. piper is local and allowed; gtts and elevenlabs are refused.

FIXES: voice dead after a switch → its notification row says why · wake tone then nothing → STT is off or still loading · wake word misses → lower WAKEWORD_THRESHOLD toward 0.4–0.5 (false wakes: raise it; default 0.6) · text long before voice → Settings > TTS > Streaming, with Kokoro or Piper · she hears herself → CONVERSATION_DTLN or headphones · she cuts the user off → raise CONVERSATION_ENDPOINT_SILENCE_MS (900–1200) · coughs interrupt her → raise CONVERSATION_BARGE_HOLD_MS · no sound in the browser → click the page once · "Private chat" refusal → a local STT/TTS, or type · more: section='Quick Troubleshooting'.

MANAGED MODE: Audio and Wakeword tabs hidden; sapphire_router does STT and TTS. DEVICES: AUDIO_INPUT_DEVICE / AUDIO_OUTPUT_DEVICE null = auto; Test buttons on the Audio tab.
