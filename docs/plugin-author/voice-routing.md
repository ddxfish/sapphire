# Sending Her Voice Somewhere Else

An avatar with lipsync, a game character, a robot body, a bot in a voice channel: anything that should speak Sapphire's replies instead of (or as well as) this computer's speakers and the browser. Two pieces, both in core:

1. **Your plugin listens.** The TTS hooks hand you the text and, with streaming TTS on, her audio.
2. **The user flips one switch.** Settings > TTS > **Play speech here** off: speech is still made and every hook still fires, but nothing plays on this computer or in its browser.

No core change, no double voice.

## What the hooks carry

| You want | Hook | Fires | Carries |
|----------|------|-------|---------|
| The reply text, once | `post_llm` | after the LLM, before save | `response` |
| The spoken text, cleaned the way she'd say it | `pre_tts` | before whole-blob speech: the speakers, satellites, Telegram voice notes | `tts_text` |
| Sentence-sized text as it is made | `tts_chunk_text` | streaming TTS, per chunk, before synth | `tts_text` |
| **Her audio**, sentence by sentence | `tts_chunk_audio` | streaming TTS, per chunk, after synth | `metadata['audio']['audio_bytes']`, `['content_type']`, `metadata['chunk_text']`, `metadata['plays_here']` |
| Turn boundaries | `tts_stream_start`, `tts_stream_end` | once per streaming turn | `metadata['stream_id']` |
| Stopped | `tts_stop` | the user stopped her voice | `metadata['stream_id']` (or None + `chat_wide`) |

Audio exists only on the streaming lane (Kokoro or Piper, Settings > TTS > Streaming on). Say so in your plugin's README. If your avatar does its own TTS from text, `post_llm` alone is enough and you never touch the TTS hooks. Full hook reference: [Hooks](hooks.md#streaming-tts-hooks).

## The switch: Play speech here

Key `TTS_LOCAL_PLAYBACK`, Settings > TTS, default on. Your plugin never reads it; users flip it and the hooks keep firing.

One case makes no speech at all: the switch is off, the reply would be voiced whole (Stream speech off, or a voice with no streaming such as ElevenLabs), and no plugin has a `tts_chunk_text` or `tts_chunk_audio` handler. Nobody would get that audio, so the engine is never asked and no stream hooks fire. Register a chunk handler and it is made as usual.

Off means every lane that plays on **this computer or in its browser** goes quiet after its hooks fire: web chat (streaming and whole-blob), the Replay button, wake-word turns on the speakers, continuity tasks of every kind (heartbeat, scheduled, daemons, webhooks), chat rooms, local conversation mode, the MCP persona voices.

Not governed, because they were never "here": satellites and other devices, phone calls, Discord voice, Telegram voice notes, the voice preview button, the chime.

## Per turn instead: "only if my side took it"

A plugin that wants local speech as the fallback when the avatar is down decides per turn with `skip_tts`:

- `pre_tts`: `event.skip_tts = True` cancels whole-blob speech. Handlers that ran before yours still saw the text.
- `tts_stream_start`: `skip_tts` cancels the whole streaming turn before any synth. It fires at her first token, so decide from the last webhook result or a health ping; `post_llm` has not fired yet.
- `tts_chunk_audio`: `event.skip_tts = True` after you forwarded the bytes. Take only when `metadata['plays_here']` is true: false means the chunk is already on its way to a satellite or a phone, and taking it silences that device. You may still read the bytes there (an avatar can mouth along). The chunk is **consumed**: not played here, counted as voiced, no "couldn't be voiced" notice. Nulling `audio_bytes` instead means *dropped* and raises that notice.

## Example: an avatar plugin

`plugin.json`:

```json
"capabilities": {
  "hooks": {
    "tts_stream_start": "hooks/avatar.py",
    "tts_chunk_audio": "hooks/avatar.py",
    "tts_stream_end": "hooks/avatar.py",
    "pre_tts": "hooks/avatar.py"
  }
}
```

`hooks/avatar.py`:

```python
import requests

AVATAR = "http://127.0.0.1:9000"


def tts_stream_start(event):
    requests.post(f"{AVATAR}/turn/start", json={"id": event.metadata["stream_id"]}, timeout=1)


def tts_chunk_audio(event):
    audio = event.metadata["audio"]
    try:
        r = requests.post(f"{AVATAR}/say", data=audio["audio_bytes"],
                          params={"text": event.metadata["chunk_text"]},
                          headers={"Content-Type": audio["content_type"]}, timeout=2)
        event.skip_tts = r.ok and event.metadata.get("plays_here", True)   # took it, unless it is a device's voice
    except Exception:
        pass                           # avatar down: plays here as usual


def tts_stream_end(event):
    requests.post(f"{AVATAR}/turn/end", json={"id": event.metadata["stream_id"]}, timeout=1)


def pre_tts(event):                    # whole-blob lane: text only, the avatar voices it itself
    try:
        event.skip_tts = requests.post(f"{AVATAR}/say-text", json={"text": event.tts_text}, timeout=2).ok
    except Exception:
        pass
```

With Play speech here left on, this plugin goes quiet locally only when the avatar accepted the chunk. With it off, the user never hears a double voice, even while the avatar is down. Both behaviors come from the same four handlers.

Want a toggle of your own? Declare it in your `plugin.json` settings ([Settings](settings.md)); it renders in Settings > Plugins. The one core switch is Play speech here.

## Reference for AI

VOICE ROUTING (send her voice to an avatar, robot or bot):
- Text: `post_llm` (`response`, once per reply); `pre_tts` (`tts_text`, whole-blob lanes: speakers, satellites, Telegram voice notes).
- Every chat reply (streamed, or held and voiced whole when TTS_STREAMING_ENABLED is off or the provider can't stream): `tts_stream_start` (metadata stream_id, voice, speed, plays_here) -> `tts_chunk_text` (tts_text, before synth) -> `tts_chunk_audio` (metadata audio.audio_bytes, audio.content_type, chunk_text, chunk_index, plays_here, stream_id) -> `tts_stream_end` (chunk_count, total_chars, interrupted). `tts_stream_end` = the reply is MADE, not heard: a player of your own (a face that plays her voice) also listens to `tts_stop` to flush, since a reply made whole ends before its first word plays.
- skip_tts: pre_tts = cancel whole-blob speech; tts_stream_start = cancel the turn before synth; tts_chunk_audio = chunk CONSUMED (not played here, counted voiced, no drop notice). Nulling audio_bytes = dropped (notice). Take a chunk only when plays_here is true (false = going to a satellite or phone).
- User switch: TTS_LOCAL_PLAYBACK, Settings > TTS > "Play speech here", default on, hot. Off = hooks fire, nothing plays here. Not governed: satellites, phone, Discord, Telegram voice notes, voice preview, chime. Plugins never read it.
- Engine: core/tts/stream_pump.py (plays_here), core/tts/tts_client.py (render; QUIET_REASONS -> 204).
