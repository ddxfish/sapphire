# Game Room

The host for games and interactive stories with her. On by default. It ships
empty: games (Hold'em is `game-holdem`) and story packs arrive as their own
plugins and show up as tiles. Every session is a chat, so the chat is the
save: progress survives restarts, and deleting the session deletes the save.

Open it from the Chat group in the nav rail: **Chat > Game Room**. The full
guide is `docs/GAME-ROOM.md`.

## Tools

Story tools, for her inside a story chat:

| Tool | What it does |
|---|---|
| `story_act(verb, target?, answer?)` | Resolves one act the player chose (`move`, `search`, `solve`, `look`, `take`, or a verb an object declares). The engine referees; she narrates the verdict |
| `story_place(name, room?, desc?, verb?, response?, hidden?)` | Puts a new lasting object into a story room |
| `story_end()` | Closes this chat's story and restores the previous prompt. The journal is kept |
| `story_status()` | For plain chats: every open playthrough and the installed stories |

Games use no tools: in a hidden-information game she plays through a sealed
seat that sees only what her seat may see.

## Settings

Per game (⚙ on its tile), per session (the room sidebar, like any chat), and
per story (the story gear). No plugin-card settings.

## For authors

Games: `docs/plugin-author/games.md`. Stories: `docs/plugin-author/stories.md`.
