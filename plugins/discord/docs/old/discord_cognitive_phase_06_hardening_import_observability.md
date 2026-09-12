# Phase 06: Hardening, Import Tooling, and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the complete plugin for long-running use, improve observability, add optional import tooling from `leona_discord`, and verify the system behaves coherently under failure and load.

**Architecture:** This phase does not introduce a new domain capability. It makes all prior phases durable, debuggable, recoverable, and administratively safe to operate. It is where the plugin becomes trustworthy as a full replacement architecture.

**Tech Stack:** Python, SQLite, pytest, integration harnesses, Sapphire admin/runtime surfaces.

---

## Scope

Includes:

- structured observability across text, media, affect, proactive behavior, and voice
- privacy and retention controls
- import tooling from `leona_discord`
- full failure-path hardening
- restart/recovery testing
- operator-facing debugging and inspection surfaces

## File Structure

**Create:**

- `plugins/discord/tools/import_from_leona.py` or equivalent import utility module
- `plugins/discord/tests/test_import_tooling.py`
- `plugins/discord/tests/test_recovery_integration.py`
- `plugins/discord/tests/test_policy_regressions.py`
- `plugins/discord/tests/test_trace_coverage.py`

**Modify:**

- traces APIs/UI
- settings models
- lifecycle manager
- repositories and retention jobs
- admin routes and docs

## Deliverables

- end-to-end traces explain why the agent acted or did not act
- restart and degraded-provider cases recover cleanly
- privacy controls exist for retention and forgetting
- optional import path from `leona_discord` exists
- the plugin is operationally supportable

## Tasks

### Task 1: Expand trace coverage

**Files:**
- Modify: trace service, traces API, UI
- Create: `plugins/discord/tests/test_trace_coverage.py`

- [x] Add structured traces for:
  - intention generation
  - policy rejection
  - memory injection
  - affect/relationship modulation
  - proactive actions
  - media interpretation
  - voice participation decisions
- [x] Ensure traces are useful without requiring raw prompt inspection in every case.

### Task 2: Harden lifecycle and recovery

**Files:**
- Modify: lifecycle manager, transport, scheduler loop
- Create: `plugins/discord/tests/test_recovery_integration.py`

- [ ] Verify clean restart after transport failure.
- [x] Verify scheduler recovery after exceptions.
- [ ] Verify background distillation and voice tasks shut down cleanly.
- [x] Add fault-injection tests for degraded dependencies.

### Task 3: Add retention and privacy controls

**Files:**
- Modify: settings, repositories, admin API/UI

- [x] Add retention settings for:
  - messages
  - traces
  - LLM debug logs
  - transcripts
  - profile buffers
- [x] Add explicit data deletion/admin workflows.
- [x] Ensure `/forget-me` behavior is documented and test-covered.

### Task 4: Build import tooling from `leona_discord`

**Files:**
- Create: import utility and tests
- Create: `plugins/discord/tests/test_import_tooling.py`

- [x] Support optional import of:
  - selected settings
  - pinned memory
  - profile facts
  - profile summaries
  - maybe selected message history
- [x] Do not assume schema parity; map explicitly.
- [x] Make imports idempotent and auditable.

### Task 5: Harden policy and safety regressions

**Files:**
- Create: `plugins/discord/tests/test_policy_regressions.py`

- [x] Add regression cases for:
  - hostile-user handling
  - high irritability / low patience safety
  - meme appropriateness
  - voice speaking permission boundaries
  - no-double-send behavior
- [ ] Ensure "dislike" cannot become abusive behavior.

### Task 6: Improve operator UI and documentation

**Files:**
- Modify: `plugins/discord/web/index.js`
- Update or create docs in plugin folder

- [x] Add operator views for:
  - current affect state
  - relationship summaries
  - active intentions/tasks
  - recent proactive actions
  - voice sessions and summaries
- [x] Document import path and operational safeguards.

### Task 7: Final system verification

**Files:**
- test suites and docs

- [x] Run full test matrix for:
  - text
  - memory/profile/affect
  - proactive features
  - media
  - voice
- [ ] Verify coexistence with `leona_discord`.
- [ ] Verify the plugin can run long-lived without obvious state corruption or repeated-action loops.

## Exit Criteria

- the plugin is fully observable and supportable
- import tooling exists if the user chooses to migrate old data
- safety and recovery behavior are proven by tests
- all planned feature areas are complete and integrated

## Final Outcome

At the end of this phase, the new plugin should be a complete realization of the architecture in:

- `plugins/discord/leona_discord_next_evolution_design.md`
- `plugins/discord/leona_discord_world_model.md`
- `plugins/discord/leona_discord_review.md`
