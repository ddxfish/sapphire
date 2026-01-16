# Technical Reference

System architecture and internals for developers and power users.

---

## Architecture Overview

```
main.py (runner with restart loop)
└── sapphire.py (VoiceChatSystem)
    ├── LLMChat (core/chat/)
    │   ├── llm_providers → Claude, OpenAI, Fireworks, LM Studio
    │   ├── module_loader → plugins/*, core/modules/*
    │   ├── function_manager → functions/*
    │   └── session_manager → chat history
    ├── TTS Server (core/tts/) → port 5012
    ├── STT Server (core/stt/) → port 5050
    ├── Wake Word (core/wakeword/)
    ├── Internal API (core/api.py) → 127.0.0.1:8071
    └── Event Scheduler (core/event_handler.py)

Web Interface (interfaces/web/web_interface.py)
└── HTTP proxy → 0.0.0.0:8073
    └── Proxies to Internal API
```

**Process model:** `main.py` is a runner that spawns `sapphire.py` with automatic restart on crash or restart request (exit code 42). `sapphire.py` spawns the web interface and TTS server as subprocesses via `ProcessManager`. STT runs as a thread. Everything else runs in the main process.

---

## Dual API Architecture

Sapphire has two API layers:

| Layer | Binds To | Purpose |
|-------|----------|---------|
| Internal API | `127.0.0.1:8071` | Backend logic, no auth |
| Web Interface | `0.0.0.0:8073` | HTTP proxy with sessions, CSRF, rate limiting |

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

### Reload Behavior

| Type | When Applied | Examples |
|------|--------------|----------|
| Hot | Immediate | Names, TTS voice/speed/pitch, generation params |
| Component restart | Next component init | TTS/STT enabled, server URLs |
| Full restart | App restart | Ports, model configs |

---

## Authentication & Credentials

### Password / API Key

One bcrypt hash serves as:
- Login password
- API key (`X-API-Key` header)
- Flask session secret

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
| Web Interface | 8073 | `0.0.0.0` (all interfaces, HTTP) |
| Internal API | 8071 | `127.0.0.1` (localhost only) |
| TTS Server | 5012 | `localhost` |
| STT Server | 5050 | `localhost` |
| LM Studio (default) | 1234 | External |

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

### Audio Device Manager

- Manager: `core/audio/device_manager.py`
- Handles device enumeration, sample rate detection, fallback logic
- Shared by STT and wakeword systems

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
- Settings (prompt, voice, toolset, LLM provider, spice config)
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

Full routes in `core/api.py`, `core/settings_api.py`, and `interfaces/web/web_interface.py`.

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
| `core/api.py` | Internal API routes |
| `core/settings_api.py` | Settings and LLM API routes |
| `core/settings_manager.py` | Settings merge logic |
| `core/credentials_manager.py` | API keys and secrets |
| `core/setup.py` | Bootstrap, auth, first-run |
| `core/chat/chat.py` | LLM interaction |
| `core/chat/llm_providers/` | Provider abstraction (Claude, OpenAI, etc.) |
| `core/chat/module_loader.py` | Plugin loading |
| `core/chat/function_manager.py` | Tool loading |
| `core/chat/history.py` | Session management |
| `core/audio/device_manager.py` | Audio device handling |
| `core/event_handler.py` | Scheduled events |
| `interfaces/web/web_interface.py` | Web proxy, auth |

---

## Reference for AI

Sapphire architecture for troubleshooting and development.

PROCESSES:
- main.py: Runner with restart loop (exit 42 = restart)
- sapphire.py: Core VoiceChatSystem
- web_interface.py: Web UI proxy (port 8073, HTTP)
- core/api.py: Internal API (port 8071, HTTP)
- TTS server: Kokoro (port 5012, if enabled)

PORTS:
- 8073: Web UI (HTTP, user-facing)
- 8071: Internal API (localhost only)
- 5012: TTS server (if enabled)
- 5050: STT server (if enabled)
- 1234: Default LLM (LM Studio)

LLM PROVIDERS:
- LLM_PROVIDERS dict with lmstudio, claude, fireworks, openai, other
- LLM_FALLBACK_ORDER controls Auto mode priority
- Per-chat override via session settings
- API keys in ~/.config/sapphire/credentials.json or env vars

KEY DIRECTORIES:
- core/: Main application code
- core/chat/llm_providers/: LLM abstraction
- functions/: AI-callable tools
- plugins/: Keyword-triggered modules
- interfaces/web/: Web UI
- user/: All user data

CREDENTIALS:
- ~/.config/sapphire/secret_key: Password/API key hash
- ~/.config/sapphire/credentials.json: LLM and SOCKS credentials
- Not in user/ directory, not in backups

HOT RELOAD:
- Settings: ~2s after file change
- Prompts: ~2s after file change
- Toolsets: ~2s after file change
- Code changes: Require restart

LOGS:
- user/logs/sapphire.log: Main log
- user/logs/tts.log: TTS server log
- Check errors: grep -i error user/logs/*.log