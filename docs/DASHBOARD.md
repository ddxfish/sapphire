# Dashboard & Metrics

Sapphire tracks your local LLM usage — tokens, costs, cache hits, and daily trends. Everything stays on your machine.

## Accessing the Dashboard

Open **Settings** — the Dashboard is the first tab. It shows three cards: System, Updates, and Token Metrics.

## System Card

Shows your current Sapphire version and branch. Has buttons to:

- **Restart** — Restart Sapphire (useful after config changes)
- **Shutdown** — Stop Sapphire entirely

## Updates Card

Sapphire checks GitHub for new versions automatically (every 24 hours, starting 30 seconds after boot).

- Shows your current version vs latest available
- One-click **Update** button runs `git pull --ff-only` with an automatic pre-update backup
- After updating, Sapphire restarts itself

### How Updates Work

1. Reads your local `VERSION` file
2. Checks the same file on GitHub for your current branch
3. Compares versions — if remote is newer, shows the update button
4. On update: creates backup → `git pull --ff-only` → restarts

### Special Cases

| Scenario | What happens |
|----------|-------------|
| **Docker** | Shows `docker compose pull && docker compose up -d` instructions instead |
| **Fork** | Links to upstream releases on GitHub |
| **No .git** | Links to GitHub releases for manual download |

## Token Metrics

Tracks every LLM call over a rolling 30-day window.

### What's Tracked

- **Total calls** — How many times the LLM was called
- **Prompt tokens** — Input tokens sent to the model
- **Completion tokens** — Output tokens generated
- **Thinking tokens** — Extended thinking tokens (Claude)
- **Cache read/write** — Prompt caching hits and misses
- **Call duration** — How long each call took

### Charts

- **Daily usage** — Line chart showing token usage trends over 30 days
- **Model breakdown** — Bar chart of your top 5 models by usage, with cache hit percentages

Token counts use K/M abbreviations for readability (e.g., 1.2M tokens).

### Enabling Metrics

Metrics tracking is a toggle in the Dashboard. When disabled, no usage data is recorded. When enabled, data is stored in a local SQLite database at `user/metrics/token_usage.db`.

All data is local — nothing is sent anywhere.

## Troubleshooting

- **Metrics not showing** — Check the toggle is enabled. Data only appears after LLM calls are made
- **Update button missing** — You might be on Docker, a fork, or missing .git
- **Update failed** — Usually means you have local changes that conflict with upstream. Check git status

## Reference for AI

Dashboard with system info, auto-updater, and token metrics.

DASHBOARD LOCATION:
- Settings → Dashboard tab (first tab)

SYSTEM:
- Shows version + branch
- Restart and Shutdown buttons

UPDATES:
- Auto-checks GitHub every 24 hours
- GET /api/system/update-check - check for updates
- POST /api/system/update - run update (backup + git pull + restart)
- Docker/fork/no-git cases handled with appropriate instructions

METRICS API:
- GET /api/metrics/enabled - check if tracking is on
- PUT /api/metrics/enabled - toggle tracking
- GET /api/metrics/summary?days=30 - overall usage stats
- GET /api/metrics/breakdown?days=30 - per-model breakdown
- GET /api/metrics/daily?days=30 - daily totals for charts

METRICS TRACKED:
- Total LLM calls, prompt/completion/thinking tokens
- Cache read/write tokens, call duration
- Per-model and per-provider breakdown
- Stored in user/metrics/token_usage.db (SQLite, WAL mode)

TROUBLESHOOTING:
- No metrics: check toggle enabled, need LLM calls first
- Update failed: local git changes conflicting with upstream
