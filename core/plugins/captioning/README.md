# Captioning

Live captions while she speaks: each sentence shows in a banner near the
bottom of the screen as it is spoken, and the banner fades when she is done.
Off by default.

Needs streaming TTS: the captions come from the streaming-TTS hooks, one per
spoken sentence. No tools, no settings, no dependencies.

## For plugin authors

This plugin is the worked example of the streaming-TTS hook surface:

| Hook | Fires |
|---|---|
| `tts_stream_start` | once, as a spoken turn begins |
| `tts_chunk_text` | once per spoken chunk, before it is synthesized (`event.tts_text`) |
| `tts_stream_end` | once when the turn is done or interrupted |

`hooks/captions.py` republishes each one on the event bus (`captioning_start`,
`captioning_chunk`, `captioning_end`); `web/main.js` listens and draws the
banner. Chunk text is published ephemeral, so it never sits in the replay ring
after a vault lock. Hooks: `docs/plugin-author/hooks.md`.
