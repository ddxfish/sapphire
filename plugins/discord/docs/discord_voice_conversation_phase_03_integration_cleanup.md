# Phase 03: Integration, World Model, and Cleanup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fully integrate core conversation events with Discord cognition, remove legacy batch conversational loop, and harden observability.

**Depends on:** [Phase 02](./discord_voice_conversation_phase_02_core_conversation.md)

---

## Scope

| In scope | Out of scope |
|----------|--------------|
| `VOICE_TURN_*` event bridge | New TTS providers |
| Retire `VoiceConversationService` batch path | Discord agent tools for voice |
| World model session sync | Rolling summaries (`rolling_summary_seconds`) |
| Reconnect / health | Realtime trigger rules for Discord |
| Operator docs + diagnostics | Multi-guild slot scaling beyond core cap |

---

## Tasks

### Task 1: Event bridge for core voice turns

**Files:** `sapphire/event_bridge.py` or new `sapphire/voice_event_bridge.py`

Subscribe to core event bus:

| Event | Action |
|-------|--------|
| `VOICE_TURN_START` | Trace `voice_conversation_turn`; record user text if `chat` starts with `discord:` |
| `VOICE_TURN_CHUNK` | Optional: stream preview to trace (debug) |
| `VOICE_TURN_END` | Trace completion; link to voice session |
| `TTS_PLAYING` / `TTS_STOPPED` | Trace when `surface == discord` |

- [ ] Filter by `chat` prefix `discord:`
- [ ] Map `chat_name` → `(guild_id, channel_id)` → active `VoiceSession`
- [ ] Tests with mock event bus

---

### Task 2: World model integration

**Files:** `voice/voice_perception_service.py`, `cognition/world_model_service.py`

- [ ] On `VOICE_TURN_START` (addressed turns): record observation with speaker from dominant user
- [ ] On per-user utterance finalize (side channel): keep existing `voice_transcript` observations
- [ ] Sync `VoiceSession.health` from conversation state: `connecting` → `active` → `conversational` → `ended`
- [ ] Update phase 05 doc checklist: world model session sync

---

### Task 3: Retire legacy conversational path

**Files:** `voice/voice_conversation_service.py`, `voice_listener_service.py`, `voice_turn_taking_service.py`

- [ ] Remove or gate `VoiceConversationService.handle_transcript` conversational branch behind `conversation_core_enabled == False` deprecation flag
- [ ] After validation period, delete batch LLM path entirely
- [ ] `VoiceTurnTakingService`: conversational mode delegates to core; keep for non-conversational speak intentions
- [ ] Remove duplicate barge-in in listener when core active
- [ ] Update tests

---

### Task 4: Chat history hygiene

**Files:** `sapphire/voice_chat.py`, optional admin API

- [ ] Voice chat titles: `"Voice: #channel-name (guild)"` for operator clarity
- [ ] `GET voice/sessions` includes `chat_name` and `conversation_active: bool`
- [ ] Document that voice history lives in dedicated chats (not text channel chat)

---

### Task 5: Session health and reconnect

**Files:** `voice/voice_session_service.py`, `voice/voice_service.py`

- [ ] Increment `reconnect_count` on voice client reconnect
- [ ] On reconnect while conversational: restart `start_external` if dropped
- [ ] Mark session `health=degraded` after N reconnects
- [ ] Trace reconnect events

---

### Task 6: Diagnostics endpoint

**Files:** `api/voice.py`

- [ ] `GET voice/diagnostics` — voice stack info + active external sessions + streaming playback state
- [ ] Include `voice_deps.voice_stack_info()` and conversation slot usage

---

### Task 7: Operator documentation

**Files:** `docs/discord_voice_conversation_operator.md`

- [ ] Prerequisites: TTS streaming on, py-cord/DAVE, `CONVERSATION_EXTERNAL_SLOTS`
- [ ] Addressing modes explained
- [ ] Troubleshooting: silent bot (streaming off), slot cap, DAVE not ready

---

### Task 8: End-to-end tests

**Files:** `tests/test_voice_conversation_e2e.py`

- [ ] Mock `ConversationManager` + sink; simulate turn cycle
- [ ] Addressing filter skips undirected speech
- [ ] Barge-in calls `stop_streaming_playback`
- [ ] Session stop calls `stop_external`

---

## Exit criteria

- [x] Batch path gated behind `conversation_core_enabled=false`
- [x] Voice turns visible in traces (`voice_conversation_turn`) and world model
- [x] Operator can diagnose voice state from `GET voice/diagnostics`
- [x] Session health updates (`conversational` / `connected` / `degraded`)
- [x] Reconnect recovery restarts conversation runner when listener still active
- [ ] Phase 05 doc checklist fully updated (optional)
- [x] Voice tests pass

---

## Deprecation timeline

| Milestone | Action |
|-----------|--------|
| Phase 2 ship | `conversation_core_enabled` default `true`; batch path fallback |
| +2 weeks stable | `conversation_core_enabled` only `true` in UI |
| +4 weeks | Remove batch conversational code |
