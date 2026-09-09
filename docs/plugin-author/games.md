# Game Plugins

Ship a playable game as a plugin. The **Game Room** (system plugin `game-room`) is the host: it owns the Surface room, sessions, the **real chat rail** (table talk IS the chat), per-game settings, and Sapphire's sealed seat for moves. Your plugin brings the game — an engine, a board module, and one manifest declaration. Core carries zero game code; if the host is disabled, your registration just holds harmlessly.

Reference implementations: **`plugins/game-holdem`** (turn game, sealed seat — ships with the app) and **game-darkhorse** (free-mount real-time game — lives in its own external repo and installs like any store plugin).

## Anatomy

```
my-game-plugin/
├── plugin.json              # manifest with capabilities.games
├── games/
│   └── <id>/
│       ├── meta.json        # engine-side meta
│       └── engine.py        # server engine (loaded by the host)
├── app/
│   └── <id>.js              # board module (served via /plugin-web)
└── web/
    └── tile.webp            # optional tile art
```

## Manifest declaration

```json
"capabilities": {
  "games": [
    {
      "id": "poker",
      "title": "Heads-Up Hold'em",
      "genre": "card",
      "desc": "1000 chips each, blinds 10/20.",
      "icon": "♠",
      "surfaces": ["room"],
      "entry_js": "app/poker.js",
      "players": "heads-up — you vs her",
      "facts": ["Sealed seat: her hole cards never touch the chat"],
      "tile": "poker-tile.webp"
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Slug `[a-z0-9][a-z0-9_-]{0,32}`, **globally unique and stable forever** — saves and session chats key on it. First plugin to register an id wins. |
| `title` | No | Display name on the library tile |
| `genre` | No | Freeform ("card", "defense") — tiles group on it |
| `desc` | No | One-liner for the tile |
| `icon` | No | Emoji fallback when there's no tile art |
| `surfaces` | No | `["room"]` (default) and/or `["chat_sidebar"]` |
| `entry_js` | room only | Plugin-relative path to the board module |
| `players` | No | Short player-count line for the tile accordion |
| `facts` | No | Up to 8 bullet facts (≤200 chars each) for the tile accordion |
| `tile` | No | Tile art path, served as `/plugin-web/<plugin>/<tile>` |

## `games/<id>/meta.json`

```json
{ "id": "poker", "title": "Heads-Up Hold'em", "icon": "♠", "desc": "…", "order": 1 }
```

`order` sorts the engine-side lobby list. That's the whole file.

## `games/<id>/engine.py` — the engine contract

The host loads your engine by file path and calls it under its own lock. `import gameroom_core` works inside the engine (the host is always loaded first) — it provides `add_talk` and `IllegalAction`.

**Core set (every engine):**

```
new_session(player_name, ai_name, cfg) -> state    # state carries session{player_name, ai_name},
                                                   # talk[], talk_seq
can_start(state) -> str | None                     # error string if a round can't start
start_round(state) -> None                         # deal/setup (raises IllegalAction)
whose_turn(state) -> 'player' | 'ai' | None
apply_action(state, who, action, args) -> None     # raises IllegalAction on bad moves
safe_action(state) -> (action, args)               # fallback when the AI move is invalid
redact(state) -> dict                              # client-safe state (strip hidden info)
```

`cfg` arrives from the per-game settings (see below). The host calls `new_session` with keyword args (`cfg`, plus `player_name` when the room configures one) — give every parameter a default. Engines predating `cfg` are called without it.

**Seat set (only if `whose_turn` ever returns `'ai'`)** — the host runs her sealed one-shot seat:

```
legal_actions(state) -> dict                       # {'actions': [...], ...bounds}
CONTRACT                                           # prompt block; {ai_name}/{opp_name} slots
build_user_msg(view) -> str                        # the turn prompt
validate_decision(data, view) -> dict | None       # {'action','args','say'} or None to reject
view_for_ai(state) -> dict                         # MUST include my_name, opp_name, talk.
                                                   # MUST NOT include hidden opponent info —
                                                   # the sealed-seat law. She cannot peek
                                                   # because the data never reaches her.
```

**Rail set (the chat sees the table — strongly recommended):**

```
view_public(state) -> dict                         # the table as the CHAT may see it —
                                                   # never hidden info (this view is
                                                   # persisted with the chat)
build_ghost(view) -> str                           # one line of live table state
```

Table talk is a normal chat turn on the session (the room transplants the real chat rail). On every chat turn the host puts `build_ghost(view_public(state))` on the ghost rail, so she genuinely sees the board when you talk to her — and the seat still hears the table: the host mirrors chat lines (yours and hers) into `state['talk']`. Without a rail set, the host contributes nothing mid-round (it can't know which keys are hidden) and only `view_between` between rounds.

**Banter set (REST table talk — the `play/{game}/banter` route; the room itself no longer calls it):**

```
BANTER_CONTRACT                                    # persona block for pure talk
view_between(state) -> dict                        # banter context when no round is live
build_banter_msg(view) -> str                      # the banter prompt
```

Views may omit `my_name`/`opp_name` — the host fills them from `state['session']`.

**Per-game settings** — export a `SETTINGS` list and the host renders a settings modal and passes saved values as `cfg`:

```python
SETTINGS = [
    {"key": "start_gold", "label": "Starting gold", "type": "number",
     "min": 50, "max": 2000, "step": 10, "tab": "Start", "default": 220},
    {"key": "instructions", "label": "Her style", "type": "text", "rows": 6,
     "tab": "Rules", "default": "..."},
]
```

Types: `text` (+`rows`), `string`, `number`, `range` (+`min`/`max`/`step`). `tab` groups fields.

Saved values are stored per *game*, plugin-wide — a player's house rules carry across every session. They are not chat-scoped and not encrypted with a private chat, so keep conversation content out of them.

## The board module (`entry_js`)

Loaded fresh on every room entry as an ES module from `/plugin-web/<your-plugin>/<entry_js>`. Two shapes:

- **Turn game** — export `renderBoard`/`renderActions`; the host owns all chrome (the real chat rail + composer, her quip strip, voice, sidebar). Helpers arrive via `ctx` — don't import the host. See `game-holdem/app/poker.js`.
- **Free-mount** — export `{mount, unmount}`; you own the whole stage (canvas, input, your own sim loop). Server stays authoritative for setup and checkpoints. See `app/towerd.js` in the external game-darkhorse repo.

`ctx` (both shapes): `api(path, method, body)` (plugin routes; the session is attached for you), `post(path, body, label)` (a MOVE on the sealed seat — busy gate, board redraw, her quip spoken), `state()`, `cfg()` (her seat), `session()`, `settings` (the session chat's settings, live), `save(patch)`, `busy()`, `esc`, `prettyCodes`, `stageInfo(html)` (one-line status in the stage bar), `showError(msg)`, `composerText()`, `draft(text)` (pre-fill the real composer — the player sends), `sendTurn({text, images, refocus})` (a real chat turn on the session, through the full pipeline), `refreshState()`.

**One table, one transcript.** Whatever the player typed rides the move button they click (`say` on `play/{game}/act`); Send alone is a normal chat turn. Every seat call lands on the session chat as one pair: the player's row (`↳ raise to 20 — you blinked`) and hers (fresh dealer lines, then her quip). If the player typed something and the move gave your seat no turn (a fold ends the hand), the host asks your banter set for her answer, so words at the table are always answered. Silent `_` verbs log nothing. Export `describe_action(action, args) -> str` to phrase the player's row (`'raise to 20'`); the fallback is the verb plus any `amount`. Manifest extras for the room: `stage_mode` (`side` default, `stack`, `fullscreen`), `keeps_focus: true` when your stage owns keyboard focus (the composer won't steal it back after her turn).

Load sibling assets relative to your own module so the path survives any rename:

```js
const base = new URL('./towerd', import.meta.url).pathname;
const defs = await fetch(`${base}/defs.json`).then(r => r.json());
```

## Sessions, saves, and silent verbs

- **Sessions are chats** — the chat IS the save, literally: the host writes your engine's state as a row *on the session chat* (core's chat-scoped store, key `game:<id>`), so your plugin never touches disk for state. Keep state JSON-serializable; it round-trips through JSON on every save.
- **Saves follow the chat** — renamed with it, encrypted with it if it goes private, deleted with it. They survive your plugin moving or being renamed, but never survive changing the game `id`, so don't.
- **Private sessions are allowed.** Mark a session chat private (🗝) and its save seals with the chat. While the vault is locked that chat is hidden: the host reads no state and refuses to write (loudly — never a silent drop), and her seat fails closed rather than playing on a fallback provider. Unlock and the table is exactly where you left it.
- Player actions POST through the host's `play/{game}/act`. Actions prefixed `_` are **silent system verbs** (checkpoints, state syncs from real-time games): no talk line, no AI turn.
- Real-time games checkpoint at natural boundaries (wave end, hand end) via a silent verb; keep snapshots small (the tower defense engine caps at 60KB).

## Checklist

1. `id` chosen and final; manifest declaration + `meta.json` + `engine.py` + board module.
2. `python tools/sign_plugin.py <path-to-your-plugin>` — unsigned plugins are blocked by default.
3. Restart, watch for `[GAMES] Game registered: '<id>' from '<your-plugin>'`.
4. Tile appears in the Game Room library; sessions create, play, save, resume.

See [Publishing](publishing.md) to submit to the store, and [Stories](stories.md) if your idea is narrative rather than mechanical.

## Reference for AI

GAME PLUGINS (Game Room host):
- Manifest: `capabilities.games` = [{id (required, [a-z0-9][a-z0-9_-]{0,32}, globally unique, stable FOREVER — saves and session chats key on it, first registrant wins), title, genre, desc, icon, surfaces (["room"] default and/or ["chat_sidebar"]), entry_js (room games: plugin-relative board module path), players, facts (bullet list, short), tile (art served at /plugin-web/<plugin>/<tile>)}].
- Files: games/<id>/meta.json ({id, title, icon, desc, order}) + games/<id>/engine.py; board module at entry_js served as an ES module from /plugin-web/<plugin>/<entry_js>, loaded fresh on every room entry.
- Engine core set: new_session(...)->state (host calls with kwargs cfg + optional player_name — default every param; state carries session{player_name, ai_name}, talk[], talk_seq), can_start(state)->str|None, start_round(state), whose_turn(state)->'player'|'ai'|None, apply_action(state, who, action, args) (raises IllegalAction), safe_action(state)->(action, args), redact(state)->dict (client-safe, strip hidden info). `import gameroom_core` inside engines gives add_talk + IllegalAction.
- Seat set (required iff whose_turn can return 'ai'): legal_actions(state)->{'actions': [...], ...bounds}, CONTRACT (prompt block, {ai_name}/{opp_name} slots), build_user_msg(view)->str, validate_decision(data, view)->{'action','args','say'}|None, view_for_ai(state)->dict (MUST include my_name/opp_name/talk; MUST NOT include hidden opponent info — sealed-seat law). Invalid AI move -> safe_action fallback.
- Rail set (recommended): view_public(state) (the table as the CHAT may see it — no hidden info; persisted with the chat) + build_ghost(view) → one line on the ghost rail every chat turn; the host mirrors chat lines (player + hers) into state['talk'] so the seat hears the table. Banter set (REST `play/{game}/banter` only): BANTER_CONTRACT, view_between(state), build_banter_msg(view). Views may omit my_name/opp_name (host fills from state['session']).
- Board ctx: api/post/state/cfg/session/settings/save/busy/esc/prettyCodes/stageInfo/showError/composerText/draft/sendTurn/refreshState. ONE TABLE, ONE TRANSCRIPT: the composer's text rides the clicked move (`say`); every seat call appends one pair to the session chat by name — player row `↳ <describe_action> — <say>`, her row = fresh dealer lines + her quip (banter set answers when the seat had no turn); silent `_` verbs log nothing. Optional engine `describe_action(action, args)->str`. Manifest extras: stage_mode (side|stack|fullscreen), keeps_focus.
- Per-game settings: export SETTINGS = [{key, label, type: text(+rows)|string|number|range(+min/max/step), tab?, default}]; saved per game plugin-wide (NOT chat-scoped, NOT vault-encrypted — no conversation content), arrives as cfg.
- Sessions ARE chats: state stored as a chat-scoped row (key `game:<id>`) — keep state JSON-serializable; saves follow the chat (rename/private-encrypt/delete), survive plugin rename, never survive changing the game id. Private (vault-locked) session: host reads no state, refuses writes LOUDLY, AI seat fails closed.
- Actions prefixed `_` are silent system verbs (checkpoints, state sync): no talk line, no AI turn. Real-time games checkpoint at natural boundaries; keep snapshots small.
- Ship: sign (`python tools/sign_plugin.py <plugin>`), restart, expect log `[GAMES] Game registered: '<id>' from '<plugin>'`.
