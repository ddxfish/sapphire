# Phase 02: Text Conversation Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full text-message reactive core: Discord event ingestion, observation creation, batching, Sapphire event routing, reply delivery, slash commands, and Discord tools.

**Architecture:** This phase turns the plugin from a foundation shell into a working text-first conversational system, while keeping the new separation between transport, perception, conversation logic, and execution. It should reproduce the strongest text behaviors of `leona_discord` without inheriting its global-state-heavy implementation.

**Tech Stack:** Python, `discord.py`, SQLite, Sapphire event bridge, Sapphire LLM/task execution, pytest.

---

## Scope

Includes:

- normalized message observations
- text-channel and DM ingestion
- per-channel batching
- text prompt-context assembly (recent history only in this phase)
- Sapphire `discord_message` event emission
- reply delivery with chunking and quote reply behavior
- slash commands
- Discord tools for read/send/upload/react/GIF
- basic traces and no-double-send protections

Excludes:

- deep memory recall
- profile context
- mood/relationship modulation
- proactive scheduling
- image understanding beyond raw media capture hooks
- voice

## File Structure

**Create:**

- `plugins/discord/models/observations.py`
- `plugins/discord/models/intentions.py`
- `plugins/discord/transport/discord_event_adapter.py`
- `plugins/discord/transport/discord_commands.py`
- `plugins/discord/conversation/batching_service.py`
- `plugins/discord/conversation/conversation_service.py`
- `plugins/discord/conversation/prompt_context_service.py`
- `plugins/discord/conversation/reply_style_service.py`
- `plugins/discord/conversation/reaction_service.py`
- `plugins/discord/conversation/gif_service.py`
- `plugins/discord/cognition/policy_service.py`
- `plugins/discord/cognition/observation_interpreter.py`
- `plugins/discord/api/profiles.py` (placeholder endpoint structure if needed)
- `plugins/discord/tests/test_event_adapter.py`
- `plugins/discord/tests/test_batching_service.py`
- `plugins/discord/tests/test_conversation_service.py`
- `plugins/discord/tests/test_reply_style_service.py`
- `plugins/discord/tests/test_discord_commands.py`
- `plugins/discord/tests/test_discord_tools.py`

**Modify:**

- `plugins/discord/transport/discord_transport.py`
- `plugins/discord/sapphire/event_bridge.py`
- `plugins/discord/web/index.js`

## Deliverables

- bot receives Discord messages
- observations are persisted
- messages are batched by channel
- reply intentions emit Sapphire events
- returned LLM output is delivered to Discord
- slash commands function end-to-end
- tool-driven sends do not double-post

## Tasks

### Task 1: Define observation and intention models

**Files:**
- Create: `plugins/discord/models/observations.py`
- Create: `plugins/discord/models/intentions.py`

- [x] Define typed observation models for text messages, typing activity, slash command invocations, and reaction-triggering message context.
- [x] Define initial intention models for `reply_message`, `summarize_channel`, `record_user_fact`, and `add_reaction`.
- [x] Keep models small and serializable because they bridge storage, cognition, and execution.

### Task 2: Implement Discord event adaptation

**Files:**
- Create: `plugins/discord/transport/discord_event_adapter.py`
- Modify: `plugins/discord/transport/discord_transport.py`

- [x] Register `on_message` and `on_typing` handlers through the transport.
- [x] Convert raw Discord objects into internal observations.
- [x] Persist message observations and typing observations with proper account/guild/channel/user identifiers.
- [x] Ignore self-authored messages cleanly.
- [x] Add tests for DM vs guild behavior, self filtering, and typing-event translation.

### Task 3: Implement batching service

**Files:**
- Create: `plugins/discord/conversation/batching_service.py`
- Create: `plugins/discord/tests/test_batching_service.py`

- [x] Implement per-channel batch windows.
- [x] Extend batch windows on typing signals where appropriate.
- [x] Support urgency-aware shortening for hard triggers.
- [ ] Persist enough batch metadata to recover or explain behavior.
- [x] Test single-message, multi-message, and staggered-typing scenarios.

### Task 4: Implement policy pre-checks for text flow

**Files:**
- Create: `plugins/discord/cognition/policy_service.py`

- [ ] Add policy checks for reply mode, allowlist/denylist, ignore bots, rate limits, cooldowns, and basic channel permissions.
- [x] Keep these checks independent from reply generation and transport.
- [x] Return structured outcomes suitable for trace logging.

### Task 5: Implement prompt context assembly (recent-only)

**Files:**
- Create: `plugins/discord/conversation/prompt_context_service.py`
- Create: `plugins/discord/cognition/observation_interpreter.py`

- [x] Build a first prompt-context layer using:
  - recent transcript
  - trigger metadata
  - channel/guild context
  - bot identity hints
  - basic media references if present
- [ ] Do not yet include deep memory or profile context; reserve extension points for Phase 03.

### Task 6: Implement conversation orchestration

**Files:**
- Create: `plugins/discord/conversation/conversation_service.py`
- Modify: `plugins/discord/sapphire/event_bridge.py`
- Create: `plugins/discord/tests/test_conversation_service.py`

- [x] Convert approved batched observations into reply intentions.
- [x] Emit Sapphire events through the event bridge using a stable payload schema.
- [x] Track pending-event metadata so delivery can correlate returned outputs.
- [x] Ensure non-accepted events fail cleanly without transport side effects.

### Task 7: Implement reply delivery

**Files:**
- Create: `plugins/discord/conversation/reply_style_service.py`
- Create: `plugins/discord/conversation/reaction_service.py`
- Create: `plugins/discord/conversation/gif_service.py`
- Create: `plugins/discord/tests/test_reply_style_service.py`

- [ ] Parse returned LLM output for:
  - chunking boundaries
  - quote reply opportunities
  - inline tags for reactions/GIF/edit hooks
- [x] Send messages with Discord-size-safe chunking.
- [ ] Add typing-hint support and realistic pauses where the architecture allows.
- [x] Implement no-double-send markers for tool-based sends vs auto replies.

### Task 8: Implement slash commands

**Files:**
- Create: `plugins/discord/transport/discord_commands.py`
- Create: `plugins/discord/tests/test_discord_commands.py`

- [x] Implement `/ask`, `/summarize`, `/remember`, `/forget-me`.
- [ ] Route `/ask` and `/summarize` through the same intention/event path as ordinary messages.
- [x] Keep `/remember` and `/forget-me` behavior compatible with later profile storage.

### Task 9: Implement Discord tools

**Files:**
- Modify or create tool registration within transport/execution boundaries
- Create: `plugins/discord/tests/test_discord_tools.py`

- [x] Implement:
  - server listing
  - message reading
  - message sending
  - file upload
  - GIF send
  - reaction add
- [ ] Ensure triggering-channel auto-reply protections exist from day one.
- [x] Route tool sends through execution primitives rather than ad-hoc transport calls.

### Task 10: Surface traces and conversation status

**Files:**
- Modify: `plugins/discord/api/traces.py`
- Modify: `plugins/discord/web/index.js`

- [ ] Expose enough traces to debug:
  - dropped vs queued messages
  - batch formation
  - event emission
  - delivery path choice
- [ ] Add minimal UI visibility for text pipeline state.

## Exit Criteria

- text chat works end-to-end for guild channels and DMs
- slash commands function
- tool sends coexist safely with auto replies
- traces explain major decision points
- the plugin is useful as a text-first bot even before memory/profiles/proactive features land

## Dependencies for Next Phase

Phase 03 assumes:

- stable observation and intention models
- working text ingestion and delivery
- stable prompt-context extension point
- persisted transcripts and traces
