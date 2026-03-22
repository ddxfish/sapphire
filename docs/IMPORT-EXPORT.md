# Import & Export

Share your Sapphire configurations with others or back them up. Personas, prompts, toolsets, spices, knowledge, tasks, and people can all be exported as JSON files and imported back.

## How It Works

Every exportable item has an **Export** button (usually in its editor or list view). Exporting gives you two options:

- **Copy to clipboard** — JSON text, ready to paste
- **Download as file** — Saves a `.json` file to your computer

Importing is the reverse:

- **Paste from clipboard** — Paste JSON text
- **Upload file** — Select a `.json` file from your computer

Everything is validated on import — bad JSON or wrong types are rejected with a clear error.

## What Can Be Exported

| Item | File format | Where to find it |
|------|-------------|-----------------|
| **Personas** | `.persona.json` | Personas page → export button |
| **Prompts** | `.prompt.json` | Prompts page → export button |
| **Toolsets** | `.toolset.json` | Toolsets page → export button |
| **Spices** | `.spice.json` | Spices page → export button |
| **Knowledge tabs** | `.knowledge.json` | Mind → Knowledge → export |
| **People** | `.person.json` | Mind → People → export |
| **Tasks** | `.task.json` | Schedule → export button |

### Selective Export

Some items let you choose what to include:
- **Personas** — optionally include/exclude avatar
- **Knowledge** — choose which entries and metadata to include

## Importing

1. Click the **Import** button in the relevant page
2. Paste JSON or upload a file
3. If the name already exists, you'll be asked to rename or overwrite
4. The item appears immediately — no restart needed

### Name Collisions

If you import something with the same name as an existing item, Sapphire prompts you:
- **Rename** — adds `-imported` suffix
- **Overwrite** — replaces the existing item

## File Format

Exported files are plain JSON with a type marker:

```json
{
  "sapphire_export": true,
  "type": "toolset",
  "version": 1,
  "name": "my-coding-tools",
  "emoji": "💻",
  "functions": ["run_command", "web_search", "save_memory"]
}
```

The `sapphire_export` and `type` fields are required for validation. Everything else depends on the item type.

## Sharing

Since exports are just JSON files, you can share them however you like:
- Drop them in a Discord server
- Email them
- Post them on GitHub
- Share via the Sapphire Plugin Store (for personas)

## Troubleshooting

- **Import rejected** — Check the JSON is valid and has the right `type` field
- **Missing data after import** — Some items depend on plugins being installed (e.g., a toolset referencing tools from a plugin you don't have)
- **Avatar missing** — If a persona was exported without its avatar, you'll need to set one manually

## Reference for AI

Client-side import/export system for Sapphire configurations.

SUPPORTED TYPES:
- Personas (.persona.json) — prompt, voice, model, tools, mind, spices, optional avatar
- Prompts (.prompt.json) — monolith or assembled prompt components
- Toolsets (.toolset.json) — named tool groups with emoji
- Spices (.spice.json) — spice set categories and entries
- Knowledge (.knowledge.json) — tabs and entries
- People (.person.json) — contact records
- Tasks (.task.json) — cron/daemon/webhook task definitions

FORMAT:
- All exports are JSON with sapphire_export: true and type field
- Version field for forward compatibility
- File naming: {name}.{type}.json

IMPORT FLOW:
- Paste clipboard or upload .json file
- Validates JSON structure and type marker
- Name collision → rename (suffix -imported) or overwrite
- Immediate availability, no restart

IMPLEMENTATION:
- Client-side only (shared/import-export.js)
- No server endpoints — each view handles its own serialization
- Entity APIs handle persistence (persona-api.js, prompt-api.js, etc.)
