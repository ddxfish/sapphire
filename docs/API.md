# API Reference

Sapphire runs a single FastAPI server on port 8073 (HTTPS). Almost every endpoint needs a login: a browser session or an API token. The exceptions are listed under [Open routes](#open-routes).

Routes are split across modules under `core/routes/`, plus a few app-level routes in `core/api_fastapi.py` (pages, avatar files, plugin web assets, static mounts). Plugins add their own under `/api/plugin/{plugin}/…` and `/ws/plugin/{plugin}/…`.

## Authentication

### Browser Session
Log in at `/login` with your password. Sessions last 30 days. A password change logs out every other session.

### Named API Tokens (Programmatic Access)
For scripts, external tools, and integrations, mint a named token at **Settings > System > API Keys** (or `POST /api/system/api-tokens`) and send it as a Bearer header:

```bash
curl -k https://localhost:8073/api/status \
  -H "Authorization: Bearer $SAPPHIRE_TOKEN"
```

The full token value is shown **once** at creation — copy it then. Listing shows only the last 4 characters; each token is individually revocable (`DELETE /api/system/api-tokens/{token_id}`).

**Persona keys.** A token made with a `persona` works at `/mcp` only. Anywhere else it is refused with 401, so a persona key can never act as a full admin key. See [MCP.md](MCP.md).

### Open routes
No login needed:

| Route | Guard |
|-------|-------|
| `GET /api/health` | none; says only status, instance name, MCP on |
| `GET/POST/PUT /api/events/webhook/{path}` | the webhook task's own secret (`X-Webhook-Secret` or GitHub-style `X-Hub-Signature-256`), 1 MB cap |
| `GET /login`, `POST /login`, `GET /setup`, `POST /setup` | form CSRF field, 5 attempts per 60 s per IP |
| `GET /favicon.ico`, `/static/*`, `/user-assets/*` (`user/public/`), `/user-fonts/*`, `/core-widgets/*` | static files |
| The 11 device doors under `/api/devices/{id}/…` | the device's own key as Bearer (see [Devices](#devices)) |

### CSRF
A browser session sends `X-CSRF-Token` on every POST/PUT/PATCH/DELETE. It must equal the session's token, which the server stamps into the page it serves (there is no API to fetch it). A missing or wrong token is **403** "This page is out of date. Reload it and try again." `/login` and `/setup` carry it as the form field `csrf_token`. Bearer-token callers have no cookie and need no CSRF header.

### WebSockets
`/ws/conversation` and `/ws/plugin/{plugin}/{path}` accept the session cookie only (no Bearer), and the `Origin` must match `Host`. A refused handshake is accepted and closed with **4401**. Plugin sockets also close 4404 (no such socket), 4429 (that plugin already holds 8), 4410 (the plugin unloaded), 1011 (its handler crashed).

### Status codes
- **401**: no login on an `/api/*` route (or `/mcp`), or a persona key used off `/mcp`.
- **307**: no login on a page route redirects to `/login`; before setup is done, to `/setup`.
- **403**: CSRF failure, a wrong vault passphrase, a wrong webhook secret. Never "not logged in".

### Rate Limiting
`/login` and `/setup`: 5 attempts per 60 s per IP. Other buckets count per session (its CSRF token), else per IP, and answer **429**:

| Bucket | Limit per minute |
|--------|------------------|
| `/api/chat` + `/api/chat/stream` | 30 |
| `/api/tts`, `/api/tts/stream`, `/api/tts/preview` (shared) | 30 |
| `/api/transcribe` | 20 |
| `/api/stt/vad-test` | 10 |
| `/api/system/password` | 5 |
| Devices (login routes) | 240 reads, 60 writes, 1200 glass frames |
| IR codes, wake word library | 60 writes |
| Device doors | 30 per door per caller address; `text` reads and `history` 600 |
| Plugin routes `/api/plugin/*` | 60 GET, 30 other, per plugin per verb (`media: true` GETs unmetered) |

---

## Endpoints

### Core

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/health` | Health check, open. `name` when the instance has one, `mcp: true` when the MCP door is on |
| GET | `/api/status` | Unified UI state (prompt, context, spice, TTS/STT readiness) |
| GET | `/api/init` | Mega initialization (all toolsets, prompts, personas, spices, settings) |

### MCP (Model Context Protocol)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/mcp` | Streamable HTTP MCP server, tools only, JSON answers, no session. Off until Settings > MCP Server turns it on (404). Bearer API token (a persona key works here and only here) or the browser session |
| GET | `/mcp` | 405: the door opens no stream of its own |
| DELETE | `/mcp` | 200: there is no session to end |
| GET | `/api/mcp/tools` | Every tool with its state for the settings page: `{enabled, tools: [{name, description, module, plugin, emoji, scoped, exposed, own}]}` |

The door speaks `initialize`, `ping`, `tools/list` and `tools/call`; notifications get 202. It always offers two tools of its own: `ask` (`text`, `chat`, `from`: a turn in that chat, made if missing, her answer as the result) and `tell` (the same without waiting). While Settings > MCP Server takes room invitations (`MCP_ROOM_INVITES`) it adds `room_invite`, `room_reset` and `room_update`. The rest are the tools ticked in settings; tools of plugins that declare scopes are not offered yet. Clients: another Sapphire added as a device, or `claude mcp add --transport http sapphire https://host:8073/mcp --header "Authorization: Bearer <token>"`. Full guide: [MCP.md](MCP.md).

**Persona keys.** An API token made with a persona (`POST /api/system/api-tokens` with `persona`, or the picker under Settings > System > API Keys) is that persona at this door. The client never names a voice or a scope; both come from the persona. These tools are for the client outside and are not in her own toolkit:

| Tool | What it does |
|------|--------------|
| `speak` (`text`) | Says the words on this machine's speakers in the persona's voice, pitch and speed. Returns when said. Waits for her own voice to end, holds the wake word off, and never changes her voice |
| `ding` | Plays a short chime on this machine's speakers |
| `listen` (`seconds`, 3 to 60) | Offered only when `MCP_SERVER_MIC` is on. A chime, then this machine's microphone records until the speaker stops; returns `Heard: "..."`. The wake word's stream is closed for the length of it and brought back after. Refused during a voice turn of her own, a live conversation, or a private chat with a cloud transcriber |
| `memory_save`, `memory_search`, `memory_recent`, `memory_delete` | The persona's own memory scope and no other. Not offered when the persona's memory scope is `default`, `global` or unset: no key holds her scope |
| `room_read`, `room_wait`, `room_post` | A seat at a chat room's door here (a seat added with `door: true`): read the room, wait up to 120 s for the turn, post the line. See [CHAT-ROOM.md](CHAT-ROOM.md) |

`tools/mcp-bridge.py` is a stdio bridge to this door for clients that will not take a self-signed cert (Claude Code). It reads the token from `user/mcp-persona.token` (or `SAPPHIRE_MCP_TOKEN`, or the file named by `SAPPHIRE_MCP_TOKEN_FILE`, so two clients can each hold their own key), answers the handshake itself while she restarts, and serves the last tool list it saw.

### Chat

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/chat` | Send `{text}`, get `{response, notices?}`. Always the active chat: `chat` in the body is ignored (use the stream for a named chat) |
| POST | `/api/chat/stream` | Streaming SSE response. The first line is `{type:"ticket", ticket, chat}`. A send while a turn is live on that chat is **queued** in the chat's inbox, not refused: the body says `{type:"queued", ticket, chat, position}` and closes; the tab rejoins by ticket (`/api/chat/attach`) when the bus says `inbox_ticket`. Typed sends waiting together fold into one turn (`merged`); a dropped one hears `queued_dropped`. Optional `chat` addresses a chat BY NAME (a room's rail bound to its session): the active chat runs as before; any other chat runs pinned to its own stored settings, never the active pointer. Sealed or an inbox refusal → 409, missing → 404. `continue_from` (an assistant turn's timestamp) resumes the chat's last reply in place: no user row is sent or saved, the row is edited on success and untouched on Stop |
| POST | `/api/chat/attach` | Rejoin a turn after losing the feed (`{ticket?, chat?, since?, audio?}`). Turns are server-owned: the engine runs on its own thread and a viewer leaving never ends it. By **ticket** (the stream's first line names it): still queued → **202** `{state:"queued", position}`; live or just finished → replays the ring exact-once after `since` (every SSE line carries `seq`, first line `{type:"attached", audio}`), then follows live; **204** = it ran and is gone; 404 unknown ticket (a restart); 409 sealed chat. `audio: true` asks to hear her — granted only while nothing has been spoken yet. By chat alone: **204** = no live turn; 404 unknown chat; 409 sealed |
| POST | `/api/cancel` | Cancel active stream (`?chat=` scopes to one chat) |
| GET | `/api/events` | SSE event stream (real-time UI updates) |
| GET | `/api/history` | Get chat message history (`?chat=` reads a chat by name; absent = active) |
| GET | `/api/chat/queue` | What waits in a chat's inbox (`?chat=`, else active): ids, sources, lanes, ages, never text |
| POST | `/api/chat/queue/drop` | `{ticket, chat?}`: take a waiting typed turn out of the inbox (the live turn is Stop's job) |

### Perception & cadence

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/perception/{chat}` | `{frames: [{data, media_type}], text, source}`: what she should see next for `chat` (a game board, an mpv watcher). Latest wins; frames are model-only for the next unprompted turn, never persisted. 404 for an unknown or sealed chat |
| GET | `/api/cadence/{chat}` | The cadence record for `chat`: `armed, mode (event\|timer), paused, next_in, last_at, skips, pending, fired, every, pokes, ttl_in` (event mode fires on every Nth poke). Arming is the room host plugin's business (`/api/plugin/game-room/room/cadence/*`) |

### Slash Commands

See [SLASH-COMMANDS.md](SLASH-COMMANDS.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/commands` | The composer's command menu `{commands}` |
| POST | `/api/commands/run` | `{line}`: run one slash command on `?chat=` (else the active chat). Never enters the turn engine |

### Chat Rooms

A room is a chat with several speakers. Not a room → 404; a bad request → 400. See [CHAT-ROOM.md](CHAT-ROOM.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/rooms` | List rooms `{rooms}` |
| POST | `/api/rooms` | `{name, purpose?}`: make one; answers its description |
| GET | `/api/rooms/{chat}` | Purpose, rules, seats |
| PUT | `/api/rooms/{chat}` | Purpose and rules (`human_every`, `pause_s`, `max_rounds`, `bots_enabled`, `shuffle`) |
| POST | `/api/rooms/{chat}/seats` | `{persona, name?, door?}` seats a persona (`door: true` = answered at the MCP door by a key that speaks as her); `{device, name?, instructions?}` invites another Sapphire and waits for her decision (minutes) → `{joined, said, seat?}` |
| PUT | `/api/rooms/{chat}/seats/{seat_id}` | Change a seat |
| DELETE | `/api/rooms/{chat}/seats/{seat_id}` | Remove a seat |
| POST | `/api/rooms/{chat}/say` | `{text, images?}` (up to 8 `{data, media_type}`): the person's line, lands between seat turns → `{ticket, state}`; 409 when the inbox refuses |
| POST | `/api/rooms/{chat}/clear` | Wipe the transcript; seats, rules and purpose stay |
| POST | `/api/rooms/{chat}/import` | `{messages?, room?}` from a chat export |
| POST | `/api/rooms/{chat}/start` | Run one more round; `{rounds}` = N rounds before the human rule asks again |
| POST | `/api/rooms/{chat}/stop` | Stop the rounds |
| GET | `/api/rooms/{chat}/state` | The runner's state |
| POST | `/api/rooms/{chat}/voice` | The tab reading lines aloud: `{on}` while reading, `{mid}` when a line is read, `{flush}` when it went quiet. The rounds keep pace with it |

### Chat Sessions

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/chats` | List all chats |
| POST | `/api/chats` | Create new chat |
| GET | `/api/chats/search` | Search chat content (private chats excluded while the vault is sealed) |
| DELETE | `/api/chats/{name}` | Delete chat (also deletes its plugin/playthrough data) |
| POST | `/api/chats/{name}/activate` | Switch active chat |
| GET | `/api/chats/active` | `{active_chat, landing}`: the active chat, and where voice lands right now |
| POST | `/api/chats/{name}/landing` | `{on}`: where voice (wake word, satellites, browser conversation) lands while a room is open; `on: false` clears it. Typed turns stay on the active chat → `{landing, active_chat}`; 404 unknown chat |
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
| GET/PUT | `/api/chats/compress/instructions` | Read or save the compress instructions override |
| GET | `/api/chats/{name}/prompt-preview` | The exact system prompt + ghost envelope the next turn would send |

### Message History

| Method | Endpoint | Purpose |
|--------|----------|---------|
| DELETE | `/api/history/messages` | Remove messages (by count, user message, or clear all with count=-1) |
| POST | `/api/history/messages/remove-last-assistant` | Remove last assistant message |
| POST | `/api/history/messages/remove-from-assistant` | Remove from last assistant message onward |
| DELETE | `/api/history/tool-call/{id}` | Delete specific tool call |
| POST | `/api/history/messages/edit` | Edit a message. The message-edit routes act on `?chat=` by name when given (a room's rail), else the active chat |
| GET | `/api/history/raw` | Export raw chat history |
| POST | `/api/history/import` | Import chat history |

### TTS / STT / Audio

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/tts` | Generate TTS audio |
| POST | `/api/tts/stream` | Streaming TTS for known text, as SSE (`tts_stream_start`, `tts_chunk`, `tts_stream_end`). With streaming off or a provider that can't stream, the text is made whole first, then sent the same way. 503 only with TTS off or no engine: fall back to `/api/tts` |
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

Provider switches are jobs. `STT_PROVIDER`, `TTS_PROVIDER`, `EMBEDDING_PROVIDER`, `WAKE_WORD_ENABLED`, `WAKEWORD_MODEL` (and the legacy `STT_ENABLED` / `TTS_ENABLED` toggles, which map onto their provider key) come back as `"status": "started"` with a notification row id per key (`job`, and `jobs` on the batch). The switch runs in the background under that row; the key lands in config and on disk only when the provider is really ready; `settings_changed` fires on finish with the live value and `ok: true|false`. A request for a key already switching to a different value is refused with `409`. Save an API key or model size in the same batch as its provider and it is written first.

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
| GET | `/api/llm/generation-params` | `?provider=&model=`: the `temperature`, `top_p`, `max_tokens` a blank provider-card field inherits for that model |

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
| POST | `/api/vault/move-batch` | Move many at once (`{direction, items:[{kind:'prompt',name}|{kind:'piece',comp_type,key}]}`) — one request, per-item receipts, one change event; 409 if the vault is locked |

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

### Memory (legacy engine)

> The Memory, Knowledge, People and Goals tables below belong to the **legacy memory engine** (plugin `memory`) and read its classic databases. The default engine, the Mind Palace, serves its own routes under `/api/plugin/mindpalace/` (see its README in Help → Plugins and [MEMORY.md](MEMORY.md)).

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

### Knowledge (legacy engine)

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

### People (legacy engine)

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

### Goals (legacy engine)

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
| GET | `/api/chats/{name}/images-in-view` | Pictures she can still see, with turns left |
| POST | `/api/chats/{name}/images-in-view/{id}/hide` | Stop replaying a picture |

### Heartbeat (Scheduled Tasks)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/continuity/tasks` | List scheduled tasks |
| POST | `/api/continuity/tasks` | Create task |
| GET | `/api/continuity/tasks/{id}` | Get task |
| PUT | `/api/continuity/tasks/{id}` | Update task |
| DELETE | `/api/continuity/tasks/{id}` | Delete task |
| POST | `/api/continuity/tasks/{id}/run` | Run task now |
| POST | `/api/continuity/tasks/{id}/cancel` | Stop a task's in-flight run (the ⏹ on a running card). Ends at the next cancel point — between LLM rounds — and drops queued fires behind it; the task stays enabled. Toggling a task off cancels implicitly |
| GET | `/api/continuity/status` | Scheduler status |
| GET | `/api/continuity/activity` | Recent activity log |
| GET | `/api/continuity/timeline` | Upcoming schedule (future only) |
| GET | `/api/continuity/merged-timeline` | Past activity + future schedule with NOW marker |

### Daemon Events

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/events/sources` | List daemon event sources from loaded plugins |
| POST | `/api/events/emit/{source}` | Emit a daemon event to trigger matching tasks |
| GET/POST/PUT | `/api/events/webhook/{path}` | **No login.** Fires the webhook task matching path + method. A task with a secret needs `X-Webhook-Secret` or `X-Hub-Signature-256` (403 otherwise); 1 MB cap (413); 404 no such webhook → `{status:"triggered", task, queued}`. See [DAEMONS-WEBHOOKS.md](DAEMONS-WEBHOOKS.md) |

### Notifications

See [NOTIFICATIONS.md](NOTIFICATIONS.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/notifications` | Rows (active first, newest first) + counts; `?level=error,warning&source=piper&unread=true&limit=200` |
| POST | `/api/notifications` | A row from the browser `{title, body?, level?, source?, go?, key?}` (that tab painted its toast already) |
| POST | `/api/notifications/read` | Mark everything read (opening the page does this) |
| POST | `/api/notifications/{id}/read` | Mark one row read |
| DELETE | `/api/notifications/{id}` | Remove one row; 409 while it is active |
| DELETE | `/api/notifications` | Remove every row that is not active |

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
| POST | `/api/backup/send` | `{filename?}` (newest by default): send one backup, sealed, to every device that holds backups → `{status:"sending", filename, targets}`; 409 already sending. `/api/backup/health` says when it landed |
| PUT | `/api/backup/password` | `{password}` (`''` clears): THE backup password (encrypted backups, device/path targets, Remembrance). Stored machine-bound, outside `user/` → `{ok, status}` |

### Agents

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/agents/status` | List agents (?chat=name to filter) |
| GET | `/api/agents/kinds` | the agent kinds plugins declared (available, cloud, conversational, spawn_schema) |
| GET | `/api/agents/chats` | chats with agents (live / kept) — hidden chats left out |
| GET | `/api/agents/list?chat=` | one chat's agents: live (progress, pending question, transcript tail) + rows; chat-local, hidden = 404 |
| POST | `/api/agents/spawn` | `{chat, kind, mission, options}` — the privacy gate reads the chat's own settings |
| GET | `/api/agents/{id}/transcript?chat=` | an agent's transcript ring (chat-local) |
| POST | `/api/agents/{id}/answer` · `/say` · `/stop` | `{chat, value}` — resolve its question · a follow-up turn · end it |
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
| GET | `/api/system/extras` | The optional package sets core knows (`flash`, …): label, note, installed, the install job in flight |
| GET | `/api/system/extras/{name}` | One set's install state (`idle`/`running`/`done`/`failed`, pip's last lines) — `plugin:{plugin}` for a plugin's own set |
| POST | `/api/system/extras/{name}/install` | Install a set in the background (core/extras.py); one at a time; refused on hosted installs |
| POST | `/api/plugins/{name}/install-deps` | Install a plugin's declared packages (`pip_dependencies` and the optional `extra`) in the background through core's one pip engine; returns the job state, poll `GET /api/system/extras/plugin:{name}`; the plugin reloads when pip is done. conda/venv only; refused on hosted installs |
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
| POST | `/api/plugins/{name}/purge` | Delete everything the plugin stores about you (its state files + its per-chat data rows); it stays installed and configured. Sealed chats' rows are skipped and counted |

### Plugin Routes & Sockets

What a plugin declares in its manifest. See [plugin-author/routes.md](plugin-author/routes.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET/POST/PUT/DELETE | `/api/plugin/{plugin}/{path}` | The plugin's own HTTP routes. Session login, or a Bearer matching the key the plugin keeps in `user/plugin_state/{plugin}_mcp_key.json`. A non-object JSON body arrives as `{}`; 404 no such route |
| WS | `/ws/plugin/{plugin}/{path}` | The plugin's own WebSocket (`capabilities.sockets`). Session cookie + same origin; 8 per plugin; close codes under [WebSockets](#websockets) |

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
| PUT | `/api/apps/pins` | `{name, pinned}`: pin or unpin an app on the Apps menu; answers like `GET /api/apps`. 400 for an app with a place in the rail, 404 unknown |
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

### Devices

Login required, except the 11 doors a device opens with its own key (marked **Device key**: `Authorization: Bearer <device key>`; 401 says the same for a wrong key and an unknown device). Every Devices route answers 404 on a hosted install. See [DEVICES.md](DEVICES.md) and [Satellite Protocol](SATELLITE-PROTOCOL.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/devices` | Every device with its status, and the device types |
| POST | `/api/devices` | Add a device |
| GET | `/api/devices/{id}` | One device: settings, status, what it can do |
| PUT | `/api/devices/{id}` | Change a device. `locked` is a map `{capability: bool}` and changes only the capabilities it names |
| GET | `/api/devices/found/{driver}` | What that driver can see right now, for a pick list: `{found: [{id, name, kind}]}`. `?device=<id>` lends a saved device's settings to the look |
| POST | `/api/devices/found/{driver}/do` | `{id, do, config}`: a button under a pick-list entry (a phone's Pair), before it is a device |
| POST | `/api/devices/provision` | `{label, driver?, location?, mac?, replace?, sapphire?}`: a board set up from the browser: the device row, two fresh keys, Sapphire's LAN address and cert. The page carries them to the board over USB |
| GET | `/api/devices/here` | This machine's likely LAN addresses `{sapphire, addresses}` |
| DELETE | `/api/devices/{id}` | Remove a device and its stored secrets |
| POST | `/api/devices/{id}/connections` | `{driver, config}`: add a connection (a part) to a device |
| DELETE | `/api/devices/{id}/connections/{driver}` | Remove one connection |
| POST | `/api/devices/{id}/test` | Check the device now. Answers `{status, device}`: a device may have just said what it has |
| POST | `/api/devices/{id}/run` | Run one action. Answers `{text, ok, images?}` |
| GET | `/api/devices/{id}/screen/stream` | What the board's glass takes, who has claimed it, the stream's status |
| POST | `/api/devices/{id}/screen/frame` | Body = one JPEG frame for the glass (newest wins, paced to the board) |
| DELETE | `/api/devices/{id}/screen/stream` | End the cast |
| POST | `/api/devices/{id}/wake` | Device key. Its wake word fired: may it take this one? → `{yours}` |
| POST | `/api/devices/{id}/voice` | Device key. A satellite sends what it heard, as the body with an `audio/*` Content-Type or as the form file `audio` |
| POST | `/api/devices/{id}/room` | Device key. A piece of room audio during a capture; headers `X-Capture`, `X-Chunk`, `X-Fires`, `X-Fire-Count`, `X-Test`, `X-Model`. `accepted: false` tells the board to stop |
| POST | `/api/devices/{id}/text` | Device key. Typed text (`text/plain` or `{text}`, 16 KB); her reply reaches the screen through `text` events |
| GET | `/api/devices/{id}/text` | Device key. A piece of her reply: `?msg=&from=&max=` (no `msg` = the latest) |
| POST | `/api/devices/{id}/press` | Device key. `{button, how: short\|long\|double, job?}`: a button press set to reach her |
| POST | `/api/devices/{id}/ask` | Device key. `{job: stop\|backup}`: work with no chat turn → `{ok, accepted, job, said}` |
| GET | `/api/devices/{id}/chats` | Device key. The chats it may pick from `{ok, chat, chats}`; a private chat is never named |
| POST | `/api/devices/{id}/chat` | Device key. `{name}`: the chat it talks in |
| GET | `/api/devices/{id}/history` | Device key. That chat read back: `?before=&max=`, newest first, with a cursor |
| GET | `/api/devices/{id}/events` | Device key. SSE stream of what that satellite's light and screen should show |

### Firmware & Flasher

Login, the Devices limits, 404 on a hosted install.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/devices/firmware` | `{boards, sources: [{source, official, on, error, boards}], error}` |
| PUT | `/api/devices/firmware/sources` | `{sources: [...], off: [...]}`: your firmware sources in order, and the ones switched off; 422 bad |
| POST | `/api/devices/firmware/own` | Multipart `file`: your own firmware, kept as board `own`; 413 too large |
| GET | `/api/devices/firmware/{board}/{path}` | One part's bytes (fetched and cached on first ask) |
| GET | `/api/devices/flash/ports` | Serial ports on this machine `{ports}` |
| POST | `/api/devices/flash/chip` | `{port}`: what chip is there |
| POST | `/api/devices/flash/start` | `{port, board}`: flash in the background, one at a time |
| GET | `/api/devices/flash/status` | `{state: idle\|getting\|writing\|done\|failed, percent, text, error}` |
| POST | `/api/devices/flash/ask` | `{port, line, wait?}` (1-60 s): send a line to the board's console → its answer |
| POST | `/api/devices/flash/close` | `{port}`: let go of the port |

### IR Codes

Writes 60/min; a library error is 400.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/ir/library` | `{appliances, brands}`: every appliance and its named codes, plus built-in brands |
| POST | `/api/ir/appliance` | `{name, brand?}`: a new, empty appliance |
| POST | `/api/ir/import` | `{brand, appliance?}`: seed an appliance from a brand's built-in codes |
| POST | `/api/ir/command` | `{appliance, command, protocol: "nec", addr, cmd}` (hex bytes): add or replace a code |
| DELETE | `/api/ir/library/{appliance}` | Remove an appliance |
| DELETE | `/api/ir/library/{appliance}/{command}` | Remove one command |

### Wake Word Library

Writes 60/min. See [VOICE.md](VOICE.md).

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/wakeword/library` | `{models, listeners, families}` (no file paths) |
| POST | `/api/wakeword/library` | Multipart `file` (`.onnx` or `.tflite`), `manifest?`, `name?`, `phrase?`, `overwrite?`; 409 name taken, 413 too large |
| DELETE | `/api/wakeword/library/{family}/{name}` | Remove; allowed while in use, answers `used_by` |
| GET | `/api/wakeword/library/{family}/{name}/file` | The model's bytes |
| POST | `/api/wakeword/switch` | `{listener, model}`: listener `this computer` or a device id; model a name, `''` or `own` |

### Shared Worker Doors

Who may use this computer's shared workers. A door is `kokoro`, or a loaded plugin with a client mode.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/worker-door/{door}` | `{clients, seen, address}` |
| POST | `/api/worker-door/{door}` | `{name, address?}` → `{name, pairing}`: the pairing string to paste on the client, shown once |
| DELETE | `/api/worker-door/{door}/{name}` | Remove a client |

### Conversation Mode

| Method | Endpoint | Purpose |
|--------|----------|---------|
| WS | `/ws/conversation` | Browser conversation-mode WebSocket — connecting starts the mode, disconnecting ends it (session-cookie auth only) |
| GET | `/api/runtime/true-speech` | Current true-speech (conversation) mode state — ephemeral, for UI load-state |
| PUT | `/api/runtime/true-speech` | Enter/exit true-speech mode (continuous listen, no wakeword) |

### Setup Wizard

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/setup/state` | Where setup stands: `complete`, the `step` to resume at, `consent` time, the `steps` that apply here, `managed`, `docker`, and `packages` (tts/stt/wakeword sets installed) |
| PUT | `/api/setup/state` | `{complete?: bool, step?: id, consent?: true}`; the server stamps the consent time; a bad step is a 400 |
| GET | `/api/setup/featured` | The plugins the wizard offers `{featured: [{name, group, title, blurb, description, icon, enabled, loaded, missing_deps, legacy}]}` |

The wizard itself is the `#setup` view: it hosts the real Settings tabs, so provider readiness and model downloads are the provider switch jobs and their notification rows (above). Provider status and package checks have no routes of their own any more.

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
| GET | `/api/system/gpu` | Whether torch can use the NVIDIA card: the boot probe's verdict, the `install` plan, `pending`, `last_install`, `whisper`. `?refresh=1` re-probes in the background |
| POST | `/api/system/gpu/install` | Queue the GPU torch install and restart (pip runs before Sapphire loads). 400 nothing to install, 503 no restart in this run mode |
| POST | `/api/system/restart` | Restart Sapphire |
| POST | `/api/system/shutdown` | Shutdown Sapphire |

### API Tokens

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/system/api-tokens` | List programmatic API tokens |
| POST | `/api/system/api-tokens` | Create a named API token. Optional `persona`: the persona it speaks as at the MCP door (400 when there is no such persona) |
| DELETE | `/api/system/api-tokens/{token_id}` | Revoke an API token |
| POST | `/api/system/password` | Change the login password (`{current, new}`; verifies the current one, 5/min, 10+ chars and ≤72 bytes). The calling tab stays logged in; **every other session is logged out**. Named bearer tokens are unaffected |

### Media (Tool-Generated Images)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/tool-image/{id}` | Serve tool-generated image |

### Docs

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/docs` | List documentation tree |
| GET | `/api/docs/search` | Search across all docs (?q=query) |
| GET | `/api/docs/{path}` | Get raw markdown content of a doc |

### Pages, Static Assets & Plugin Web

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/` | The app (login) |
| GET/POST | `/login` | Login page and form (**no login**; form CSRF field, 5/60 s per IP) |
| GET/POST | `/setup` | First-run password page and form (**no login**; same guards) |
| POST | `/logout` | End this session |
| GET | `/favicon.ico` | **No login** |
| GET | `/static/*`, `/user-assets/*`, `/user-fonts/*`, `/core-widgets/*` | **No login.** App files, `user/public/`, downloaded fonts, built-in dashboard widgets |
| GET | `/plugin-web/{name}/{path}` | Serve plugin web/app assets |
| GET | `/workspace/{project}/{path}` | Serve Claude Code workspace files |
| GET | `/cdn-cache/{path}` | Serve locally cached third-party assets |

---

## Reference for AI

Sapphire API reference for programmatic access. Single FastAPI server, `https://localhost:8073`; login-gated except the open routes below.

AUTH:
- Browser: session cookie via /login (30-day sessions). Mutations (POST/PUT/PATCH/DELETE) need header `X-CSRF-Token` = the session token stamped into the page; wrong/missing = 403
- Programmatic: `Authorization: Bearer <token>` — named API tokens minted at Settings > System > API Keys or POST /api/system/api-tokens (full value shown ONCE at creation; list shows last-4; revoke via DELETE /api/system/api-tokens/{token_id})
- Bearer auth needs no CSRF token
- Persona key (token made with `persona`): works ONLY at /mcp; 401 on every other route
- Open (no login): GET /api/health; /api/events/webhook/{path} (task's own secret); /login, /setup (form CSRF, 5/60s per IP); /favicon.ico; static mounts /static /user-assets /user-fonts /core-widgets; the 11 device doors /api/devices/{id}/{wake,voice,room,text,press,ask,chats,chat,history,events} (device key as Bearer)
- WebSockets /ws/conversation and /ws/plugin/{plugin}/{path}: session cookie only + Origin == Host; refused = close 4401
- 429 buckets per session/IP: chat 30/min, TTS 30/min, transcribe 20, plugin routes 60 GET/30 other, devices 240 reads/60 writes
- 401 = not logged in on /api/* (or persona key off /mcp); 307 = page route → /login (or /setup before setup); 403 = CSRF, vault passphrase, webhook secret

MCP DOOR (POST /mcp, off until Settings > MCP Server; see MCP.md):
- JSON-RPC: initialize, ping, tools/list, tools/call. Own tools: ask, tell; with MCP_ROOM_INVITES: room_invite, room_reset, room_update; plus the tools ticked in settings
- Persona key adds: speak, ding, listen (MCP_SERVER_MIC), memory_* on the persona's own scope, room_read/room_wait/room_post (a door seat in a room here)

ROUTE MODULES (core/routes/):
- chat.py: chat (active chat only) + stream (SSE; busy chat QUEUES), attach, queue + queue/drop, cancel, events SSE, health/status/init, history editing (?chat= by name), chat sessions + lifecycle (rename, archive, trim, repair, compress + status, bulk delete/clear/export, single export, prompt-preview, landing)
- content.py: prompts, prompt components, piece trash/rename/usage, toolsets, functions, spices, spice sets, personas (PNG character-card export.png / import-card, legacy JSON import)
- settings.py: settings CRUD, credentials, SOCKS proxy + /api/socks/status, LLM providers + test + test-thinking + generation-params, custom providers, presets, TTS/STT/embedding provider registries, system status, system prompt (read-only)
- system.py: extras install, GPU probe + install, backup suite (list/create/delete/download tar.gz/health/estimate/restore/restore-upload/restore-result/send/password), audio devices, continuity tasks, avatars, restart/shutdown, update + cancel + last-update-result, integrity verify + repair, metrics, api-tokens, password, daemon events + webhook (no login), dashboard system-info + component-status, runtime true-speech GET/PUT
- plugins.py: plugin listing/toggle/rescan/reload/surfaces, install/uninstall/revert/check-update/purge, deps check/install, per-plugin conda envs (build-env, env-status, env DELETE), apps + apps/pins, games/themes/motions, plugin settings, HA/email/bitcoin/gcal/github/ssh routes, the /api/plugin/{plugin}/{path} dispatcher, /ws/plugin/{plugin}/{path}
- knowledge.py: embedding test/integrity/reembed, memory, goals, knowledge tabs/entries/dedup, people, per-chat RAG documents, images-in-view, export/import
- tts.py: TTS generate/stream (SSE)/preview/status/stop/test, voices, transcribe, mic active, STT VAD status/test, image upload
- agents.py: agent status/kinds/chats/list/spawn/transcript/answer/say/stop/providers/dismiss, workspace run/stop/status
- notifications.py: notification center rows (list, post, read, delete)
- setup.py: setup wizard state + featured plugins
- perception.py: perception deposit, cadence status
- devices.py: devices, connections, provision, glass stream, firmware + flasher, the 11 device-key doors
- wakeword.py: wake word library + per-listener switch
- ir.py: IR code library
- mcp.py: /mcp door + /api/mcp/tools
- rooms.py: chat rooms (seats, say, rounds, voice pacing)
- commands.py: slash command menu + run
- worker_door.py: shared worker door clients (kokoro, plugin client modes)
- vault.py: privacy vault setup/unlock/lock/rekey/move
- conversation.py: /ws/conversation WebSocket (browser conversation mode — connect starts, disconnect ends)
- store.py: read-only store proxy — plugins AND personas (status, categories, list/search, detail, persona install)
- dashboard.py: dashboard widget layout + available widgets
- backgrounds.py: scene background library (list/serve/upload/delete, webp)
- fonts.py: font registry, serve, pinned sha256-verified download
- videos.py: in-app Video Guide feed (cached)
- media.py: tool-image serving
- docs.py: doc tree, search, markdown content
Plus app-level routes in core/api_fastapi.py: /, /login, /logout, /setup, /favicon.ico, /api/avatar/{filename}, /plugin-web/{name}/{path}, /workspace/{project}/{path}, /cdn-cache/{path}, and the static mounts /static, /user-assets, /user-fonts, /core-widgets.

KEY ENDPOINTS:
- GET /api/status — unified UI state (prompt, context, spice, streaming, TTS/STT readiness, vault state)
- GET /api/init — mega endpoint (all toolsets, prompts, personas, spices, settings in one call)
- POST /api/chat/stream — SSE streaming chat response (a busy chat queues the send; 409 only sealed chat or inbox refusal)
- GET /api/events — SSE event stream for real-time UI updates

CHAT FLOW:
1. POST /api/chat/stream with {"text": "message", "chat": "optional chat name"} — `chat` names the target chat; the active chat runs as before, another chat runs pinned to its own stored settings (never the active pointer). POST /api/chat ignores `chat` and runs the active chat, answering {response, notices?}
2. Response streams as SSE lines: first `ticket`, then the turn's lines, each carrying `seq` — stream_started, iteration_start, content, thinking, tool_pending, tool_start, tool_end, tool_result, image, file, notice, llm_done, reload, tts_* (plus attached, resync on a rejoin); the last line is {done}, {cancelled} or {error} (no type)
3. POST /api/cancel (?chat= to scope) to abort; a send on a busy chat is QUEUED in the chat's inbox — the body says `queued {ticket, chat, position}` and closes, the bus says `inbox_ticket {ticket, state}` when it starts; waiting typed sends fold into one turn (`merged`); a dropped one hears `queued_dropped`. Turns are server-owned: dropping the SSE socket does NOT cancel — POST /api/chat/attach {ticket, since?} rejoins your turn (202 still queued, 204 = it already finished). GET /api/chat/queue?chat= peeks the inbox; POST /api/chat/queue/drop {ticket} removes a waiting turn
4. GET /api/history?chat=<name> reads any chat by name; GET /api/chats?kind=game&slim=1 lists game/story sessions with trimmed settings; POST /api/chats {"name", "settings"} stamps settings at birth (private_chat refused there — use the vault flip)

PLUGIN MANAGEMENT:
- POST /api/plugins/install — GitHub URL or zip upload
- DELETE /api/plugins/{name}/uninstall — user plugins only
- POST /api/plugins/{name}/revert — roll back to the version retained by the last update
- POST /api/plugins/{name}/reload — hot-reload
- POST /api/plugins/rescan — discover new plugins
- PUT /api/webui/plugins/toggle/{name} — live enable/disable
- POST /api/plugins/{name}/purge — delete the plugin's stored user data, keep it installed
- Plugin routes: GET/POST/PUT/DELETE /api/plugin/{plugin}/{path} (manifest-declared; session, or Bearer matching user/plugin_state/{plugin}_mcp_key.json); sockets WS /ws/plugin/{plugin}/{path} (8 per plugin; close 4401/4404/4429/4410/1011)

DEVICES (see DEVICES.md, SATELLITE-PROTOCOL.md):
- Login routes under /api/devices (list, add, provision, connections, test, run, glass screen stream/frame, firmware, flash); 404 on a hosted install
- Device doors take the DEVICE's key as Bearer, no login: wake, voice, room, text (POST + GET), press, ask, chats, chat, history, events (SSE)

ROOMS (see CHAT-ROOM.md):
- /api/rooms CRUD, /seats (persona, door seat, or invite another Sapphire by device), /say, /start, /stop, /clear, /import, /state, /voice
- POST /api/chats/{name}/landing {on} — where voice lands while a room is open

WEBHOOKS (see DAEMONS-WEBHOOKS.md):
- GET/POST/PUT /api/events/webhook/{path} — no login; fires the matching webhook task; secret via X-Webhook-Secret or X-Hub-Signature-256; 1 MB cap

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
- 200/201 success, 202 queued/notification, 204 nothing live, 400 validation, 401 not logged in, 403 CSRF/vault passphrase/webhook secret, 404 not found (or hidden/sealed), 409 conflict (sealed chat, inbox refusal, existing vault, switch in flight), 413 too large, 429 rate limited, 503 system not ready
- Perception: POST /api/perception/{chat} {frames, text, source} deposits what she sees next; GET /api/cadence/{chat} reads the cadence record
- Slash commands: GET /api/commands, POST /api/commands/run {line} (?chat=); see SLASH-COMMANDS.md
