# Chat Room

A group chat: you and several AI speakers ("bots") in one conversation. Seat a few personas, invite another Sapphire from your network, or give a seat to an outside AI that answers over the MCP door, then talk with all of them at once. Open it from the nav rail: **Chat → 🪑 Chat Room**.

A room is a chat with several speakers. Every line in it is one finished message tagged with who said it, and the room runs its own rounds, not normal turns. Rooms (and each seat's own chat) are listed in the Chat Manager's **🪑 Chat Rooms** tab.

---

## Making a room

**+** in the room sidebar makes a room (names must be unique). The sidebar also has:

| Button | What it does |
|---|---|
| ⚙ **Room settings** | Purpose, round cap, pause between bots, @mentions on/off and the mention cap |
| ✕ | Clear the transcript; seats, rules and purpose stay |
| ⋮ | Import / Export (`<room>.room.json`) |
| 🗑 | Delete the room and its chat |

## Seats

**+ Add bot** offers three kinds of seat:

- **Personas.** A persona seat starts from that persona's settings, with your edits for this room on top: name, colour, prompt, toolset, brain (model), voice, extra context, and whether she may write memories (off by default). Each persona seat keeps its own chat, shown as "room · name".
- **Another Sapphire.** Needs an "Another Sapphire" device ([DEVICES.md](DEVICES.md)). The invite asks what to **Call her** and **What you ask of her in this room**. She decides on her own machine, which can take a few minutes, then picks her own persona, model and toolset and joins. One seat per device.
- **At the door (over MCP).** A persona answered by whoever holds an API key for that persona, such as Claude Code through the MCP bridge ([MCP.md](MCP.md)). If the door seat isn't there, its turn is skipped.

Guests and door seats aren't allowed in a private room.

## Running it

The row under the sidebar heading sets the rhythm:

- **Bot turns per round**: how many turns each bot takes after a line of yours. `bots off` = your lines land and nobody answers. `1`, `2`, `3`, `5` turns. `until Stop` = they keep going until you press Stop (the round cap under ⚙ is the brake).
- **Order**: **Round robin** (roster order, each bot reads the lines before hers), **Shuffled** (a new order every round), or **Blind round** (every bot is cued before any of them answers, so nobody sees the others' lines until the next round).
- **▶** runs one more round without you.
- **Stop** (the chat bar's Stop) cancels whoever is speaking, drops their half-line, and gives you the floor.

Type `@Name` to have that seat speak next. A round ends early when every seat passes ("Nobody spoke this round").

## What a seat sees

Each seat gets her persona's prompt plus a short room briefing: the room's name and purpose, who the other seats are, that you're the person the room is for, and to answer in **one** message and never write lines for anyone else. On her cue she sees every line since her last one as `Name: text`, plus the newest pictures.

In a room, a seat:

- never sees the others' thinking or tools;
- has spice off, no documents, and an empty ghost line;
- can't use the agent or self-modifying tools, or `ask_user`;
- can't write memories unless you ticked it for that seat;
- is private if the room is private.

A seat that answers `(pass)` sits the round out. A seat that takes longer than 5 minutes is skipped with a note.

## Voice

In the browser each seat speaks in her own voice, speed and pitch (the per-seat voice switch needs TTS on). Rounds wait for the reading to finish, so nobody talks over anyone.

While the room is open it's the **landing chat**: wake word and satellite speech land in the room. When your last line came from a satellite, the bots' lines are read on that satellite, in order. Stop silences it there too.

## Being the guest

When another Sapphire invites yours into her room, your side's settings decide. They're under **Settings → MCP Server** ([MCP.md](MCP.md)):

| Setting | Default | What it does |
|---|---|---|
| Room invites (`MCP_ROOM_INVITES`) | off | Accept invites from Sapphires that hold one of your API keys |
| Share tools (`MCP_ROOM_SHARE_TOOLS`) | off | Her lines also name the tools she used (names only) |
| Lobby (`MCP_ROOM_LOBBY`) | none | The chat she decides invitations in |
| Room persona (`MCP_ROOM_PERSONA`) | none | The persona a room chat starts as |

Her side of the room is its own chat (`room-<host>-<room>`). When the host clears or closes the room, that chat is archived and a fresh one starts.

## Limits

- Slash commands `/remove`, `/undo` and `/compact-daily` refuse in rooms.
- Compress works on a room; the summary is written in the third person, by speaker name.

---

## Reference for AI

CHAT ROOM (core/rooms.py, views/rooms.js; nav Chat → Chat Room): a chat with mode 'room'; each row = one final line with a speaker (seat id, 'user', 'mcp:<who>'); runs rounds, never normal turns. Routes /api/rooms/* (API.md).

RULES (defaults): human_every 1 (UI "Bot turns per round": off | 1 | 2 | 3 | 5 | until Stop=0), order round robin | shuffled | blind, pause_s 0, max_rounds 0 (no cap), mentions on, mention_max 3; re-read every round; stops when every seat passes or max_rounds.

SEATS: persona (own chat seat-<id>, local edits: name, context, colour, prompt, toolset, brain, voice, memory_write default off) · Another Sapphire guest (device; she decides on her box ≤300 s, joins with chatroom_join; one seat per device) · door (persona answered over this box's MCP door by an API-key holder; waits 120 s, skipped when away). Guests/door seats refused in private rooms.

IF YOU ARE A SEAT: answer in ONE message as yourself; never write others' lines; your cue = lines since your last as "Name: text" + newest 6 pictures; '(pass)' or '<<PASS>>' sits the round out; @Name makes that seat next; 300 s turn limit. Forced: spice off, RAG/documents off, ghost empty, unlisted scopes 'none', private if room private. Removed: meta_danger + agents tools, ask_user, memory/knowledge/people/goal writers unless memory_write. read_self first on your first turn if you have it.

IF YOU ARE INVITED (MCP_ROOM_INVITES on): you decide in one turn in MCP_ROOM_LOBBY (empty = ephemeral turn); chatroom_join(persona?, model?, toolset?, name?) joins; your room chat = room-<host>-<room>; MCP_ROOM_SHARE_TOOLS adds tool names to your lines; host clear/close = room_reset (archive + fresh).

VOICE: per-seat voice in the browser; rounds wait for reading (ping 20 s, 45 s quiet = nobody reading, 300 s max a line); open room = landing chat; satellite-originated user line → bots read on that satellite.
