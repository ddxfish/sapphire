# API Reference

Sapphire runs a single FastAPI server on port 8073 (HTTPS). Every endpoint below requires authentication — either a browser session or an API key.

Routes are split across modules under `core/routes/`, plus a few app-level routes in `core/api_fastapi.py` (login/setup pages, avatar files, plugin web assets). This doc covers the full surface.

## Authentication

### Browser Session
Log in at `/login` with your password. Sessions last 30 days.

### Named API Tokens (Programmatic Access — preferred)
For scripts, external tools, and integrations, mint a named token at **Settings > System > API Keys** (or `POST /api/system/api-tokens`) and send it as a Bearer header:

```bash
curl -k https://localhost:8073/api/status \
  -H "Authorization: Bearer $SAPPHIRE_TOKEN"
```

The full token value is shown **once** at creation — copy it then. Listing shows only the last 4 characters; each token is individually revocable (`DELETE /api/system/api-tokens/{token_id}`). Prefer this over X-API-Key: named, revocable per caller, and it never exposes your password hash.

### X-API-Key (legacy)
Older scripts and internal tools may still send the bcrypt password hash as a header:

```bash
curl -k https://localhost:8073/api/status \
  -H "X-API-Key: $(cat ~/.config/sapphire/secret_key)"
```

The key is the bcrypt hash stored in your config directory:

| OS | Path |
|----|------|
| Linux | `~/.config/sapphire/secret_key` |
| macOS | `~/Library/Application Support/Sapphire/secret_key` |
| Windows | `%APPDATA%\Sapphire\secret_key` |

This file is created during initial setup. To reset, delete it and restart Sapphire.

### CSRF
CSRF tokens are required for browser sessions on POST/PUT/DELETE requests. Bearer-token and X-API-Key auth **bypass CSRF** — no extra headers needed. The `/ws/conversation` WebSocket accepts session cookies only (same-origin enforced).

### Rate Limiting
5 attempts per 60 seconds per IP on auth endpoints.

---

## Endpoints

### Core

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/health` | Health check |
| GET | `/api/status` | Unified UI state (prompt, context, spice, TTS/STT readiness) |
| GET | `/api/init` | Mega initialization (all toolsets, prompts, personas, spices, settings) |

### Chat

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/chat` | Send message, get response |
| POST | `/api/chat/stream` | Streaming SSE response — one turn per chat: a second stream while a turn is live on that chat returns **409** |
| POST | `/api/cancel` | Cancel active stream |
| GET | `/api/events` | SSE event stream (real-time UI updates) |
| GET | `/api/history` | Get chat message history |

### Chat Sessions

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/chats` | List all chats |
| POST | `/api/chats` | Create new chat |
| GET | `/api/chats/search` | Search chat content (private chats excluded while the vault is sealed) |
| DELETE | `/api/chats/{name}` | Delete chat (also deletes its plugin/playthrough data) |
| POST | `/api/chats/{name}/activate` | Switch active chat |
| GET | `/api/chats/active` | Get active chat name |
| GET | `/api/chats/{name}/settings` | Get chat settings |
| PUT | `/api/chats/{name}/settings` | Update chat settings |
| POST | `/api/chats/{name}/rename` | Rename a chat (carries tool images, message rows, and RAG scope along) |
| POST | `/api/chats/{name}/archive` | Toggle a chat's archived flag (UI shade — chat stays fully functional) |
| GET | `/api/chats/{name}/export` | Full raw export of one chat (name, settings, messages) |
| POST | `/api/chats/bulk-export` | Export many chats as one JSON document |
| POST | `/api/chats/bulk-export-zip` | Export many chats as a zip, one JSON file per chat |
| POST | `/api/chats/bulk-delete` | Delete many chats in one call (per-chat results) |
| POST | `/api/chats/bulk-clear` | Clear messages in many chats (chats survive, histories wiped) |
| POST | `/api/chats/{name}/trim` | Turn-snapped middle trim — keep the first A and last B turns |
| POST | `/api/chats/{name}/repair` | Diagnose/repair a chat's unreadable message rows |
| POST | `/api/chats/{name}/compress` | Start the background compress job (summarize history) |
| GET | `/api/chats/compress/status` | Status of the one-at-a-time compress job (UI polls this) |
| GET | `/api/chats/{name}/prompt-preview` | The exact system prompt + ghost envelope the next turn would send |

### Message History

| Method | Endpoint | Purpose |
|--------|----------|---------|
| DELETE | `/api/history/messages` | Remove messages (by count, user message, or clear all with count=-1) |
| POST | `/api/history/messages/remove-last-assistant` | Remove last assistant message |
| POST | `/api/history/messages/remove-from-assistant` | Remove from last assistant message onward |
| DELETE | `/api/history/tool-call/{id}` | Delete specific tool call |
| POST | `/api/history/messages/edit` | Edit a message |
| GET | `/api/history/raw` | Export raw chat history |
| POST | `/api/history/import` | Import chat history |

### TTS / STT / Audio

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/tts` | Generate TTS audio |
| POST | `/api/tts/stream` | Streaming TTS — per-chunk OGG over chunked transfer (v2.7.0) |
| POST | `/api/tts/preview` | Preview voice sample |
| GET | `/api/tts/status` | TTS server status |
| POST | `/api/tts/stop` | Stop TTS playback |
| POST | `/api/tts/test` | Test TTS provider connectivity |
| GET | `/api/tts/voices` | List voices for active TTS provider |
| POST | `/api/tts/voices` | List voices (with optional api_key for pre-save browsing) |
| POST | `/api/transcribe` | Transcribe audio file |
| GET | `/api/stt/vad-status` | Silero VAD warmup status |
| POST | `/api/stt/vad-test` | Test mic input against the VAD threshold |
| POST | `/api/mic/active` | Set web mic active state (suppresses wakeword) |
| POST | `/api/upload/image` | Upload image for chat |
| GET | `/api/audio/devices` | List audio devices |
| POST | `/api/audio/test-input` | Test input device |
| POST | `/api/audio/test-output` | Test output device |

### Settings

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/settings` | Get all settings |
| GET | `/api/settings/{key}` | Get a single setting |
| PUT | `/api/settings/{key}` | Update a single setting |
| DELETE | `/api/settings/{key}` | Reset a setting to default |
| PUT | `/api/settings/batch` | Batch update multiple settings |
| POST | `/api/settings/reload` | Force reload from disk |
| POST | `/api/settings/reset` | Reset all settings to defaults |
| GET | `/api/settings/help` | Get setting descriptions |
| GET | `/api/settings/help/{key}` | Get help for a specific setting |
| GET | `/api/settings/tiers` | Get hot vs restart-required status |
| GET | `/api/settings/tool-settings` | Get tool-specific settings |
| GET | `/api/settings/chat-defaults` | Get chat default settings |
| PUT | `/api/settings/chat-defaults` | Update chat defaults |
| DELETE | `/api/settings/chat-defaults` | Reset chat defaults to factory |
| GET | `/api/settings/wakeword-models` | List available wakeword models |

### Credentials & SOCKS Proxy

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/credentials` | List configured credential keys |
| PUT | `/api/credentials/llm/{provider}` | Set LLM API key |
| DELETE | `/api/credentials/llm/{provider}` | Remove LLM API key |
| GET | `/api/credentials/socks` | Get SOCKS proxy config |
| PUT | `/api/credentials/socks` | Set SOCKS proxy config |
| DELETE | `/api/credentials/socks` | Remove SOCKS proxy config |
| POST | `/api/credentials/socks/test` | Test SOCKS proxy connection |
| GET | `/api/socks/status` | Trust-strip truth: what the proxy env actually covers right now |

### LLM Providers

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/llm/providers` | List LLM providers |
| PUT | `/api/llm/providers/{key}` | Update provider config |
| PUT | `/api/llm/fallback-order` | Set LLM fallback order |
| POST | `/api/llm/test/{provider}` | Test LLM connection |
| POST | `/api/llm/test-thinking/{provider}` | Probe whether the provider's 'disable thinking' switch actually works |
| POST | `/api/llm/custom-providers` | Add a custom LLM provider |
| DELETE | `/api/llm/custom-providers/{key}` | Remove a custom LLM provider |
| GET | `/api/llm/custom-providers/{key}/models` | Fetch models from a custom provider |
| GET | `/api/llm/presets` | List LLM provider presets |

### Provider Registry (TTS / STT)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/tts/providers` | List TTS providers (core + plugin) |
| GET | `/api/stt/providers` | List STT providers (core + plugin) |

### Embeddings

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/embedding/test` | Test embedding provider |
| GET | `/api/embedding/providers` | List available embedding providers |
| GET | `/api/embedding/integrity` | Check embedding dimension/integrity across stores |
| POST | `/api/embedding/reembed` | Re-embed all stored vectors (after provider/model change) |
| GET | `/api/embedding/reembed/status` | Re-embed progress |
| POST | `/api/embedding/reembed/cancel` | Cancel an in-progress re-embed |

### Privacy Vault

Vault state (`{exists, unlocked}`) rides the top-level `vault` key on `GET /api/status`. Wrong passphrase returns **403**, never 401. See [PRIVACY.md](PRIVACY.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/vault/setup` | Create the vault and unlock it (409 if one exists) |
| POST | `/api/vault/unlock` | Unlock with the passphrase (idempotent) |
| POST | `/api/vault/lock` | Lock now — synchronous; evicts a private active chat first |
| POST | `/api/vault/rekey` | Change the passphrase (needs the current one; lock state preserved) |
| POST | `/api/vault/move` | Move a prompt or piece in/out of the vault |

Chat privacy is per-chat: `PUT /api/chats/{name}/settings` with `private_chat`. Membership changes need the vault unlocked (403 otherwise); while sealed, private chats answer as nonexistent on every by-name route.

### System Prompt

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/system/status` | System status (detailed) |
| GET | `/api/system/prompt` | Get current system prompt |
| POST | `/api/system/merge-updates` | Merge missing prompts + personas from app updates |

### Personas

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/personas` | List all personas |
| GET | `/api/personas/{name}` | Get persona details |
| POST | `/api/personas` | Create persona |
| PUT | `/api/personas/{name}` | Update persona |
| DELETE | `/api/personas/{name}` | Delete persona |
| POST | `/api/personas/{name}/duplicate` | Clone persona |
| POST | `/api/personas/{name}/load` | Activate persona on current chat |
| POST | `/api/personas/from-chat` | Create persona from current chat settings |
| POST | `/api/personas/{name}/avatar` | Upload avatar (max 4MB) |
| DELETE | `/api/personas/{name}/avatar` | Remove avatar |
| GET | `/api/personas/{name}/avatar` | Get avatar image |
| PUT | `/api/personas/default` | Set default persona for new chats |
| DELETE | `/api/personas/default` | Clear default persona |
| GET | `/api/personas/{name}/export.png` | Export persona as a PNG character card (avatar image + full bundle in a `sapphire_persona` tEXt chunk) |
| POST | `/api/personas/import-card` | Import persona from an uploaded PNG character card (multipart; `overwrite_prompt`/`overwrite_avatar`/`overwrite_persona` flags; JSON bundles go through `/api/personas/import`) |
| POST | `/api/personas/import` | Import persona from a portable JSON bundle (legacy format) |

### Prompts

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/prompts` | List prompts (pack entries with `kind` != `user` are omitted; a `hidden` {name: kind} map rides along for dropdown labels) |
| GET | `/api/prompts/{name}` | Get prompt details |
| PUT | `/api/prompts/{name}` | Create or update prompt |
| DELETE | `/api/prompts/{name}` | Delete prompt |
| POST | `/api/prompts/{name}/load` | Activate prompt on current chat |
| POST | `/api/prompts/reload` | Reload from disk |
| POST | `/api/prompts/reset` | Reset to defaults |
| POST | `/api/prompts/merge` | Merge defaults into current |
| POST | `/api/prompts/reset-chat-defaults` | Reset chat defaults to factory |
| GET | `/api/prompts/components` | List prompt components (hidden pack scaffolding omitted; `hidden_keys` carries their names for missing-ref checks) |
| PUT | `/api/prompts/components/{type}/{key}` | Save prompt component |
| DELETE | `/api/prompts/components/{type}/{key}` | Delete prompt component |
| GET | `/api/prompts/piece-usage` | Usage index: which prompts use each piece (+ dangling refs, vault flags) |
| POST | `/api/prompts/pieces/rename` | Safe rename: moves a piece in its store and repoints every reference |
| POST | `/api/prompts/pieces/strip-danglers` | Strip all references to missing pieces (409 while vault locked) |
| GET | `/api/prompts/pieces/trash` | List trashed pieces (plaintext + unlocked-vault stores) |
| POST | `/api/prompts/pieces/trash` | Soft-delete pieces into the trash |
| POST | `/api/prompts/pieces/trash/restore` | Restore trashed pieces (never overwrites a live key) |
| POST | `/api/prompts/pieces/trash/purge` | Empty the piece trash (both stores while unlocked) |

### Toolsets

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/toolsets` | List toolsets (?filter=sidebar to exclude module-level) |
| GET | `/api/toolsets/current` | Get active toolset |
| POST | `/api/toolsets/{name}/activate` | Activate toolset |
| POST | `/api/toolsets/custom` | Save custom toolset |
| DELETE | `/api/toolsets/{name}` | Delete toolset |
| POST | `/api/toolsets/{name}/emoji` | Set toolset emoji |
| GET | `/api/functions` | List all available functions |
| POST | `/api/functions/enable` | Enable specific functions |

### Spices

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/spices` | List all spices |
| POST | `/api/spices` | Add a new spice to a category |
| PUT | `/api/spices/{category}/{index}` | Update a spice |
| DELETE | `/api/spices/{category}/{index}` | Delete a spice |
| POST | `/api/spices/category` | Create spice category |
| PUT | `/api/spices/category/{name}` | Rename spice category |
| DELETE | `/api/spices/category/{name}` | Delete spice category |
| POST | `/api/spices/category/{name}/emoji` | Set category emoji |
| POST | `/api/spices/category/{name}/toggle` | Enable/disable category |
| POST | `/api/spices/reload` | Reload spices from disk |

### Spice Sets

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/spice-sets` | List spice sets |
| GET | `/api/spice-sets/current` | Get active spice set |
| POST | `/api/spice-sets/{name}/activate` | Activate spice set |
| POST | `/api/spice-sets/custom` | Save custom spice set |
| DELETE | `/api/spice-sets/{name}` | Delete spice set |
| POST | `/api/spice-sets/{name}/emoji` | Set spice set emoji |

### Memory

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/memory/scopes` | List memory scopes |
| POST | `/api/memory/scopes` | Create scope |
| DELETE | `/api/memory/scopes/{name}` | Delete scope |
| GET | `/api/memory/list` | List memories (grouped by label) |
| PUT | `/api/memory/{id}` | Update memory |
| DELETE | `/api/memory/{id}` | Delete memory |
| GET | `/api/memory/export` | Export all memories in scope as JSON |
| POST | `/api/memory/import` | Import memories from JSON |
| GET | `/api/memory/duplicates` | Find near-duplicate memories via vector similarity |

### Knowledge

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/knowledge/scopes` | List knowledge scopes |
| POST | `/api/knowledge/scopes` | Create scope |
| DELETE | `/api/knowledge/scopes/{name}` | Delete scope |
| GET | `/api/knowledge/tabs` | List knowledge tabs (in scope) |
| POST | `/api/knowledge/tabs` | Create tab |
| GET | `/api/knowledge/tabs/{id}` | Get tab with entries |
| PUT | `/api/knowledge/tabs/{id}` | Update tab |
| DELETE | `/api/knowledge/tabs/{id}` | Delete tab |
| POST | `/api/knowledge/tabs/{id}/entries` | Add entry |
| POST | `/api/knowledge/tabs/{id}/upload` | Upload file (auto-chunks + embeds) |
| DELETE | `/api/knowledge/tabs/{id}/file/{name}` | Delete uploaded file entries |
| PUT | `/api/knowledge/entries/{id}` | Update entry |
| DELETE | `/api/knowledge/entries/{id}` | Delete entry |
| GET | `/api/knowledge/tabs/{id}/export` | Export knowledge tab as JSON |
| POST | `/api/knowledge/tabs/import` | Import knowledge tab from JSON |
| GET | `/api/knowledge/dedup` | Find near-duplicate knowledge entries |
| DELETE | `/api/knowledge/dedup/resolve` | Resolve/remove a duplicate entry |

### People

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/knowledge/people/scopes` | List people scopes |
| POST | `/api/knowledge/people/scopes` | Create scope |
| DELETE | `/api/knowledge/people/scopes/{name}` | Delete scope |
| GET | `/api/knowledge/people` | List people (in scope) |
| POST | `/api/knowledge/people` | Create/update person |
| DELETE | `/api/knowledge/people/{id}` | Delete person |
| POST | `/api/knowledge/people/import-vcf` | Import vCard file |
| GET | `/api/knowledge/people/export` | Export people as JSON |
| POST | `/api/knowledge/people/import` | Import people from JSON |

### Goals

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/goals/scopes` | List goal scopes |
| POST | `/api/goals/scopes` | Create scope |
| DELETE | `/api/goals/scopes/{name}` | Delete scope |
| GET | `/api/goals` | List goals (filtered by scope/status) |
| GET | `/api/goals/{id}` | Get a single goal |
| POST | `/api/goals` | Create goal |
| PUT | `/api/goals/{id}` | Update goal |
| POST | `/api/goals/{id}/progress` | Add progress note |
| DELETE | `/api/goals/{id}` | Delete goal |

### Per-Chat Documents (RAG)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/chats/{name}/documents` | Upload document to chat |
| GET | `/api/chats/{name}/documents` | List chat documents |
| DELETE | `/api/chats/{name}/documents/{file}` | Remove document |

### Heartbeat (Scheduled Tasks)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/continuity/tasks` | List scheduled tasks |
| POST | `/api/continuity/tasks` | Create task |
| GET | `/api/continuity/tasks/{id}` | Get task |
| PUT | `/api/continuity/tasks/{id}` | Update task |
| DELETE | `/api/continuity/tasks/{id}` | Delete task |
| POST | `/api/continuity/tasks/{id}/run` | Run task now |
| GET | `/api/continuity/status` | Scheduler status |
| GET | `/api/continuity/activity` | Recent activity log |
| GET | `/api/continuity/timeline` | Upcoming schedule (future only) |
| GET | `/api/continuity/merged-timeline` | Past activity + future schedule with NOW marker |

### Daemon Events

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/events/sources` | List daemon event sources from loaded plugins |
| POST | `/api/events/emit/{source}` | Emit a daemon event to trigger matching tasks |

### Backup

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/backup/list` | List backups |
| POST | `/api/backup/create` | Create backup |
| DELETE | `/api/backup/delete/{name}` | Delete backup |
| GET | `/api/backup/download/{name}` | Download backup archive (tar.gz) |
| GET | `/api/backup/health` | Backup trust at a glance: scheduler liveness, newest backup age, sentinel halt |
| POST | `/api/backup/estimate` | Estimate backup size with the given exclude patterns (per-folder breakdown) |
| POST | `/api/backup/restore` | Restore an existing backup over user/ (validates, decrypts if needed, restarts) |
| POST | `/api/backup/restore-upload` | Restore from an uploaded backup file |
| GET | `/api/backup/restore-result` | Outcome of the last restore, for the post-reboot banner |
| DELETE | `/api/backup/restore-result` | Dismiss the restore-result banner |

### Agents

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/agents/status` | List agents (?chat=name to filter) |
| GET | `/api/agents/providers` | List available LLM providers for agents |
| POST | `/api/agents/{id}/dismiss` | Dismiss an agent |

### Workspace Runner

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/workspace/run` | Run a command in a workspace project |
| POST | `/api/workspace/stop` | Stop a running workspace process |
| GET | `/api/workspace/status` | Get status of all running workspaces |

### Plugins — Listing & Lifecycle

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/webui/plugins` | List all plugins (loaded, enabled, manifest info) |
| PUT | `/api/webui/plugins/toggle/{name}` | Enable/disable a plugin (live load/unload) |
| POST | `/api/plugins/rescan` | Discover newly added plugins without restart |
| POST | `/api/plugins/{name}/reload` | Hot-reload a plugin (unload + load) |
| GET | `/api/plugins/{name}/check-deps` | Check a plugin's pip dependencies |
| POST | `/api/plugins/{name}/install-deps` | Install a plugin's declared pip dependencies |
| PUT | `/api/plugins/{name}/surfaces` | Set where the plugin's presence injections show up |
| POST | `/api/plugins/{name}/build-env` | Build (or rebuild) a plugin's dedicated conda env in the background |
| GET | `/api/plugins/{name}/env-status` | Env build/readiness status + build log tail (UI polls this) |
| DELETE | `/api/plugins/{name}/env` | Remove a plugin's conda env (services stop; rebuild any time) |

### Plugins — Install & Uninstall

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/plugins/install` | Install plugin from GitHub URL or zip upload |
| DELETE | `/api/plugins/{name}/uninstall` | Uninstall user plugin (unload + delete) |
| GET | `/api/plugins/{name}/check-update` | Check for updates from install source |
| POST | `/api/plugins/{name}/revert` | Swap a plugin back to the version retained by its last update |

### Store (read-only proxy of sapphireblue.dev catalog)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/store/status` | Store reachability + cache info |
| GET | `/api/store/categories` | List plugin categories with counts |
| GET | `/api/store/plugins/list` | List/search store plugins (`q`, `category`, `featured`, `sort`, `page`, `per_page`) |
| GET | `/api/store/plugins/{slug}` | Single plugin detail (description, screenshots, version, author) |
| GET | `/api/store/personas/list` | List/search store personas (same query params as plugins) |
| GET | `/api/store/personas/categories` | Persona categories with counts |
| GET | `/api/store/personas/{slug}` | Detail page for one persona |
| POST | `/api/store/personas/{slug}/install` | Download a persona's PNG card and import it (same overwrite flags as import-card) |

### Dashboard

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/dashboard/system-info` | Mem usage, thread count, uptime, disk stats, display name |
| GET | `/api/dashboard/component-status` | TTS/STT/wakeword/LLM readiness states |
| GET | `/api/dashboard/widgets` | List active widget panels for the user's dashboard |
| PUT | `/api/dashboard/widgets` | Save the user's panel layout (order, sizes, settings) |
| GET | `/api/dashboard/widgets/available` | List widgets registered by enabled plugins |

### Plugin Settings

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/webui/plugins/{name}/settings` | Get plugin settings |
| PUT | `/api/webui/plugins/{name}/settings` | Save plugin settings |
| DELETE | `/api/webui/plugins/{name}/settings` | Reset plugin settings |
| GET | `/api/webui/plugins/config` | Get plugin config metadata |

### Apps, Games & Themes

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/apps` | List plugin apps (plugins with an app/ directory) |
| GET | `/api/games` | List games registered via `capabilities.games` (empty unless game plugins are enabled) |
| GET | `/api/themes` | List all themes (core + plugin manifest themes) |
| GET | `/api/motions` | List motion themes (animation layer) |

### Scene Backgrounds

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/backgrounds` | List the scene library (`[{name, url, thumb}]`) |
| GET | `/api/backgrounds/{name}` | Serve a scene image (`?thumb=1` for thumbnail) |
| POST | `/api/backgrounds` | Upload a scene (re-encoded to webp, full + thumb) |
| DELETE | `/api/backgrounds/{name}` | Delete a scene (full + thumb) |

### Fonts

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/fonts` | Font registry + downloaded state (Visual tab's Type cards) |
| GET | `/api/fonts/file/{family}` | Serve a downloaded font file |
| POST | `/api/fonts/download` | Fetch a pinned font (SOCKS-aware), verify sha256, install atomically |

### Video Guide

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/videos` | Multi-channel video feed for the in-app Video Guide (cached; `?refresh=1` re-fetches) |

### Home Assistant Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/webui/plugins/homeassistant/defaults` | Get HA default settings |
| POST | `/api/webui/plugins/homeassistant/test-connection` | Test HA connection |
| POST | `/api/webui/plugins/homeassistant/test-notify` | Test HA notification |
| PUT | `/api/webui/plugins/homeassistant/token` | Save HA token |
| GET | `/api/webui/plugins/homeassistant/token` | HA token status |
| POST | `/api/webui/plugins/homeassistant/entities` | Fetch HA entities |

### Image Generation Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/webui/plugins/image-gen/test-connection` | Test image gen connection |
| GET | `/api/webui/plugins/image-gen/defaults` | Get image gen defaults |

### Email Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/webui/plugins/email/credentials` | Get email credentials |
| PUT | `/api/webui/plugins/email/credentials` | Save email credentials |
| DELETE | `/api/webui/plugins/email/credentials` | Remove email credentials |
| POST | `/api/webui/plugins/email/test` | Test email connection |
| GET | `/api/email/accounts` | List email accounts (multi-scope) |
| PUT | `/api/email/accounts/{scope}` | Set email account for scope |
| DELETE | `/api/email/accounts/{scope}` | Remove email account for scope |
| POST | `/api/email/accounts/{scope}/test` | Test email account |

### GitHub Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/github/accounts` | List GitHub accounts (multi-scope) |
| PUT | `/api/github/accounts/{scope}` | Set GitHub account (PAT) for scope |
| DELETE | `/api/github/accounts/{scope}` | Remove GitHub account for scope |

### Bitcoin Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/bitcoin/wallets` | List bitcoin wallets (multi-scope) |
| PUT | `/api/bitcoin/wallets/{scope}` | Set wallet for scope |
| DELETE | `/api/bitcoin/wallets/{scope}` | Remove wallet for scope |
| POST | `/api/bitcoin/wallets/{scope}/check` | Check wallet balance |
| GET | `/api/bitcoin/wallets/{scope}/export` | Export wallet details |

### Google Calendar Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/gcal/accounts` | List Google Calendar accounts (multi-scope) |
| PUT | `/api/gcal/accounts/{scope}` | Set GCal account for scope |
| DELETE | `/api/gcal/accounts/{scope}` | Remove GCal account for scope |

### SSH Plugin

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/webui/plugins/ssh/servers` | Get configured SSH servers |
| PUT | `/api/webui/plugins/ssh/servers` | Replace SSH servers list |
| POST | `/api/webui/plugins/ssh/test` | Test SSH connection |

### Avatars

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/avatars` | Get avatar paths for user/assistant |
| POST | `/api/avatar/upload` | Upload avatar (max 4MB) |
| GET | `/api/avatar/check/{role}` | Check if avatar exists for role |
| GET | `/api/avatar/{filename}` | Serve avatar file |

### Body (Multi-Body Runtime)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/body/wake` | Trigger a wake event on a registered body |
| GET | `/api/body/health` | Body runtime health/status |
| GET | `/api/body/events` | SSE stream of body/avatar events |

### Conversation Mode

| Method | Endpoint | Purpose |
|--------|----------|---------|
| WS | `/ws/conversation` | Browser conversation-mode WebSocket — connecting starts the mode, disconnecting ends it (session-cookie auth only) |
| GET | `/api/runtime/true-speech` | Current true-speech (conversation) mode state — ephemeral, for UI load-state |
| PUT | `/api/runtime/true-speech` | Enter/exit true-speech mode (continuous listen, no wakeword) |

### Setup Wizard

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/setup/provider-status` | Check STT/TTS provider readiness |
| GET | `/api/setup/check-packages` | Check optional package installation status |
| GET | `/api/setup/wizard-step` | Get current wizard step |
| PUT | `/api/setup/wizard-step` | Set wizard step |

### Metrics (Token Usage)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/metrics/enabled` | Check if metrics tracking is enabled |
| PUT | `/api/metrics/enabled` | Toggle metrics tracking |
| GET | `/api/metrics/summary` | Aggregate token usage summary (?days=30) |
| GET | `/api/metrics/breakdown` | Usage broken down by model (?days=30) |
| GET | `/api/metrics/daily` | Daily usage for charting (?days=30) |

### System Updates & Integrity

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/system/update-check` | Check for Sapphire updates |
| POST | `/api/system/update` | Apply update |
| DELETE | `/api/system/update` | Cancel a scheduled update that hasn't applied yet |
| GET | `/api/system/last-update-result` | Result of the most recent deferred update attempt (read-once, then cleared) |
| GET | `/api/system/integrity` | Verify the core install against the shipped manifest (SHA256) |
| POST | `/api/system/integrity/repair` | Restore files that don't match the manifest (git installs), per-file status |
| POST | `/api/system/restart` | Restart Sapphire |
| POST | `/api/system/shutdown` | Shutdown Sapphire |

### API Tokens

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/system/api-tokens` | List programmatic API tokens |
| POST | `/api/system/api-tokens` | Create a named API token |
| DELETE | `/api/system/api-tokens/{token_id}` | Revoke an API token |
| POST | `/api/system/password` | Change the login password (`{current, new}`; verifies the current one, 5/min; sessions survive; rotates the legacy X-API-Key hash) |

### Media (Tool-Generated Images)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/tool-image/{id}` | Serve tool-generated image |
| GET | `/api/sdxl-image/{id}` | Serve SDXL-generated image |

### Docs

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/docs` | List documentation tree |
| GET | `/api/docs/search` | Search across all docs (?q=query) |
| GET | `/api/docs/{path}` | Get raw markdown content of a doc |

### Static Assets & Plugin Web

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/plugin-web/{name}/{path}` | Serve plugin web/app assets |
| GET | `/workspace/{project}/{path}` | Serve Claude Code workspace files |
| GET | `/cdn-cache/{path}` | Serve locally cached third-party assets |

---

## Reference for AI

Sapphire API reference for programmatic access. Single FastAPI server, `https://localhost:8073`; every endpoint is auth-gated.

AUTH:
- Browser: session cookie via /login (30-day sessions; CSRF token required on mutations)
- Programmatic (preferred): `Authorization: Bearer <token>` — named API tokens minted at Settings > System > API Keys or POST /api/system/api-tokens (full value shown ONCE at creation; list shows last-4; revoke via DELETE /api/system/api-tokens/{token_id})
- Programmatic (legacy): `X-API-Key` header carrying the bcrypt password hash from the secret_key file
- Bearer and X-API-Key both bypass CSRF
- /ws/conversation accepts session-cookie auth only (same-origin enforced)
- Rate limit: 5 attempts/60s per IP on auth endpoints

ROUTE MODULES (core/routes/):
- chat.py: chat + stream (SSE; one turn per chat — 409 if a turn is already live), cancel, events SSE, health/status/init, history editing, chat sessions + lifecycle (rename, archive, trim, repair, compress + status, bulk delete/clear/export, single export, prompt-preview)
- content.py: prompts, prompt components, piece trash/rename/usage, toolsets, functions, spices, spice sets, personas (PNG character-card export.png / import-card, legacy JSON import)
- settings.py: settings CRUD, credentials, SOCKS proxy + /api/socks/status, LLM providers + test + test-thinking, custom providers, presets, TTS/STT/embedding provider registries, system status, system prompt (read-only)
- system.py: backup suite (list/create/delete/download tar.gz/health/estimate/restore/restore-upload/restore-result), audio devices, continuity tasks, setup wizard, avatars, restart/shutdown, update + cancel + last-update-result, integrity verify + repair, metrics, api-tokens, daemon events, dashboard system-info + component-status, runtime true-speech GET/PUT
- plugins.py: plugin listing/toggle/rescan/reload/surfaces, install/uninstall/revert/check-update, deps check/install, per-plugin conda envs (build-env, env-status, env DELETE), apps/games/themes/motions, plugin settings, HA/image-gen/email/bitcoin/gcal/github/ssh routes
- knowledge.py: embedding test/integrity/reembed, memory, goals, knowledge tabs/entries/dedup, people, per-chat RAG documents, export/import
- tts.py: TTS generate/stream/preview/status/stop/test, voices, transcribe, mic active, STT VAD status/test, image upload
- agents.py: agent status/providers/dismiss, workspace run/stop/status
- vault.py: privacy vault setup/unlock/lock/rekey/move
- conversation.py: /ws/conversation WebSocket (browser conversation mode — connect starts, disconnect ends)
- store.py: read-only store proxy — plugins AND personas (status, categories, list/search, detail, persona install)
- dashboard.py: dashboard widget layout + available widgets
- backgrounds.py: scene background library (list/serve/upload/delete, webp)
- fonts.py: font registry, serve, pinned sha256-verified download
- videos.py: in-app Video Guide feed (cached)
- media.py: tool-image, sdxl-image serving
- docs.py: doc tree, search, markdown content
- body.py: multi-body runtime (wake, health, events SSE)
Plus app-level routes in core/api_fastapi.py: /login, /logout, /setup, /api/avatar/{filename}, /plugin-web/{name}/{path}, /workspace/{project}/{path}, /cdn-cache/{path}.

KEY ENDPOINTS:
- GET /api/status — unified UI state (prompt, context, spice, streaming, TTS/STT readiness, vault state)
- GET /api/init — mega endpoint (all toolsets, prompts, personas, spices, settings in one call)
- POST /api/chat/stream — SSE streaming chat response (409 on a busy chat)
- GET /api/events — SSE event stream for real-time UI updates

CHAT FLOW:
1. POST /api/chat or /api/chat/stream with {"text": "message", "chat_name": "optional"}
2. Response streams as SSE events (content, tool_pending, tool_start, tool_end, reload)
3. POST /api/cancel to abort; a second stream on the same busy chat returns 409

PLUGIN MANAGEMENT:
- POST /api/plugins/install — GitHub URL or zip upload
- DELETE /api/plugins/{name}/uninstall — user plugins only
- POST /api/plugins/{name}/revert — roll back to the version retained by the last update
- POST /api/plugins/{name}/reload — hot-reload
- POST /api/plugins/rescan — discover new plugins
- PUT /api/webui/plugins/toggle/{name} — live enable/disable

MULTI-ACCOUNT CREDENTIALS (email/gcal/github):
- GET /api/{type}/accounts — list all scoped accounts
- PUT /api/{type}/accounts/{scope} — set account for scope
- DELETE /api/{type}/accounts/{scope} — remove
- Bitcoin differs: /api/bitcoin/wallets[/{scope}] (+ /check, /export) — wallets, not accounts

COMMON PATTERNS:
- Scoped endpoints use ?scope=name query param
- File uploads use multipart/form-data
- Toolsets: /api/toolsets (not /api/abilities — legacy name removed)
- Most endpoints return JSON
- 200/201 success, 400 validation, 403 auth/CSRF, 404 not found, 409 conflict (busy chat, existing vault), 503 system not ready
