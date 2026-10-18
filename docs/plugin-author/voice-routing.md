# Sending Her Voice Somewhere Else

An avatar with lipsync, a game character, a robot body, a bot in a voice channel: anything that should speak Sapphire's replies instead of (or as well as) this computer's speakers and the browser. Two pieces, both in core:

1. **Your plugin listens.** The TTS hooks hand you the text and, with streaming TTS on, her audio.
2. **The user flips one switch.** Settings > Voice > **Play speech here** off: speech is still made and every hook still fires, but nothing plays on this computer or in its browser.

No core change, no double voice.

## What the hooks carry

| You want | Hook | Fires | Carries |
|----------|------|-------|---------|
| The reply text, once | `post_llm` | after the LLM, before save | `response` |
| The spoken text, cleaned the way she'd say it | `pre_tts` | before whole-blob speech: the speakers, satellites, the browser's non-streaming playback, Telegram voice notes | `tts_text` |
| Sentence-sized text as it is made | `tts_chunk_text` | streaming TTS, per chunk, before synth | `tts_text` |
| **Her audio**, sentence by sentence | `tts_chunk_audio` | streaming TTS, per chunk, after synth | `metadata['audio']['audio_bytes']`, `['content_type']`, `metadata['chunk_text']` |
| Turn boundaries | `tts_stream_start`, `tts_stream_end` | once per streaming turn | `metadata['stream_id']` |

Audio exists only on the streaming lane (Kokoro or Piper, Settings > Voice > Streaming on). Say so in your plugin's README. If your avatar does its own TTS from text, `post_llm` alone is enough and you never touch the TTS hooks. Full hook reference: [Hooks](hooks.md#streaming-tts-hooks).

## The switch: Play speech here

Key `TTS_LOCAL_PLAYBACK`, Settings > Voice, default on. Your plugin never reads it; users flip it and the hooks keep firing.

Off means every lane that plays on **this computer or in its browser** goes quiet after its hooks fire: web chat (streaming and whole-blob), the Replay button, wake-word turns on the speakers, continuity tasks of every kind (heartbeat, scheduled, daemons, webhooks), chat rooms, local conversation mode, the MCP persona voices.

Not governed, because they were never "here": satellites and other devices, phone calls, Discord voice, Telegram voice notes, the voice preview button, the chime.

## Per turn instead: "only if my side took it"

A plugin that wants local speech as the fallback when the avatar is down decides per turn with `skip_tts`:

- `pre_tts`: `event.skip_tts = True` cancels whole-blob speech. Handlers that ran before yours still saw the text.
- `tts_stream_start`: `skip_tts` cancels the whole streaming turn before any synth. It fires at her first token, so decide from the last webhook result or a health ping; `post_llm` has not fired yet.
- `tts_chunk_audio`: `event.skip_tts = True` after you forwarded the bytes. The chunk is **consumed**: not played here, counted as voiced, no "couldn't be voiced" notice. Nulling `audio_bytes` instead means *dropped* and raises that notice.

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
        event.skip_tts = r.ok          # the avatar has it: not played here
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
