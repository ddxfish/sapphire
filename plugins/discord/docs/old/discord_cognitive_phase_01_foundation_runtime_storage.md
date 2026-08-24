# Phase 01: Foundation, Runtime, and Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the new plugin identity and the permanent runtime, storage, settings, and bridge foundations that every later phase will build on.

**Architecture:** This phase establishes the plugin container, lifecycle manager, typed settings, SQLite layer, repository interfaces, and Sapphire/Discord adapter seams. It should result in a plugin that loads cleanly, can connect to Discord accounts at a basic level, and exposes stable internal boundaries without yet implementing the full conversational agent.

**Tech Stack:** Python, `discord.py`, SQLite, Sapphire plugin manifest/routes/daemon APIs.

---

## Scope

This phase includes:

- new plugin manifest and folder identity
- daemon startup/shutdown lifecycle
- dependency injection container
- typed settings models and settings service
- SQLite bootstrap and migrations
- repository layer skeleton
- Sapphire adapter skeletons
- Discord transport skeleton with multi-account connection support
- basic admin routes for health/settings/accounts
- initial test harness and fixtures

This phase does **not** include:

- full message ingestion pipeline
- batching and reply generation
- memory recall
- profiles and affect
- proactive scheduling
- media understanding
- voice participation

## File Structure

**Create:**

- `plugins/discord/plugin.json`
- `plugins/discord/daemon.py`
- `plugins/discord/runtime/container.py`
- `plugins/discord/runtime/lifecycle.py`
- `plugins/discord/runtime/health.py`
- `plugins/discord/runtime/scheduler_loop.py`
- `plugins/discord/models/settings.py`
- `plugins/discord/models/world.py`
- `plugins/discord/storage/sqlite.py`
- `plugins/discord/storage/migrations.py`
- `plugins/discord/storage/repositories/accounts.py`
- `plugins/discord/storage/repositories/channels.py`
- `plugins/discord/storage/repositories/messages.py`
- `plugins/discord/storage/repositories/memory.py`
- `plugins/discord/storage/repositories/profiles.py`
- `plugins/discord/storage/repositories/tasks.py`
- `plugins/discord/storage/repositories/traces.py`
- `plugins/discord/storage/repositories/presence.py`
- `plugins/discord/storage/repositories/media.py`
- `plugins/discord/storage/repositories/voice_sessions.py`
- `plugins/discord/sapphire/event_bridge.py`
- `plugins/discord/sapphire/llm_bridge.py`
- `plugins/discord/sapphire/scheduler_bridge.py`
- `plugins/discord/sapphire/settings_bridge.py`
- `plugins/discord/sapphire/speech_bridge.py`
- `plugins/discord/transport/discord_transport.py`
- `plugins/discord/api/accounts.py`
- `plugins/discord/api/settings.py`
- `plugins/discord/api/traces.py`
- `plugins/discord/web/index.js`
- `plugins/discord/tests/test_container.py`
- `plugins/discord/tests/test_settings_models.py`
- `plugins/discord/tests/test_storage_bootstrap.py`
- `plugins/discord/tests/test_discord_transport.py`

## Deliverables

- plugin loads from `plugins/discord/`
- daemon starts and stops cleanly
- runtime container constructs all core services
- database initializes with schema version tracking
- account records can be stored and listed
- typed settings load/save works
- Discord transport can connect/disconnect named accounts safely
- health endpoint and minimal settings/accounts API are reachable

## Tasks

### Task 1: Create plugin identity and manifest

**Files:**
- Create: `plugins/discord/plugin.json`

- [x] Define the new plugin name, short name, version placeholder, description, daemon entry, and route registration.
- [x] Add only foundational capabilities in the manifest for this phase: daemon, routes, settings UI placeholder.
- [x] Keep the manifest distinct from `leona_discord` to allow coexistence.
- [ ] Verify Sapphire discovers the plugin without conflicting tool or route names.

### Task 2: Implement lifecycle shell

**Files:**
- Create: `plugins/discord/daemon.py`
- Create: `plugins/discord/runtime/container.py`
- Create: `plugins/discord/runtime/lifecycle.py`
- Create: `plugins/discord/runtime/health.py`
- Create: `plugins/discord/runtime/scheduler_loop.py`

- [x] Implement a `RuntimeContainer` that owns all service construction.
- [x] Implement `start()` and `stop()` in `daemon.py`.
- [x] Make lifecycle startup ordering explicit: settings -> storage -> bridges -> transport -> scheduler.
- [x] Make shutdown ordering explicit and reversible.
- [x] Add health-state tracking for startup complete, degraded, and shutting down states.

### Task 3: Define typed settings and configuration layering

**Files:**
- Create: `plugins/discord/models/settings.py`
- Create: `plugins/discord/api/settings.py`

- [x] Define typed settings models for global, guild, channel, DM, presence, safety, profile, media, and voice config.
- [ ] Implement validation and default ranges in one place.
- [x] Add layering semantics matching the design doc.
- [x] Expose minimal settings GET/POST routes using the new models.
- [x] Write tests for defaults, coercion, and override merge behavior.

### Task 4: Implement storage bootstrap and migrations

**Files:**
- Create: `plugins/discord/storage/sqlite.py`
- Create: `plugins/discord/storage/migrations.py`
- Create: repository files under `plugins/discord/storage/repositories/`

- [x] Create SQLite path resolution and connection management.
- [x] Add schema-version table and forward-only migrations.
- [x] Create initial tables for accounts, guilds, channels, users, observations, messages, tasks, traces, presence state, and metadata.
- [x] Stub later-phase tables where useful, but do not overbuild behavior in this phase.
- [x] Add repository interfaces and minimal CRUD coverage for accounts/settings metadata.

### Task 5: Implement Sapphire bridges

**Files:**
- Create: `plugins/discord/sapphire/event_bridge.py`
- Create: `plugins/discord/sapphire/llm_bridge.py`
- Create: `plugins/discord/sapphire/scheduler_bridge.py`
- Create: `plugins/discord/sapphire/settings_bridge.py`
- Create: `plugins/discord/sapphire/speech_bridge.py`

- [x] Wrap plugin-loader access behind the settings bridge.
- [x] Wrap daemon event emission behind the event bridge.
- [x] Define LLM bridge interface even if it is mostly a stub in this phase.
- [x] Define scheduler and speech capability interfaces.
- [x] Ensure the rest of the code imports these adapters instead of Sapphire internals directly.

### Task 6: Implement Discord transport skeleton

**Files:**
- Create: `plugins/discord/transport/discord_transport.py`
- Create: `plugins/discord/api/accounts.py`

- [x] Build multi-account transport with connect, disconnect, reconnect, and health reporting.
- [x] Persist account metadata through repository/API instead of raw plugin state.
- [x] Keep transport responsible only for connection and raw Discord operations.
- [x] Add tests with mocked Discord clients for lifecycle behavior.

### Task 7: Provide minimal operator surfaces

**Files:**
- Create: `plugins/discord/api/traces.py`
- Create: `plugins/discord/web/index.js`

- [x] Add a minimal UI shell that can show plugin health, accounts, and settings.
- [x] Add a health/status route and a traces placeholder route.
- [x] Make the UI intentionally minimal but structurally aligned with later phases.

### Task 8: Establish test harness

**Files:**
- Create: `plugins/discord/tests/test_container.py`
- Create: `plugins/discord/tests/test_settings_models.py`
- Create: `plugins/discord/tests/test_storage_bootstrap.py`
- Create: `plugins/discord/tests/test_discord_transport.py`

- [x] Add test fixtures for temporary SQLite DBs.
- [x] Mock Discord transport dependencies.
- [x] Verify container construction and teardown.
- [x] Verify migrations are idempotent.

## Exit Criteria

- plugin loads and unloads without leaking tasks or connections
- all foundational tests pass
- settings and accounts can be managed through the new API
- the phase leaves stable interfaces ready for text conversation work

## Dependencies for Next Phase

Phase 02 assumes this phase has completed:

- plugin identity
- runtime container
- storage layer
- settings layer
- Discord transport
- Sapphire bridges
