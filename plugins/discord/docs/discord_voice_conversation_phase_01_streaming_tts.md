# Phase 01: Streaming TTS Playback

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play TTS audio in Discord voice channels incrementally from `tts_chunk` events instead of waiting for a complete synthesized file. Reduces time-to-first-audio and prepares the sink contract for Phase 2.

**Architecture:** A thread-safe PCM queue feeds a custom `discord.AudioSource`. Chunk decoder converts base64 OGG/WAV from core TTS into 48 kHz stereo PCM. `DiscordExecution` manages one streaming session per `(account, channel)` with barge-in via `stop()`.

**Tech Stack:** Python, py-cord `AudioSource`, numpy/soundfile for decode, existing `discord_audio` resample helpers.

**Does not change:** STT path, utterance detection, LLM call pattern, core conversation mode.

---

## Problem

Today `play_voice_audio()` writes the full TTS blob to a temp file and plays via `FFmpegPCMAudio`:

```
LLM done → synthesize entire reply → write temp WAV → FFmpeg → play
```

User hears nothing until synthesis completes. Core TTS already streams chunks; we are not consuming them.

---

## Solution

```
tts_chunk (audio_b64) → decode → resample 48k stereo → queue → AudioSource.read() → VoiceClient
```

Mirror Twilio's `feed_chunk` / `wait` / `stop` contract locally in the plugin so Phase 2 can plug `DiscordConversationSource` in with no playback rewrite.

---

## File structure

**Create:**

| File | Responsibility |
|------|----------------|
| `transport/discord_tts_chunks.py` | Decode `tts_chunk` dict → stereo PCM bytes |
| `transport/discord_streaming_playback.py` | `StreamingVoicePlayback` + `QueuedPCMSource` |
| `voice/voice_streaming_playback_service.py` | Facade: start / feed / finish / stop / wait |
| `tests/test_discord_tts_chunks.py` | Chunk decode unit tests |
| `tests/test_discord_streaming_playback.py` | Queue drain, stop, frame size tests |

**Modify:**

| File | Change |
|------|--------|
| `transport/discord_execution.py` | `start_streaming_playback`, `feed_streaming_chunk`, `finish_streaming_playback`, `stop_streaming_playback` |
| `transport/voice_transport.py` | Delegate streaming methods |
| `voice/voice_execution_service.py` | Optional streaming path when `streaming_playback_enabled` |
| `models/settings.py` | `VoiceSettings.streaming_playback_enabled` |
| `runtime/container.py` | Wire `VoiceStreamingPlaybackService` |

---

## Tasks

### Task 1: TTS chunk decoder

**Files:** Create `transport/discord_tts_chunks.py`

- [x] `decode_tts_chunk(chunk: dict) -> bytes` — base64 decode, soundfile read, mono→stereo, resample to 48 kHz int16 LE
- [x] Return empty bytes on missing/invalid chunk (never raise into driver thread)
- [x] Handle float and int16 PCM from soundfile

**Tests:** `tests/test_discord_tts_chunks.py`

- [x] Round-trip synthetic WAV chunk through decode → correct frame count at 48 kHz
- [x] Invalid base64 returns empty

---

### Task 2: Queued PCM audio source

**Files:** Create `transport/discord_streaming_playback.py`

- [x] `DISCORD_FRAME_BYTES` = 3840 (20 ms @ 48 kHz stereo s16)
- [x] `QueuedPCMSource(discord.AudioSource)` — `read()` pulls from `queue.Queue`, pads partial frames with silence
- [x] `StreamingVoicePlayback`:
  - `feed(pcm_stereo: bytes)` — enqueue
  - `stop()` — set flag, clear queue (barge-in)
  - `finish()` — mark no more chunks incoming
  - `wait(timeout)` — block until queue drained and source idle
  - `is_playing` property
- [x] Thread-safe; safe to call `feed` from driver thread

**Tests:** `tests/test_discord_streaming_playback.py`

- [x] `read()` returns exact frame sizes
- [x] `stop()` clears pending audio
- [x] `wait()` returns after drain

---

### Task 3: Discord execution streaming session

**Files:** Modify `transport/discord_execution.py`

- [x] `_streaming_sessions: dict[(account, channel), StreamingVoicePlayback]`
- [x] `start_streaming_playback(account, channel)` — stop any prior playback, `voice_client.play(QueuedPCMSource(...))`, wait DAVE ready
- [x] `feed_streaming_chunk(account, channel, chunk_dict)` — decode + feed
- [x] `finish_streaming_playback(account, channel)` — signal end of turn
- [x] `stop_streaming_playback(account, channel)` — barge-in
- [x] `wait_streaming_playback(account, channel, timeout)` — drain
- [x] Sync wrappers via existing transport pattern (`run_coroutine_threadsafe`)

---

### Task 4: Voice streaming playback service

**Files:** Create `voice/voice_streaming_playback_service.py`

- [x] Thin facade over `voice_transport` streaming methods
- [x] Implements driver sink shape: `start()`, `feed_chunk()`, `finish()`, `stop()`, `wait()` for Phase 2 reuse
- [ ] `surface` metadata for event bus (`discord`) — Phase 2

---

### Task 5: Wire into voice execution (incremental)

**Files:** Modify `voice/voice_execution_service.py`, `voice_transport.py`, `container.py`

- [ ] When `streaming_playback_enabled` and speech bridge can stream: use `begin_stream` / `chat_stream` for conversational replies (Phase 2)
- [x] **Minimum deliverable:** streaming API exists and is tested; conversational service can adopt in Phase 2
- [x] Wire service in `build_voice()`

---

### Task 6: Settings

**Files:** Modify `models/settings.py`, `web/index.js` (optional toggle)

- [x] `VoiceSettings.streaming_playback_enabled: bool = True`
- [ ] Document in settings help string / web UI toggle

---

## Sink contract (Phase 2 preview)

`StreamingVoicePlayback` must implement:

```python
def start(self): ...       # re-arm per turn
def feed_chunk(self, chunk): ...  # tts_chunk event dict
def finish(self): ...       # no more chunks this turn
def stop(self): ...         # barge-in
def wait(self, timeout=180): ...
def close(self): ...        # session teardown
```

Phase 1 implements this on the playback object. Phase 2's `DiscordConversationSource` delegates sink calls to it.

---

## Exit criteria

- [x] `StreamingVoicePlayback` plays PCM chunks in order with ≤1 frame latency jitter
- [x] `stop()` silences playback within one frame (~20 ms) of call
- [x] `wait()` returns when queue empty after `finish()`
- [x] Unit tests pass without Discord network
- [x] No regression to batch `play_voice_audio()` path

---

## Risks

| Risk | Mitigation |
|------|------------|
| py-cord `read()` timing | Pad with silence; never block `read()` on decode |
| DAVE not ready | Reuse `wait_for_dave_ready` before `play()` |
| Chunk format varies by TTS provider | soundfile auto-detect; log content_type on failure |
