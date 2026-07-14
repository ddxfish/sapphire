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
      "pieces": "prompts/pieces.json"
    }
  }
}
```

Both keys are optional — ship either or both. Paths are relative to the
plugin directory and the files are signed content like everything else
(edit → re-sign).

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
