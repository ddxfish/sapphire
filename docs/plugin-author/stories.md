# Story Packs

Ship an interactive story as a plugin. The **Game Room** host carries the story engine: a one-tool referee, an event-journal save system, per-room backdrops, and an identity layer that can turn Sapphire into your story's lead. Your plugin brings rooms, art, and (optionally) a role.

The engine's law: **the AI narrates, the referee rules.** Solutions, hidden objects, and player-authored surprises live engine-side and never enter the AI's context — she plays fair because cheating is structurally impossible.

Reference implementation: the **story-titanic** plugin (its repo doubles as the template).

## Anatomy

```
my-story-plugin/
├── plugin.json                  # ordinary manifest — no story capability needed
├── stories/
│   └── <slug>/
│       ├── story.json           # pack meta
│       ├── rooms/
│       │   └── 1-first-room.json    # {id}-{slug}.json, one file per room
│       └── backdrops/           # jpgs/webp — tile, room art, ending cards
└── prompts/
    └── pieces.json              # optional emotion pieces (see below)
```

Any plugin with a `stories/` dir is a story pack — the host scans every plugin's `stories/` at read time. Backdrops are served through an **images-only lane**: room JSONs (your solutions) are never web-served.

## `story.json`

| Field | Required | Description |
|-------|----------|-------------|
| `slug` | Yes | Stable id (folder name is the fallback). Slugs starting `_` stay out of the picker. |
| `title`, `description` | Yes | Library tile |
| `start` | Yes | Room id the story opens in |
| `premise` | No | Scenario text woven into her prompt |
| `role` | No | `{"name": "Lucretia", "text": "…full persona…"}` — enables **story mode**: a total identity swap; the prompt dropdown shows the role's name |
| `player_role` | No | Who the PLAYER is ("Giuseppe") — shown on the tile |
| `dm_guide` | No | Per-story GM guidance (user-editable in ⚙ GM Settings; your text is the shipped default) |
| `initial_flags` | No | `{"jack_hp": 10}` — starting stats, seeded as replayable events |
| `tags`, `facts`, `tile` | No | Library tile metadata; `tile` is a filename in `backdrops/` |
| `open_flag` | No | The **zork-line**: the flag that "opens the house". Player room-text overrides apply when it turns truthy (any flag your effects set: `chest_opened`, `killed_goblin`, a `flag_gte` threshold…). Player objects and exits are NOT gated — they exist as authored. |
| `slots` | No | Mad-Libs setup form, shown before room 1 — see below |

## Setup slots (Mad-Libs)

```json
"slots": [
  {"key": "relationship", "label": "We are…",
   "options": ["coworkers", "married", "old war buddies"],
   "allow_custom": true, "default": "coworkers"},
  {"key": "combo", "label": "The chest combo", "sealed": true,
   "seal_key": "3:chest:open"}
]
```

- Declared slots make the story open with a setup form (dropdowns + "Other"); stories without slots keep zero-friction auto-start.
- Form layout (optional, pure cosmetics): `"rows": N` turns a slot into an N-line textarea; `"section": "Characters"` groups consecutive slots under a named header; `"width": 20` (percent, 10–100) lets slots share a line — e.g. a 20% name beside its 80% backstory textarea (rows wrap responsively). An explicitly empty `"label": ""` renders no label — the section header or the neighboring field carries the meaning.
- `{key}` tokens substitute into role name AND text, premise, `player_role`, `dm_guide`, and every string in every room — once, at load, per playthrough.
- The assembled-setup-as-role pattern: declare `"role": {"name": "{ai_character}", "text": "{ai_backstory}"}` and the setup form becomes the story's whole identity — story mode activates (a role now ships), and the costume registers under the resolved name. Give every such slot a solid `default` so an untouched form still plays.
- `sealed: true` slots never substitute: the player's text is written into the sealed blank named by `seal_key` (the room-scoped `"{room_id}:{object}:{verb}"` key of a `sealed` interaction you declare) — she discovers it in play.
- Players save **scenarios** — one named thing per story holding the filled setup form (slots) AND the environment (placed objects, room-text overrides, added exits). One dropdown in the setup/gear modal; picking one SWAPS the playthrough to it, right then (blank = the shipped story), verbatim — an object gates only if its own Visible-when says so. Scenarios live user-side; your pack never changes.
- **Takeable objects**: `"takeable": true` lets the generic `take` verb (grab/get/pocket aliases) move the object itself into inventory — journaled `taken` event, the object leaves the room (visibility, search, and her room text all exclude it). Optional `take_message`. A declared `take` interaction on the object overrides the generic verb; non-takeable objects keep the off-script narrate-freely license.
- **Conditions** also accept `{"did": "door1"}` (the object has been used — replayed interaction history, verb-agnostic) and `{"solved": "vault"}` (its puzzle is answered) — no flag wiring needed for has-opened / password gates. The in-app editor's sections compile to exactly this grammar — **Visible when** → the object's top-level `condition` (+ `hidden` for search-reveal), **Requirements** → condition + blocked_message per verb (usable-when), `puzzle` + `{solved}` for passwords/riddles, `roll` for d20/d100 chances, and **Effects** → `set`/`adjust`/`gives`.
- **Shadow law** (editor v2): the Environment editor also shows your SHIPPED objects. A player editing one stores a same-name override that field-merges at load — desc/hidden replace, interaction *messages* overlay per-verb, and your mechanics (effects, seals, dice, conditions) always survive the reword. Deleting a shipped object writes a restorable tombstone. All of it lives in the user layer; the pack stays canonical.
- **Exits follow the same law** (exits editor, 2026-08-21): players edit a shipped exit's *text* (label/desc — a keyed-by-destination shadow), wall one off (restorable tombstone), or add their own exits carrying the full grammar (visible_when / requirements / dice / effects). Added exits are NOT zork-gated — they exist as authored; gating is the author's explicit Visible-when choice.
- **What she carries, she can use** (2026-08-21): taken objects stay fully interactable anywhere — look, declared verbs, dice, seals, solve. Seal and once-dice keys anchor to the object's *home room* (the room whose JSON ships it), so a spent chance stays spent and a revealed seal stays revealed wherever it travels. `examine`/`inspect` fall back to `look` unless an object declares them as real verbs.
- **`goto` effect** (2026-08-21): any effects block may carry `"goto": room_id` — teleports the player (emits the moved event). The destination's `on_enter` does NOT fire on a teleport.
- **Prompt Pieces** (2026-08-21): per-story prompt fragments toggled by effects (`extras` / `extras_remove` lists of names). Authored in the gear's Characters tab (their one home — hidden from the Prompts page by design); stored keys are `story_{slug}_{name}`, resolution tries the story-scoped key first so packs and players never collide. Active pieces append to her assembled story prompt live.
- **Shipped OBJECT mechanics edit through the same gate** (2026-08-21): an object whose spec round-trips through the editor losslessly prefills live; a save stores a `_replace` entry that swaps your object **wholesale** for that playthrough (verbatim-equals-shipped clears it; restore lifts it). Fields the editor doesn't show — per-verb `aliases`, `emotions`/`emotions_remove`, `found_by`/`gives` — are *passengers*: they ride a gated save verbatim, attached to their verb (delete the verb and they go with it). `sealed` interactions, multi-answer `solutions`, per-verb-divergent locks, and custom roll shapes fail the gate and show read-only in plain words; text rewording still shadows those, machinery riding untouched.
- **Shipped exit mechanics edit through a fidelity gate** (2026-08-21): when your door's machinery fits the editor grammar (single-clause has/did/flag conditions, d20/d100 rolls in the editor's own idiom, set/gives/adjust effects), the editor prefills it live and a save stores a `mechanics` unit on the shadow that **replaces yours wholesale** for that playthrough (`{}` = stripped bare; editing back to your verbatim grammar clears it). Richer grammar — `flags` dicts, `flag_gte`, custom roll branches, `generate` — shows read-only as a plain-words summary instead; text edits still shadow and your machinery rides untouched. Restore-to-shipped clears mechanics along with text.

## Room JSON

```json
{
  "id": 1, "slug": "promenade-rail", "title": "The Promenade Rail at Dusk",
  "template": "AI-side scene: rich, spoiler-bearing prose she narrates from.",
  "player_desc": "Player-side scene: short and spoiler-free.",
  "player_hints": [{"after_turns": 6, "text": "Shown behind a 💡 click."}],
  "ghost_hints": [{"after_turns": 5, "whisper": "Private nudge to the AI."}],
  "on_enter": {"emotions": ["curious"]},
  "exits": [
    {"to": 2, "label": "the grand saloon", "desc": "her world",
     "condition": {"has": "key"}, "blocked_message": "The way is closed."}
  ],
  "objects": { "…": "see below" },
  "blockers": [{"until": {"flags": {"chose": true}}, "text": "choose first"}],
  "backdrop": "stern-rail.jpg",
  "ending_cards": [{"card": "dawn-together.jpg", "condition": {"flags": {"her_trust": 3}}}]
}
```

- `template` is what SHE sees (per-turn context block); `player_desc` is what the player sees in the sidebar. Write both.
- A room with **no exits is an ending**. `ending_cards` picks outcome-specific THE END art — first entry whose condition matches wins.
- Hints gate on `turns_in_room >= after_turns`; player hints are click-to-reveal, never auto-spoiled.

**Exits carry the full grammar** (2026-08-21): besides `condition` +
`blocked_message` (visible but refusing — tension), an exit may declare:

- `visible_when` — a condition gating its *existence* (secret doors): until it
  holds the exit isn't listed, isn't traversable, and never shows as blocked.
  Classic compose: search reveals a lever object → its effect sets a flag →
  the passage appears.
- `effects` — fires on a successful traverse, *before* the destination's
  `on_enter` ("the rope ladder snaps behind you": `{"set": {"ladder_gone": true}}`).
- `roll` — dice-gated traversal, same shape as interaction rolls
  (`{"sides": 20, "beat": 11, "once": false, "success": {…effects},
  "failure": {"message": "A plank gives way."}}`). The rolled value is
  journaled either way; branch effects fire either way; only success moves.
- No `puzzle` on exits — a riddle door is a door *object* with a password,
  and the exit then requires `{"did": "door1"}` (or `{"solved": "door1"}`).

## Objects

```json
"journal": {
  "desc": "Giuseppe's journal, corners soft from salt air",
  "hidden": false,
  "gives": "sketchbook",
  "puzzle": {"riddle": "…", "solutions": ["map", "an atlas"],
             "on_solve": {"message": "…", "gives": "amulet"}},
  "interactions": {
    "show": {
      "aliases": ["share", "open"],
      "condition": {"flag": "met_her"},
      "blocked_message": "Not yet.",
      "message": "What she says when it works.",
      "adjust": {"her_trust": 1},
      "emotions": ["smitten"]
    }
  }
}
```

- `hidden: true` + `found_by: "search"` — invisible until the player searches. Hidden-and-unfound objects never appear in her context.
- Puzzle answers match forgivingly: case, punctuation, and leading articles are ignored (`"A Map!"` solves `"map"`).
- `aliases` are the assist-intent net: synonyms resolve silently to the canonical verb. **Off-script verbs are a feature** — anything undeclared returns an improvisation license ("narrate freely, the tracked world doesn't change"), never a refusal.

**Effects** (usable on interactions, `on_solve`, `on_enter`, roll branches):

| Key | Does |
|-----|------|
| `set` | `{"flag": value}` — set flags |
| `adjust` | `{"her_trust": 1}` — numeric arithmetic (HP, gold, trust) |
| `gives` | put an item in inventory |
| `emotions` / `emotions_remove` | live emotion layers in her prompt |
| `extras` / `extras_remove` | extra prompt pieces on / off |

**Conditions** (on exits, interactions, blockers, ending cards): `{"has": "item"}`, `{"flag": "name"}` (truthy), `{"flags": {"k": expected}}`, `{"flag_gte": {"k": 50}}` (numeric threshold) — all listed clauses must hold.

- An object may also carry a **top-level** `condition`: until it holds, the object doesn't exist at all (invisible, unsearchable, unactable). This is what the editor's Visible-when section writes — e.g. dormant-until-the-zork-line objects.
- `look` with no target (or target `room`) returns the room's current truth — title, scene text, visible objects, exits. Useful in single-room stories whose contents change.

## Dice

```json
"break": {"roll": {"sides": 100, "beat": 60, "once": true,
  "success": {"message": "It gives way.", "set": {"gate_open": true}},
  "failure": {"message": "It holds."},
  "retry_message": "That chance is spent."}}
```

The rolled value is journaled at roll time — replay never re-rolls, revert keeps history honest. `once: true` spends the chance permanently.

## Sealed blanks — the player writes the reveal

```json
"open": {
  "sealed": {
    "ask": "You reached the chest first. What's inside?",
    "fallback": "old charts and a dead man's boots",
    "hold_message": "optional custom hold line",
    "wait": 180
  },
  "adjust": {"her_trust": 1}
}
```

The author leaves the reveal **blank**; the player types it mid-run in a popup; the AI discovers it verbatim when she performs the verb. The text never passes through her on the way in — the player surprises the storyteller.

One trigger rule: the popup only ever raises because **she reached for the blank** — live (with a countdown) if the player is watching, as an urgent catch-up on their next visit if not. Nothing prompts at room entry; the ✍ chip in the scene strip is the player's volitional early-fill.

- **Live moment**: if the player is in the story room when she performs the verb on an *unfilled* blank, her tool call waits — the popup raises instantly with a countdown (default 120s; per-seal `wait` or story-level `meta.seal_wait` override, clamped 5–600), and the player's words come back as the reveal in that very turn. The player can extend (+60s per click, 600s ceiling) or take the author's line.
- Unfilled with nobody in the room, or the countdown expiring = the act **holds** (honest: she learns a surprise exists, never what). Her early attempt re-raises the player's popup with urgency.
- `fallback` fires only on the player's explicit choice — omit it to make the blank mandatory.
- Fills are journal events: replay never re-prompts; revert re-opens the blank. Declared effects fire once, on first reveal.
- Player text fills the *fiction*, not the mechanics — tracked items and flags stay author-declared.

## Identity modes

If your pack ships a `role`, a playthrough defaults to **story mode**: full identity swap, the prompt registers under the role's name, and the 👁 preview shows the player byte-exactly what she gets. `local` keeps her own persona narrating; `combined` interleaves her persona with the role. No hidden instructions, ever — your `dm_guide` and the shared GM style are user-editable in ⚙ GM Settings and visible in the preview.

## Emotion pieces

Ship `prompts/pieces.json` with keys namespaced `story_<slug>_<emotion>` (e.g. `story_titanic_smitten`). Resolution order: your story-specific piece, then the generic `story_engine_<emotion>`, else the emotion is silently skipped.

## Saves

The chat is the save — literally. Every resolved outcome appends a row to a per-(story, chat) journal stored on the chat itself, next to the active-story pointer and the rendered role prompt; current state is a pure replay of that journal — no LLM, no randomness, no clock. Ship nothing.

The journal follows its chat: renamed with it, encrypted with it when the chat goes private, and deleted with it. Run and revert archives live as extra rows *inside* the living chat, so reverting never loses history — but deleting the chat ends that playthrough for good.

**Private playthroughs work.** Mark a story chat private (🗝) and the whole run seals with it — journal, the sealed-blank text the player wrote, the rendered role prompt. While the vault is locked that chat is hidden: the playthrough reads as if it never happened, the engine refuses writes against it, and the role's prompt name doesn't surface in the prompt list. Unlock and it resumes. One limit today: in `local`/`combined` mode the local persona can't be a vault prompt — use a non-vault prompt, or pure `story` mode.

## Checklist

1. `story.json` + rooms (every room reachable, `start` exists, endings have cards if you have art).
2. Backdrops as ~q82 JPGs; tile art in `backdrops/` too.
3. `python tools/sign_plugin.py <path-to-your-plugin>`, restart.
4. Your 📖 tile appears in the Game Room library; play it end to end — then hand it to someone who can be surprised.

See [Games](games.md) for mechanical games, [Publishing](publishing.md) for the store.
