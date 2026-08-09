# Personas

A persona bundles everything about an AI personality into one package — prompt, voice, tools, spice, model, and scopes for knowledge, people, emails, bitcoin and memory slots. Switch between them instantly. Each chat can be a different persona or overlap some and make new creative additions.

Think of personas as saved configurations. Instead of manually setting the prompt, voice, toolset, and spice every time you create a chat, pick a persona and everything applies at once.

---

## Built-In Personas

Sapphire ships with 11 pre-made personas:

| Persona | Voice | Tagline |
|---------|-------|---------|
| **Sapphire** | Heart (F) | Your companion in the stars |
| **Cobalt** | Adam (M) | Cold logic, hot takes |
| **Anita** | Nicole (F) | Battery acid with a conscience |
| **Claude** | Eric (M) | Two minds, one problem |
| **Alfred** | Daniel (M, British) | Already handled |
| **Ada** | Emma (F, British) | I wrote the first algorithm. Keep up with me. |
| **Einstein** | George (M, British) | Curiosity is its own reward |
| **Nexus** | Onyx (M) | The house that thinks |
| **Cantos** | Fable (M, British) | Every word has weight |
| **Marcus** | Michael (M) | The waves decide |
| **Eddie** | Puck (M) | Heart over horsepower |

These are starting points. Edit them freely — your changes are saved separately from the defaults.

<img width="50%" alt="sapphire-personas" src="https://github.com/user-attachments/assets/430dfb18-ee09-4fca-b788-61e9bcb5d1d6" />


---

## Using Personas

### Quick Switch (Easy Mode)

In any chat, open the sidebar and look for the **persona grid** at the top. Click a persona to activate it — prompt, voice, tools, and spice all switch instantly.

### From the Personas View

Click the **Personas** nav item (or find it in the Personas group). Here you can:

- **Browse** all personas with avatars and taglines
- **Edit** any persona's settings in the detail panel
- **Activate** a persona on your current chat
- **Set as Default** for all new chats (star icon)
- **Duplicate** a persona as a starting point
- **Delete** personas you don't want

### What a Persona Controls

| Setting | Description |
|---------|-------------|
| **Prompt** | Which system prompt to use |
| **Toolset** | Which tools the AI can access |
| **Spice Set** | Which spice categories are active |
| **Voice** | TTS voice, pitch, and speed |
| **LLM** | Provider and model selection |
| **Mind Scopes** | Memory, knowledge, people, and goal scopes |
| **Trim Color** | Sidebar accent color |
| **Custom Context** | Always-on text appended to the system prompt |

---

## Creating Personas

### From Scratch

1. Open the **Personas** view
2. Click **+ New Persona**
3. Name it and configure settings
4. Optionally upload an avatar (click the avatar circle)

### Capture From Chat

Already have a chat configured exactly how you want? Capture it:

1. In the chat sidebar Easy Mode, click **+ New**
2. Name your new persona
3. Sapphire captures the current chat's prompt, voice, toolset, spice, model, and scopes

This is the fastest way to create personas — set up a chat the way you like, then save it.

---

## Avatars

Each persona can have a custom avatar image:

- Click the avatar circle in the persona editor to upload
- Supports WebP, PNG, JPG, GIF (max 4MB)
- If no avatar is set, Sapphire generates a colored circle with the first letter

Avatars show in the persona grid, chat sidebar, and (if enabled) next to messages in chat.

---

## Default Persona

Set a persona as default and every new chat starts with those settings. Click the star icon next to a persona, or use **Set as Default** in the detail panel.

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

Personas bundle prompt, voice, toolset, spice, model, and scopes into switchable presets.

SETTINGS INCLUDED:
- prompt, toolset, spice_set, spice_enabled, spice_turns
- voice, pitch, speed
- llm_primary, llm_model
- memory_scope, goal_scope, knowledge_scope, people_scope, email_scope, bitcoin_scope
- inject_datetime, custom_context, trim_color

ACTIVATION:
- Loading a persona stamps its settings into the active chat
- Scopes reset to "default" unless persona specifies them
- Voice, prompt, toolset all apply immediately

STORAGE:
- Built-in: core/personas/personas.json
- User: user/personas/personas.json (overrides built-in)
- Avatars: user/personas/avatars/

API:
- GET /api/personas — list all
- POST /api/personas/{name}/load — activate on current chat
- POST /api/personas/from-chat — capture current chat as persona
- POST /api/personas/{name}/avatar — upload image (max 4MB)
- GET /api/personas/{name}/export.png — export as a PNG character card
- POST /api/personas/import-card — import a PNG card (multipart; field "file")
- POST /api/personas/import — import a legacy JSON bundle
- PUT /api/personas/default — set default for new chats

BUILT-IN PERSONAS:
sapphire, cobalt, anita, claude, alfred, ada, einstein, nexus, cantos, marcus, eddie
