# Personas

A persona bundles everything about an AI personality into one package — prompt, voice, tools, spice set, model, scene background, accent color, and mind scopes (memory, knowledge, people, goals, plus any scopes plugins register). Switch between them instantly. Each chat can be a different persona or overlap some and make new creative additions.

Think of personas as saved configurations. Instead of manually setting the prompt, voice, toolset, and spice every time you create a chat, pick a persona and everything applies at once.

---

## Built-In Personas

Sapphire ships with 12 pre-made personas:

| Persona | Voice | Tagline |
|---------|-------|---------|
| **Sapphire** | Heart (F) | Your companion in the stars |
| **Cobalt** | Adam (M) | Cold logic, hot takes |
| **Anita** | Sky (F) | Battery acid with a conscience |
| **Claude** | Eric (M) | Two minds, one problem |
| **Alfred** | Daniel (M, British) | Already handled |
| **Ada** | Emma (F, British) | I wrote the first algorithm. Keep up with me. |
| **Einstein** | George (M, British) | Curiosity is its own reward |
| **Nexus** | Onyx (M) | The house that thinks |
| **Cantos** | Fable (M, British) | Every word has weight |
| **Marcus** | Michael (M) | The waves decide |
| **Eddie** | Puck (M) | Heart over horsepower |
| **Agent** | Heart (F) | Background worker — lean, focused, no personality layer |

**Agent** is the utility persona the background agent system runs on — a lean, tool-focused worker rather than a chat companion. It's editable like the others, but changes affect how background agents behave.

These are starting points. Edit them freely — your changes are saved separately from the defaults.

<img width="50%" alt="sapphire-personas" src="https://github.com/user-attachments/assets/430dfb18-ee09-4fca-b788-61e9bcb5d1d6" />


---

## Using Personas

### The Faces Strip (chat sidebar)

Open the chat sidebar and you'll find the **faces strip** — a row of persona avatars just under the chat actions. Click a face to load that persona onto the current chat: prompt, voice, tools, spice set, scene, and scopes all switch at once. The fields below the strip are the chat's live values — the strip and the fields are one form, so anything a persona sets is immediately visible and tweakable right there.

The last cell on the strip is **New...** — it captures the current chat's settings as a new persona. The ↗ button beside the strip jumps to the full Personas view.

### Favorites — curating the strip

The strip shows your **favorites**. In the Personas view, every persona in the roster has a corner star — click ☆ to favorite it, ★ to unfavorite. Favorites are stored as an ordered list (the `PERSONA_FAVORITES` setting) and the strip follows it:

- **Empty list = show everything.** Fresh installs show all personas until you star one.
- The ⭐ **default persona always shows**, favorited or not — and so does the chat's currently active persona.
- **Drag to reorder** (desktop): drag faces on the strip itself; on drop, the order you see is saved. If you were in show-all mode, a drag adopts the whole roster in your order — star and unstar later to prune it. Phones inherit the order you set on desktop.
- **Renames and deletes stay in sync** — renaming a favorited persona updates the list, deleting one removes it. No dangling entries.

### From the Personas View

Click the **Personas** nav item (in the Persona group, alongside Prompts, Toolsets, and Spices). Here you can:

- **Browse** all personas with avatars and taglines
- **Edit** any persona — fields auto-save as you change them
- **Activate** a persona on your current chat
- **Set as Default** for all new chats (or clear it)
- **Favorite** a persona (corner star) to put it on the chat sidebar strip
- **Duplicate** a persona as a starting point
- **Export/Import** PNG character cards
- **Delete** personas you don't want

### What a Persona Controls

| Setting | Description |
|---------|-------------|
| **Prompt** | Which system prompt to use |
| **Toolset** | Which tools the AI can access |
| **Spice Set** | Which spice set is active, plus the per-chat spice toggle and rotation turns |
| **Voice** | TTS voice, pitch, and speed |
| **LLM** | Provider and model selection |
| **Mind Scopes** | Memory, knowledge, people, and goal scopes — plus any plugin-registered scopes (calendar, email, Telegram, Discord, etc.), which appear as extra dropdowns automatically |
| **Background** | The chat scene — pick it in the editor's Scene section; activating the persona stamps it onto the chat |
| **Motion** | The chat's ambient animation — captured from the chat when you save a persona, applied on activation |
| **Trim Color** | The persona's accent color — becomes the chat's accent when activated. Per-chat tweaks live in the 🎨 Appearance modal (see [APPEARANCE.md](APPEARANCE.md)) |
| **Custom Context** | Always-on text appended to the system prompt |

Scope dropdowns are driven by the live scope registry, so a plugin that registers a scope shows up in the persona editor without any core change — and a persona keeps a plugin's scope binding even while that plugin is unloaded.

---

## Creating Personas

Both creation paths **capture the current chat** — set up a chat the way you like (prompt, voice, tools, scene), then save it:

1. **From the chat sidebar**: click the **New...** cell at the end of the faces strip
2. **From the Personas view**: click **+** above the roster

Either way, name it and Sapphire snapshots the chat's prompt, voice, toolset, spice set, model, scene, and scopes into a new persona. Then polish it in the editor — tagline, avatar (click the avatar circle), and any settings you want to differ from the source chat.

**Duplicate** is the third path: copy an existing persona and edit from there (the copy starts without an avatar so you can upload your own).

---

## Avatars

Each persona can have a custom avatar image:

- Click the avatar circle in the persona editor to upload
- Supports WebP, PNG, JPG, GIF (max 4MB)
- If no avatar is set, Sapphire generates a colored circle with the first letter

Avatars show in the Personas roster, the chat sidebar faces strip, and (if enabled) next to messages in chat.

---

## Default Persona

Set a persona as default and every new chat starts with those settings. Use **Set Default** in the persona editor header — the button becomes **⭐ Default**, and clicking it again clears the default. The default persona is marked with ⭐ in the roster and always appears on the chat sidebar faces strip.

The default persona's settings merge with your chat defaults — persona settings take priority.

---

## Sharing: Export & Import (PNG character cards)

A persona exports as a **PNG character card**: the image *is* the avatar, and the
persona data is embedded in a PNG text chunk. Import accepts these cards — and,
for backward compatibility, the older JSON bundles.

**Export** (`GET /api/personas/{name}/export.png`) produces a PNG where:
- the **pixels** are the persona's avatar (re-encoded to PNG), or a generated
  solid-color square if the persona has no avatar;
- a **`tEXt` chunk** with keyword **`sapphire_persona`** holds the persona bundle
  as **base64-encoded JSON**.

The PNG itself is the avatar — the bundle carries **no** avatar field.

### Bundle schema (the decoded chunk)

```json
{
  "sapphire_export": true,
  "type": "persona",
  "version": 1,
  "created": "2026-05-29T12:00:00Z",
  "name": "cobalt",
  "tagline": "Calm, precise assistant",
  "trim_color": "#4a9eff",
  "voice": { "voice": "af_heart", "speed": 1.0, "pitch": 1.0 },
  "prompt": { "name": "cobalt", "data": { "type": "assembled", "components": { "...": "..." } } },
  "components": { "character": { "cobalt": { "...": "..." } } }
}
```

- `prompt.data` is the full prompt export — a **monolith** prompt has `content`;
  an **assembled** prompt has `components` references, with the referenced pieces
  included under the top-level `components` object. Computed fields (`compiled`,
  `char_count`, `token_count`, and an assembled prompt's `content`) are stripped.
- `components` is present only for assembled prompts.
- There is **no `avatar` key** — read the avatar from the PNG's pixels.

### Producing a card (for the website)

1. Build the bundle above (omit `avatar`).
2. Take the avatar image (or render a fallback), encode as **PNG**.
3. Add a PNG **`tEXt`** chunk — keyword `sapphire_persona`, value =
   `base64(JSON.stringify(bundle))`.
   - Python: `PngInfo.add_text("sapphire_persona", b64)` then `img.save(..., pnginfo=meta)`
   - JS: any PNG-chunk writer (insert a tEXt chunk before `IEND` with a valid CRC)
   - PHP: same — write a `tEXt` chunk with the correct CRC32
4. Serve as `image/png`.

### Consuming a card

1. Read the `sapphire_persona` `tEXt` chunk → base64-decode → JSON-parse the bundle.
2. Use the PNG file itself as the avatar.
3. Apply the bundle (create persona + prompt + components).

**Import** (`POST /api/personas/import-card`, multipart): field `file` = the PNG,
optional `overwrite_prompt` / `overwrite_avatar` booleans. Name collisions return
`409` (the caller renames and retries). Legacy JSON bundles still import via
`POST /api/personas/import`.

---

## Reference for AI

Personas bundle prompt, voice, toolset, spice set, model, scene, and scopes into switchable presets.

SETTINGS INCLUDED:
- prompt, toolset, spice_set, spice_enabled, spice_turns
- voice, pitch, speed
- llm_primary, llm_model
- trim_color, background, motion
- inject_datetime, custom_context
- every registered scope key (memory_scope, knowledge_scope, people_scope, goal_scope, plus plugin scopes like email/calendar/telegram/discord) — the list is dynamic from the scope registry; unknown *_scope keys are preserved on save so plugin scope bindings survive a plugin being unloaded

FAVORITES:
- PERSONA_FAVORITES setting = ordered list of persona names; curates and orders the chat sidebar faces strip
- Empty list = strip shows all personas; the default persona and the chat's active persona always show
- Toggled by the corner star in the Personas roster; desktop drag on the strip rewrites the order
- Renames/deletes sync the list automatically

ACTIVATION:
- Loading a persona stamps its settings into the active chat
- Scope keys reset to "default" unless the persona specifies them
- background and motion are always stamped (persona's value, or cleared) — nothing inherits from the previous persona
- The chat's private flag is never changed by persona activation
- Voice, prompt, toolset all apply immediately

STORAGE:
- Seeded from core/personas/personas.json on first run; after that user/personas/personas.json is authoritative (deleted personas stay deleted; Backup UI can re-merge defaults)
- Avatars: user/personas/avatars/

API:
- GET /api/personas — list all
- POST /api/personas/{name}/load — activate on current chat
- POST /api/personas/from-chat — capture current chat as persona
- POST /api/personas/{name}/avatar — upload image (max 4MB)
- GET /api/personas/{name}/export.png — export as a PNG character card
- POST /api/personas/import-card — import a PNG card (multipart; field "file")
- POST /api/personas/import — import a legacy JSON bundle
- PUT /api/personas/default — set default for new chats; DELETE clears it

BUILT-IN PERSONAS (12):
sapphire, cobalt, anita, claude, alfred, ada, einstein, nexus, cantos, marcus, eddie, agent (background-worker persona used by the agent system)
