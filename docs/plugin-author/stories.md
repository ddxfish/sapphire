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

**Conditions** (on exits, interactions, blockers, ending cards): `{"has": "item"}`, `{"flag": "name"}` (truthy), `{"flags": {"k": expected}}` — all listed clauses must hold.

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

The chat is the save. Every resolved outcome appends to a per-(story, chat) journal; current state is a pure replay — no LLM, no randomness, no clock. Ship nothing.

## Checklist

1. `story.json` + rooms (every room reachable, `start` exists, endings have cards if you have art).
2. Backdrops as ~q82 JPGs; tile art in `backdrops/` too.
3. `python tools/sign_plugin.py <path-to-your-plugin>`, restart.
4. Your 📖 tile appears in the Game Room library; play it end to end — then hand it to someone who can be surprised.

See [Games](games.md) for mechanical games, [Publishing](publishing.md) for the store.
