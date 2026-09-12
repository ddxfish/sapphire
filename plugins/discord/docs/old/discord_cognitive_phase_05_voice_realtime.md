# Phase 05: Real-Time Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Discord voice-session awareness, transcription, summarization, and staged spoken participation without breaking the text/world-model architecture.

**Architecture:** Voice is implemented as another perception and execution surface, not a special-case bolt-on to text chat. The first deliverable in this phase should be robust session presence and transcription-backed world updates, then optional spoken participation modes.

**Tech Stack:** Python, Discord voice transport stack, speech-to-text integration, optional text-to-speech integration, SQLite, Sapphire speech bridge.

---

## Scope

Includes:

- join/leave voice channel support
- voice session state model
- participant tracking
- speech-to-text pipeline
- voice-session observations
- transcript storage
- end-of-session or rolling summarization
- staged speaking modes:
  - listen-only
  - transcribe-only
  - summarize-only
  - conversational speaking mode

## File Structure

**Create:**

- `plugins/discord/transport/voice_transport.py`
- `plugins/discord/voice/voice_service.py`
- `plugins/discord/voice/voice_perception_service.py`
- `plugins/discord/voice/voice_execution_service.py`
- `plugins/discord/voice/voice_session_service.py`
- `plugins/discord/voice/voice_turn_taking_service.py`
- `plugins/discord/models/voice.py`
- `plugins/discord/tests/test_voice_session_service.py`
- `plugins/discord/tests/test_voice_perception_service.py`
- `plugins/discord/tests/test_voice_execution_service.py`
- `plugins/discord/tests/test_voice_turn_taking_service.py`

**Modify:**

- task/intention models
- policy service
- event bridge/speech bridge
- storage repositories for sessions/transcripts
- admin UI/API

## Deliverables

- the plugin can join and leave configured voice channels
- voice sessions are represented in world state
- speech can be transcribed into observations
- sessions can be summarized
- spoken participation is possible in controlled modes

## Tasks

### Task 1: Implement voice session model and storage

**Files:**
- Create: `plugins/discord/models/voice.py`
- Modify: repositories and migrations

- [x] Add `VoiceSession` model with:
  - account
  - guild/channel
  - participants
  - start/end timestamps
  - current mode
  - transcript references
- [x] Add storage for transcript segments and summaries.

### Task 2: Implement voice transport

**Files:**
- Create: `plugins/discord/transport/voice_transport.py`

- [ ] Implement connect/disconnect primitives for Discord voice channels.
- [ ] Track session health and reconnect behavior.
- [x] Keep transport free of reasoning logic.

### Task 3: Implement voice session service

**Files:**
- Create: `plugins/discord/voice/voice_session_service.py`
- Create: `plugins/discord/tests/test_voice_session_service.py`

- [x] Manage session lifecycle:
  - create
  - update participants
  - close
  - summarize
- [ ] Synchronize session state into the world model.

### Task 4: Implement voice perception

**Files:**
- Create: `plugins/discord/voice/voice_perception_service.py`
- Create: `plugins/discord/tests/test_voice_perception_service.py`

- [x] Convert audio input into transcript segments via the speech bridge.
- [x] Attribute segments to speakers where possible.
- [x] Persist transcript segments and convert them into observations.
- [x] Mark confidence and uncertainty in transcript records.

### Task 5: Implement voice summarization

**Files:**
- Modify or extend voice perception/session services

- [ ] Support rolling or end-of-session summaries.
- [ ] Feed summaries into memory and task creation where appropriate.
- [x] Allow follow-up intentions after a voice session ends.

### Task 6: Implement voice execution

**Files:**
- Create: `plugins/discord/voice/voice_execution_service.py`
- Create: `plugins/discord/tests/test_voice_execution_service.py`

- [x] Implement text-to-speech output via the speech bridge.
- [x] Support controlled spoken output in:
  - explicit command mode
  - approved intention mode
- [x] Record spoken outputs in traces and outcomes.

### Task 7: Implement turn-taking and conversational voice policy

**Files:**
- Create: `plugins/discord/voice/voice_turn_taking_service.py`
- Create: `plugins/discord/tests/test_voice_turn_taking_service.py`

- [ ] Handle:
  - interruptions
  - overlapping speakers
  - minimum silence before speaking
  - cooldown after speaking
- [x] Prevent the agent from talking over humans constantly.
- [x] Provide a conservative default behavior.

### Task 8: Add operator controls and safety

**Files:**
- Modify: settings models/API/UI and policy service

- [x] Add per-guild and per-channel voice opt-in settings.
- [x] Add listen-only vs speaking-mode toggles.
- [x] Add emergency disable controls.
- [x] Add policy checks for when speaking is allowed.

## Exit Criteria

- the plugin can reliably join voice sessions and track them
- transcript observations reach the world model
- voice sessions can be summarized
- spoken participation exists in a bounded, policy-gated mode
- text and voice share the same world-model architecture

## Dependencies for Next Phase

Phase 06 assumes:

- all major modalities exist
- logs, traces, and settings cover text/media/voice
- full-system hardening can now occur across the whole stack
