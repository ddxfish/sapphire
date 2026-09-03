# Dashboard & Metrics

Sapphire tracks your local LLM usage — tokens, cache hits, and daily trends. Everything stays on your machine.

## Accessing the Dashboard

Open **Settings** — the Dashboard is the first tab. It's a widget-based command center: a hero header with Sapphire's status orb, a customizable row of **widget panels**, and below that the Token Metrics and Plugin Spotlight cards.

## The Hero

The top of the dashboard shows at a glance:

- **Status orb** — a mood ring (Online / Working / Issues / Error / Idle) derived from component health, update availability, and disk usage
- **Display name** — click Sapphire's name to rename her; the name persists with the install
- **Version and branch** — shown under the name
- **Store / Help** quick links
- **Component pills** — emb / tts / stt / ww (embeddings, text-to-speech, speech-to-text, wakeword) with health dots; idle just means a subsystem is configured off, not broken

## Widgets

The panel row under the hero is yours to arrange. Each widget is a small card with a title, a few info lines, and an **Actions** dropdown.

**Built-in widgets:**

| Widget | What it does |
|--------|--------------|
| **System** | Disk usage, memory, restart and shutdown |
| **Updates** | Sapphire version status and plugin updates |
| **Backups** | Backup count, size, schedule, quick actions |
| **Maintenance** | Uptime, app status, cleanup tools |
| **Plugin Spotlight** | Rotating featured plugins from the store |

Plugins can ship their own widgets too — they appear in the picker alongside the built-ins (the `sample-widgets` plugin in the repo is a commented reference for writing one).

**Customizing the row:**

- **+ Add** opens the widget picker, grouped by source (built-ins first). Each row shows the widget's supported sizes; some widgets allow multiple instances ("Add another"). **Restore defaults** brings back the standard built-in set without disturbing what you've added.
- **✎ Edit** enters edit mode: drag the dot handle to reorder, click a size pill to resize (only sizes the widget supports are offered), and × removes a widget. Click **✓ Done** to finish.
- Widgets that declare settings get a **⚙ Settings...** entry in their Actions dropdown — it opens an auto-built form (a pinned-note widget's text and color, for example).

Your layout is saved per-install in `user/webui/dashboard.json`. If a plugin is uninstalled while its widget is still on your dashboard, the panel stays as a labeled placeholder until you remove it or reinstall the plugin.

## Updates Widget

Sapphire checks GitHub for new versions automatically (every 24 hours, starting 30 seconds after boot).

- Shows your current version vs latest available
- One-click **Update** button: preflight checks + automatic pre-update backup, then a *deferred* `git pull` + `pip install -r requirements.txt` applied by the runner on restart
- After updating, Sapphire restarts itself

### How Updates Work

1. Reads your local `VERSION` file
2. Checks the same file on GitHub for your current branch
3. Compares versions — if remote is newer, shows the update button
4. On update: preflight + backup → writes a pending-update marker and restarts → the runner (`main.py`) applies `git pull` + `pip install` *before* relaunching Sapphire

### Special Cases

| Scenario | What happens |
|----------|-------------|
| **Docker** | Shows `docker compose pull && docker compose up -d` instructions instead |
| **Fork** | Links to upstream releases on GitHub |
| **No .git** | Links to GitHub releases for manual download |

## Token Metrics

The Token Metrics card sits below the widget row, with a **Track** toggle in its header. It tracks every LLM call. Usage is retained for 90 days (the dashboard's default view shows the last 30).

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

## Plugin Spotlight

Next to Token Metrics, the Plugin Spotlight card shows featured community plugins from the plugin store — with installed/update-available badges. Click a tile to open that plugin in the Store. The card hides itself when the store is unreachable. (There's also a smaller Plugin Spotlight *widget* for the panel row if you want it up top.)

If any installed plugin is missing Python dependencies, a **Missing Dependencies** card appears above the content row with a Fix button that takes you to the Plugins tab.

## Troubleshooting

- **Metrics not showing** — Check the toggle is enabled. Data only appears after LLM calls are made
- **Update button missing** — You might be on Docker, a fork, missing .git, or on a branch other than `main`
- **Update failed** — Usually means you have local changes that conflict with upstream. Check git status

## Reference for AI

Widget-based dashboard: hero (status orb + component pills) + customizable widget panels + token metrics.

DASHBOARD LOCATION:
- Settings → Dashboard tab (first tab)

LAYOUT:
- Hero: status orb (mood from component health/updates/disk), editable display name, version + branch, Store/Help links, emb/tts/stt/ww pills
- Widget panels: built-ins System / Updates / Backups / Maintenance / Plugin Spotlight, plus plugin-shipped widgets (sample-widgets = reference plugin)
- "+ Add" = widget picker (grouped, sizes, multi-instance, Restore defaults); "✎ Edit" = drag reorder / resize / remove
- Widgets with settings_schema get an auto ⚙ Settings... form in their Actions dropdown
- Panel list persists in user/webui/dashboard.json; uninstalled plugin widgets render as placeholders
- Below the panels: Token Metrics card + Plugin Spotlight card (store-featured; hides when store unreachable) + conditional Missing Dependencies card

WIDGET API:
- GET /api/dashboard/widgets - user's panel list (auto-seeds defaults)
- PUT /api/dashboard/widgets - save panel list
- GET /api/dashboard/widgets/available - widget catalog
- GET /api/dashboard/system-info, /api/dashboard/component-status - hero data

UPDATES:
- Auto-checks GitHub every 24 hours
- GET /api/system/update-check - check for updates
- POST /api/system/update - run update (preflight + backup + deferred git pull/pip on restart)
- Docker/fork/no-git cases handled with appropriate instructions; auto-update only runs on the `main` branch (any other branch, e.g. `dev`, is blocked — pull manually)

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
