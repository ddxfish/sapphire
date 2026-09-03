# Game Room — play games and interactive stories with your AI

## What it is

The Game Room is where you sit down at a table with your AI — card games on a live board, or interactive stories where she plays a character and a rules engine referees the world. Everything happens inside ordinary chats: every game session and story playthrough **is a chat**, so progress saves itself, survives restarts, and can even be private. The Game Room ships empty — games and stories arrive as plugins (Hold'em ships as its own plugin, story packs too) and appear as tiles in the library.

---

## Using it

### Opening the library

Find **🎲 Game Room** in the nav rail's Chat group flyout (hover or tap the Chat entry). The library shows every installed game and story as a wide tile with art, a description, and a stats line. Use the **search box** and **type chips** (card, arcade, story, ...) in the sidebar to filter. **More Info ▾** on a tile expands pack-authored details.

Each tile shows how many sessions or playthroughs you already have ("2 sessions" / "new table"). Clicking a tile resumes your newest session — or starts a fresh one if you have none.

Games and stories are bookmarkable: the address bar carries a deep link while you're inside one, so a browser bookmark drops you straight back at the table.

### Sessions are chats — the chat is the save

This is the core idea worth internalizing:

- Starting a game creates a **chat** tagged as a game session. Chips, board state, table talk, story progress — all of it lives with that chat.
- Progress **survives restarts**. Come back tomorrow, click the tile, and you're exactly where you left off.
- The session picker at the top of the room sidebar switches between sessions of the same game — each is its own world.
- Game and story chats are hidden from the normal chat dropdown, so they don't clutter your chat list.
- **Deleting a session deletes its save.** The 🗑 button removes the chat *and* all game state stored with it — this is by design, there is no separate save file to recover. The ✕ clear button is gentler: it resets the game (fresh chips, empty history) but keeps the chat name.

### Playing a game

Click Play on a game tile. You get:

- **The board** in the main pane, with your action buttons below it.
- **Table talk** on the right — a running banter log. Type in the composer to chat; your line rides along with your next move, or hit Send to talk without moving.
- **🔊 Speak** in the sidebar toggles her table talk out loud (when TTS is on).
- **Sidebar** — session picker, new/clear/delete session, and the same per-chat settings (persona, model, voice) any chat has. The persona you pick is who sits across the table.

She plays through a sealed seat: the engine shows her exactly what a player in her seat could see — never your hidden cards. The rules engine validates every move (hers and yours), so nobody can cheat, including her.

The ← back button returns you to the library; leaving the Game Room entirely hands the Chat view back to your regular chats, so a game session is never left sitting as your active conversation.

### Game settings (⚙)

The gear on a tile (or **Game Settings** in the room sidebar) opens that game's settings — the schema comes from the game itself. Hold'em, for example, exposes her playing style instructions, temperature, starting chips, and blinds. These are **per-game, not per-session**: your poker settings follow you across every poker session.

### Playing a story

Story tiles (📖) open a playthrough chat in the story room: the real chat rail and composer, floating over a full-bleed stage. You just talk — describe what you do, and she narrates. Behind the scenes a referee engine rules on every action: exits, hidden objects, puzzles, and items are tracked in the engine, and she narrates its verdicts. Solutions never enter her context, so she's genuinely surprised alongside you.

The story room gives you:

- **Scene panel** (sidebar) — room art, title, description, exit chips, visible objects (🧩 unsolved puzzle, ✓ solved), what you're carrying, mood/stats, and turn-gated 💡 hints you reveal by clicking — never auto-spoiled. Tap a verb chip to draft that action into the composer.
- **Backdrops** — each room can paint the chat background with its own art, so moving through the story changes the scenery. An earned ending card lingers on the stage after THE END.
- **Controls** — ⏸ pause / ▶ resume and the ⚙ story gear. Pause is an intermission: the chat returns to your regular persona (configurable) until you resume. Ending the story is deliberate — it lives inside the gear's State tab.
- **Identity modes** — the sidebar's identity dropdown picks how she plays: fully become the story's lead (**story**), stay herself (**local**), or blend both (**combined**). The 👁 preview shows the exact assembled prompt. An **include story tools** checkbox controls whether story tools ride along with whatever toolset the chat uses.

### Story setup — scenarios and slots

Stories that declare fill-in-the-blank **slots** (names, places, twists) show a setup form before room one — Mad-Libs style. The **scenario bar** on that form saves and loads named presets of your answers (💾), so you can keep a "canon run" and an "everything goes wrong" run side by side. Plain stories skip setup and start instantly.

### Sealed blanks — write the surprises yourself

Some stories leave reveals deliberately blank *for you to write* — a letter's contents, what's in the chest. When she reaches for one that isn't written yet, a popup appears: **"✍ The story left this blank for you."** What you type goes straight into the engine — **it never passes through her** — so when she opens the letter, the surprise is real.

- If you're at the keyboard when she reaches, a **live countdown** runs — she waits inside her turn while you write. **⏳ More time** extends the wait.
- If the moment passes (or you weren't there), she gets an **honest hold**: she learns a surprise exists, never what it is, and finds your words on her next try.
- **Skip** uses the author's fallback line instead (only offered when the pack ships one).
- ✍ chips in the scene panel let you fill or edit blanks early, on your own initiative.
- Some setup forms include sealed slots — those are written before turn one, same machinery.

### Shaping the world mid-run

Both of you can add to the world:

- **She** can author objects into rooms with her `story_place` tool — a note she leaves, a gift she hides — and they become part of the tracked world.
- **You** can edit through the story gear's world tabs (Rooms, Characters, items): place objects, edit room text, add exits, stock the house. Some stories unlock this progressively — you'll get a toast when "the house is open" and the Rooms panel goes live.
- Dice, effects, and puzzle outcomes are journaled by the engine as they happen — chance rolls once and history keeps it, so a replay never re-rolls your luck. The gear's read-only **State** tab is the inspector if you want to peek at the machinery.

### GM settings

The story gear also carries the storyteller's conduct — all user-editable, nothing hidden:

- **This story** — role backstory, premise, and player role (pack defaults shown; edit freely). Slot-driven stories edit these through the setup form instead.
- **GM style** — a shared narration style used by all stories, with a per-story on/off toggle, plus a per-story DM guide.
- **Max one move per turn** — the engine refuses a second room-move until your next message, so she can't sprint through scenes you haven't seen.
- **AI tools fence** (in-game gear, per playthrough) — untick story tools you don't want her using this run; fenced tools vanish from her toolset entirely. The referee tool itself always stays.

Saved GM settings apply on her very next turn — a live tuning loop.

### Private playthroughs

With the vault set up and **unlocked**, every Play button grows a ▾ menu with **🗝 Play Private**: a new session born private. When the vault locks, the session seals — it vanishes from the library, its save rows and rendered story prompt seal with it, and the room bounces you back to the library. Unlock and it's all back. Private sessions also refuse cloud models: her seat runs local-only there.

### Ending a story

⚙ → State → **⏹ End story**. The journal is kept. What the chat becomes afterward follows your return settings (see below) — by default the story costume stays wearable, so you can keep chatting with the character after the tale closes.

---

## Settings

In the library sidebar's **Room** section:

| Setting | What it does |
|---------|--------------|
| Player name | Your seat name in new sessions |
| Model | Override stamped onto game/story sessions as you enter them; default leaves each chat on its persona's model |
| Return prompt | Who the chat becomes when you ⏸ pause a story, and the default after it ends; blank = stay in the story costume |

Per-game rules live behind each tile's ⚙. Per-session persona/voice/model live in the room sidebar like any chat. Story conduct lives in the story gear (GM tabs).

---

## Quick Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| Story character wrong after a restart (she's herself, not the role) | The story prompt re-registers shortly after boot — give it a minute | Reopen the playthrough from the library; entering re-asserts the costume |
| Game or story tile missing from the library | Settings → Plugins: is the plugin that ships it enabled and signed? | Enable it, or re-sign it if you edited its files; unsigned plugins are blocked at boot |
| Library says no games installed | The game-room plugin itself may be disabled/unsigned | Same check — the host plugin must load for any tiles to appear |
| Saves gone after deleting a chat | Did you 🗑 the session? | That's by design — the chat IS the save; deleting it deletes chips, journals, everything. Use ✕ clear to reset without deleting |
| Story tools missing mid-run | Sidebar: "include story tools" checkbox; gear → Setup: the AI tools fence | Re-tick the checkbox, or unfence the tool; a ⏸ paused story also runs without story context until resumed |
| Private session vanished | Vault locked? | Unlock the vault — sealed sessions return untouched |
| Clearing a story chat restarted the tale | Expected — journals die with the transcript | Your placed objects and settings survive; the story replays from the top |

---

## See also

- [plugin-author/games.md](plugin-author/games.md) — build and ship your own game plugin
- [plugin-author/stories.md](plugin-author/stories.md) — author a story pack (rooms, puzzles, sealed blanks, backdrops)
- [PERSONAS.md](PERSONAS.md) — who sits across the table
- [PLUGINS.md](PLUGINS.md) — installing and signing plugins
- [PRIVACY.md](PRIVACY.md) — the vault and private chats
- [TOOLSETS.md](TOOLSETS.md) — toolsets and the extra-toolsets union

## Reference for AI

GAME ROOM (plugin `game-room`, host only — games/stories ship as separate plugins registering via core games_registry / any plugin's stories/ dir):
- Library: nav Chat-group flyout → Game Room app; tiles from GET /api/games (capabilities.games) + story scan; Play resumes newest session else creates one.
- Sessions ARE chats: mode='game', game_id='<id>' or 'story:<slug>'; hidden from normal chat picker; state in chat-scoped plugin storage (plugin_chat_data) — survives restarts, DIES WITH THE CHAT on delete (by design, warn users). Clear chat = game reset; for stories only journals die (costumes/objects survive, tale restarts).
- Games: engine validates all moves (server-side, gameroom_core); AI seat sees view_for_ai only (no hidden opponent info). Per-game settings = engine SETTINGS schema, stored per-game not per-session (routes play/{game}/settings). Table talk composer rides with moves or sends solo.
- Stories: referee engine rules, AI narrates (story_act tool = only world-mutation door; story_place authors new objects; story_end closes; story_status for plain chats only — in-story context already carries state). Ghost block injects live room state per turn; solutions never in AI context.
- Identity modes story/local/combined (sidebar dropdown); "include story tools" = extra_toolsets union of module plugin_game-room_story_tools; 👁 = exact prompt preview.
- Sealed blanks: player-written reveals, never pass through the AI; reach → popup with live countdown (extendable, capped) → honest hold if unfilled (AI knows a surprise exists, not what); Skip → author fallback only if pack ships one; ✍ chips = early fill; journaled at write, replay never re-prompts.
- Setup: slot (Mad-Libs) form pre-start; scenario bar = named slot snapshots (💾 save/load).
- GM settings: shared GM style + per-story DM guide + one-move-per-turn law, all user-editable, live next turn; per-playthrough AI tools fence via tools_filter hook (story_act never fenceable).
- Backdrops: per-room, client-side paint of chat background; ending card persists post-END.
- Private: Play Private (vault unlocked only); vault lock seals session + save rows + rendered prompt, evicts open room; private seat = local providers only.
- Pause = intermission (chat returns to room-config return_prompt persona; no story context/fence while paused). End keeps journal; return_prompt setting rules the post-story costume (blank = costume stays wearable).
- Troubleshoot: missing tiles → owning plugin disabled/unsigned (engine load refuses non-verified plugins); wrong character post-restart → boot re-merge thread + reopening playthrough reasserts costume.
