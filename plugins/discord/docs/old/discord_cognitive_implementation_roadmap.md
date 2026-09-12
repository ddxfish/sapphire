# Discord Cognitive Plugin Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break the new Discord cognitive plugin into buildable phases that together implement the full design.

**Architecture:** The implementation is staged so the plugin becomes usable early while preserving the final world-model architecture. Each phase adds permanent subsystems rather than throwaway MVP code, and later phases extend the same runtime/container/service boundaries.

**Tech Stack:** Python, `discord.py`, SQLite, Sapphire plugin APIs, Sapphire LLM bridges, optional speech/media/model integrations.

---

## Phase Order

1. `discord_cognitive_phase_01_foundation_runtime_storage.md`
   - plugin identity, runtime container, settings, storage, bridges, transport skeleton
2. `discord_cognitive_phase_02_text_conversation_core.md`
   - text event ingestion, batching, Sapphire event bridge, reply delivery, slash commands, tools
3. `discord_cognitive_phase_03_memory_profiles_affect.md`
   - world model, task service, attention, goals, intentions, memory, user profiles, mood/relationship state, prompt context, profile distillation
4. `discord_cognitive_phase_04_proactive_media_presence.md`
   - proactive behaviors, presence, image/GIF/meme pipeline, media-aware intentions
5. `discord_cognitive_phase_05_voice_realtime.md`
   - voice transport, session model, transcription, summarization, spoken participation modes
6. `discord_cognitive_phase_06_hardening_import_observability.md`
   - observability, policy hardening, migration/import tooling, scale and failure recovery

## Global Rules

- Build in `sapphire/plugins/discord/`.
- Do not modify `plugins/leona_discord/`.
- Prefer additive implementation per phase; avoid scaffolding that will be thrown away later.
- Every phase must leave the plugin in a runnable state.
- Tests should be added as the implementation grows, with service-level and integration-level coverage increasing across phases.

## Completion Criteria

The roadmap is complete when:

- all six phase plan documents are implemented
- the new plugin can coexist with `leona_discord`
- the full design in `leona_discord_next_evolution_design.md` is represented in code
- text, media, proactive, affective, and voice behavior all run through the same world-model architecture
