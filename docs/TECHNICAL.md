# Technical Reference

System architecture and internals for developers and power users.

---

## Architecture Overview

```
main.py (runner with restart loop)
└── sapphire.py (VoiceChatSystem)
    ├── LLMChat (core/chat/)
    │   ├── llm_providers → Claude, OpenAI, Fireworks, LM Studio, Responses
    │   ├── module_loader → plugins/*, core/modules/*
    │   ├── function_manager → functions/*
    │   └── session_manager → chat history
    ├── Continuity (core/modules/continuity/)
    │   ├── scheduler → cron-based task runner
    │   └── executor → context isolation, task execution
    ├── TTS Server (core/tts/) → port 5012 (HTTP subprocess)
    ├── STT (core/stt/) → thread in main process
    ├── Wake Word (core/wakeword/)
    ├── FastAPI Server (core/api_fastapi.py) → 0.0.0.0:8073
    └── Event Scheduler (core/event_handler.py)
```

**Process model:** `main.py` is a runner that spawns `sapphire.py` with automatic restart on crash or restart request (exit code 42). `sapphire.py` spawns the TTS server as a subprocess via `ProcessManager`. STT runs as a thread. The FastAPI/uvicorn server handles all web traffic directly (auth, static files, API, SSE) on a single port. Everything else runs in the main process.

---

## API Architecture

Sapphire runs a single **FastAPI/uvicorn** server that handles everything:

| Layer | Binds To | Purpose |
|-------|----------|---------|
| FastAPI Server | `0.0.0.0:8073` | All routes, auth, static files, SSE, API |

**Flow:** Browser → FastAPI (8073) → VoiceChatSystem

The server provides:
- Session-based login (bcrypt password)
- CSRF protection on forms
- Rate limiting on auth endpoints
- Security headers (X-Frame-Options, etc.)
- Static file serving and Jinja2 templates
- SSE streaming for real-time events
- All API endpoints in one process

**Direct API access:** Send `X-API-Key` header with the bcrypt hash from `secret_key` file.

---

## User Directory

All user customization lives in `user/` (gitignored). Created on first run.

```
user/
├── settings.json           # Your settings overrides
├── settings/
│   └── chat_defaults.json  # Defaults for new chats
├── prompts/
│   ├── prompt_monoliths.json
│   ├── prompt_pieces.json
│   └── prompt_spices.json
├── toolsets/
│   └── toolsets.json       # Custom toolsets
├── continuity/
│   ├── tasks.json          # Scheduled task definitions
│   └── activity.json       # Task execution log
├── webui/
│   └── plugins/
│       └── homeassistant.json  # HA settings (URL, blacklist)
├── functions/              # Your custom tools
├── plugins/                # Your private plugins
├── history/                # Chat session files
├── public/
│   └── avatars/            # Custom user/assistant avatars
├── memory.db               # SQLite memory storage
└── logs/                   # Application logs
```

**Bootstrap:** On first run, `core/setup.py` copies factory defaults from `core/modules/system/prompts/` to `user/prompts/`.

---

## Configuration System

```
config.py (thin proxy)
    ↓
core/settings_manager.py
    ↓ merges
core/settings_defaults.json  ← Factory defaults (don't edit)
        +
user/settings.json           ← Your overrides
        =
Runtime config
```

**Access pattern:** `import config` then `config.TTS_ENABLED`, `config.LLM_PROVIDERS`, etc.

**File watcher:** Settings reload automatically when `user/settings.json` changes (~2 second delay).

### Settings Categories

| Category | Examples |
|----------|----------|
| identity | `DEFAULT_USERNAME`, `DEFAULT_AI_NAME` |
| network | `SOCKS_ENABLED`, `SOCKS_HOST`, `SOCKS_PORT` |
| features | `MODULES_ENABLED`, `PLUGINS_ENABLED`, `FUNCTIONS_ENABLED` |
| wakeword | `WAKE_WORD_ENABLED`, `WAKEWORD_MODEL`, `WAKEWORD_THRESHOLD` |
| stt | `STT_ENABLED`, `STT_MODEL_SIZE`, `STT_ENGINE` |
| tts | `TTS_ENABLED`, `TTS_VOICE_NAME`, `TTS_SPEED`, `TTS_PITCH_SHIFT` |
| llm | `LLM_PROVIDERS`, `LLM_FALLBACK_ORDER`, `LLM_MAX_HISTORY` |
| audio | `AUDIO_INPUT_DEVICE`, `AUDIO_OUTPUT_DEVICE` |
| tools | `MAX_TOOL_ITERATIONS`, `MAX_PARALLEL_TOOLS` |
| backups | `BACKUPS_ENABLED`, `BACKUPS_KEEP_DAILY`, etc. |
| continuity | Task schedules stored in `user/continuity/tasks.json` |

### LLM Configuration

```json
{
  "LLM_PROVIDERS": {
    "lmstudio": { "provider": "openai", "base_url": "http://127.0.0.1:1234/v1", "enabled": true },
    "claude": { "provider": "claude", "model": "claude-sonnet-4-5", "enabled": false },
    "fireworks": { "provider": "fireworks", "base_url": "...", "model": "...", "enabled": false },
    "openai": { "provider": "openai", "base_url": "...", "model": "gpt-4o", "enabled": false }
  },
  "LLM_FALLBACK_ORDER": ["lmstudio", "claude", "fireworks", "openai"]
}
```

Providers are tried in fallback order. Each chat can override to use a specific provider.

### Claude-Friendly Settings

Claude works well with Sapphire but requires specific settings for optimal cost and performance:

**For prompt caching (90% cost savings):**
- Enable caching: Gear icon → App Settings → LLM → Claude → Enable prompt caching
- **Disable Spice** — Changes system prompt every turn, breaks cache (25% write penalty)
- **Disable Datetime injection** — Same problem, changes every turn
- **Disable State vars in prompt** — Changes on state updates, breaks cache
- "Story in prompt" is fine — Only changes on scene advance

**Other recommendations:**
- Claude supports parallel tool calls natively
- Extended thinking adds extra reasoning tokens, good for complex tasks
- Cache TTL can be 5m (default) or 1h for longer sessions

**What the logs show:**
```
[CACHE] ✓ HIT - 1234 tokens read from cache (90% savings)
[CACHE] ✗ MISS - 1234 tokens written to cache
```

---

## Extended Thinking & Reasoning

Different providers handle "thinking" differently:

| Provider | Feature | How It Works |
|----------|---------|--------------|
| **Claude** | Extended Thinking | Structured thinking blocks with budget, uses `thinking` API param |
| **GPT-5.x** | Reasoning Summaries | Uses Responses API, `reasoning_summary` param |
| **Fireworks** | Reasoning Effort | Models like Qwen-Thinking, Kimi-K2 use `reasoning_effort` param |

### Claude Extended Thinking

Enable in LLM settings → Claude → Extended Thinking. Set a budget (default 10,000 tokens).

The thinking is shown in a collapsible UI block. Thinking blocks are preserved across tool call cycles so Claude maintains context.

**Auto-disable:** Thinking is automatically disabled for:
- Continue mode (can't inject thinking into existing response)
- Active tool cycles that started without thinking

### GPT-5.x Reasoning Summaries

GPT-5 and later models use the Responses API which provides reasoning summaries instead of raw thinking. Configure:
- `reasoning_effort`: low, medium, high
- `reasoning_summary`: auto, detailed, concise

### Fireworks Reasoning Models

Models with "thinking" in the name (Qwen3-Thinking, Kimi-K2-Thinking) return reasoning in `reasoning_content` field when `reasoning_effort` is set.

**Note:** When switching between providers, thinking blocks are stripped from history sent to non-Claude providers to avoid format conflicts.

### Reload Behavior

| Type | When Applied | Examples |
|------|--------------|----------|
| Hot | Immediate | Names, TTS voice/speed/pitch, generation params |
| Hot toggle | Immediate | Wakeword on/off (hot-swaps real/null detector at runtime) |
| Component restart | Next component init | STT enabled (requires restart to load speech model) |
| Full restart | App restart | Ports, model configs |

---

## Authentication & Credentials

### Password / API Key

One bcrypt hash serves as:
- Login password
- API key (`X-API-Key` header)
- Session secret

| OS | Path |
|----|------|
| Linux | `~/.config/sapphire/secret_key` |
| macOS | `~/Library/Application Support/Sapphire/secret_key` |
| Windows | `%APPDATA%\Sapphire\secret_key` |

**Reset password:** Delete the `secret_key` file and restart. Setup wizard will reappear.

### Credential Manager

API keys and SOCKS credentials are stored separately from settings via `core/credentials_manager.py`.

| OS | Path |
|----|------|
| Linux | `~/.config/sapphire/credentials.json` |
| macOS | `~/Library/Application Support/Sapphire/credentials.json` |
| Windows | `%APPDATA%\Sapphire\credentials.json` |

**Not included in backups** for security.

**Priority order:**
1. Stored credential in `credentials.json` (set via Sapphire UI)
2. Environment variable fallback (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `FIREWORKS_API_KEY`, `SAPPHIRE_SOCKS_USERNAME`, `SAPPHIRE_SOCKS_PASSWORD`)

---

## Default Ports

| Service | Port | Binding |
|---------|------|---------|
| FastAPI Server | 8073 | `0.0.0.0` (all interfaces, HTTPS) |
| TTS Server | 5012 | `0.0.0.0` (configurable) |
| LM Studio (default) | 1234 | External |

---

## Component Services

### TTS (Text-to-Speech)

- Server: `core/tts/tts_server.py` (Kokoro, HTTP subprocess)
- Client: `core/tts/tts_client.py`
- Null impl: `core/tts/tts_null.py` (when disabled)

Started by `ProcessManager` if `TTS_ENABLED=true`. Auto-restarts on crash.

### STT (Speech-to-Text)

- Server: `core/stt/server.py` (faster-whisper, loaded in main process)
- Recorder: `core/stt/recorder.py`
- Guard: `core/stt/utils.py` (shared `can_transcribe()` check)

Runs as thread in main process if `STT_ENABLED=true`. Requires restart to load the speech model — the status endpoint reports both `stt_enabled` (setting) and `stt_ready` (model loaded).

### Wake Word

- Detector: `core/wakeword/wake_detector.py` (OpenWakeWord)
- Recorder: `core/wakeword/audio_recorder.py`
- Null impl: `core/wakeword/wakeword_null.py`

Downloads models on first run via `core/setup.py`. Supports **hot-toggle** — can be enabled/disabled at runtime without restart via `VoiceChatSystem.toggle_wakeword()`. Respects STT guard: if wakeword fires but STT is unavailable, shows a toast notification instead.

### Audio Device Manager

- Manager: `core/audio/device_manager.py`
- Handles device enumeration, sample rate detection, fallback logic
- Shared by STT and wakeword systems

---

## Privacy Mode

When enabled, blocks cloud LLM providers to keep conversations local.

**Provider behavior:**
- `is_local: True` providers (lmstudio) — always allowed
- `privacy_check_whitelist: True` providers (other, lmstudio, responses) — allowed if their `base_url` passes the endpoint whitelist (localhost, LAN IPs, .local domains)
- All other cloud providers (claude, openai, fireworks) — blocked

Toggle via Settings or `/api/privacy` endpoint.

---

## File Watchers

These auto-reload on file changes:

| Watcher | Files | Delay |
|---------|-------|-------|
| Settings | `user/settings.json` | ~2s |
| Prompts | `user/prompts/*.json` | ~2s |
| Toolsets | `user/toolsets/toolsets.json` | ~2s |

Started in `sapphire.py`, stopped on shutdown.

---

## Event Bus & SSE

Real-time UI updates use Server-Sent Events (SSE) via the event bus.

**Backend:** `core/event_bus.py` publishes events. Subscribe via `/events` endpoint.

**Frontend:** `interfaces/web/static/core/event-bus.js` manages SSE connection and dispatches to UI components.

**Event types:** `tts_state`, `llm_state`, `chat_switch`, `settings_change`, `prompt_change`, `stt_error`

The `/status` endpoint provides a unified polling fallback with context usage (token count and percent bar), prompt state, spice info, streaming status, and feature readiness (`stt_enabled`, `stt_ready`, `wakeword_enabled`, `wakeword_ready`).

---

## Chat Sessions

Sessions stored in SQLite database `user/history/sapphire_history.db` (WAL mode):

```
Schema: chats(name TEXT PRIMARY KEY, settings JSON, messages JSON, updated_at TEXT)
```

Each session has:
- Message history (user, assistant, tool messages)
- Settings (prompt, voice, toolset, LLM provider, spice config)
- Metadata (updated timestamp)

Managed by `core/chat/history.py` via `SessionManager`.

---

## API Endpoints

Key endpoints (all require session auth or `X-API-Key` header):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/chat` | POST | Send message, get response |
| `/chat/stream` | POST | Streaming response |
| `/status` | GET | Unified UI state (prompt, context, spice, TTS, STT/wakeword readiness) |
| `/events` | GET | SSE stream for real-time events |
| `/history` | GET | Get chat history |
| `/api/settings` | GET/PUT | Read/write settings |
| `/api/privacy` | GET/PUT | Privacy mode toggle |
| `/api/prompts` | GET | List prompts |
| `/api/prompts/<n>` | GET/PUT/DELETE | CRUD prompts |
| `/api/llm/providers` | GET | List LLM providers |
| `/api/llm/providers/<key>` | PUT | Update provider config |
| `/api/llm/test/<key>` | POST | Test provider connection |
| `/api/functions` | GET | List available tools |
| `/api/abilities` | GET | List toolsets |
| `/api/chats` | GET | List chat sessions |
| `/api/chats/<n>` | GET/PUT/DELETE | CRUD sessions |
| `/api/credentials/llm/<provider>` | PUT/DELETE | Manage API keys |
| `/backup/create` | POST | Create backup |
| `/backup/list` | GET | List backups |
| `/api/continuity/tasks` | GET/POST | List or create tasks |
| `/api/continuity/tasks/<id>` | GET/PUT/DELETE | CRUD single task |
| `/api/continuity/tasks/<id>/run` | POST | Manually trigger task |
| `/api/continuity/status` | GET | Scheduler status |
| `/api/continuity/timeline` | GET | Upcoming task schedule |
| `/api/continuity/activity` | GET | Recent task activity log |
| `/api/webui/plugins/homeassistant` | GET/POST | HA settings |
| `/api/webui/plugins/homeassistant/test-connection` | POST | Test HA connection |
| `/api/webui/plugins/homeassistant/token` | GET/PUT | HA token status/save |
| `/api/webui/plugins/homeassistant/entities` | POST | Preview HA entities |

All routes defined in `core/api_fastapi.py`.

---

## Extensions

See dedicated docs:
- **Tools/Functions:** [TOOLS.md](TOOLS.md)
- **Toolsets:** [TOOLSETS.md](TOOLSETS.md)
- **Backend Plugins:** [PLUGINS.md](PLUGINS.md)
- **Web UI Plugins:** [WEB-PLUGINS.md](WEB-PLUGINS.md)
- **Prompts:** [PROMPTS.md](PROMPTS.md)

---

## Key Source Files

| Path | Purpose |
|------|---------|
| `main.py` | Runner with restart loop |
| `sapphire.py` | VoiceChatSystem entry point |
| `config.py` | Settings proxy |
| `core/api_fastapi.py` | Unified FastAPI server (all routes, auth, static files) |
| `core/auth.py` | Session auth, CSRF, rate limiting |
| `core/ssl_utils.py` | Self-signed certificate generation |
| `core/settings_manager.py` | Settings merge logic |
| `core/credentials_manager.py` | API keys and secrets |
| `core/setup.py` | Bootstrap, auth, first-run |
| `core/chat/chat.py` | LLM interaction |
| `core/chat/llm_providers/` | Provider abstraction (Claude, OpenAI, Fireworks, Responses, etc.) |
| `core/chat/module_loader.py` | Plugin loading |
| `core/chat/function_manager.py` | Tool loading |
| `core/chat/history.py` | Session management |
| `core/stt/utils.py` | Shared STT guard logic (`can_transcribe()`) |
| `core/audio/device_manager.py` | Audio device handling |
| `core/event_handler.py` | Scheduled events |
| `core/event_bus.py` | Real-time event pub/sub for SSE |
| `core/modules/continuity/scheduler.py` | Cron-based task scheduler |
| `core/modules/continuity/executor.py` | Task execution with context isolation |
| `functions/homeassistant.py` | Home Assistant tools (12 functions) |

---

## Reference for AI

Sapphire architecture for troubleshooting and development.

PROCESSES:
- main.py: Runner with restart loop (exit 42 = restart)
- sapphire.py: Core VoiceChatSystem
- core/api_fastapi.py: Unified FastAPI server (port 8073, HTTPS)
- TTS server: Kokoro HTTP subprocess (port 5012, if enabled)
- STT: Faster-whisper thread in main process (no port, loaded as library)

PORTS:
- 8073: FastAPI server (HTTPS, user-facing, all routes)
- 5012: TTS server (if enabled)
- 1234: Default LLM (LM Studio)

LLM PROVIDERS:
- LLM_PROVIDERS dict with lmstudio, claude, fireworks, openai, other, responses
- LLM_FALLBACK_ORDER controls Auto mode priority
- Per-chat override via session settings
- API keys in ~/.config/sapphire/credentials.json or env vars
- Privacy mode blocks cloud providers, whitelist-based for configurable-endpoint providers

KEY DIRECTORIES:
- core/: Main application code
- core/chat/llm_providers/: LLM abstraction
- functions/: AI-callable tools
- plugins/: Keyword-triggered modules
- interfaces/web/: Web UI (templates, static assets)
- user/: All user data

CREDENTIALS:
- ~/.config/sapphire/secret_key: Password/API key hash
- ~/.config/sapphire/credentials.json: LLM and SOCKS credentials
- Not in user/ directory, not in backups

HOT RELOAD:
- Settings: ~2s after file change
- Prompts: ~2s after file change
- Toolsets: ~2s after file change
- Wakeword: hot-toggle on/off at runtime (no restart needed)
- STT: setting change is hot, but model load requires restart
- Continuity tasks: immediate on save
- Code changes: Require restart

CONTINUITY:
- Scheduler: core/modules/continuity/scheduler.py
- Executor: core/modules/continuity/executor.py
- Tasks stored: user/continuity/tasks.json
- Activity log: user/continuity/activity.json
- Cron format: minute hour day month weekday
- Background mode: isolated execution, no UI switching

HOME ASSISTANT:
- Tools: functions/homeassistant.py (12 functions)
- Settings: user/webui/plugins/homeassistant.json
- Token: ~/.config/sapphire/credentials.json (key: homeassistant_token)
- Blacklist patterns: exact entity, domain.*, area:Name

LOGS:
- user/logs/sapphire.log: Main log
- user/logs/tts.log: TTS server log
- Check errors: grep -i error user/logs/*.log
