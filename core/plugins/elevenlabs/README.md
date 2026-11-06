# ElevenLabs

Cloud text-to-speech with ElevenLabs' voices. Off by default. Needs an API key
and is billed per character by ElevenLabs. Your text leaves this machine, so a
private chat refuses it and asks for a local TTS provider.

## Setup

1. Get a key at elevenlabs.io (Settings > API Keys).
2. Enable the plugin in **Settings > Plugins** and put the key in its settings.
3. Pick **ElevenLabs (Cloud)** in **Settings > TTS**.

## Settings

| Setting | Default | |
|---|---|---|
| `api_key` | empty | Falls back to the stored credential, then `TTS_ELEVENLABS_API_KEY`, then the `ELEVENLABS_API_KEY` environment variable |
| `model` | `eleven_turbo_v2_5` | Turbo v2.5 (fast), Multilingual v2 (quality), Monolingual v1 (legacy) |
| `voice_id` | empty | An ElevenLabs voice ID; empty = their default voice |

Speech comes back as OGG/Opus. Depends on `httpx`.

This plugin is also the reference for writing a TTS provider plugin:
`docs/plugin-author/providers.md`.
