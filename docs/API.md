# API Reference

Sapphire runs a single FastAPI server on port 8073 (HTTPS). Every endpoint below requires authentication — either a browser session or an API key.

## Authentication

### Browser Session
Log in at `/login` with your password. Sessions last 30 days.

### API Key (Programmatic Access)
For scripts or external tools, send your API key as a header:

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
CSRF tokens are required for browser sessions on POST/PUT/DELETE requests. API key auth **bypasses CSRF** — no extra headers needed.

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
| GET | `/api/modules` | List loaded modules |

### Chat

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/chat` | Send message, get response |
| POST | `/api/chat/stream` | Streaming SSE response |
| POST | `/api/cancel` | Cancel active stream |
| GET | `/api/events` | SSE event stream (real-time UI updates) |
| GET | `/api/history` | Get chat message history |

### Chat Sessions

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/chats` | List all chats |
| POST | `/api/chats` | Create new chat |
| POST | `/api/chats/private` | Create private chat |
| DELETE | `/api/chats/{name}` | Delete chat |
| POST | `/api/chats/{name}/activate` | Switch active chat |
| GET | `/api/chats/active` | Get active chat name |
| GET | `/api/chats/{name}/settings` | Get chat settings |
| PUT | `/api/chats/{name}/settings` | Update chat settings |

### Message History

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/history/remove-last` | Remove last message |
| POST | `/api/history/remove-from-assistant` | Remove from last assistant message |
| DELETE | `/api/history/tool-call/{id}` | Delete specific tool call |
| PUT | `/api/history/message/{index}` | Edit message at index |
| GET | `/api/history/export` | Export raw chat history |
| POST | `/api/history/import` | Import chat history |

### TTS / STT / Audio

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/tts` | Generate TTS audio |
| POST | `/api/tts/preview` | Preview voice sample |
| GET | `/api/tts/status` | TTS server status |
| POST | `/api/tts/stop` | Stop TTS playback |
| POST | `/api/transcribe` | Transcribe audio file |
| POST | `/api/mic/active` | Set web mic active state (suppresses wakeword) |
| POST | `/api/upload/image` | Upload image for chat |
| GET | `/api/audio/devices` | List audio devices |
| POST | `/api/audio/test-input` | Test input device |
| POST | `/api/audio/test-output` | Test output device |

### Settings

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/settings` | Get all settings |
| PUT | `/api/settings` | Update settings |
| PUT | `/api/settings/batch` | Batch update multiple settings |
| POST | `/api/settings/reload` | Force reload from disk |
| GET | `/api/settings/help` | Get setting descriptions |
| GET | `/api/settings/tiers` | Get hot vs restart-required status |
| GET | `/api/settings/chat-defaults` | Get chat default settings |
| PUT | `/api/settings/chat-defaults` | Update chat defaults |

### Credentials

| Method | Endpoint | Purpose |
|--------|----------|---------|
| PUT | `/api/credentials/llm/{provider}` | Set LLM API key |
| DELETE | `/api/credentials/llm/{provider}` | Remove LLM API key |
| POST | `/api/llm/test/{provider}` | Test LLM connection |
| GET | `/api/llm/providers` | List LLM providers |
| PUT | `/api/llm/providers/{key}` | Update provider config |

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

### Prompts

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/prompts` | List prompts |
| GET | `/api/prompts/{name}` | Get prompt details |
| POST | `/api/prompts` | Create prompt |
| PUT | `/api/prompts/{name}` | Update prompt |
| DELETE | `/api/prompts/{name}` | Delete prompt |
| POST | `/api/prompts/reload` | Reload from disk |
| POST | `/api/prompts/reset` | Reset to defaults |
| POST | `/api/prompts/merge` | Merge defaults into current |

### Toolsets

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/abilities` | List toolsets |
| GET | `/api/functions` | List available functions |
| POST | `/api/abilities` | Create toolset |
| PUT | `/api/abilities/{name}` | Update toolset |
| DELETE | `/api/abilities/{name}` | Delete toolset |
| POST | `/api/abilities/{name}/activate` | Set active toolset |

### Spices

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/spices` | List spice categories |
| POST | `/api/spices` | Create category |
| PUT | `/api/spices/{category}` | Update category |
| DELETE | `/api/spices/{category}` | Delete category |
| POST | `/api/spices/{category}/toggle` | Enable/disable category |
| POST | `/api/spices/reload` | Reload from disk |

### Spice Sets

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/spice-sets` | List spice sets |
| POST | `/api/spice-sets` | Create spice set |
| PUT | `/api/spice-sets/{name}` | Update spice set |
| DELETE | `/api/spice-sets/{name}` | Delete spice set |
| POST | `/api/spice-sets/{name}/activate` | Activate spice set |

### Memory

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/memory/scopes` | List memory scopes |
| POST | `/api/memory/scopes` | Create scope |
| DELETE | `/api/memory/scopes/{name}` | Delete scope |
| GET | `/api/memory/list` | List memories (grouped by label) |
| PUT | `/api/memory/{id}` | Update memory |
| DELETE | `/api/memory/{id}` | Delete memory |

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

### Goals

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/goals/scopes` | List goal scopes |
| POST | `/api/goals/scopes` | Create scope |
| GET | `/api/goals` | List goals (filtered by scope/status) |
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

### Story Engine

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/story/presets` | List available presets |
| POST | `/api/story/start` | Start story on a chat |
| GET | `/api/story/{chat}` | Get current state |
| POST | `/api/story/{chat}/set` | Update state variable |
| POST | `/api/story/{chat}/reset` | Reset to preset defaults |
| GET | `/api/story/{chat}/history` | State transition log |
| GET | `/api/story/saves/{preset}` | List saved games |
| POST | `/api/story/saves/{preset}` | Save game state |

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
| GET | `/api/continuity/timeline` | Upcoming schedule |
| GET | `/api/continuity/activity` | Recent activity log |

### Backup

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/backup/list` | List backups |
| POST | `/api/backup/create` | Create backup |
| DELETE | `/api/backup/{name}` | Delete backup |
| GET | `/api/backup/{name}/download` | Download backup zip |

### Plugin Settings

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/webui/plugins/{name}/settings` | Get plugin settings |
| PUT | `/api/webui/plugins/{name}/settings` | Save plugin settings |
| POST | `/api/webui/plugins/homeassistant/test-connection` | Test HA connection |
| GET | `/api/webui/plugins/homeassistant/token` | HA token status |
| PUT | `/api/webui/plugins/homeassistant/token` | Save HA token |

### System

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/system/restart` | Restart Sapphire |
| POST | `/api/system/shutdown` | Shutdown Sapphire |
| GET | `/api/privacy` | Get privacy mode status |
| PUT | `/api/privacy` | Toggle privacy mode |

---

## Reference for AI

Sapphire API reference for programmatic access.

AUTH:
- Browser: Session cookie via /login
- Programmatic: X-API-Key header with bcrypt hash from secret_key file
- API key bypasses CSRF
- Rate limit: 5 attempts/60s per IP

KEY ENDPOINTS:
- GET /api/status - unified UI state (prompt, context, spice, streaming, TTS/STT readiness)
- GET /api/init - mega endpoint (all toolsets, prompts, personas, spices, settings in one call)
- POST /api/chat/stream - SSE streaming chat response
- GET /api/events - SSE event stream for real-time UI updates

CHAT FLOW:
1. POST /api/chat or /api/chat/stream with {"text": "message", "chat_name": "optional"}
2. Response streams as SSE events (content, tool_pending, tool_start, tool_end, reload)
3. POST /api/cancel to abort

COMMON PATTERNS:
- Scoped endpoints use ?scope=name query param
- File uploads use multipart/form-data
- Most endpoints return JSON
- 200/201 success, 400 validation, 403 auth/CSRF, 404 not found, 503 system not ready
