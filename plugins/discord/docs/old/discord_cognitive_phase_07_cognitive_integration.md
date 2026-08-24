# Phase 07: Cognitive Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 03 cognitive layer into the live runtime as the single authority for intention generation, without making behavior more aggressive by default.

**Architecture:** Integration over aggression — `CognitiveOrchestrator` unifies message, proactive, and task follow-up paths through `WorldStateBuilder` → `IntentEngine` → policy → execution. Conservative thresholds and affect modulation remain the default.

**Tech Stack:** Python, existing cognition services, pytest.

---

## Scope

Includes:

- cognitive settings (`enabled`, `mode`, feature toggles)
- world state builder from observations + attention + profile/affect
- orchestrator for reactive and proactive intention paths
- affect/relationship threshold modulation in `IntentEngine`
- task follow-up intentions from pending world-model tasks
- proactive policy uses real affect state

Excludes:

- live Discord message → batching → `process_batch()` wiring (still transport gap)
- expressive/unbounded autonomy modes
- new memory or profile capabilities

## File Structure

**Create:**

- `plugins/discord/cognition/world_state_builder.py`
- `plugins/discord/cognition/cognitive_orchestrator.py`
- `plugins/discord/tests/test_cognitive_orchestrator.py`

**Modify:**

- `plugins/discord/models/settings.py` — `CognitiveSettings`
- `plugins/discord/cognition/intent_engine.py` — modulation + task follow-ups
- `plugins/discord/conversation/conversation_service.py` — orchestrator routing
- `plugins/discord/proactive/proactive_coordinator.py` — orchestrator + affect in policy
- `plugins/discord/cognition/policy_service.py` — proactive affect parameter
- `plugins/discord/runtime/container.py` — wiring

## Tasks

### Task 1: Cognitive settings

- [x] Add `CognitiveSettings` with `enabled`, `mode`, `task_follow_up_enabled`, `affect_modulation_enabled`.
- [x] Default to `integrated` mode with conservative thresholds.

### Task 2: World state builder

- [x] Build snapshots from observations, attention scores, profile, and affect.
- [x] Support proactive and task-driven evaluation without a new message.

### Task 3: Cognitive orchestrator

- [x] `evaluate_message_batch()` — message path through `IntentEngine`.
- [x] `evaluate_proactive()` — greeting, outreach, sleep, task follow-ups.
- [x] `complete_task()` — mark tasks done after execution.

### Task 4: Intent engine deepening

- [x] Affect/relationship threshold modulation (`conservative` / `integrated` / `expressive`).
- [x] `generate_task_follow_up()` for pending voice/world-model tasks.

### Task 5: Service integration

- [x] `ConversationService` routes through orchestrator when cognitive enabled.
- [x] `ProactiveCoordinator` uses orchestrator and passes affect to proactive policy.
- [x] Container wires builder + orchestrator.

### Task 6: Tests and docs

- [x] `test_cognitive_orchestrator.py` covers message, proactive, and task paths.
- [x] `OPERATIONS.md` documents cognitive settings.

## Modes

| Mode | Threshold | Notes |
|------|-----------|-------|
| `conservative` | 0.35 | Default-safe; affect raises threshold when irritable/low energy |
| `integrated` | 0.35 | Full orchestrator path (default) |
| `expressive` | 0.25 | Still policy-gated |

## Exit Criteria

- [x] All 64 plugin tests pass.
- [x] Cognitive path is opt-out via `cognitive.enabled`.
- [x] End-to-end live Discord flow uses orchestrator (transport → batching → `process_batch()`).

## Remaining Gaps (post-Phase 07)

- Wire `check_forced_wake` / `drain_wake_buffer` into scheduler or message path
- Call `reply_style_service.mark_tool_sent()` from `discord_tools` on tool sends
- Add `distill_profile`, `describe_image`, `summarize_text` to `SapphireLlmBridge`
