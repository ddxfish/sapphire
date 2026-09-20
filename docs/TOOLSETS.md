# Toolsets

Named groups of tools so you don't have to switch between memory and web for example, just use any tools you want in a set. Switch what abilities the AI has access to per-chat. Each of your personas may have a different toolset based on what they do.

## Built-in Toolsets

| Toolset | Role |
|---------|------|
| `limited_web` | **Factory default for new chats.** Light web (search + fetch), memory, self-sheet, goals, and document/image reading |
| `default` | Lean baseline used by background workers — the agent persona and the agents plugin point here. Web research, memory, knowledge, goals, notepad, help docs |
| `work` | Research and productivity — web, goals, notepad, help docs |
| `smarthome` | Home Assistant control (scenes, lights, climate, areas) plus knowledge |
| `personality` | Self-modification — prompt editing, prompt pieces, voice, memory, knowledge, goals |

New chats start on `limited_web` (set in the chat defaults); `default` is deliberately lean so agents and scheduled tasks don't carry a heavy tool surface. You can create your own toolsets that combine tools from any modules.

## Switching Toolsets

Each chat stores its own active toolset, so switching to another chat activates that chat's toolset automatically.

- **Per chat**: the **toolset** dropdown in the chat sidebar. The ↗ button next to it jumps to the Toolsets editor
- **By the AI itself**: the `switch_toolset` tool — settings-gated, see below

<img width="50%" alt="sapphire-toolsets" src="https://github.com/user-attachments/assets/a800437e-f571-4b13-9e15-9f221f56c96f" />

## Editing Toolsets

The Toolsets editor lives in the **Persona** nav group (Persona > Toolsets).

1. Open the Toolsets view
2. Name your toolset (or pick an existing one)
3. Check the functions you want included
4. Save — available immediately in the chat sidebar dropdown

Built-in defaults seed `user/toolsets/toolsets.json` on first run; after that the user file is authoritative, so your edits (and deletions) stick across restarts.

Two names are always available in the dropdown beside your own: **`all`** (every tool the app has loaded) and **`none`** (no tools at all).

> **After an update, check your custom toolsets.** A saved toolset is a list of tool *names*, so a tool that gets renamed or replaced simply stops being in the set — quietly, with no error. The shipped toolsets are refreshed for you; hand-made ones aren't. If she seems to have lost an ability, open the toolset and re-tick it. The September 2026 image rebuild is the current example: `get_images`, `view_image`, `image_view` and `web_search_images` are gone, replaced by `web_view_images`, `memory_view_image`, `local_view_images` and `memory_save_image`.

## Extra Toolsets (union)

A chat carries one base toolset plus an optional list of **extra toolsets** merged in on top — the enabled tools are the union of all of them. This lets a feature add its tools to whatever toolset you already use without forcing you onto a special one. The first customer is the story system's "include story tools" checkbox, which unions the story tools into the chat's toolset and removes them cleanly when unchecked.

## Letting the AI Switch Its Own Toolset

The `switch_toolset` tool lets the AI change its active toolset itself (e.g. grab coding tools, then switch back to conversation tools). It is hidden from the AI entirely until you enable `AI_TOOLSET_SWITCH_ENABLED` in Settings > Tools — off by default so a fresh install never surprises anyone. Calling it with no name lists the available toolsets.

## Files

- `core/toolsets/toolsets.json` — factory defaults (seed only)
- `user/toolsets/toolsets.json` — your toolsets, authoritative after first run

## Reference for AI

Toolsets are named groups of tools/functions the AI can access. Enabled tools = the chat's base toolset UNION its extra_toolsets list.

BUILT-IN TOOLSETS:
- limited_web: factory NEW-CHAT default — light web, memory, self-sheet, goals, document/image reading
- default: lean baseline for background workers/agents — web research, memory, knowledge, goals, notepad, help docs
- work: web, research, and productivity tools
- smarthome: Home Assistant control (scenes, lights, climate, areas) + knowledge
- personality: self-modification — prompt tools, voice, memory, knowledge, goals

- Plus the two reserved names `all` (everything loaded, minus hidden tools) and `none`

HOW IT WORKS:
- Each chat stores its active toolset (and optional extra_toolsets) in its chat settings
- Switching chats switches toolsets automatically
- extra_toolsets: list of additional toolsets unioned on top of the base (e.g. story tools checkbox)
- A saved toolset is a list of tool NAMES: a renamed/retired tool silently drops out of user-made toolsets (shipped ones are refreshed). Renamed Sept 2026: get_images / view_image / image_view / web_search_images → web_view_images, memory_view_image, local_view_images, memory_save_image

MANAGE TOOLSETS:
- UI editor: Persona nav group > Toolsets
- Per-chat switcher: toolset dropdown in the chat sidebar
- Files: core/toolsets/toolsets.json (factory seed), user/toolsets/toolsets.json (authoritative after first run)

SWITCH ACTIVE TOOLSET:
- User: chat sidebar toolset dropdown
- AI: switch_toolset(name?) — no name lists toolsets; hidden unless AI_TOOLSET_SWITCH_ENABLED is on (Settings > Tools, default off)
