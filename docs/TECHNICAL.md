# Technical Reference

System architecture and internals for developers.

---

## Architecture Overview

```
main.py
└── VoiceChatSystem
    ├── LLMChat (core/chat/)
    │   ├── module_loader → plugins/*, core/modules/*
    │   ├── function_manager → functions/*
    │   └── session_manager → chat history
    ├── TTS Server (core/tts/) → port 5012
    ├── STT Server (core/stt/) → port 5050
    ├── Wake Word (core/wakeword/)
    ├── Internal API (core/api.py) → 127.0.0.1:8071
    └── Event Scheduler (core/event_handler.py)

Web Interface (interfaces/web/web_interface.py)
└── HTTPS proxy → 0.0.0.0:8073
    └── Proxies to Internal API
```

**Process model:** `main.py` spawns the web interface and TTS server as subprocesses via `ProcessManager`. STT runs as a thread. Everything else runs in the main process.

---

## Dual API Architecture

Sapphire has two API layers:

| Layer | Binds To | Purpose |
|-------|----------|---------|
| Internal API | `127.0.0.1:8071` | Backend logic, no auth UI |
| Web Interface | `0.0.0.0:8073` | HTTPS proxy with sessions, CSRF, rate limiting |

**Flow:** Browser → Web Interface (8073) → Internal API (8071) → VoiceChatSystem

The web interface adds:
- Session-based login (bcrypt password)
- CSRF protection on forms
- Rate limiting on auth endpoints
- Security headers (X-Frame-Options, etc.)
- API key injection for backend calls

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
├── functions/              # Your custom tools
├── plugins/                # Your private plugins
├── history/                # Chat session files
├── public/
│   └── avatars/            # Custom user/assistant avatars
├── memory.db               # SQLite memory storage
└── .socks_config           # SOCKS5 credentials (legacy)
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

**Access pattern:** `import config` then `config.TTS_ENABLED`, `config.LLM_PRIMARY`, etc.

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
| llm | `LLM_PRIMARY`, `LLM_FALLBACK`, `LLM_MAX_HISTORY`, `GENERATION_DEFAULTS` |
| tools | `MAX_TOOL_ITERATIONS`, `MAX_PARALLEL_TOOLS` |
| backups | `BACKUPS_ENABLED`, `BACKUPS_KEEP_DAILY`, etc. |

### Reload Behavior

| Type | When Applied | Examples |
|------|--------------|----------|
| Hot | Immediate | Names, TTS voice/speed/pitch, generation params |
| Component restart | Next component init | TTS/STT enabled, server URLs |
| Full restart | App restart | Ports, model configs |

---

## Authentication

One bcrypt hash serves as:
- Login password
- API key (`X-API-Key` header)
- Flask session secret

### Secret Key Location

| OS | Path |
|----|------|
| Linux | `~/.config/sapphire/secret_key` |
| macOS | `~/Library/Application Support/Sapphire/secret_key` |
| Windows | `%APPDATA%\Sapphire\secret_key` |

**Reset password:** Delete the `secret_key` file and restart. Setup wizard will reappear.

**Permissions:** On Unix, file is chmod 600.

### Other Secrets

| File | Purpose | Env Var Override |
|------|---------|------------------|
| `socks_config` | SOCKS5 proxy creds (line 1: user, line 2: pass) | `SAPPHIRE_SOCKS_USERNAME`, `SAPPHIRE_SOCKS_PASSWORD` |
| `claude_api_key` | Anthropic API key | `ANTHROPIC_API_KEY` |

Legacy location for SOCKS: `user/.socks_config`

---

## Default Ports

| Service | Port | Binding |
|---------|------|---------|
| Web Interface | 8073 | `0.0.0.0` (all interfaces, HTTPS) |
| Internal API | 8071 | `127.0.0.1` (localhost only) |
| TTS Server | 5012 | `localhost` |
| STT Server | 5050 | `localhost` |

---

## Component Services

### TTS (Text-to-Speech)

- Server: `core/tts/tts_server.py` (Kokoro)
- Client: `core/tts/tts_client.py`
- Null impl: `core/tts/tts_null.py` (when disabled)

Started by `ProcessManager` if `TTS_ENABLED=true`. Auto-restarts on crash.

### STT (Speech-to-Text)

- Server: `core/stt/server.py` (faster-whisper)
- Client: `core/stt/client.py`
- Recorder: `core/stt/recorder.py`

Runs as thread in main process if `STT_ENABLED=true`.

### Wake Word

- Detector: `core/wakeword/wake_detector.py` (OpenWakeWord)
- Recorder: `core/wakeword/audio_recorder.py`
- Null impl: `core/wakeword/wakeword_null.py`

Downloads models on first run via `core/setup.py`.

---

## File Watchers

These auto-reload on file changes:

| Watcher | Files | Delay |
|---------|-------|-------|
| Settings | `user/settings.json` | ~2s |
| Prompts | `user/prompts/*.json` | ~2s |
| Toolsets | `user/toolsets/toolsets.json` | ~2s |

Started in `main.py`, stopped on shutdown.

---

## Chat Sessions

Sessions stored as JSON in `user/history/`:

```
user/history/
├── default.json           # Default chat
├── work-project.json      # Named chat
└── ...
```

Each session has:
- Message history (user, assistant, tool messages)
- Settings (prompt, voice, ability, spice config)
- Metadata (created, updated timestamps)

Managed by `core/chat/history.py` via `SessionManager`.

---

## Internal API Endpoints

Key endpoints (all require `X-API-Key` header):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/chat` | POST | Send message, get response |
| `/chat/stream` | POST | Streaming response |
| `/history` | GET | Get chat history |
| `/api/settings` | GET/PUT | Read/write settings |
| `/api/prompts` | GET | List prompts |
| `/api/prompts/<name>` | GET/PUT/DELETE | CRUD prompts |
| `/api/functions` | GET | List available tools |
| `/api/functions/enable` | POST | Set active toolset |
| `/api/abilities/custom` | POST | Save custom toolset |
| `/api/chats` | GET | List chat sessions |
| `/api/chats/<name>` | GET/PUT/DELETE | CRUD sessions |
| `/backup/create` | POST | Create backup |
| `/backup/list` | GET | List backups |

Full routes in `core/api.py` and `interfaces/web/web_interface.py`.

---

## Extensions

See dedicated docs:
- **Tools/Functions/Abilities:** [TOOLS.md](TOOLS.md)
- **Plugins/Modules:** [PLUGINS.md](PLUGINS.md)
- **Prompts:** [PROMPTS.md](PROMPTS.md)

---

## Key Source Files

| Path | Purpose |
|------|---------|
| `main.py` | Entry point, VoiceChatSystem |
| `config.py` | Settings proxy |
| `core/api.py` | Internal API routes |
| `core/settings_manager.py` | Settings merge logic |
| `core/setup.py` | Bootstrap, auth, secrets |
| `core/chat/chat.py` | LLM interaction |
| `core/chat/module_loader.py` | Plugin loading |
| `core/chat/function_manager.py` | Tool loading |
| `core/chat/history.py` | Session management |
| `core/event_handler.py` | Scheduled events |
| `interfaces/web/web_interface.py` | Web proxy, auth |

## Reference for AI

Sapphire architecture overview for troubleshooting and development.

PROCESSES:
- main.py: Entry point, spawns subprocesses
- sapphire.py: Core VoiceChatSystem (LLM, TTS, STT orchestration)
- web_interface.py: Web UI proxy (port 8073, HTTPS)
- core/api.py: Internal API (port 8071, HTTP)
- TTS server: Kokoro (port 5012, if enabled)

PORTS:
- 8073: Web UI (HTTPS, user-facing)
- 8071: Internal API (HTTP, localhost only)
- 5012: TTS server (if enabled)
- 1234: Default LLM server (LM Studio)

KEY DIRECTORIES:
- core/: Main application code
- functions/: AI-callable tools
- plugins/: Keyword-triggered modules
- interfaces/web/: Web UI (Flask + static JS)
- user/: All user data (settings, history, prompts)
- docs/: Documentation

DATA FILES:
- user/settings.json: All settings
- user/history/*.json: Chat sessions
- user/prompts/*.json: Prompt definitions
- user/memory/: Memory storage

HOT RELOAD:
- Settings: ~2s after file change
- Prompts: ~2s after file change
- Toolsets: ~2s after file change
- Code changes: Require restart

LOGS:
- user/logs/sapphire.log: Main log
- user/logs/tts.log: TTS server log
- Check logs for errors: grep -i error user/logs/*.log