# Game Plugins

Ship a playable game as a plugin. The **Game Room** (system plugin `game-room`) is the host: it owns the Surface room, sessions, the talk rail, per-game settings, and Sapphire's sealed seat. Your plugin brings the game — an engine, a board module, and one manifest declaration. Core carries zero game code; if the host is disabled, your registration just holds harmlessly.

Reference implementations: **`plugins/game-holdem`** (turn game, sealed seat) and **`game-darkhorse`** (free-mount real-time game).

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

`cfg` arrives from the per-game settings (see below). Engines predating `cfg` are called without it.

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

**Banter set (state-aware table talk — strongly recommended):**

```
BANTER_CONTRACT                                    # persona block for pure talk
view_between(state) -> dict                        # banter context when no round is live
build_banter_msg(view) -> str                      # the banter prompt
```

With these, talking to her at the table means she genuinely sees the board.

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

## The board module (`entry_js`)

Loaded fresh on every room entry as an ES module from `/plugin-web/<your-plugin>/<entry_js>`. Two shapes:

- **Turn game** — export `renderBoard`/`renderActions`; the shell owns all chrome (talk rail, composer, voice). Helpers arrive via `ctx` — don't import the shell. See `game-holdem/app/poker.js`.
- **Free-mount** — export `{mount, unmount}`; you own the whole stage (canvas, input, your own sim loop). Server stays authoritative for setup and checkpoints. See `game-darkhorse/app/towerd.js`.

Load sibling assets relative to your own module so the path survives any rename:

```js
const base = new URL('./towerd', import.meta.url).pathname;
const defs = await fetch(`${base}/defs.json`).then(r => r.json());
```

## Sessions, saves, and silent verbs

- **Sessions are chats** — the chat IS the save. The host keys state per `(game, session)` in its own store; your plugin never touches disk for state. Saves survive your plugin moving or renaming — but never survive changing the game `id`, so don't.
- Player actions POST through the host's `play/{game}/act`. Actions prefixed `_` are **silent system verbs** (checkpoints, state syncs from real-time games): no talk line, no AI turn.
- Real-time games checkpoint at natural boundaries (wave end, hand end) via a silent verb; keep snapshots small (the tower defense engine caps at 60KB).

## Checklist

1. `id` chosen and final; manifest declaration + `meta.json` + `engine.py` + board module.
2. `python tools/sign_plugin.py <path-to-your-plugin>` — unsigned plugins are blocked by default.
3. Restart, watch for `[GAMES] Game registered: '<id>' from '<your-plugin>'`.
4. Tile appears in the Game Room library; sessions create, play, save, resume.

See [Publishing](publishing.md) to submit to the store, and [Stories](stories.md) if your idea is narrative rather than mechanical.
