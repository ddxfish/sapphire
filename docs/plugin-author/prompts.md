# Prompt Packs

Ship prompts with your plugin: monolith prompts and/or assembled-prompt
pieces (characters, emotions, locations, scenarios). They appear in the
Prompts UI beside the user's own, badged with your plugin name.

## Manifest

```json
{
  "capabilities": {
    "prompts": {
      "monoliths": "prompts/monoliths.json",
      "pieces": "prompts/pieces.json",
      "kind": "user"
    }
  }
}
```

Both file keys are optional — ship either or both. Paths are relative to the
plugin directory and the files are signed content like everything else
(edit → re-sign).

`kind` declares what your pack entries ARE, and visibility derives from it:

- `user` (default) — user-facing prompts/pieces; listed in the Prompts UI,
  prompt pickers, and the AI's prompt tools, badged with your plugin name.
- `internal` — engine scaffolding your own plugin consumes when rendering
  (format blocks, machine-selected emotion texts). Hidden from every picker
  and accordion; still fully resolvable by name, so rendering and existing
  references keep working.
- `story` — reserved for rendered story prompts (the game-room engine stamps
  this on dynamic entries; manifests normally don't need it). Hidden like
  `internal`, and pickers label a chat still pointing at one with 📖.

An unknown `kind` is coerced to `user` with a boot warning — a typo makes
your prompts visible, never silently missing.

## File shapes

Identical to the user's own `user/prompts/` files:

`prompts/monoliths.json`:
```json
{
  "my_narrator": {
    "content": "You are ... {ai_name} and {user_name} templates work here.",
    "privacy_required": false
  }
}
```
(A plain string value also works — it's normalized to the object form.)

`prompts/pieces.json`:
```json
{
  "components": {
    "emotions": { "my_dread": "A creeping dread colors your replies..." },
    "character": { "my_char": "You are ..." }
  },
  "scenario_presets": {
    "my_scene": { "character": "my_char", "emotions": ["my_dread"] }
  }
}
```

Component types: `character`, `location`, `relationship`, `goals`, `format`,
`scenario`, `extras`, `emotions`.

## Rules (mirror-only)

- **Your prompts never leave the plugin.** They are merged into the prompt
  system at read time and are never written to `user/prompts/*.json`.
- **The user always wins name collisions.** A user prompt or piece with the
  same name shadows yours (this is also the edit path — editing your prompt
  in the UI saves a user copy that wins). Deleting the user copy reveals
  yours again.
- **Pack prompts are read-only** — delete is refused while your plugin is
  enabled. Disabling the plugin makes them vanish (dark, never deleted).
- If your prompt is ACTIVE when the plugin is disabled, the active preset
  hands off to `default` loudly (log + event) — no silent stale prompt.
- Prefix your names (`myplugin_dread`, not `dread`) — cross-pack collisions
  go to the first registrant and are logged, not merged.

## Reference for AI

PROMPT PACKS:
- Manifest: `capabilities.prompts` = {monoliths?: path, pieces?: path, kind?: "user"|"internal"|"story"}. Paths relative to the plugin dir; files are signed content (edit -> re-sign). Either or both files.
- kind: "user" (default) = visible in Prompts UI/pickers/AI prompt tools, badged with the plugin name; "internal" = hidden from pickers but resolvable by name; "story" = reserved for rendered story prompts (engine-stamped). Unknown kind coerces to "user" WITH a boot warning (fail-open to visible). A monolith entry may override the pack kind with its own "kind".
- monoliths.json: {name: {content, privacy_required}} — plain string values normalized to the object form. {ai_name}/{user_name} templates work in content.
- pieces.json: {"components": {type: {key: text}}, "scenario_presets": {name: {component: value}}}. Component types: character, location, relationship, goals, format, scenario, extras, emotions.
- Mirror-only invariants: pack prompts merge at read time, never written to user/prompts/*.json; a same-name USER prompt/piece shadows the pack's (UI edits save a user copy that wins; deleting it reveals the pack's again); pack prompts are delete-refused while the plugin is enabled; disabling the plugin makes them vanish (dark, not deleted); an ACTIVE pack prompt hands off to `default` loudly (log + event) on disable.
- Prefix names (`myplugin_dread`) — cross-pack collisions go to the first registrant and are logged, not merged.
