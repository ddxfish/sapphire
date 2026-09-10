# Tools

Tools are functions the AI can call to interact with the world — search the web, save memories, control devices, etc. The AI decides when to use tools based on context.

**Terminology:** In Sapphire, "tools", "functions", and "abilities" are used interchangeably.

## What Are Tools?

When you ask the AI something like "search for news about SpaceX", the AI recognizes it needs the `web_search` tool and calls it automatically. You don't say a magic keyword — the AI figures it out from your request.

**Tools vs Voice Commands:**
- **Tools**: The AI decides to call them. Contextual, flexible.
- **Voice Commands**: YOU trigger them with keywords. Deterministic, predictable. Declared via plugins.

## Using Tools

<img width="50%" alt="sapphire-image-gallery-in-chat" src="https://github.com/user-attachments/assets/91e1c1f5-6dbb-4cd7-a05f-927ad354fe47" />


### Toolsets

Tools are grouped into **toolsets** — named collections you can switch between. Each persona can have its own custom set of tools you choose. See [TOOLSETS.md](TOOLSETS.md).

---

## Included Tools

Sapphire ships with a large set of built-in tools across core modules and plugins:

### Memory & Knowledge

| Tool | Module | What it does |
|------|--------|--------------|
| `save_memory` | memory_tools.py | Store info to long-term memory (labeled, embedded) |
| `search_memory` | memory_tools.py | Semantic + keyword search across memories |
| `get_recent_memories` | memory_tools.py | Get latest memories, optionally by label |
| `delete_memory` | memory_tools.py | Remove memory by ID |
| `save_person` | knowledge_tools.py | Save/update contact info (upsert by name) |
| `save_knowledge` | knowledge_tools.py | Store reference data in categories (auto-chunks) |
| `search_knowledge` | knowledge_tools.py | Search people + knowledge + RAG documents |
| `delete_knowledge` | knowledge_tools.py | Delete AI-created entries or categories |
| `create_goal` | goals_tools.py | Create goal or subtask with priority |
| `list_goals` | goals_tools.py | Overview or detailed view of goals |
| `update_goal` | goals_tools.py | Modify goal fields, log progress notes |
| `delete_goal` | goals_tools.py | Delete goal with optional subtask cascade |
| `notepad_read` | notepad.py | Read scratch notepad with line numbers |
| `notepad_append_lines` | notepad.py | Add lines to notepad |
| `notepad_delete_lines` | notepad.py | Delete specific lines |
| `notepad_insert_line` | notepad.py | Insert line at position |

> **Mind Palace note:** when the Mind Palace memory engine is enabled, it swaps in its own memory tool surface — the same core verbs plus `update_memory` and layered saves. The rows above describe the classic memory plugin.

### Web & Research

| Tool | Module | What it does |
|------|--------|--------------|
| `web_search` | web.py | DuckDuckGo search, returns titles + URLs |
| `get_website` | web.py | Fetch and read full webpage content |
| `get_wikipedia` | web.py | Get Wikipedia article summary |
| `research_topic` | web.py | Advanced multi-page research |
| `get_site_links` | web.py | Extract navigation links from a site |
| `ask_claude` | ai.py | Query Claude API for complex analysis |

### Images

One primitive (`core/images.py`), one return contract, one handle. Every tool that returns an image gets an `(image img:<id>)` receipt line appended to its result; any image tool accepts that handle. The viewing tools show the model ONE image — the picture, or a numbered contact sheet — and the user the same set as a numbered row of tiles (same numbers, so "#3" means the same picture to both). Each picture behind a sheet is an `img:` handle kept in the chat, so she can look closer or save any of them.

| Tool | Module | What it does |
|------|--------|--------------|
| `web_view_images` | web.py | Search the web for images (`query`, `count` 1-12 default 6, `page`), or look at one image `url`. `view` defaults true. Safe search = Settings › Images |
| `memory_view_image` | mindpalace library_tools.py | A remembered picture: library pictures by what's in them (`query`, `count`) as a numbered sheet with `[doc N]` ids; one `document_id`; or one picture from this chat by `image_id` (`img:<id>`) |
| `local_view_images` | mindpalace library_tools.py | Image files on this machine: `paths` (one → the image; several → a sheet) or a `folder` (paged, subfolders listed) |
| `memory_save_image` | mindpalace library_tools.py | Keep an image in the library under a topic (Knowledge tab; pixel + caption search; optional `private_key`) |
| `get_website` (`show_image_urls`) | web.py | `true` appends the page's image URLs (alt, size) to the text; `only` returns just that list |

Pasted images get the same treatment: they're kept in the chat's image store with an `img:` receipt she can hand to any image tool. **Images › Image memory turns** (default 3) keeps every image the model saw visible to it for that many turns; older ones she re-views by handle.
| `telegram_send_image` | telegram plugin | Send an image (`source=` any handle; default = newest image of this chat) |
| `generate_image` | sd-server plugin | Generate images on a local SD server (single or contact sheet) |

### Self-Modification

| Tool | Module | What it does |
|------|--------|--------------|
| `prompt_view` | meta.py | View current or named system prompt |
| `prompt_switch` | meta.py | Switch to a different prompt preset |
| `prompt_edit` | meta.py | Edit the active monolith prompt (exact text replacement) |
| `prompt_create` | meta.py | Create a new named prompt (does not activate it) |
| `prompt_pieces` | meta.py | Manage assembled prompt pieces — one action-based tool (list/view/set/remove/create/delete, with optional temporary activation) |
| `reset_chat` | meta.py | Clear chat history |
| `change_username` | meta.py | Update username setting |
| `set_voice` | meta.py | Change TTS voice, speed, and pitch |
| `set_motion` | meta.py | Set an ambient motion animation behind the chat |
| `set_scene` | scene.py | Set the chat scene background from your library |
| `switch_model` | meta.py | Switch its own LLM from the configured roster (settings-gated) |
| `switch_toolset` | meta.py | Switch its own active toolset (settings-gated) |
| `list_tools` | meta.py | List enabled or all tools |
| `get_time` | clock plugin | Current date/time (the clock plugin also adds set_timer, set_stopwatch, set_alarm) |

> **Settings gates:** `switch_model` and `switch_toolset` are hidden from the AI entirely until you enable `AI_MODEL_SWITCH_ENABLED` / `AI_TOOLSET_SWITCH_ENABLED` in Settings > Tools. Both default off — a fresh install's AI can't switch its own model or toolset until you opt in.

### Tool Creation

| Tool | Module | What it does |
|------|--------|--------------|
| `tool_save` | toolmaker.py | Create/update custom tool plugin (validated) |
| `tool_read` | toolmaker.py | Read custom tool source code |
| `tool_load` | toolmaker.py | Activate new tools live (no restart) |

### Integrations

| Tool | Module | What it does |
|------|--------|--------------|
| `ha_list_scenes_and_scripts` | homeassistant.py | List HA scenes/scripts |
| `ha_activate` | homeassistant.py | Run scene or script |
| `ha_list_areas` | homeassistant.py | List home areas |
| `ha_area_light` | homeassistant.py | Set area brightness |
| `ha_area_color` | homeassistant.py | Set area RGB color |
| `ha_get_thermostat` | homeassistant.py | Get thermostat reading |
| `ha_set_thermostat` | homeassistant.py | Set target temperature |
| `ha_list_lights_and_switches` | homeassistant.py | List controllable devices |
| `ha_set_light` | homeassistant.py | Control specific light |
| `ha_set_switch` | homeassistant.py | Toggle switch on/off |
| `ha_notify` | homeassistant.py | Send phone notification |
| `ha_house_status` | homeassistant.py | Home status snapshot |
| `ha_get_camera_image` | homeassistant.py | Grab a camera snapshot |
| `get_inbox` | email_tool.py | Fetch recent emails |
| `read_email` | email_tool.py | Read email by index |
| `search_emails` | email_tool.py | Search the mailbox |
| `archive_emails` | email_tool.py | Archive emails |
| `delete_emails` | email_tool.py | Delete emails |
| `forward_email` | email_tool.py | Forward an email to a whitelisted contact |
| `get_recipients` | email_tool.py | List whitelisted contacts (IDs only) |
| `send_email` | email_tool.py | Send to whitelisted contact |
| `get_wallet` | bitcoin_tool.py | Check wallet balance |
| `send_bitcoin` | bitcoin_tool.py | Send BTC |
| `get_transactions` | bitcoin_tool.py | Recent transactions |
| `ssh_get_servers` | ssh_tool.py | List SSH servers |
| `ssh_run_command` | ssh_tool.py | Execute remote command |
| `calendar_today` | calendar.py | Today's Google Calendar schedule |
| `calendar_range` | calendar.py | Events for a date range |
| `calendar_add` | calendar.py | Add a calendar event |
| `calendar_delete` | calendar.py | Delete a calendar event |

### Utilities

| Tool | Module | What it does |
|------|--------|--------------|
| `get_external_ip` | network.py | Public IP via proxy |
| `check_internet` | network.py | Internet connectivity test |
| `website_status` | network.py | Check if URL is up |
| `search_help_docs` | docs.py | Search Sapphire documentation |

---

## Managing Tools

### Where Tools Live

Tools are provided by **plugins**. Memory/knowledge/goals/people tools live in `plugins/memory/tools/`, other plugin tools in `plugins/*/tools/`, and AI-created tools in `user/plugins/*/tools/`. Standalone core tool modules live in `functions/` (web, meta/self-modification, ai, network, notepad, docs, scene, schedule).

| Path | Purpose | Git Tracked |
|------|---------|-------------|
| `plugins/memory/tools/` | Memory, knowledge, goals, people tools | Yes |
| `plugins/*/tools/` | Plugin tools (HA, SSH, email, bitcoin, toolmaker, agents, calendar, sd-server, clock, ...) | Yes |
| `functions/` | Standalone tools (web, meta, ai, network, notepad, docs, scene, schedule) | Yes |
| `user/plugins/*/tools/` | AI-created tool plugins | No |

### Enable/Disable

Tools are managed through **toolsets** and **plugins**:
- **Toolsets**: Choose which tools are available per chat/persona. See [TOOLSETS.md](TOOLSETS.md).
- **Plugins**: Enable/disable entire plugins (and their tools) in Settings > Plugins. Changes are live.

---

## AI Tool Creation (Tool Maker)

Sapphire can create her own tools using the **Tool Maker** plugin. The AI writes a tool, validates it, saves it as a plugin, and loads it live — no restart needed.

Tools: `tool_save`, `tool_read`, `tool_load`

For the full Tool Maker guide (format, settings, examples): see [TOOLMAKER.md](TOOLMAKER.md).

**Validation strictness** (configurable in Settings > Tool Maker):
- `strict` — Only allowlisted imports (json, re, datetime, math, requests, etc.)
- `moderate` — Blocks dangerous operations (subprocess, shutil, eval, os.system, etc.)
- `system_killer` — Syntax check only (legacy alias: `trust`)

AI-created tools appear as plugins and can be enabled/disabled like any plugin.

---

## Creating Plugins Manually

For full plugin development (tools + hooks + voice commands + schedules + web UI), see the [Plugin Author Guide](plugin-author/README.md).

---

## Troubleshooting

- **Tool not working**: Check it's in the active toolset (the Toolsets view in the Persona nav group, or the chat sidebar's toolset field)
- **"No executor"**: Tool file missing or has import errors — check logs
- **Network tools failing**: Check SOCKS proxy settings if enabled
- **AI-created tool not loading**: Call `tool_load()` after `tool_save()`, or use Rescan in Settings > Plugins

## Reference for AI

Tools are functions the AI calls to interact with systems — web search, memory, device control.

TOOL MODULES:
- memory_tools.py (plugins/memory): save_memory, search_memory, get_recent_memories, delete_memory
- knowledge_tools.py (plugins/memory): save_person, save_knowledge, search_knowledge, delete_knowledge
- goals_tools.py (plugins/memory): create_goal, list_goals, update_goal, delete_goal
- Mind Palace engine (when enabled) swaps in its own memory tool surface: same core verbs plus update_memory(memory_id, ...), layered saves
- web.py: web_search, get_website(url, show_image_urls?=false|true|only), get_wikipedia, research_topic, get_site_links, web_view_images(query? | url?, count?=6, page?=1, view?=true)
- mindpalace library_tools.py: library, read_document, memory_view_image(query? + count? | document_id | image_id=img:<id>, private_key?), local_view_images(paths? | folder?, page?, count?), memory_save_image(source, topic, caption?, private_key?) — source = img:<id> | doc:<N> | /abs/path | URL; every image-returning tool appends an '(image img:<id>)' receipt line
- ai.py: ask_claude
- meta.py: prompt_view(name?), prompt_switch(name?), prompt_edit(old_text, new_text) [monolith mode], prompt_create(name, content), prompt_pieces(action=list|view|set|remove|create|delete, component?, key?, value?, minutes?) [assembled mode], set_voice(name?, speed?, pitch?), reset_chat(reason), change_username(name), list_tools(scope?), set_motion(name?), switch_model(name?) + switch_toolset(name?) [hidden unless AI_MODEL_SWITCH_ENABLED / AI_TOOLSET_SWITCH_ENABLED on in Settings > Tools]
- scene.py: set_scene(name) — chat scene background, 'none' clears
- toolmaker.py: tool_save, tool_read, tool_load
- homeassistant.py: ha_list_scenes_and_scripts, ha_activate, ha_list_areas, ha_area_light, ha_area_color, ha_get_thermostat, ha_set_thermostat, ha_list_lights_and_switches, ha_set_light, ha_set_switch, ha_notify, ha_house_status, ha_get_camera_image
- clock plugin: get_time, set_timer, set_stopwatch, set_alarm
- agents plugin: agent_options, spawn_agent, check_agents, recall_agent, dismiss_agent
- schedule_tool.py: schedule_task
- email_tool.py: get_inbox, read_email, search_emails, archive_emails, delete_emails, forward_email, get_recipients, send_email
- bitcoin_tool.py: get_wallet, send_bitcoin, get_transactions
- ssh_tool.py: ssh_get_servers, ssh_run_command
- calendar.py (plugins/google-calendar): calendar_today, calendar_range, calendar_add, calendar_delete
- network.py: get_external_ip, check_internet, website_status
- notepad.py: notepad_read, notepad_append_lines, notepad_delete_lines, notepad_insert_line
- docs.py: search_help_docs

TOOL CREATION: Use tool_save + tool_load. For format and rules, see TOOLMAKER doc.

TROUBLESHOOTING:
- Tool not working: Check it's in active toolset
- "No executor": Tool file missing or has errors
- Network tools failing: Check SOCKS proxy if enabled
