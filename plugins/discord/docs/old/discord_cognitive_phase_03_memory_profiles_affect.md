# Phase 03: World Model, Memory, Profiles, and Affect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the cognitive core: world model state, tasks, attention, goals, intentions, persistent memory, relationship-aware user profiles, mood/affect state, and profile distillation.

**Architecture:** This phase makes the plugin meaningfully cognitive rather than only reactive. World state, attention, and intention generation become first-class services, and memory/profile/affect data become durable inputs to both prompt context and proactive decision-making.

**Tech Stack:** Python, SQLite, Sapphire LLM bridge for distillation, pytest.

---

## Scope

Includes:

- world-model service
- task service
- attention service
- goal engine
- intent engine
- channel memory recall
- pinned memory
- per-user profile rows and facts
- relationship state vectors
- global affect state
- prompt-context integration for memory/profile/affect
- profile distillation pipeline
- `/remember` and `/forget-me` fully backed by persistent profile/memory storage

Excludes:

- proactive cron behaviors
- advanced media understanding
- voice participation

## File Structure

**Create:**

- `plugins/discord/cognition/world_model_service.py`
- `plugins/discord/cognition/attention_service.py`
- `plugins/discord/cognition/goal_engine.py`
- `plugins/discord/cognition/intent_engine.py`
- `plugins/discord/memory/memory_service.py`
- `plugins/discord/memory/profile_service.py`
- `plugins/discord/memory/profile_distill_service.py`
- `plugins/discord/models/profiles.py`
- `plugins/discord/tests/test_world_model_service.py`
- `plugins/discord/tests/test_attention_service.py`
- `plugins/discord/tests/test_intent_engine.py`
- `plugins/discord/tests/test_memory_service.py`
- `plugins/discord/tests/test_profile_service.py`
- `plugins/discord/tests/test_profile_distill_service.py`

**Modify:**

- `plugins/discord/models/world.py`
- `plugins/discord/models/settings.py`
- `plugins/discord/conversation/prompt_context_service.py`
- `plugins/discord/conversation/conversation_service.py`
- `plugins/discord/transport/discord_commands.py`
- storage repositories for memory/profiles/tasks

## Deliverables

- world state is explicitly represented rather than inferred from ad-hoc helpers
- tasks and candidate intentions exist as persistent or reconstructible state
- attention scoring determines what receives expensive reasoning
- memory is persisted and retrievable
- per-user relationship state is stored and updated over time
- global affect state exists and changes over time
- prompts include memory/profile/affect context
- distillation can compact raw interaction history into facts and summaries

## Tasks

### Task 1: Implement world model and task service

**Files:**
- Create: `plugins/discord/cognition/world_model_service.py`
- Modify: `plugins/discord/models/world.py`
- Modify: repositories for channels/users/tasks/observations
- Create: `plugins/discord/tests/test_world_model_service.py`

- [x] Define the authoritative world-model read/write API.
- [x] Represent:
  - users
  - channels
  - guilds
  - active conversations
  - observations
  - tasks
  - execution outcomes
- [x] Ensure text observations from Phase 02 update durable world state rather than only transcript storage.
- [x] Add task creation/update primitives for later proactive and voice phases.

### Task 2: Implement attention, goals, and intentions

**Files:**
- Create: `plugins/discord/cognition/attention_service.py`
- Create: `plugins/discord/cognition/goal_engine.py`
- Create: `plugins/discord/cognition/intent_engine.py`
- Create: `plugins/discord/tests/test_attention_service.py`
- Create: `plugins/discord/tests/test_intent_engine.py`

- [x] Implement activation scoring for users, channels, tasks, conversations, and topics.
- [x] Define built-in goals from the design doc.
- [x] Generate candidate intentions from current world state even when no new message is being replied to immediately.
- [x] Keep first-pass intention generation conservative and explainable.

### Task 3: Extend schema for memory and profile state

**Files:**
- Modify: storage migrations and repositories
- Create/modify: `plugins/discord/models/profiles.py`

- [x] Add tables for:
  - pinned memories
  - profile facts
  - profile summaries
  - profile buffers
  - profile pending work
  - affect snapshots or current affect state
- [x] Ensure one profile per Discord user per bot account.
- [x] Add repository methods for reading/writing profile vectors and facts.

### Task 4: Implement memory service

**Files:**
- Create: `plugins/discord/memory/memory_service.py`
- Create: `plugins/discord/tests/test_memory_service.py`

- [ ] Implement recent + long-term memory retrieval with scored provenance.
- [x] Implement pinned memory storage and retrieval.
- [x] Keep the retrieval interface structured so later semantic upgrades are possible.
- [x] Test recent-only, older recall, and pinned-memory inclusion.

### Task 5: Implement profile service

**Files:**
- Create: `plugins/discord/memory/profile_service.py`
- Create: `plugins/discord/tests/test_profile_service.py`

- [x] Define relationship dimensions:
  - fondness
  - trust
  - patience
  - respect
  - interest
  - familiarity
- [x] Update profile state from observed interactions and outcomes.
- [x] Support explicit fact creation and user forgetting.
- [x] Add service methods that modulate reply likelihood or style inputs.

### Task 6: Implement global affect state

**Files:**
- Modify: `plugins/discord/models/world.py`
- Modify: `plugins/discord/memory/profile_service.py` or dedicated affect storage/service within same phase

- [x] Add `AgentAffect` state with:
  - energy
  - sociability
  - irritability
  - playfulness
  - stress
- [ ] Define update rules driven by:
  - sleep schedule placeholders
  - interaction intensity
  - recent friction
  - elapsed quiet time
- [x] Expose affect state to prompt-context and intention scoring.

### Task 7: Integrate world model, memory, profile, and affect into prompt context

**Files:**
- Modify: `plugins/discord/conversation/prompt_context_service.py`
- Modify: `plugins/discord/conversation/conversation_service.py`

- [ ] Make prompt assembly read from `WorldModelService` instead of direct scattered lookups.
- [x] Inject relevant recalled memory into prompt assembly.
- [x] Inject relationship context at a bounded token cost.
- [x] Inject affect and style steering hints without making prompts unstable.
- [x] Ensure prompt context remains debuggable and traceable.

### Task 8: Back slash commands with real storage

**Files:**
- Modify: `plugins/discord/transport/discord_commands.py`

- [x] Make `/remember` write:
  - pinned memory
  - high-confidence user fact
- [x] Make `/forget-me` remove:
  - profile row
  - profile facts
  - related memory as designed by policy
- [x] Keep behavior compatible with the design doc.

### Task 9: Implement profile distillation

**Files:**
- Create: `plugins/discord/memory/profile_distill_service.py`
- Create: `plugins/discord/tests/test_profile_distill_service.py`

- [x] Buffer profile-relevant interactions.
- [ ] Queue distillation work.
- [ ] Use the Sapphire LLM bridge for extracting facts/summaries/disposition nudges.
- [x] Add confidence-aware merge rules.
- [x] Ensure distillation is optional and can fail gracefully.

### Task 10: Add traces for world-model and affect-driven behavior

**Files:**
- Modify: traces API/UI/services

- [x] Record world-model mutations and intention generation summaries.
- [ ] Record when:
  - a profile dimension changed meaningfully
  - affect altered reply likelihood
  - memory snippets were injected
  - a user was deprioritized due to low patience/high irritability
- [x] Keep traces inspectable without leaking excessive private detail by default.

## Exit Criteria

- world-model state, attention, and intentions exist as explicit services
- the agent remembers people and channels across restarts
- prompt context includes meaningful memory and relationship information
- affect exists as persistent state and influences behavior
- `/remember` and `/forget-me` are fully functional
- profile distillation works in the background with graceful degradation

## Dependencies for Next Phase

Phase 04 assumes:

- structured media-aware prompt extension points
- relationship and affect data available for proactive and social behavior
- memory/profile context stable enough for broader planning
