# Technical Reference

System architecture and internals for developers and power users. For API endpoints, see [API.md](API.md).

---

## Architecture Overview

```
main.py (runner with restart loop)
└── sapphire.py (VoiceChatSystem)
    ├── LLMChat (core/chat/)
    │   ├── llm_providers → Claude, OpenAI, Fireworks, LM Studio, Responses
    │   ├── module_loader → plugins/*, core/modules/*
    │   ├── function_manager → functions/*, scopes, story tools
    │   └── session_manager → chat history (SQLite)
    ├── Continuity (core/modules/continuity/)
    │   ├── scheduler → cron-based task runner
    │   └── executor → context isolation, task execution
    ├── TTS Server (core/tts/) → port 5012 (HTTP subprocess)
    ├── STT (core/stt/) → thread in main process (hot-toggleable)
    ├── Wake Word (core/wakeword/) → thread (hot-toggleable)
    ├── FastAPI Server (core/api_fastapi.py) → 0.0.0.0:8073
    └── Event Bus (core/event_bus.py) → SSE pub/sub
```

**Process model:** `main.py` is a runner that spawns `sapphire.py` with automatic restart on crash or restart request (exit code 42). `sapphire.py` spawns the TTS server as a subprocess via `ProcessManager`. STT runs as a thread. The FastAPI/uvicorn server handles all web traffic directly (auth, static files, API, SSE) on a single port. Everything else runs in the main process.

---

## Scopes Architecture

Seven scope types isolate data per-chat via ContextVars in `function_manager.py`:

| Scope | What it isolates | Overlay |
|-------|-----------------|---------|
| `scope_memory` | Memory slot | Yes (sees own + global) |
| `scope_goal` | Goal set | Yes |
| `scope_knowledge` | Knowledge tabs | Yes |
| `scope_people` | Contacts | Yes |
| `scope_email` | Email account | No |
| `scope_bitcoin` | Wallet | No |
| `scope_rag` | Per-chat documents | No (strict) |

**Global overlay:** Memory, goals, knowledge, and people scopes see both their own data AND entries in the "global" scope. RAG is strict — only the chat's own documents.

**Setting scopes:** Per-chat in Chat Settings sidebar → Mind Scopes. Set to "none" to disable a system for that chat.

**ContextVars:** Thread/async-safe isolation. Each chat execution context gets its own scope values via `function_manager.set_*_scope()`.

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
├── personas/
│   ├── personas.json       # Persona definitions
│   └── avatars/            # Persona avatar images
├── toolsets/
│   └── toolsets.json       # Custom toolsets
├── continuity/
│   ├── tasks.json          # Scheduled task definitions
│   └── activity.json       # Task execution log
├── story_presets/           # Custom story presets
├── webui/
│   └── plugins/            # Plugin settings (HA, email, etc.)
├── functions/              # Your custom tools
├── plugins/                # Your private plugins
├── history/
│   └── sapphire_history.db # Chat sessions (SQLite WAL)
├── public/
│   └── avatars/            # User/assistant avatars
├── memory.db               # Long-term memory (SQLite)
├── knowledge.db            # Knowledge + people (SQLite)
├── goals.db                # Goals + progress (SQLite)
├── ssl/                    # Self-signed cert (10yr, persistent)
└── logs/                   # Application logs
```

**Bootstrap:** On first run, `core/setup.py` copies factory defaults from `core/modules/system/` to `user/`.

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

### Settings Categories

| Category | Examples |
|----------|----------|
| identity | `DEFAULT_USERNAME`, `DEFAULT_AI_NAME` |
| network | `SOCKS_ENABLED`, `SOCKS_HOST`, `SOCKS_PORT` |
| privacy | `START_IN_PRIVACY_MODE`, `PRIVACY_NETWORK_WHITELIST` |
| features | `MODULES_ENABLED`, `PLUGINS_ENABLED`, `FUNCTIONS_ENABLED` |
| wakeword | `WAKE_WORD_ENABLED`, `WAKEWORD_MODEL`, `WAKEWORD_THRESHOLD` |
| stt | `STT_ENABLED`, `STT_MODEL_SIZE`, `STT_ENGINE` |
| tts | `TTS_ENABLED`, `TTS_VOICE_NAME`, `TTS_SPEED`, `TTS_PITCH_SHIFT` |
| llm | `LLM_PROVIDERS`, `LLM_FALLBACK_ORDER`, `LLM_MAX_HISTORY` |
| audio | `AUDIO_INPUT_DEVICE`, `AUDIO_OUTPUT_DEVICE` |
| tools | `MAX_TOOL_ITERATIONS`, `MAX_PARALLEL_TOOLS`, `TOOL_MAKER_VALIDATION` |
| rag | `RAG_SIMILARITY_THRESHOLD` |
| backups | `BACKUPS_ENABLED`, `BACKUPS_KEEP_DAILY`, etc. |

### Settings Reload Tiers

| Tier | When Applied | Examples |
|------|-------------|---------|
| **Hot** | Immediate | Names, TTS voice/speed/pitch, LLM settings, SOCKS, privacy mode, generation params |
| **Hot-toggle** | Runtime on/off | Wakeword, STT (no restart needed) |
| **File-watched** | ~2s after save | settings.json, prompts/*.json, toolsets.json |
| **Restart** | Exit code 42 | Port changes, model configs, code changes |

The settings manager tracks which changes need restart via `get_pending_restart_keys()`.

**Tool-registered settings:** Tool modules can declare `SETTINGS` and `SETTINGS_HELP` dicts. These are registered at startup via `register_tool_settings()` and appear in the Settings UI under Custom Tools.

### LLM Configuration

```json
{
  "LLM_PROVIDERS": {
    "lmstudio": { "provider": "openai", "base_url": "http://127.0.0.1:1234/v1", "enabled": true },
    "claude": { "provider": "claude", "model": "claude-sonnet-4-5", "enabled": false },
    "fireworks": { "provider": "fireworks", "base_url": "...", "model": "...", "enabled": false },
    "openai": { "provider": "openai", "base_url": "...", "model": "gpt-4o", "enabled": false },
    "responses": { "provider": "responses", "base_url": "...", "enabled": false },
    "other": { "provider": "openai", "base_url": "...", "enabled": false }
  },
  "LLM_FALLBACK_ORDER": ["lmstudio", "claude", "fireworks", "openai"]
}
```

Providers are tried in fallback order. Each chat can override to use a specific provider.

### Claude-Friendly Settings

**For prompt caching (90% cost savings):**
- Enable caching: Gear icon → App Settings → LLM → Claude → Enable prompt caching
- **Disable Spice** — Changes system prompt every turn, breaks cache
- **Disable Datetime injection** — Same problem, changes every turn
- **Disable State vars in prompt** — Changes on state updates, breaks cache
- "Story in prompt" is fine — Only changes on scene advance

Cache TTL can be 5m (default) or 1h for longer sessions.

---

## Extended Thinking & Reasoning

| Provider | Feature | How It Works |
|----------|---------|--------------|
| **Claude** | Extended Thinking | Structured thinking blocks with budget, `thinking` API param |
| **GPT-5.x** | Reasoning Summaries | Responses API, `reasoning_summary` param |
| **Fireworks** | Reasoning Effort | Qwen-Thinking, Kimi-K2 use `reasoning_effort` param |

**Claude:** Enable in LLM settings → Claude → Extended Thinking. Budget default: 10,000 tokens. Auto-disables for continue mode and tool cycles without thinking. Thinking blocks preserved across tool calls.

**GPT-5.x:** Uses Responses API. Configure `reasoning_effort` (low/medium/high) and `reasoning_summary` (auto/detailed).

**Fireworks:** Models with "thinking" in the name return reasoning in `reasoning_content` field.

**Cross-provider:** Thinking blocks are stripped from history when switching to non-Claude providers.

---

## Authentication & Credentials

### Password / API Key

One bcrypt hash serves as login password, API key (`X-API-Key` header), and session secret.

| OS | Path |
|----|------|
| Linux | `~/.config/sapphire/secret_key` |
| macOS | `~/Library/Application Support/Sapphire/secret_key` |
| Windows | `%APPDATA%\Sapphire\secret_key` |

**Reset password:** Delete the `secret_key` file and restart.

### Credential Manager

API keys, SOCKS credentials, email accounts, and wallet keys stored separately via `core/credentials_manager.py`.

| OS | Path |
|----|------|
| Linux | `~/.config/sapphire/credentials.json` |
| macOS | `~/Library/Application Support/Sapphire/credentials.json` |
| Windows | `%APPDATA%\Sapphire\credentials.json` |

**Not included in backups** for security. Sensitive fields encrypted with machine-identity Fernet key.

**Priority:** Stored credential → Environment variable fallback (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `FIREWORKS_API_KEY`, `SAPPHIRE_SOCKS_USERNAME`, `SAPPHIRE_SOCKS_PASSWORD`)

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

Started by `ProcessManager` if `TTS_ENABLED=true`. Auto-restarts on crash. Server auto-restarts at 3GB memory or 500 requests.

17 voices available (American and British, male and female). Pitch shifting via resampling, speed control via Kokoro parameter.

### STT (Speech-to-Text)

- Server: `core/stt/server.py` (faster-whisper, loaded in main process)
- Recorder: `core/stt/recorder.py` (adaptive VAD, silence detection)
- Guard: `core/stt/utils.py` (shared `can_transcribe()` check)

Runs as thread if `STT_ENABLED=true`. Supports **hot-toggle** at runtime via `VoiceChatSystem.toggle_stt()`. GPU (CUDA) with CPU fallback.

### Wake Word

- Detector: `core/wakeword/wake_detector.py` (OpenWakeWord)
- Recorder: `core/wakeword/audio_recorder.py`
- Null impl: `core/wakeword/wakeword_null.py`

Supports **hot-toggle** at runtime. Auto-suppresses when web UI mic is active. Custom models supported in `user/wakeword/models/` (.onnx, .tflite).

### Audio Device Manager

- Manager: `core/audio/device_manager.py` (singleton)
- Cross-platform device detection, sample rate negotiation, fallback logic
- Shared by STT and wakeword systems

---

## Privacy Mode

Blocks cloud LLM providers to keep conversations local.

- `is_local: True` providers (lmstudio) — always allowed
- `privacy_check_whitelist: True` providers — allowed if `base_url` passes whitelist
- Cloud providers (claude, openai, fireworks) — blocked
- Whitelist supports CIDR ranges (e.g., `192.168.0.0/16`)

Toggle via Settings or `PUT /api/privacy`.

---

## Event Bus & SSE

Real-time UI updates via Server-Sent Events.

- Backend: `core/event_bus.py` — thread-safe pub/sub with sync and async subscribers
- Frontend: `core/event-bus.js` — EventSource client with auto-reconnect
- Boot version tracking: detects server restarts without clearing browser state
- 50-event replay buffer for late subscribers
- 15-second keepalive pings

**Event types:** AI typing, messages, TTS/STT state, chat switches, settings/prompt/toolset changes, continuity tasks, wakeword detection, errors.

---

## File Watchers

| Watcher | Files | Delay |
|---------|-------|-------|
| Settings | `user/settings.json` | ~2s |
| Prompts | `user/prompts/*.json` | ~2s |
| Toolsets | `user/toolsets/toolsets.json` | ~2s |

---

## Chat Sessions

SQLite database `user/history/sapphire_history.db` (WAL mode):

```
Schema: chats(name TEXT PRIMARY KEY, settings JSON, messages JSON, updated_at TEXT)
```

Each session has message history, per-chat settings (prompt, voice, toolset, LLM, spice, scopes), and metadata. Story engine state stored in `state_current` and `state_log` tables in the same database.

---

## Key Source Files

| Path | Purpose |
|------|---------|
| `main.py` | Runner with restart loop |
| `sapphire.py` | VoiceChatSystem entry point |
| `config.py` | Settings proxy |
| `core/api_fastapi.py` | Unified FastAPI server (156 endpoints) |
| `core/auth.py` | Session auth, CSRF, rate limiting |
| `core/ssl_utils.py` | Self-signed certificate generation |
| `core/settings_manager.py` | Settings merge, file watcher, restart tiers |
| `core/credentials_manager.py` | API keys, secrets, Fernet encryption |
| `core/setup.py` | Bootstrap, auth, first-run |
| `core/event_bus.py` | Real-time event pub/sub for SSE |
| `core/chat/chat.py` | LLM orchestration |
| `core/chat/chat_streaming.py` | SSE response streaming |
| `core/chat/llm_providers/` | Claude, OpenAI, Fireworks, Responses providers |
| `core/chat/function_manager.py` | Tool loading, scopes, story tools |
| `core/chat/history.py` | Session management |
| `core/story_engine/engine.py` | Story state, presets, custom tools |
| `core/modules/continuity/scheduler.py` | Cron-based task scheduler |
| `core/audio/device_manager.py` | Audio device handling |
| `functions/knowledge.py` | Knowledge base + people |
| `functions/memory.py` | Long-term memory + embeddings |

---

## Reference for AI

Sapphire architecture for troubleshooting and development.

PROCESSES:
- main.py: Runner with restart loop (exit 42 = restart)
- sapphire.py: Core VoiceChatSystem
- core/api_fastapi.py: Unified FastAPI server (port 8073, HTTPS, 156 endpoints)
- TTS server: Kokoro HTTP subprocess (port 5012, if enabled)
- STT: Faster-whisper thread in main process

PORTS:
- 8073: FastAPI server (HTTPS, all routes)
- 5012: TTS server (if enabled)
- 1234: Default LLM (LM Studio)

SCOPES (7 types, ContextVar-based):
- scope_memory, scope_goal, scope_knowledge, scope_people: global overlay
- scope_email, scope_bitcoin: no overlay
- scope_rag: strict per-chat isolation
- Set per-chat in sidebar Mind Scopes

LLM PROVIDERS:
- lmstudio, claude, fireworks, openai, other, responses
- LLM_FALLBACK_ORDER controls Auto mode
- Per-chat override via session settings
- API keys: ~/.config/sapphire/credentials.json or env vars
- Privacy mode blocks cloud, whitelist-based for configurable endpoints

CREDENTIALS:
- ~/.config/sapphire/secret_key: Password/API key hash
- ~/.config/sapphire/credentials.json: LLM, SOCKS, email, bitcoin, SSH, HA
- Not in user/ directory, not in backups
- Sensitive fields Fernet-encrypted (machine identity key)

HOT RELOAD:
- Settings/prompts/toolsets: ~2s after file change
- Wakeword/STT: hot-toggle on/off at runtime
- TTS: hot-stop/start via ProcessManager
- LLM settings, SOCKS, privacy: immediate
- Ports, models, code: require restart

API: See docs/API.md for all 156 endpoints

DATABASES:
- user/history/sapphire_history.db: chats, state_current, state_log
- user/memory.db: memories, memories_fts, memory_scopes
- user/knowledge.db: people, knowledge_tabs, knowledge_entries, knowledge_fts
- user/goals.db: goals, progress_journal

LOGS:
- user/logs/sapphire.log: Main log
- user/logs/tts.log: TTS server log
