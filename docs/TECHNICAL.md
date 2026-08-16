# Technical Reference

System architecture and internals for developers and power users. For API endpoints, see [API.md](API.md).

---

## Architecture Overview

```
main.py (runner with restart loop)
└── sapphire.py (VoiceChatSystem)
    ├── LLMChat (core/chat/)
    │   ├── llm_providers → Claude, OpenAI, Gemini (core) + custom + plugin-provided
    │   ├── plugin_loader → plugins/*, user/plugins/*
    │   ├── function_manager → plugin tools, functions/*, scopes
    │   └── session_manager → chat history (SQLite)
    ├── Continuity (core/continuity/)
    │   ├── scheduler → cron-based task runner
    │   └── executor → context isolation, task execution
    ├── TTS (core/tts/) → provider-based: Kokoro (core) + plugins
    ├── STT (core/stt/) → provider-based: faster-whisper, fireworks-whisper (core) + plugins
    ├── Wake Word (core/wakeword/) → thread (hot-toggleable)
    ├── Provider Registry (core/provider_registry.py) → TTS, STT, Embedding, LLM
    ├── Agents (core/agents/) → agent spawning, registry, lifecycle
    ├── FastAPI Server (core/api_fastapi.py + core/routes/) → 0.0.0.0:8073
    └── Event Bus (core/event_bus.py) → SSE pub/sub
```

**Process model:** `main.py` is a runner that spawns `sapphire.py` with automatic restart on crash or restart request (exit code 42). `sapphire.py` spawns the TTS server as a subprocess via `ProcessManager`. STT runs as a thread. The FastAPI/uvicorn server handles all web traffic directly (auth, static files, API, SSE) on a single port. Everything else runs in the main process.

---

## Scopes Architecture

Scopes isolate data per-chat via ContextVars. Only `rag` and `private` are hardcoded in `function_manager.py`; every other scope is **plugin-manifest-driven** — a plugin declares scopes in `capabilities.scopes` and `register_plugin_scope()` creates the ContextVar at load time, so the available scopes depend on which plugins are enabled. Typical scopes with the core + integration plugins loaded:

| Scope | What it isolates | Overlay | Source |
|-------|-----------------|---------|--------|
| `scope_memory` | Memory slot | Yes (sees own + global) | memory plugin |
| `scope_goal` | Goal set | Yes | memory plugin |
| `scope_knowledge` | Knowledge tabs | Yes | memory plugin |
| `scope_people` | Contacts | Yes | memory plugin |
| `scope_github` | GitHub account | No | github plugin |
| `scope_email` | Email account | No | email plugin |
| `scope_bitcoin` | Wallet | No | bitcoin plugin |
| `scope_gcal` | Calendar account | No | google-calendar plugin |
| `scope_telegram` | Telegram account | No | telegram plugin |
| `scope_discord` | Discord account | No | discord plugin |
| `scope_rag` | Per-chat documents | No (strict) | core (hardcoded) |
| `scope_private` | Private chat (bool) — non-local tools refuse | N/A | core (hardcoded) |

**Global overlay:** Memory, goals, knowledge, and people scopes see both their own data AND entries in the "global" scope. RAG is strict — only the chat's own documents.

**Setting scopes:** Per-chat in Chat Settings sidebar → Mind Scopes. Set to "none" to disable a system for that chat.

**ContextVars:** Thread/async-safe isolation. Core scopes use `set_rag_scope()` / `set_private_chat()`; all scopes (including plugin-registered ones) are applied per execution context via `apply_scopes_from_settings()`.

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
│   ├── prompt_spices.json
│   └── prompt_vault.enc      # Encrypted vault (prompts + chat data key)
├── personas/
│   ├── personas.json       # Persona definitions
│   └── avatars/            # Persona avatar images
├── toolsets/
│   └── toolsets.json       # Custom toolsets
├── continuity/
│   ├── tasks.json          # Scheduled task definitions
│   └── activity.json       # Task execution log
├── webui/
│   └── plugins/            # Plugin settings (HA, email, etc.)
├── functions/              # Legacy custom tools (most moved to plugins/memory/)
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

**Bootstrap:** On first run, `core/setup.py` copies factory defaults from `core/prompt_defaults/` to `user/`.

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
| privacy | `VAULT_IDLE_MINUTES`, `METRICS_ENABLED` |
| features | `ALLOW_UNSIGNED_PLUGINS`, `STORE_ENABLED`, `METRICS_ENABLED` |
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
| **Hot** | Immediate | Names, TTS voice/speed/pitch, LLM settings, SOCKS, vault idle timeout, generation params |
| **Hot-toggle** | Runtime on/off | Wakeword, STT (no restart needed) |
| **File-watched** | ~2s after save | settings.json, prompts/*.json, toolsets.json |
| **Restart** | Exit code 42 | Port changes, model configs, code changes |

The settings manager tracks which changes need restart via `get_pending_restart_keys()`.

**Tool-registered settings:** Tool modules can declare `SETTINGS` and `SETTINGS_HELP` dicts. These are registered at startup via `register_tool_settings()` and appear in the Settings UI under Custom Tools.

### LLM Configuration

```json
{
  "LLM_PROVIDERS": {
    "claude": { "provider": "claude", "model": "claude-sonnet-4-5", "enabled": false },
    "openai": { "provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "enabled": false },
    "gemini": { "provider": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "model": "gemini-2.5-flash", "enabled": false }
  },
  "LLM_CUSTOM_PROVIDERS": {
    "lmstudio": { "template": "openai", "base_url": "http://127.0.0.1:1234/v1", "is_local": true, "enabled": true }
  },
  "LLM_FALLBACK_ORDER": ["lmstudio", "claude", "gemini"]
}
```

Core providers (claude, openai, gemini) live in `LLM_PROVIDERS`. Custom/user-added providers (lmstudio default) live in `LLM_CUSTOM_PROVIDERS`. Plugins can also register LLM providers via `capabilities.providers`. Providers are tried in fallback order. Each chat can override to use a specific provider.

### Claude-Friendly Settings

**For prompt caching (up to 90% cost savings on cached input):**
- Enable caching: Settings → LLM → Claude → Enable prompt caching (default ON since 2.6.4)
- Sapphire automatically caches system prompt + tools + full conversation history
- Per-turn variations (spice, datetime) ride the **ghost-message rail** outside the cached prefix — they don't break cache and don't need to be disabled

**The only thing that disables system-prompt caching:** plugins registering a `prompt_inject` hook (RAG/context injectors, Vanta-class plugins). Most plugins use the safer `ghost_inject` hook (see `core/ghost_messages.py`) which has zero caching impact.

Cache TTL can be 5m (default) or 1h for longer sessions with idle gaps.

### Per-Turn Injection (Ghost Messages)

Per-turn ephemera (spice, current datetime, plugin-contributed context) lives in `core/ghost_messages.py` and is delivered as a labeled user-role message inserted right before the new user message. The envelope opens with `[System context from Sapphire's own app — not written by the user]` so the assistant sees these contributions as app-provided metadata, not user voice (reworded 2026-08-03 — the old "operator-injected" header read like prompt-injection vocabulary and made models suspicious). Each line is attributed to the contributing plugin name. Ghost messages are NEVER persisted to chat history. **Full guide: [GHOST_MESSAGES.md](GHOST_MESSAGES.md)** — the three contribution paths (built-in, the per-chat "Ghost Message" sidebar box, the `ghost_inject` plugin hook), the anti-manipulation gate, and the cache mechanics.

This is the rail that keeps spice/datetime/plugin context cache-friendly. Plugins use the `ghost_inject` hook to contribute (see `docs/plugin-author/hooks.md`).

---

## Extended Thinking & Reasoning

| Provider | Feature | How It Works |
|----------|---------|--------------|
| **Claude** | Extended Thinking | Structured thinking blocks with budget, `thinking` API param |
| **GPT-5.x** | Reasoning Summaries | Responses API, `reasoning_summary` param |
| **Gemini** | Reasoning Effort | Gemini 2.5 Flash/Pro use `reasoning_effort` param |

**Claude:** Enable in LLM settings → Claude → Extended Thinking. Budget default: 10,000 tokens. Auto-disables for continue mode and tool cycles without thinking. Thinking blocks preserved across tool calls.

**GPT-5.x:** Uses Responses API. Configure `reasoning_effort` (low/medium/high) and `reasoning_summary` (auto/detailed).

**Gemini:** Models like Gemini 2.5 Flash support thinking via `reasoning_effort` parameter (low/medium/high).

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

**Priority:** Stored credential → Environment variable fallback (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `SAPPHIRE_SOCKS_USERNAME`, `SAPPHIRE_SOCKS_PASSWORD`)

### Credential Encryption Details

Sensitive fields (Bitcoin WIF keys, API keys, passwords) are encrypted at rest using [Fernet](https://cryptography.io/en/latest/fernet/) symmetric encryption:

| Layer | Detail |
|-------|--------|
| **Cipher** | Fernet = AES-128-CBC + HMAC-SHA256 (encrypt-then-MAC) |
| **Key derivation** | PBKDF2-HMAC-SHA256, 100,000 iterations |
| **Key input** | Random 32-byte salt + machine identity (`hostname:username`) |
| **Salt file** | `~/.config/sapphire/.scramble_salt` (permissions `0600`) |

**Machine binding:** The encryption key is derived from a salt file plus the current machine's hostname and OS username. This means `credentials.json` **cannot be decrypted on a different machine** or after an OS reinstall, even if copied.

**Permanent key loss scenarios:**
- Machine hardware failure or OS reinstall
- `~/.config/sapphire/` directory deleted
- `.scramble_salt` file deleted or corrupted
- Username or hostname changed (different key derivation input)

**Backup implications:**
- `credentials.json` is deliberately excluded from Sapphire's `user/` backup system
- For Bitcoin wallets: use the **Export Backup** button in Settings → Plugins → Bitcoin to save a plaintext WIF file you can import on any machine
- For API keys: re-enter them in Settings after a fresh install (or set via environment variables)

---

## Plugin Signing & Verification

Plugins are signed with ed25519 to detect tampering. The signing key lives outside the repo; the public key is baked into the app.

### How Signing Works (Authors)

The signing tool (`tools/sign_plugin.py`) walks every file in a plugin directory matching `SIGNABLE_EXTENSIONS` (`.py`, `.json`, `.js`, `.css`, `.html`, `.md`), computes a SHA256 hash of each, builds a JSON manifest, and signs it with an ed25519 private key. The output is `plugin.sig` in the plugin directory.

```
python tools/sign_plugin.py plugins/stop/
python tools/sign_plugin.py --all          # sign all plugins in plugins/
```

**Private key:** `user/plugin_signing_key.pem` (gitignored). Generate with `user/tools/generate_signing_key.py`.

### How Verification Works (App)

On plugin load (`core/plugin_verify.py`), the app:

1. Loads `plugin.sig` and verifies the ed25519 signature against the baked-in public key
2. Re-hashes every file listed in the manifest and compares to the signed hashes
3. Scans for any new files not in the manifest (injection detection)

**Results:** `verified` (load), `unsigned` (load with warning if sideloading enabled, block if disabled), or `tampered` (always block).

### Cross-Platform Line Ending Normalization

Both the signer and verifier normalize line endings before hashing — `CRLF` (`\r\n`) is converted to `LF` (`\n`) in memory. This ensures signatures are valid regardless of OS or git `core.autocrlf` settings.

Without this, a plugin signed on Linux (LF) would read as tampered on Windows if git converts line endings to CRLF on checkout. The normalization is in-memory only — no files are modified on disk.

### Settings

| Setting | Default | Effect |
|---------|---------|--------|
| `ALLOW_UNSIGNED_PLUGINS` | `false` | Allow unsigned plugins with sideloading confirmation |

Default is `false` — only signed+verified plugins load, unsigned plugins are blocked entirely. Toggle on in Settings > Plugins (guarded by a danger dialog) to load unsigned plugins with a warning. Tampered plugins are blocked regardless.

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

- Registry: `core/tts/providers/__init__.py` (provider registry)
- Server: `core/tts/tts_server.py` (Kokoro, HTTP subprocess)
- Client: `core/tts/tts_client.py`
- Core providers: Kokoro (local), Null (disabled)
- Plugin providers: ElevenLabs, gTTS (Google Translate), and any plugin-registered provider

Started by `ProcessManager` if `TTS_ENABLED=true`. Auto-restarts on crash. Server auto-restarts at 3GB memory or 500 requests.

Kokoro: 17 voices (American and British, male and female). Pitch shifting via resampling, speed control via Kokoro parameter. Plugin providers appear in Settings → TTS → Provider dropdown.

### STT (Speech-to-Text)

- Registry: `core/stt/providers/__init__.py` (provider registry)
- Server: `core/stt/server.py` (faster-whisper, loaded in main process)
- Recorder: `core/stt/recorder.py` (adaptive VAD, silence detection)
- Guard: `core/stt/utils.py` (shared `can_transcribe()` check)
- Core providers: faster-whisper (local GPU/CPU), fireworks-whisper (cloud)
- Plugin providers: any plugin-registered STT provider

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

## Privacy: the Vault and Private Chats

Privacy is **per-chat**, not a global toggle. The old whitelist-based privacy mode was removed in 2.8.4; there is no `/api/privacy` endpoint.

**The vault** (`core/prompt_vault.py`) is one scrypt + AES-256-GCM encrypted store at `user/prompts/prompt_vault.enc`, guarded by one passphrase. Unlocked, its prompts merge into the prompt system and private mode is armed; locked, the key is dropped from memory and its contents exist nowhere. Idle auto-lock is `VAULT_IDLE_MINUTES` (default 30). Lifecycle routes: `POST /api/vault/setup | /unlock | /lock | /rekey | /move`; state rides `vault` on `/api/status`.

**Private chats** carry `private_chat` in chat settings. Sending an operator turn while the vault is unlocked stamps the active chat private (`stamp_private_if_unlocked`, `core/chat/chat_streaming.py`) — skipped for mode-tagged (game/story/librarian/limbo) chats, already-private chats, and managed mode. Enforcement:

- **Providers** — auto mode filters to `is_local: True`; an explicitly pinned non-local provider raises. Cloud STT/TTS refuse too (`core/voice_privacy.py`).
- **Tools** — `_check_privacy_allowed` blocks any tool not flagged `is_local: True` (unflagged = blocked).
- **At rest** — message rows, chat settings, tool images, and `plugin_chat_data` rows are stored as `@enc1:` AES-256-GCM values under a random chat data key wrapped inside the vault frame (so rekey re-wraps without re-encrypting rows). Chat names stay plaintext.
- **While sealed** — `ChatSessionManager` gates every name-resolving entry point (`_vault_sealed` / `_vault_hidden`): list, search, settings, messages, export, rename, delete all answer as if the chat never existed. A private active chat is evicted at lock and at boot.
- **Plugins** — hooks are withheld from any plugin whose manifest lacks `privacy_aware: true` on a private turn (fail-closed).

User-facing guide: [PRIVACY.md](PRIVACY.md).

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
| Spice sets | `user/spice_sets/*.json` | ~2s |

---

## Chat Sessions

SQLite database `user/history/sapphire_history.db` (WAL mode):

```
Schema: chats(name TEXT PRIMARY KEY, settings JSON, messages JSON, updated_at TEXT)
```

Each session has message history, per-chat settings (prompt, voice, toolset, LLM, spice, scopes), and metadata.

---

## Key Source Files

| Path | Purpose |
|------|---------|
| `main.py` | Runner with restart loop |
| `sapphire.py` | VoiceChatSystem entry point |
| `config.py` | Settings proxy |
| `core/api_fastapi.py` + `core/routes/` | FastAPI server (~250 endpoints across 13 route modules) |
| `core/auth.py` | Session auth, CSRF, rate limiting |
| `core/ssl_utils.py` | Self-signed certificate generation |
| `core/settings_manager.py` | Settings merge, file watcher, restart tiers |
| `core/credentials_manager.py` | API keys, secrets, Fernet encryption |
| `core/setup.py` | Bootstrap, auth, first-run |
| `core/event_bus.py` | Real-time event pub/sub for SSE |
| `core/chat/chat.py` | LLM orchestration |
| `core/chat/chat_streaming.py` | SSE response streaming |
| `core/chat/llm_providers/` | Claude, OpenAI, Gemini (core) + custom + plugin providers |
| `core/ghost_messages.py` | Per-turn ephemeral injection (spice, datetime, plugin context) — cache-friendly delivery rail |
| `core/hooks.py` | Plugin hook runner (pre_chat, prompt_inject, ghost_inject, etc.) |
| `core/provider_registry.py` | Base registry for TTS, STT, Embedding, LLM |
| `core/agents/` | Agent spawning, registry, lifecycle |
| `core/chat/function_manager.py` | Tool loading, scopes |
| `core/chat/history.py` | Session management |
| `core/continuity/scheduler.py` | Cron-based task scheduler |
| `core/audio/device_manager.py` | Audio device handling |
| `plugins/memory/tools/knowledge_tools.py` | Knowledge base + people |
| `plugins/memory/tools/memory_tools.py` | Long-term memory + embeddings |
| `plugins/memory/tools/goals_tools.py` | Goals + progress journaling |

---

## Reference for AI

Sapphire architecture for troubleshooting and development.

PROCESSES:
- main.py: Runner with restart loop (exit 42 = restart)
- sapphire.py: Core VoiceChatSystem
- core/api_fastapi.py + core/routes/: FastAPI server (port 8073, HTTPS, ~250 endpoints)
- TTS server: Kokoro HTTP subprocess (port 5012, if enabled)
- STT: Faster-whisper thread in main process

PORTS:
- 8073: FastAPI server (HTTPS, all routes)
- 5012: TTS server (if enabled)
- 1234: Default LLM (LM Studio)

SCOPES (ContextVar-based; only rag/private hardcoded, the rest plugin-registered):
- scope_memory, scope_goal, scope_knowledge, scope_people: global overlay
- scope_email, scope_bitcoin, scope_gcal, scope_telegram, scope_discord: no overlay
- scope_rag: strict per-chat isolation
- scope_private: boolean — private chat; tools not flagged is_local refuse
- Set per-chat in sidebar Mind Scopes (private is not a dropdown — see PRIVACY)

LLM PROVIDERS:
- Core: claude, openai, gemini (in LLM_PROVIDERS)
- Custom: lmstudio default (in LLM_CUSTOM_PROVIDERS), user can add more
- Plugin-provided: any plugin can register LLM providers via capabilities.providers
- LLM_FALLBACK_ORDER controls Auto mode (default: lmstudio, claude, gemini)
- Per-chat override via session settings
- API keys: ~/.config/sapphire/credentials.json or env vars
- Private chats only reach providers marked is_local (auto filters; explicit non-local raises)

PRIVACY (see docs/PRIVACY.md):
- One vault: user/prompts/prompt_vault.enc (scrypt + AES-256-GCM), one passphrase, VAULT_IDLE_MINUTES auto-lock
- Unlocked = private mode armed; an operator turn marks the active chat private (skips mode-tagged chats)
- Private chat at rest: message rows, settings, tool images, plugin_chat_data as '@enc1:' values; names stay plaintext
- Sealed vault: private chats absent from list/search/settings/messages/export/rename/delete; active one evicted at lock and boot
- Plugin hooks withheld unless the manifest declares privacy_aware: true
- Routes: POST /api/vault/setup|unlock|lock|rekey|move; no /api/privacy (removed 2.8.4)

CREDENTIALS:
- ~/.config/sapphire/secret_key: Password/API key hash
- ~/.config/sapphire/credentials.json: LLM, SOCKS, email, bitcoin, SSH, HA
- Not in user/ directory, not in backups
- Sensitive fields Fernet-encrypted (machine identity key)

HOT RELOAD:
- Settings/prompts/toolsets: ~2s after file change
- Wakeword/STT: hot-toggle on/off at runtime
- TTS: hot-stop/start via ProcessManager
- LLM settings, SOCKS, vault idle timeout: immediate
- Ports, models, code: require restart

API: See docs/API.md for all ~250 endpoints

DATABASES:
- user/history/sapphire_history.db: chats
- user/memory.db: memories, memories_fts, memory_scopes
- user/knowledge.db: people, knowledge_tabs, knowledge_entries, knowledge_fts
- user/goals.db: goals, progress_journal

LOGS:
- user/logs/sapphire.log: Main log
- user/logs/tts.log: TTS server log
