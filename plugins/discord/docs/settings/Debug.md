# Debug

Operator diagnostics for the live Discord daemon. This tab is a custom panel (not a list of boolean settings).

## Cognition preview

Live social judgment from the running daemon (in-memory; clears on daemon restart):

- **Flags** — whether situation / intention competition / relationship policy / situation presence are on
- **Last situations** — per-channel vibe, heat, silence, organic multiplier, topics
- **Recent intentions** — reply / react / silent scores when intention competition is on
- **Recent gates** — organic hit/miss multipliers, relationship modulation, outreach skips

Use **Refresh now** (auto-refreshes every 15s while the tab stays open). Chat in a Default-mode channel without @mentioning her to populate it.

## Decision traces

Readable list of recent world-model / policy / delivery events (why she replied, stayed quiet, skipped outreach, distilled facts, deferred a follow-up, etc.):

- **Filter** by `trace_type` (optional)
- Expand a row for structured `detail` JSON
- Summary line shows counts by type from the last ~100 traces
- Auto-refreshes every 15s; also reflected as `trace_summary` in Operator debug JSON

## LLM Debug

Shows recent Discord → LLM attempts the plugin recorded:

- **Pending / sent / rejected** status for reply emissions
- Which LLM was configured vs resolved
- Prompt/rejection stage hints (sleep, trigger, policy, daemon, intention)
- Timestamps and channel/message identifiers

Use **Refresh** to reload. The list auto-refreshes while the panel stays open.

## Operator debug (collapsible on the main panel)

Also available above the tabbed form: raw JSON of daemon health, admin summary, and trace counts. Useful when the daemon shows offline or schedules seem stuck.

## Related APIs

| Endpoint | Purpose |
|----------|---------|
| `GET debug/cognition` | Situation / intention / gate preview |
| `GET debug/llm` | LLM debug entries |
| `GET health` | Daemon health |
| `GET traces` | Decision traces (`?type=` / `?limit=`) |
| `GET admin/summary` | Operator summary |
| `GET proactive/diagnostics` | Why proactive jobs skipped |
