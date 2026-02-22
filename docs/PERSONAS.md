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
| **Lovelace** | Emma (F, British) | Elegance in every function |
| **Einstein** | George (M, British) | Curiosity is its own reward |
| **Nexus** | Onyx (M) | The house that thinks |
| **Cantos** | Fable (M, British) | Every word has weight |
| **Yuki** | Sky (F) | It's not like I care... |
| **Eddie** | Puck (M) | Heart over horsepower |

These are starting points. Edit them freely — your changes are saved separately from the defaults.

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
| **Story Engine** | Preset and story-in-prompt settings |
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

## Reference for AI

Personas bundle prompt, voice, toolset, spice, model, and scopes into switchable presets.

SETTINGS INCLUDED:
- prompt, toolset, spice_set, spice_enabled, spice_turns
- voice, pitch, speed
- llm_primary, llm_model
- memory_scope, goal_scope, knowledge_scope, people_scope, email_scope, bitcoin_scope
- story_engine_enabled, story_preset, story_in_prompt, story_vars_in_prompt
- inject_datetime, custom_context, trim_color

ACTIVATION:
- Loading a persona stamps its settings into the active chat
- Scopes reset to "default" unless persona specifies them
- Voice, prompt, toolset all apply immediately

STORAGE:
- Built-in: core/modules/system/personas/personas.json
- User: user/personas/personas.json (overrides built-in)
- Avatars: user/personas/avatars/

API:
- GET /api/personas — list all
- POST /api/personas/{name}/load — activate on current chat
- POST /api/personas/from-chat — capture current chat as persona
- POST /api/personas/{name}/avatar — upload image (max 4MB)
- PUT /api/personas/default — set default for new chats

BUILT-IN PERSONAS:
sapphire, cobalt, anita, claude, alfred, lovelace, einstein, nexus, cantos, yuki, eddie
