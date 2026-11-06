# MCP Server

Sapphire can be an MCP server: other AI apps (Claude Code, another Sapphire, any MCP client) connect to her over the Model Context Protocol. They can ask her things and get her answers, and use the tools you choose to share. A key bound to a persona goes further: it can also speak through that persona's voice, ring a chime, listen on the microphone, and keep that persona's own memory.

This is the door *into* Sapphire. For Sapphire using *other* MCP servers, see the **MCP Client** plugin (Settings → Plugins).

---

## Turning it on

1. **Settings → MCP Server**: switch on **Serve her tools over MCP at /mcp** (`MCP_SERVER_ENABLED`, off by default). While it's off, `/mcp` answers 404.
2. **Settings → System → API Keys**: add a key. Pick a **persona** for it if the client should speak as someone (below). The full key is shown **once**, so copy it then.
3. Tick the tools to share under **Tools shared over MCP** (`MCP_SERVER_TOOLS`). Hidden tools, and tools from plugins that keep their own data scopes, are never offered.

Give each client its own key, so you can revoke one without touching the others.

## What a client gets

**Every key:**

| Tool | What it does |
|---|---|
| `ask` | Send her a message in a named chat and wait for her answer (up to 5 minutes; the chat is made if missing). Private chats are refused |
| `tell` | Same, without waiting |
| the tools you ticked | Run as if she called them |

**A key bound to a persona** also gets, as that persona (its voice, its memory scope; the client can't pick another):

| Tool | What it does |
|---|---|
| `speak` | Say something aloud on this machine's speakers, in the persona's voice (2,000 characters max) |
| `ding` | A chime, to get your attention |
| `listen` | Hear you through this machine's microphone (3–60 s). Only with **Let persona keys listen** (`MCP_SERVER_MIC`, off). Sapphire says aloud that it's listening, then chimes |
| `memory_save` / `memory_search` / `memory_recent` / `memory_delete` | The persona's own memory, when it has its own memory scope |
| `room_read` / `room_wait` / `room_post` | Its seat in a chat room here, when it's seated "at the door" ([CHAT-ROOM.md](CHAT-ROOM.md)) |

A persona key works **only** at `/mcp`; on the rest of the API it gets a 401.

**Two Sapphires never loop:** a question that reached her from another Sapphire can't ask one back.

## Connecting Claude Code (or any stdio client)

Sapphire's own certificate is self-signed, which most MCP clients refuse over HTTP. The included **stdio bridge** handles it:

```json
"mcpServers": {
  "sapphire": {
    "command": "python3",
    "args": ["/path/to/sapphire/tools/mcp-bridge.py"]
  }
}
```

- The bridge reads the key from `user/mcp-persona.token` by default. Or set `SAPPHIRE_MCP_TOKEN`, or `SAPPHIRE_MCP_TOKEN_FILE` to another file (two clients on one machine need a key each).
- For a Sapphire on another machine: `SAPPHIRE_BASE=https://her-host:8073`, and copy her certificate here and name it in `SAPPHIRE_MCP_CA`. It's pinned, so only that certificate verifies.
- The bridge survives her restarts: it keeps the last tool list and answers "try again" while she's away.

A direct HTTP connection (`claude mcp add --transport http sapphire https://host:8073/mcp --header "Authorization: Bearer <key>"`) only works behind a trusted certificate or reverse proxy.

To try a tool by hand: `bash tools/mcp-persona.sh <tool> '<json args>'`.

## Another Sapphire

Two Sapphires talk over this door. Add one as a device (**System → Devices → Another Sapphire**, see [DEVICES.md](DEVICES.md)) with a key from her, and yours can ask or tell her, or use her shared tools. **This Sapphire's name on your network** (`INSTANCE_NAME`, on the MCP Server tab) is what the other one sees.

**Chat-room invites** are also on the MCP Server tab: whether she accepts them, which chat she decides in, and which persona she joins as. See [CHAT-ROOM.md](CHAT-ROOM.md#being-the-guest).

## Protocol notes

Streamable HTTP, tools only: one `POST /mcp`, JSON answers, no session. Methods `initialize`, `ping`, `tools/list`, `tools/call`; batches accepted, notifications answer 202. `GET /mcp` is 405 (no server stream), `DELETE /mcp` is 200. Protocol version `2025-06-18`. `GET /api/health` is open and shows `name` and `mcp: true` when the door is on. `GET /api/mcp/tools` lists what's shareable.

## Privacy

- `ask` and `tell` refuse private chats.
- `listen` is refused in a private chat that uses cloud speech-to-text.
- Shared tools run with this machine's permissions. Share only what you'd let the client do.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/mcp` answers 404 | Turn the door on: Settings → MCP Server |
| 401 everywhere but `/mcp` with a persona key | Expected: persona keys only work at `/mcp`. Use a plain key for the API |
| Claude Code won't connect over HTTP | Use the stdio bridge (self-signed certificate) |
| `speak` / `memory_*` missing | The key has no persona, or the persona has no memory scope of its own |
| `listen` missing | Turn on Let persona keys listen |

---

## Reference for AI

MCP SERVER (core/mcp_server.py, core/mcp_persona.py, routes/mcp.py): door at POST /mcp, off by default (MCP_SERVER_ENABLED; off = 404). Auth = API key from Settings → System → API Keys (or a browser session). Streamable HTTP, tools only, protocol 2025-06-18.

TOOLS OFFERED: ask(text, chat, from) waits ≤300 s in that chat's inbox (chat created if missing; private chats refused); tell = no wait; plus MCP_SERVER_TOOLS (never hidden tools or tools of scope-registering plugins). Persona-bound key adds speak (persona voice, 2000 chars), ding, listen (only MCP_SERVER_MIC; 3-60 s; refused in private chat with cloud STT), memory_save/search/recent/delete (persona's own memory_scope, not default/global/none), room_read/room_wait(≤120 s)/room_post for a door seat. MCP_ROOM_INVITES adds room_invite/room_reset/room_update. Persona keys 401 outside /mcp.

LOOP GUARD: a question that came from another Sapphire can't ask one back.

CLIENT: stdio bridge tools/mcp-bridge.py (token user/mcp-persona.token | SAPPHIRE_MCP_TOKEN | SAPPHIRE_MCP_TOKEN_FILE; SAPPHIRE_BASE, SAPPHIRE_MCP_CA pin). Direct HTTP needs a trusted cert.

SETTINGS (Settings → MCP Server): MCP_SERVER_ENABLED, MCP_SERVER_TOOLS, MCP_SERVER_MIC, INSTANCE_NAME (name other Sapphires see; /api/health), MCP_ROOM_INVITES, MCP_ROOM_SHARE_TOOLS, MCP_ROOM_LOBBY, MCP_ROOM_PERSONA. Not the mcp_client plugin (that's you using other servers).
