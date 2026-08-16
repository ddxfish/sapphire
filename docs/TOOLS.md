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
| `save_memory` | memory.py | Store info to long-term memory (labeled, embedded) |
| `search_memory` | memory.py | Semantic + keyword search across memories |
| `get_recent_memories` | memory.py | Get latest memories, optionally by label |
| `delete_memory` | memory.py | Remove memory by ID |
| `save_person` | knowledge.py | Save/update contact info (upsert by name) |
| `save_knowledge` | knowledge.py | Store reference data in categories (auto-chunks) |
| `search_knowledge` | knowledge.py | Search people + knowledge + RAG documents |
| `delete_knowledge` | knowledge.py | Delete AI-created entries or categories |
| `create_goal` | goals.py | Create goal or subtask with priority |
| `list_goals` | goals.py | Overview or detailed view of goals |
| `update_goal` | goals.py | Modify goal fields, log progress notes |
| `delete_goal` | goals.py | Delete goal with optional subtask cascade |
| `notepad_read` | notepad.py | Read scratch notepad with line numbers |
| `notepad_append_lines` | notepad.py | Add lines to notepad |
| `notepad_delete_lines` | notepad.py | Delete specific lines |
| `notepad_insert_line` | notepad.py | Insert line at position |

### Web & Research

| Tool | Module | What it does |
|------|--------|--------------|
| `web_search` | web.py | DuckDuckGo search, returns titles + URLs |
| `get_website` | web.py | Fetch and read full webpage content |
| `get_wikipedia` | web.py | Get Wikipedia article summary |
| `research_topic` | web.py | Advanced multi-page research |
| `get_site_links` | web.py | Extract navigation links from a site |
| `get_images` | web.py | Extract image URLs from a page |
| `ask_claude` | ai.py | Query Claude API for complex analysis |

### Self-Modification

| Tool | Module | What it does |
|------|--------|--------------|
| `view_prompt` | meta.py | View current or named system prompt |
| `switch_prompt` | meta.py | Switch to a different prompt preset |
| `edit_prompt` | meta.py | Replace monolith prompt content |
| `set_piece` | meta.py | Set/add assembled prompt component |
| `remove_piece` | meta.py | Remove from emotions/extras list |
| `create_piece` | meta.py | Create new prompt piece and activate |
| `list_pieces` | meta.py | List available pieces for a component |
| `reset_chat` | meta.py | Clear chat history |
| `change_username` | meta.py | Update username setting |
| `set_tts_voice` | meta.py | Change TTS voice |
| `list_tools` | meta.py | List enabled or all tools |
| `get_time` | clock plugin | Current date/time (the clock plugin also adds set_timer, set_stopwatch, set_alarm) |

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
| `generate_scene_image` | image.py | Generate SDXL image from description |
| `get_inbox` | email_tool.py | Fetch recent emails |
| `read_email` | email_tool.py | Read email by index |
| `archive_emails` | email_tool.py | Archive emails |
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

Tools are provided by **plugins**. Memory/knowledge/goals/people tools live in `plugins/memory/tools/`, other plugin tools in `plugins/*/tools/`, and AI-created tools in `user/plugins/*/tools/`. A small number of standalone tools remain in `functions/` (web, meta, ai).

| Path | Purpose | Git Tracked |
|------|---------|-------------|
| `plugins/memory/tools/` | Memory, knowledge, goals, people tools | Yes |
| `plugins/*/tools/` | Plugin tools (HA, SSH, email, bitcoin, toolmaker, agents) | Yes |
| `functions/` | Standalone tools (web search, meta, ai) | Yes |
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

- **Tool not working**: Check it's in the active toolset (Settings > Toolsets or chat sidebar)
- **"No executor"**: Tool file missing or has import errors — check logs
- **Network tools failing**: Check SOCKS proxy settings if enabled
- **AI-created tool not loading**: Call `tool_load()` after `tool_save()`, or use Rescan in Settings > Plugins

## Reference for AI

Tools are functions the AI calls to interact with systems — web search, memory, device control.

TOOL MODULES (19 listed, 75+ functions):
- memory_tools.py (plugins/memory): save_memory, search_memory, get_recent_memories, delete_memory
- knowledge_tools.py (plugins/memory): save_person, save_knowledge, search_knowledge, delete_knowledge
- goals_tools.py (plugins/memory): create_goal, list_goals, update_goal, delete_goal
- web.py: web_search, get_website, get_wikipedia, research_topic, get_site_links, get_images
- ai.py: ask_claude
- meta.py: view_prompt, switch_prompt, edit_prompt, set_piece, remove_piece, create_piece, list_pieces, reset_chat, change_username, set_tts_voice, list_tools
- toolmaker.py: tool_save, tool_read, tool_load
- homeassistant.py: 13 HA control functions (incl. ha_get_camera_image)
- image.py: generate_scene_image
- clock plugin: get_time, set_timer, set_stopwatch, set_alarm
- agents plugin: agent_options, spawn_agent, check_agents, recall_agent, dismiss_agent
- schedule_tool.py: schedule_task
- email_tool.py: get_inbox, read_email, archive_emails, get_recipients, send_email
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
