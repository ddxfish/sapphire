# Memory Layers

Register your own layer in the Mind Palace memory system. Your content becomes
part of the AI's mind: searchable with `search_memory`, woven to the people and
places she knows, walkable by the connection spider, browsable on its own tab
in the web UI — without you writing a search engine, an embedding pipeline, or
a single line of frontend.

Use cases: mirror an Obsidian vault, a wiki, a documentation tree, a lore book,
an external note system — anything with items of text worth remembering.

**Requires:** the `mindpalace` plugin (the Mind Palace memory system). When it
isn't active, your layer simply doesn't light up; declare your imports lazily
(shown below) and your plugin degrades cleanly.

## How it works — the mirror model

You don't serve queries. You **mirror** your content into the palace as normal
memory chunks tagged with your layer key, and the palace does everything else:
full-text + semantic search, entity-mention weaving (names in your text link
to entities in her graph automatically), embeddings (generated in the
background), export/import, and a generated web tab.

Your source of truth stays yours. The palace holds a copy keyed by your
`source` ids, and you reconcile on change: delete by source, re-save.

## 1. Declare the layer

```json
{
  "name": "my-vault",
  "capabilities": {
    "memory_layers": [
      {
        "key": "vault",
        "label": "Vault",
        "icon": "🗄️",
        "description": "Notes mirrored from my Obsidian vault.",
        "librarian": false,
        "writable": false
      }
    ]
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `key` | yes | Lowercase identifier. Unique across plugins. The reserved core layers (`self`, `events`, `entities`, `knowledge`, `goals`) are refused. |
| `label` | no | Display name (tab, chips). Defaults to the key, title-cased. |
| `icon` | no | Emoji for the tab and flyout entry. |
| `description` | no | One line shown in the AI's search tool description and on your tab. |
| `librarian` | no (default `false`) | Opt in to librarian passes: her memory curator will review, merge, and retire chunks in your layer alongside her own memories. Leave `false` if your sync owns the content. |
| `writable` | no (default `false`) | Allow the AI to `save_memory` directly into your layer. Leave `false` for sync-owned mirrors — rows she saved would be orphaned or clobbered by your next reconciliation. |
| `mode` | no (default `'mirror'`) | Reserved. Only `'mirror'` exists today. |

Registration happens at plugin load and unwinds at unload/disable — the AI's
tool schemas update live in both directions.

## 2. Write content through `layer_api`

Import **lazily, inside functions** — never at module top-level — so plugin
load order never matters:

```python
def sync_vault(notes, scope='default'):
    try:
        from plugins.mindpalace.tools import layer_api
    except ImportError:
        return  # Mind Palace not active — mirror later

    # Batch sync: ONE transaction, ONE ledger line for the whole run
    items = [{'content': n.text[:512], 'source': n.path,
              'label': n.folder, 'fields': {'tags': n.tags}} for n in notes]
    ids, msg = layer_api.bulk_save(items, layer='vault', scope=scope)

def note_changed(note, scope='default'):
    from plugins.mindpalace.tools import layer_api
    # Reconciliation: source is your external id
    layer_api.delete_by_source('vault', note.path, scope=scope)
    layer_api.save(note.text[:512], layer='vault', scope=scope,
                   source=note.path, fields={'tags': note.tags})
```

The API (`plugins/mindpalace/tools/layer_api.py`):

- `save(content, layer, scope='default', label=None, fields=None, source=None)` → `(chunk_id, message)`. Full pipeline: inline embedding, FTS, entity-mention weaving. One ledger line per call — use for single items, not loops.
- `bulk_save(items, layer, scope='default')` → `(ids, message)`. One transaction, embeddings deferred to the background sweep, **one** ledger summary line. Use this for syncs.
- `delete_by_source(layer, source, scope='default')` → `(count, message)`. Removes every chunk you saved with that `source`, cleans its graph edges, one ledger line.
- `get_sources(layer, scope='default')` → `{source: {'count', 'fields'}}` for your mirrored chunks. The cheap diff: stamp a content hash into `fields` at save time, compare here, skip unchanged sources on the next sync.
- `sync_note(layer, summary, scope='default', detail=None)` — one ledger line about an out-of-band event. Sparingly: one per sync run, never one per item.

The fence: `layer_api` accepts **only your registered plugin layers** — the
palace's own layers are never writable through it. Content is capped at 512
chars per chunk (like every palace memory); chunk long notes yourself.

**Ledger etiquette matters.** The ledger is the AI's "what changed in my mind"
stream, read every time she reads her self-sheet. A 500-note sync must be one
line ("`my-vault` synced 500 items into vault"), not 500. `bulk_save` and
`delete_by_source` do this for you; don't loop `save()` over a big import.

## 3. What you get for free

- **Search**: `search_memory(layer='vault')` and all-layer searches include your content; your layer is listed in the tool's description automatically.
- **Weaving**: names in your content matching her entities create graph edges at write time; the spider walks them like any memory.
- **Web tab**: your layer appears in the Mind Palace tab strip and the Mind flyout, with browse/search/favorite/export — zero frontend code.
- **Scope semantics**: pass the chat's memory scope (read `scope_memory` from `core.chat.function_manager` — return `None` on error and disable, never fall back to `'default'`). Scope deletion deletes your mirrored rows with everything else.
- **Settings**: need a vault path? Declare `capabilities.settings` ([guide](settings.md)) — your tool executor receives them automatically.

## Disabled = dark

When your plugin is disabled, your layer goes **dark**: mirrored rows stay in
the database but vanish from every read path — search, browse, spider, tabs.
Re-enable the plugin and they light back up. Nothing is deleted. (Deleting a
whole scope deletes its plugin-layer rows too — scope deletion is total.)

## Reference implementations

- `user/plugins/layer-demo/` — the minimal contract in two small files: a
  manifest declaring a `lore` layer and one tool mirroring notes through
  `layer_api.save`. Start here.
- `user/plugins/note-vault/` — the full playbook, working for real: mirrors a
  folder of markdown (bundled sample vault, or point `vault_path` at your own
  notes) with hash-based reconciliation via `get_sources`, paragraph chunking,
  removed-file cleanup, `force` re-weave after new entities, ledger etiquette
  (a no-change sync writes zero ledger lines), and its own pytest suite
  (`pytest user/plugins/note-vault/tests/`). Copy this shape for anything
  Obsidian-like.

## Gotchas

- **Lazy imports only.** `from plugins.mindpalace.tools import layer_api` at module top-level breaks under load-order changes and when Mind Palace is disabled. Inside the function, wrapped in try/except: always safe.
- **Don't write the palace DB directly.** `layer_api` is the contract; raw SQL against `mind.db` will break across palace versions and skips the ledger, weaving, and FTS.
- **`writable: false` still lets users edit.** The web tab lets the user delete/favorite your mirrored chunks — your next sync should tolerate missing rows (idempotent re-save by `source`).
- **Sign after every edit** (`python tools/sign_plugin.py <your-plugin>`) like any plugin.
