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
    ├── TTS (core/tts/) → provider-based: Kokoro, sapphire_router (core) + plugins (Piper, ElevenLabs, gTTS)
    ├── STT (core/stt/) → provider-based: faster-whisper, fireworks-whisper, sapphire_router (core) + plugins
    ├── Wake Word (core/wakeword/) → thread (hot-toggleable)
    ├── Conversation (core/conversation/) → true speech mode: engine, driver, manager
    ├── Network Facade (core/net.py) → one LAN/WAN proxy decision point for outbound HTTP
    ├── Provider Registry (core/provider_registry.py) → TTS, STT, Embedding, LLM
    ├── Agents (core/agents/) → agent spawning, registry, lifecycle
    ├── FastAPI Server (core/api_fastapi.py + core/routes/) → 0.0.0.0:8073
    └── Event Bus (core/event_bus.py) → SSE pub/sub
```

**Process model:** `main.py` is a runner that spawns `sapphire.py` with automatic restart on crash or restart request (exit code 42). `sapphire.py` spawns the Kokoro TTS server as a subprocess via `ProcessManager` when that provider is selected. STT runs as a thread. The FastAPI/uvicorn server handles all web traffic directly (auth, static files, API, SSE) on a single port. Everything else runs in the main process.

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

**Access pattern:** `import config` then `config.TTS_PROVIDER`, `config.LLM_PROVIDERS`, etc.

### Settings Categories

| Category | Examples |
|----------|----------|
| identity | `DEFAULT_USERNAME`, `DASHBOARD_DISPLAY_NAME`, `DEFAULT_PERSONA`, `USER_TIMEZONE` |
| network | `SOCKS_ENABLED`, `SOCKS_HOST`, `SOCKS_PORT`, `SOCKS_TIMEOUT`, `SOCKS_ROUTE_LLM`, `SOCKS_REMOTE_DNS`, `SOCKS_NO_PROXY_EXTRA`, `UPDATE_CHECK_ENABLED` — see [NETWORK.md](NETWORK.md) |
| privacy | `VAULT_IDLE_MINUTES`, `METRICS_ENABLED`, `PRIVATE_ALLOW_UNFLAGGED_TOOLS` |
| wakeword | `WAKE_WORD_ENABLED`, `WAKEWORD_MODEL`, `WAKEWORD_THRESHOLD` |
| stt | `STT_PROVIDER`, `STT_MODEL_SIZE`, `FASTER_WHISPER_*` |
| recorder | `STT_VAD_ENABLED`, `RECORDER_*` (silence/VAD tuning), `CONVERSATION_*` (conversation-mode tuning) |
| tts | `TTS_PROVIDER`, `TTS_SERVER_PORT`, `TTS_STREAMING_ENABLED` + `TTS_STREAMING_*` tuning |
| llm | `LLM_PROVIDERS`, `LLM_CUSTOM_PROVIDERS`, `LLM_FALLBACK_ORDER`, `MODEL_GENERATION_PROFILES` |
| audio | `AUDIO_INPUT_DEVICE`, `AUDIO_OUTPUT_DEVICE` |
| tools | `MAX_TOOL_ITERATIONS`, `MAX_PARALLEL_TOOLS`, `TOOL_RESULT_MAX_CHARS` |
| embedding | `EMBEDDING_PROVIDER`, `EMBEDDING_API_URL` |
| rag / memory | `RAG_SIMILARITY_THRESHOLD`, `MEMORY_DEDUP_THRESHOLD` |
| plugins | `ALLOW_UNSIGNED_PLUGINS`, `PLUGIN_KEYS_URL` |
| store | `STORE_ENABLED`, `STORE_URL` |
| server | `WEB_UI_HOST`, `WEB_UI_PORT`, `LOG_LEVEL` |
| backups | `BACKUPS_ENABLED`, `BACKUPS_KEEP_DAILY`, etc. |

Two things that look like settings but aren't:

- `STT_ENABLED` / `TTS_ENABLED` are **derived** compatibility values, not stored keys — the settings manager computes them from `STT_PROVIDER` / `TTS_PROVIDER` (`true` when the provider isn't `none`). To turn a system on or off, set the provider.
- **Voice, pitch, and speed are per-chat settings**, not global keys — they live in the chat sidebar's TTS (Voice) accordion and travel with each chat. See [VOICE.md](VOICE.md).

### Settings Reload Tiers

| Tier | When Applied | Examples |
|------|-------------|---------|
| **Hot** | Immediate | Names, LLM settings, SOCKS, vault idle timeout, generation params |
| **Hot-toggle** | Runtime on/off | Wakeword, STT, TTS provider switch (no restart needed) |
| **File-watched** | ~2s after save | settings.json, prompts/*.json, toolsets.json |
| **Restart** | Exit code 42 | Port changes, model configs, code changes |

The settings manager tracks which changes need restart via `get_pending_restart_keys()`.

**Tool-registered settings:** Tool modules can declare `SETTINGS` and `SETTINGS_HELP` dicts. These are registered at startup via `register_tool_settings()` and appear in the Settings UI under Custom Tools.

### LLM Configuration

```json
{
  "LLM_PROVIDERS": {
    "claude": { "provider": "claude", "model": "claude-opus-4-8", "enabled": false },
    "openai": { "provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o", "enabled": false },
    "gemini": { "provider": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "model": "gemini-2.5-flash", "enabled": false }
  },
  "LLM_CUSTOM_PROVIDERS": {
    "lmstudio": { "template": "openai", "base_url": "http://127.0.0.1:1234/v1", "is_local": true, "enabled": true }
  },
  "LLM_FALLBACK_ORDER": ["lmstudio", "claude", "gemini", "openai"]
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

One bcrypt hash serves as login password and legacy API key (`X-API-Key` header); browser sessions ride a separate `session_secret` file. Changing the password (Settings › System) rotates the hash atomically, invalidates the old `X-API-Key` value, and logs out every other session — named API Keys (bearer tokens) are unaffected.

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

1. Loads `plugin.sig` and verifies the ed25519 signature — against the baked-in official key first, then the authorized third-party keys list
2. Re-hashes every file listed in the manifest and compares to the signed hashes
3. Scans for any new files not in the manifest (injection detection)

**Results:** `official` (signed with Sapphire's baked-in key), `verified_author` (signed by an authorized third-party key), `unsigned` (no `plugin.sig` — loads with a warning only when sideloading is enabled), or `failed` (signature doesn't match a trusted key, or files were modified — always blocked). Managed/Docker installs add a `validated` tier: unsigned plugins that pass strict file validation load without a signature. Full guide: [SIGNING.md](SIGNING.md).

### Cross-Platform Line Ending Normalization

Both the signer and verifier normalize line endings before hashing — `CRLF` (`\r\n`) is converted to `LF` (`\n`) in memory. This ensures signatures are valid regardless of OS or git `core.autocrlf` settings.

Without this, a plugin signed on Linux (LF) would read as tampered on Windows if git converts line endings to CRLF on checkout. The normalization is in-memory only — no files are modified on disk.

### Settings

| Setting | Default | Effect |
|---------|---------|--------|
| `ALLOW_UNSIGNED_PLUGINS` | `false` | Allow unsigned plugins with sideloading confirmation |

Default is `false` — only `official` and `verified_author` plugins load; unsigned plugins are blocked entirely (except the managed-mode `validated` lane above). Toggle on in Settings > Plugins (guarded by a danger dialog) to load unsigned plugins with a warning. `failed` plugins are blocked regardless.

---

## Default Ports

| Service | Port | Binding |
|---------|------|---------|
| FastAPI Server | 8073 | `0.0.0.0` (all interfaces, HTTPS) |
| TTS Server | 5012 | `0.0.0.0` (configurable) |
| LM Studio (default) | 1234 | External |

---

## Component Services

The user-facing guide for the whole voice stack (STT, TTS, wake word, conversation mode, echo tiers) is [VOICE.md](VOICE.md) — the notes below are the architecture view.

### TTS (Text-to-Speech)

- Registry: `core/tts/providers/__init__.py` (provider registry)
- Server: `core/tts/tts_server.py` (Kokoro, HTTP subprocess)
- Client: `core/tts/tts_client.py`
- Core providers: Kokoro (local), sapphire_router (managed), Null (disabled)
- Plugin providers: Piper (local), ElevenLabs, gTTS (Google Translate), and any plugin-registered provider

The Kokoro subprocess is started by `ProcessManager` when the Kokoro provider is selected. Auto-restarts on crash. Server auto-restarts at 3GB memory or 500 requests.

Kokoro: 28 voices (American and British, male and female). Voice, pitch, and speed are per-chat settings, not global keys. Pitch is native for Kokoro and Piper; providers without native pitch get it via resampling. Plugin providers appear in Settings → TTS → Provider dropdown.

**Streaming TTS** (`core/tts/streaming.py` + `core/tts/stream_pump.py`): with `TTS_STREAMING_ENABLED` on and a streaming-capable provider (Kokoro, Piper), Sapphire synthesizes and starts speaking each chunk as the LLM finishes it, instead of waiting for the whole reply — this is also what gives Conversation mode its voice. Other providers fall back to whole-reply playback. Details and tuning: [VOICE.md](VOICE.md).

### STT (Speech-to-Text)

- Registry: `core/stt/providers/__init__.py` (provider registry)
- Recorder: `core/stt/recorder.py` (adaptive VAD, silence detection)
- Guard: `core/stt/utils.py` (shared `can_transcribe()` check)
- Core providers: faster-whisper (local GPU/CPU), fireworks-whisper (cloud), sapphire_router (managed)
- Plugin providers: any plugin-registered STT provider

`core/stt/server.py` is a backwards-compat shim only — the faster-whisper implementation lives in `core/stt/providers/faster_whisper.py` and loads in the main process.

Runs as a thread when an STT provider is selected. Supports **hot-toggle** at runtime via `VoiceChatSystem.toggle_stt()`. GPU (CUDA) with CPU fallback.

### Conversation Mode

- `core/conversation/` — the "true speech mode" subsystem: `engine.py` (pure turn-state machine: IDLE / USER_SPEAKING / RESPONDING, barge-in arming), `driver.py` (bridges STT → streaming chat → TTS sink; barge-in cancels generation and cuts audio), `manager.py` (lifecycle, wakeword fail-safe handoff), `vad.py` plus local / duplex / browser audio sources.
- Audible replies require streaming TTS with a capable provider. Full guide: [VOICE.md](VOICE.md).

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

SQLite database `user/history/sapphire_history.db` (WAL mode). Storage is **rows-per-message**: each chat is a row in `chats`, and its messages live one-per-row in `chat_messages` keyed by `(chat_name, seq)`.

| Table | Holds |
|-------|-------|
| `chats` | One row per chat: settings JSON, `storage_format` (`blob` \| `rows`), timestamps, vault 🔒 marker |
| `chat_messages` | One row per message: `chat_name`, `seq`, `role`, `message_json` |
| `chat_messages_quarantine` | Unreadable message rows moved here verbatim by the repair tool |
| `plugin_chat_data` | Chat-scoped plugin storage — rides rename/delete/vault with its chat |
| `tool_images` | Images produced by tool calls, per chat |

Chats created before the rowify migration carry `storage_format='blob'` (messages as one JSON blob in the `chats` row) and are converted in place; a chat whose conversion hits a data-shape error latches `conversion_failed` and stays on blob.

Each chat carries per-chat settings (prompt, voice, toolset, LLM, spice, scopes) in its `chats` row — the full per-chat model is in [CHATS.md](CHATS.md).

---

## Key Source Files

| Path | Purpose |
|------|---------|
| `main.py` | Runner with restart loop |
| `sapphire.py` | VoiceChatSystem entry point |
| `config.py` | Settings proxy |
| `core/api_fastapi.py` + `core/routes/` | FastAPI server + route modules (endpoints: [API.md](API.md)) |
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
| `core/chat/history.py` | Chat session storage (rows-per-message SQLite) |
| `core/conversation/` | Conversation mode — engine, driver, manager |
| `core/net.py` | Network facade — single LAN/WAN proxy decision point ([NETWORK.md](NETWORK.md)) |
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
- core/api_fastapi.py + core/routes/: FastAPI server (port 8073, HTTPS)
- TTS server: Kokoro HTTP subprocess (port 5012, when the kokoro provider is selected)
- STT: provider thread in main process (faster-whisper / fireworks-whisper / sapphire_router; core/stt/server.py is a compat shim)
- Conversation mode: core/conversation/ (engine=turn-state machine, driver=STT->stream->TTS, manager=lifecycle+wakeword handoff) — see docs/VOICE.md
- Network: core/net.py facade — one LAN/WAN proxy decision point; LAN lane direct (trust_env off), WAN lane env-honoring/SOCKS — see docs/NETWORK.md

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
- LLM_FALLBACK_ORDER controls Auto mode (default: lmstudio, claude, gemini, openai)
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

SETTINGS:
- Real keys live in core/settings_defaults.json, overridden by user/settings.json
- STT_ENABLED / TTS_ENABLED are DERIVED compat values (true when STT_PROVIDER / TTS_PROVIDER != 'none') — never write them, set the provider
- Voice/pitch/speed: PER-CHAT settings (chat sidebar), no global keys
- Network: SOCKS_ENABLED/HOST/PORT/TIMEOUT, SOCKS_ROUTE_LLM, SOCKS_REMOTE_DNS, SOCKS_NO_PROXY_EXTRA, UPDATE_CHECK_ENABLED (docs/NETWORK.md)
- Streaming TTS: TTS_STREAMING_ENABLED + TTS_STREAMING_* tuning (docs/VOICE.md)

PLUGIN SIGNING (docs/SIGNING.md):
- Tiers: official (baked-in key) / verified_author (authorized third-party key) / unsigned / failed (tampered — always blocked)
- ALLOW_UNSIGNED_PLUGINS=false default: only official + verified_author load; managed installs add 'validated' (strict file validation)

HOT RELOAD:
- Settings/prompts/toolsets: ~2s after file change
- Wakeword/STT: hot-toggle on/off at runtime
- TTS: hot provider switch; Kokoro subprocess start/stop via ProcessManager
- LLM settings, SOCKS, vault idle timeout: immediate
- Ports, models, code: require restart

API: See docs/API.md for the full endpoint list

DATABASES:
- user/history/sapphire_history.db: chats (settings + storage_format blob|rows), chat_messages (one row per message), chat_messages_quarantine, plugin_chat_data, tool_images
- user/memory.db: memories, memories_fts, memory_scopes
- user/knowledge.db: people, knowledge_tabs, knowledge_entries, knowledge_fts
- user/goals.db: goals, progress_journal

LOGS:
- user/logs/sapphire.log: Main log
- user/logs/tts.log: TTS server log
