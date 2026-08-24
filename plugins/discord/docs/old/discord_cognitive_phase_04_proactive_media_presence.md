# Phase 04: Proactive Behavior, Media, and Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement proactive behavior, presence systems, image/GIF/meme handling, and media-aware intentions on top of the existing conversation, memory, and affect architecture.

**Architecture:** This phase expands the agent from text-reactive into socially proactive and multimodal. Scheduling, presence, and media interpretation must be implemented as first-class services driven by world state and intentions rather than isolated feature scripts.

**Tech Stack:** Python, SQLite, `discord.py`, media fetch/processing stack, Sapphire LLM bridge.

---

## Scope

Includes:

- morning greetings
- quiet outreach
- sleep/goodnight
- forced wake
- wake-buffer replies
- presence cycling and LLM statuses
- media artifact storage
- image understanding
- GIF understanding and sending
- meme understanding and meme sending

Excludes:

- real-time voice participation

## File Structure

**Create:**

- `plugins/discord/proactive/greeting_service.py`
- `plugins/discord/proactive/sleep_service.py`
- `plugins/discord/proactive/outreach_service.py`
- `plugins/discord/conversation/media_service.py`
- `plugins/discord/conversation/meme_service.py`
- `plugins/discord/models/media.py`
- `plugins/discord/tests/test_greeting_service.py`
- `plugins/discord/tests/test_sleep_service.py`
- `plugins/discord/tests/test_outreach_service.py`
- `plugins/discord/tests/test_media_service.py`
- `plugins/discord/tests/test_meme_service.py`

**Modify:**

- scheduler bridge and daemon wiring
- `plugins/discord/transport/discord_presence.py`
- `plugins/discord/conversation/gif_service.py`
- prompt-context and policy services
- repositories for media/presence/tasks

## Deliverables

- proactive features work through intentions and execution
- presence reflects awake/quiet/sleep states
- images/GIFs/memes are first-class media artifacts
- the agent can understand inbound media and optionally send outbound GIFs/memes safely

## Tasks

### Task 1: Implement proactive scheduling framework

**Files:**
- Create: `plugins/discord/proactive/greeting_service.py`
- Create: `plugins/discord/proactive/sleep_service.py`
- Create: `plugins/discord/proactive/outreach_service.py`

- [x] Define intention types for proactive actions.
- [x] Hook them into the scheduler and task service rather than ad-hoc cron-only scripts.
- [x] Keep the scheduler capable of both time-triggered and world-state-triggered activation.

### Task 2: Implement greeting and outreach

**Files:**
- Create: `plugins/discord/tests/test_greeting_service.py`
- Create: `plugins/discord/tests/test_outreach_service.py`

- [x] Implement morning greeting targeting and cooldown coordination.
- [x] Implement quiet-channel outreach with skip rules and active-hour windows.
- [x] Use affect and relationship state to modulate intensity and targeting.
- [x] Ensure proactive actions are traceable and rate-limited.

### Task 3: Implement sleep and forced wake

**Files:**
- Create: `plugins/discord/tests/test_sleep_service.py`

- [x] Implement sleep-state persistence and wake transitions.
- [x] Implement buffered mentions while asleep.
- [ ] Implement forced wake thresholds and temporary wake windows.
- [ ] Implement delayed wake-time reply replay with processing markers.

### Task 4: Implement presence system

**Files:**
- Modify: `plugins/discord/transport/discord_presence.py`
- Modify: presence repositories/settings/models

- [x] Implement awake/quiet/sleep/forced-wake presence selection.
- [ ] Support preset-based activities and optional LLM-generated statuses.
- [x] Tie presence selection to affect and policy rather than standalone timers only.
- [x] Add tests for state-dependent presence choice.

### Task 5: Implement media artifact pipeline

**Files:**
- Create: `plugins/discord/conversation/media_service.py`
- Create: `plugins/discord/models/media.py`
- Create: `plugins/discord/tests/test_media_service.py`

- [x] Detect and store media artifacts for:
  - images
  - GIFs
  - attachments
  - screenshots
  - meme-like artifacts
- [x] Build media perception and interpretation layers.
- [x] Distinguish raw media metadata from interpreted meaning.
- [x] Feed media context into prompt assembly.

### Task 6: Implement image and GIF understanding

**Files:**
- Modify: `plugins/discord/conversation/gif_service.py`
- Modify: media service and prompt context

- [ ] Describe images or package multimodal payloads through the configured model bridge.
- [x] Interpret inbound GIFs as conversational artifacts, not just URLs.
- [x] Preserve current-style outbound GIF capability through explicit and automatic paths.

### Task 7: Implement meme understanding and meme sending

**Files:**
- Create: `plugins/discord/conversation/meme_service.py`
- Create: `plugins/discord/tests/test_meme_service.py`

- [x] Classify meme-like inbound media separately from generic images.
- [x] Infer likely sentiment/joke/reaction role.
- [x] Add `send_meme` intention support.
- [ ] Support local-library or provider-backed meme retrieval behind policy checks.
- [x] Keep meme sending distinct from ordinary GIF reactions.

### Task 8: Strengthen policy for proactive and media actions

**Files:**
- Modify: policy service and traces

- [x] Add proactive frequency limits and duplicate-message avoidance.
- [x] Add media-source safety and meme appropriateness checks.
- [x] Ensure low-fondness/high-irritability states reduce engagement without generating hostile content.

## Exit Criteria

- proactive text and presence behaviors match the design
- media is handled as structured context
- GIFs and memes are supported as distinct behaviors
- all actions are explainable via traces and policy outcomes

## Dependencies for Next Phase

Phase 05 assumes:

- stable media and presence services
- world model can track live session state
- scheduler and execution layers can handle additional real-time modalities
