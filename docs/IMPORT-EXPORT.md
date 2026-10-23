# Import & Export

Share your Sapphire configurations with others or back them up. Prompts, toolsets, spice sets, knowledge tabs, people, memories, and tasks export as JSON files and import back. Personas export as PNG character cards. Chats and Mind Palace data have their own lanes, covered below.

## How It Works

Most exportable items use the same dialog. Exporting gives you two options:

- **Copy to clipboard** — JSON text, ready to paste
- **Download as file** — saves a `.json` file to your computer

Importing is the reverse:

- **Paste from clipboard** — paste JSON text
- **Upload file** — select a file from your computer

Everything is validated on import — bad JSON or wrong types are rejected with a clear error. Personas and chats work a little differently (see their sections).

## What Can Be Exported

| Item | File format | Where to find it |
|------|-------------|-----------------|
| **Personas** | PNG character card | Personas page → Export |
| **Prompts** | `.prompt.json` | Prompts page → export button |
| **Toolsets** | `.toolset.json` | Toolsets page → export button |
| **Spice sets** | `.spiceset.json` | Spices page → export button |
| **Knowledge tabs** | `.json` (one tab per file) | Mind → Knowledge → per-tab export |
| **People** | `.json` (one scope per file) | Mind → People → export |
| **Memories** | `.json` (one scope per file) | Mind → Memories → export |
| **Tasks & triggers** | `.json` (one per file) | Triggers pages → per-row export |
| **Chats** | `.json` / `.zip` | Sidebar menu & Chat Manager |
| **Mind Palace layers** | `.json` (one layer × one scope) | Mind tabs, when Mind Palace is on |
| **Library** | `.zip` | Mind → Knowledge, when Mind Palace is on |

Notes on a few of these:

- **Prompts** have an export checkbox to include the pieces an assembled prompt uses, so the whole thing travels in one file.
- **Spice sets** export the set with its enabled categories and their spices — a whole flavor loadout in one file, not individual spices.
- **People** also accepts vCard imports — an **Import VCF** button next to the JSON lane.
- **Tasks & triggers** export per-row from every Triggers page (Heartbeat, Scheduled, Daemons, Realtime, Webhooks). Imported tasks arrive **disabled** with their run history stripped, and any scope references that don't exist on this install are reset to default — check the task over, then switch it on.

## Personas: PNG Character Cards

A persona exports as a **PNG image you can share like any picture**. The pixels are the persona's avatar (or a generated placeholder if it has none), and the full persona bundle — prompt, prompt pieces, voice settings, and metadata — rides inside the image in an embedded text chunk. There's no separate avatar option: the image *is* the avatar.

Import (Personas page → Import) accepts both **`.png` cards and `.json` bundles** — plain prompt exports work too and get wrapped into a new persona. If the name collides with an existing persona, you get a preview of exactly what would be overwritten (persona, prompt, individual prompt pieces) and choose what to replace and what to keep.

Personas whose prompt is marked privacy-required, or whose assembled prompt uses vault pieces, **refuse to export** — that content stays local. See [PERSONAS.md](PERSONAS.md) for the card format in depth.

## Chats

Chats have two lanes:

- **Sidebar menu (⋮)** — Export downloads the current chat's messages as JSON. Import reads a JSON file and **replaces the current chat's history** — it does not append. To keep what's there, create a fresh chat and import into that. [CHATS.md](CHATS.md) covers this in detail.
- **Chat Manager** — each row has an export button that downloads the full chat: name, settings, and untrimmed message history. The bulk **Export** button zips the selected chats, one JSON file per chat, each importable through the sidebar lane. **Private chats are skipped from bulk exports by design** — nothing readable leaves the database in bulk; export those one at a time with the row button.

## Memories & Mind Palace

- **Classic Memories** (Mind → Memories) — export all memories in the current scope as JSON; import merges into the current scope and skips duplicates. Exports include private (keyed) rows verbatim, keys and all, and imports preserve them — an export you asked for never silently drops your own data.
- **Mind Palace** (when the palace is your memory engine) — each Mind tab (Self, Memories, Entities, Goals, and any plugin layers) has its own ⬇ Export / ⬆ Import pair: one layer × one scope per file. Import lands in the **currently viewed scope**, is additive and idempotent (re-runs skip what's already there), and refuses a file from the wrong tab.
- **Library** (Mind → Knowledge under Mind Palace) — the whole scope's library exports as a zip (files plus annotations), re-importable into any scope; re-imports skip documents already present.

Migrating between the classic engine and Mind Palace is its own guided flow — see [MIND-PALACE.md](MIND-PALACE.md).

## Importing

1. Click the **Import** button in the relevant page
2. Paste JSON or upload a file
3. Handle any name collision (below)
4. The item appears immediately — no restart needed

### Name Collisions

What happens on a duplicate name depends on the type:

- **Most types** (toolsets, prompts, spice sets, knowledge tabs, tasks) — a rename prompt pre-filled with `-imported` (edit it to whatever you want). Prompts, knowledge tabs, and spice sets also offer an explicit overwrite checkbox in the import dialog.
- **Personas** — an overwrite preview showing exactly what would be replaced, with per-piece keep choices.
- **Memories, people, palace layers** — no collision concept; imports merge and duplicates are skipped.

Toolset imports also report which tools matched — tools from plugins you don't have installed are skipped and named in the report.

## File Format

Configuration exports are plain JSON with a type marker:

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

The `sapphire_export` and `type` fields are required for validation. Everything else depends on the item type. Mind Palace files carry their own `mindpalace-export` marker instead; chat exports and Library zips are raw data without a marker.

## Sharing

Exports are ordinary files, so share them however you like:

- Drop them in a chat server
- Email them
- Post them on GitHub
- Persona cards are PNGs — post them anywhere images go, or share via the Sapphire Persona Store

## Troubleshooting

- **Import rejected** — check the JSON is valid and has the right `type` marker
- **Missing tools after a toolset import** — tools from plugins you don't have are skipped; the import toast names them
- **Export blocked on a prompt or persona** — vault prompts, privacy-required prompts, and vault pieces refuse to export; content doesn't leave the vault. See [PRIVACY.md](PRIVACY.md)
- **Chat import wiped my messages** — sidebar import replaces the current chat's history; import into a fresh chat instead. See [CHATS.md](CHATS.md)
- **Private chats missing from the bulk zip** — by design; export them one at a time with the row button
- **Imported task doesn't run** — imports arrive disabled with foreign scope references reset to default; review and re-enable it

## Reference for AI

Import/export lanes for Sapphire content — a mix of client-side dialogs and server endpoints.

CONFIG TYPES (shared dialog: copy/download, paste/upload):
- Prompts ({name}.prompt.json) — bundle {sapphire_export, type: "prompt", prompt, components?}; export checkbox includes used pieces; vault prompts/pieces refuse export; import dialog also accepts persona bundles (extracts the embedded prompt); overwrite checkbox for prompt + pieces
- Toolsets ({name}.toolset.json) — type "toolset", functions list; import skips + reports tools not installed
- Spice sets ({name}.spiceset.json) — type "spice_set"; enabled categories with spices/emoji/description; overwrite-categories checkbox
- Knowledge tabs — GET /api/knowledge/tabs/{tab_id}/export?scope= → type "knowledge_tab" with entries; POST /api/knowledge/tabs/import with overwrite flag (merge report)
- People — GET /api/knowledge/people/export?scope= / POST /api/knowledge/people/import → type "people", entries merge with dedup; POST /api/knowledge/people/import-vcf for vCard
- Memories — GET /api/memory/export?scope= / POST /api/memory/import → type "memories"; includes private/keyed rows verbatim, private_key preserved on round-trip; import dedups
- Tasks/triggers — client-side in all Triggers views (heartbeat/scheduled/daemons/realtime/webhooks); bundle {sapphire_export, type: task type, task}; filename {name}.{type}.json; import strips id/run history, forces enabled=false, resets unknown *_scope refs to default; a vault-locked chat target exports with no target

PERSONAS (PNG character cards, server lanes):
- Export: GET /api/personas/{name}/export.png — pixels = avatar (or generated fallback); bundle (prompt + components + voice + meta, NO avatar field) base64-JSON in a `sapphire_persona` PNG tEXt chunk; privacy-required prompts and vault pieces → export refused
- Import: POST /api/personas/import-card (multipart PNG; flags overwrite_persona / overwrite_prompt / overwrite_avatar + keep_components list) and POST /api/personas/import (JSON bundle); name collision without overwrite_persona → 409; UI shows an overwrite preview (persona/prompt/per-piece) before setting flags; plain prompt exports accepted and wrapped into a persona

CHATS:
- Sidebar export = current chat's raw messages (GET /api/history/raw); sidebar import = POST /api/history/import — REPLACES the active chat's history (accepts raw array or {messages})
- Per-chat: GET /api/chats/{chat_name}/export → {name, settings, messages} untrimmed (works for private chats — the explicit per-chat escape hatch)
- Bulk: POST /api/chats/bulk-export (one JSON doc) and POST /api/chats/bulk-export-zip (zip, one importable JSON per chat) — both skip private chats by design

MIND PALACE (plugin API, when active):
- Per-tab transfer: GET transfer/export?scope=&layer= / POST transfer/import {scope, expect_layer, data} — format marker "mindpalace-export"; one layer × one scope per file; additive + idempotent; import lands in the viewed scope; wrong-layer files refused
- Library: GET library/export?scope= → zip (manifest + files + annotations, re-importable into any scope); POST library/import-zip (multipart) — skips existing docs, embedding continues in background
- Classic↔palace migration is a separate guided flow (see MIND-PALACE.md)

FORMAT RULES:
- Config exports carry sapphire_export: true + type (+ version); palace transfer files carry format: "mindpalace-export"; chat exports and Library zips are unmarked raw data
- Name collisions: rename prompt pre-filled with -imported (most types), overwrite checkboxes (prompts/knowledge/spice sets), overwrite preview modal (personas), merge+dedup (memories/people/palace)

IMPLEMENTATION:
- Shared dialogs in shared/import-export.js (handles JSON paste/upload plus binary files like PNG cards); entity APIs (persona-api.js etc.) handle persistence; chat lanes in core/routes/chat.py; persona lanes in core/routes/content.py; memory/people/knowledge lanes in core/routes/knowledge.py; palace lanes in the Mind Palace plugin routes
