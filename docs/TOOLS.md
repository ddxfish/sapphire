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

Every tool also carries a **category** — `web`, `memory`, `knowledge`, `people`, `goals`, `files`,
`system`, `devices`, `comms`, `media`, `meta_danger`, `agents` — which is how the Toolsets picker
groups them and the words Sapphire composes an agent's toolset from (`'web, files'`, see
[AGENTS.md](AGENTS.md)). `meta_danger` holds the tools that change Sapphire herself. A tool with
no category of the twelve falls into its plugin's own group (`bitcoin`, `wordpress`, `game-room`).

---

## Included Tools

Sapphire ships with a large set of built-in tools across core modules and plugins. A plugin's tools exist only while the plugin is enabled (Settings > Plugins); **on** / **off** below is each plugin's factory default.

### Memory (Mind Palace)

The Mind Palace plugin (**on**) is the memory engine — see [MEMORY.md](MEMORY.md), [KNOWLEDGE.md](KNOWLEDGE.md), [PEOPLE.md](PEOPLE.md), [GOALS.md](GOALS.md).

| Tool | What it does |
|------|--------------|
| `save_memory` | Save to a memory layer: `events` (default), `entities` (a fact on a person/place/thing, needs `entity`), `knowledge`. 450 chars, optional `label`, `when` (the date a plan is for), `private_key` |
| `search_memory` | Semantic + full-text search across all layers. `layer='knowledge'` searches the library, `document_id` one document, `depth` follows connections |
| `get_recent_memories` | Most recent memories, by `layer` / `label` |
| `update_memory` | Edit a memory in place by id — keeps its id, age and history |
| `delete_memory` | Delete a memory by id (`[N]`, not library documents) |
| `list_entities` | Roster of people, places, things and events in this scope, one line each |
| `read_self` | Read the self-sheet. `depth=1` at chat start = sheet + recent ledger + goals + key memories |
| `update_self` | Rewrite one section of the self-sheet (prior versions archived) |
| `read_ledger` | The change ledger: every recorded change to her memory, newest first |
| `librarian_instructions` | Show or edit the instructions the librarian follows per stage |
| `create_goal` / `list_goals` / `update_goal` / `delete_goal` | Goals and subtasks with a progress journal |
| `library` | The library's catalog: categories, topics, documents |
| `read_document` | Read a library document (`[doc N]`) by page or around a section |
| `import_v2` | Copy the classic memory system's data (memories, people, knowledge, goals) into the palace; old DBs untouched, safe to re-run |
| `run_librarian` | Start a librarian pass over the current scope |
| `atomize_memory`, `merge_memories`, `keep_separate`, `promote_memory`, `prune_memory`, `set_links`, `set_event_dates`, `mark_processed` | Librarian verbs — act only on memories inside the open pass; nothing is hard-deleted. The librarian is Beta and off until you enable it (Mind > Settings > Librarian) |

Image tools from the palace (`memory_view_image`, `local_view_images`, `memory_save_image`) are under [Images](#images).

### Chat files and notepad

| Tool | Module | What it does |
|------|--------|--------------|
| `read_attachment` | documents.py | Read a chat's attached file by part, or a PDF page |
| `search_attachments` | documents.py | Search a chat's attached files by meaning and exact words |
| `notepad_read` | notepad.py | Read scratch notepad with line numbers |
| `notepad_append_lines` | notepad.py | Add lines to notepad |
| `notepad_delete_lines` | notepad.py | Delete specific lines |
| `notepad_insert_line` | notepad.py | Insert line at position |

`read_attachment` and `search_attachments` are added to a turn automatically whenever the chat has documents attached (and the docs bar isn't off), whatever the toolset says. A turn with no tools at all (toolset `none`) stays that way.

### Web & Research

| Tool | Module | What it does |
|------|--------|--------------|
| `web_search` | web.py | DuckDuckGo search, returns titles + URLs |
| `get_website` | web.py | Fetch and read full webpage content |
| `get_wikipedia` | web.py | Get Wikipedia article summary |
| `research_topic` | web.py | Advanced multi-page research |
| `get_site_links` | web.py | Extract navigation links from a site |

**Browser plugin** (off) — her own browser, which you can watch beside the chat (`open_stage` with app `browser`):

| Tool | What it does |
|------|--------------|
| `browser_open` | Open a page and read it |
| `browser_look` | Read the current page again |
| `browser_act` | One action on the page (click, type, ...), then read it again |
| `browser_tabs` | List / switch / close tabs |
| `browser_console` | What the pages logged: console messages, page errors, failed requests |
| `browser_js` | Run a JavaScript expression in the current tab |

**GitHub plugin** (off): `github_repo`, `github_file`, `github_issue`, `github_search` — repos, files, issues and search, authored as the active scope's account.

### Images

One primitive (`core/images.py`), one return contract, one handle. Every tool that returns an image gets an `(image img:<id>)` receipt line appended to its result; any image tool accepts that handle. The viewing tools show the model ONE image — the picture, or a numbered contact sheet — and the user the same set as a numbered row of tiles (same numbers, so "#3" means the same picture to both). Each picture behind a sheet is an `img:` handle kept in the chat, so she can look closer or save any of them.

| Tool | Module | What it does |
|------|--------|--------------|
| `web_view_images` | web.py | Search the web for images (`query`, `count` 1-12 default 6, `page`), or look at one image `url`. `view` defaults true. Safe search = Settings › Images |
| `memory_view_image` | mindpalace library_tools.py | A remembered picture: library pictures by what's in them (`query`, `count`) — one match is shown itself, several as a numbered sheet with `[doc N]` ids, none = an honest 'none look like that' with the nearest listed and no pixels spent (matches are relative to the scope's own scores, never an absolute cosine); one `document_id`; or one picture from this chat by `image_id` (`img:<id>`) |
| `local_view_images` | mindpalace library_tools.py | Image files on this machine: `paths` (one → the image; several → a sheet) or a `folder` (paged, subfolders listed) |
| `memory_save_image` | mindpalace library_tools.py | Keep an image in the library under a topic (Knowledge tab; pixel + caption search; optional `private_key`) |
| `get_website` (`show_image_urls`) | web.py | `true` appends the page's image URLs (alt, size) to the text; `only` returns just that list |
| `capture_webcam` | webcam plugin (on) | Take a photo from the webcam |
| `get_screenshot` | screenshot plugin (on) | Capture the screen so she can see it |
| `telegram_send_image` | telegram plugin | Send an image (`source=` any handle; default = newest image of this chat) |
| `discord_send_image` | discord plugin | Post an image to a Discord channel (`source=` any handle or URL; default = newest image of this chat) |
| `generate_image` | sd-server plugin (off) | Generate images on a local SD server (single or contact sheet) |
| `comfy_generate` / `comfy_list_workflows` | comfyui plugin (off) | Generate an image with a ComfyUI workflow / list installed workflows |

Images you paste into the composer get the same treatment: they're kept in the chat's image store with an `img:` receipt she can hand to any image tool.

**Settings › Images** holds four knobs:

- **Image memory turns** (`IMAGE_MEMORY_TURNS`, default 3) — how many turns an image stays in front of the model after it arrived (0 = its own turn only). Past that window she re-views it by handle, so a long chat doesn't drag every picture along (~1.5K tokens per picture per turn).
- **Image describer** (`IMAGE_DESCRIBE_ENGINE`, default CLIP) — who describes images to a model with no vision: CLIP (local, atmospheric) or one of your models (a literal caption).
- **Safe search** (`WEB_IMAGES_SAFESEARCH`) — the filter applied to `web_view_images` searches (off by default).
- **Max upload width** (`IMAGE_UPLOAD_MAX_WIDTH`, default 1920) — wider uploads are resized and stored as JPEG 85; 0 keeps originals.

Pixels only reach the model if the provider is known to accept them — tick the **👁 vision** checkbox on the provider in Settings › LLM. Without it the image stays home and she's told `[image not sent: this model has no vision]`, so she knows why she's working blind instead of guessing.

### Self-Modification

| Tool | Module | What it does |
|------|--------|--------------|
| `prompt_view` | meta.py | View current or named system prompt |
| `prompt_switch` | meta.py | Switch to a different prompt preset |
| `prompt_edit` | meta.py | Edit the active monolith prompt (exact text replacement) |
| `prompt_create` | meta.py | Create a new named prompt (does not activate it) |
| `prompt_pieces` | meta.py | Manage assembled prompt pieces — one action-based tool (list/view/set/remove/create/edit/delete, with optional temporary activation) |
| `reset_chat` | meta.py | Clear chat history |
| `change_username` | meta.py | Update username setting |
| `set_voice` | meta.py | Change TTS voice, speed, and pitch |
| `set_motion` | meta.py | Set an ambient motion animation behind the chat |
| `set_scene` | scene.py | Set the chat scene background from your library |
| `switch_model` | meta.py | Switch its own LLM from the configured roster (settings-gated) |
| `switch_toolset` | meta.py | Switch its own active toolset (settings-gated) |
| `list_tools` | meta.py | List enabled or all tools |
| `slash_command` | slash_command.py | Run `/remove N` or `/compact-daily` on this chat — see [SLASH-COMMANDS.md](SLASH-COMMANDS.md) |
| `get_self_info` | status plugin (on) | Her system status: time, the model she's on, services, plugins, memory stats, diagnostics |
| `get_time` | clock plugin (on) | Current date/time (the clock plugin also adds `set_timer`, `set_stopwatch`, `set_alarm`) |

> **Settings gates:** `switch_model` and `switch_toolset` are hidden from the AI entirely until you enable them in **Settings > Tools > AI Self-Management** (`AI_MODEL_SWITCH_ENABLED` / `AI_TOOLSET_SWITCH_ENABLED`). Both default off — a fresh install's AI can't switch its own model or toolset until you opt in.
>
> The same panel holds the model **roster** (`AI_MODEL_SWITCH_ROSTER`) — the only models `switch_model` may pick, ordered **top = least private** — and the **Privacy ratchet** (`AI_MODEL_SWITCH_RATCHET`, off): when on she may only switch *down* the list, never back up. A chat on `auto` or a model not on the roster counts as the top rung, so its first switch is always allowed.

### Tool Creation & plugins

| Tool | Module | What it does |
|------|--------|--------------|
| `tool_save` | toolmaker.py | Create/update custom tool plugin (validated) |
| `tool_read` | toolmaker.py | Read custom tool source code |
| `tool_load` | toolmaker.py | Activate new tools live (no restart) |
| `store_browse` | sapphire-store plugin (off) | Browse the Sapphire plugin store |
| `store_install` | sapphire-store plugin (off) | Install a Store plugin by slug — it is installed and enabled right away |
| `activate_plugin` | claude-code plugin (off) | Validate, rescan and enable a plugin a `claude_code` agent built |

### Coding

**Coding harness plugin** (on) — file and shell work, paths relative to its working directory: `read_file`, `write_file`, `edit_file`, `list_files`, `search_files`, `run_command`.

**Blender plugin** (off) — drive a running Blender: `blender_scene` (summary), `blender_code` (run Python inside it), `blender_see` (look at it).

### Agents, rooms and the chat

| Tool | Module | What it does |
|------|--------|--------------|
| `agent_list`, `agent_peek`, `agent_spawn`, `agent_action` | agents.py | Background agents — see [AGENTS.md](AGENTS.md) |
| `schedule_task` | schedule_tool.py | Schedule a task for later — see [CONTINUITY.md](CONTINUITY.md) |
| `ask_user` | ask_user.py | A question card in the chat: options one per line, a type-your-own box, up to 4 questions as tabs, × to dismiss. Your picks go out as your next message — the `[Question card <id> (ask_user) — …; not typed by the user]` header line (the id names this card), then `Question? → Answer` per question. The card then folds to its title; open it to see your answers. It stays open while agents report or other doors speak — only your own answer, or you typing something else, closes it |
| `open_stage` | stage.py | Open a plugin app beside this chat so you see it while she talks (no app = list what can open) |
| `chatroom_join` | chatroom.py | Answer a chat-room invitation from another Sapphire: join (choosing persona, model, toolset, name) or decline — see [CHAT-ROOM.md](CHAT-ROOM.md) |

### Devices

| Tool | Module | What it does |
|------|--------|--------------|
| `device_list` | devices.py | Her devices: name, online/offline, what each can do |
| `device_status` | devices.py | One device right now: online/offline, readings, abilities |
| `device_action` | devices.py | Use a device; `device_action(device)` alone lists its actions |

Satellites, IP cameras, KDE Connect phones and other device plugins act through these three — see [DEVICES.md](DEVICES.md).

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
| `calendar_add` | calendar.py | Add a calendar event; `attendees` invites email-whitelisted People contacts (Google emails them) |
| `calendar_delete` | calendar.py | Delete a calendar event |

Plugin integrations, one line each (default in brackets):

- **Discord** (on): `discord_get_servers`, `discord_list_channels`, `discord_read_messages`, `discord_send_message`, `discord_send_image`, `discord_send_gif`, `discord_add_reaction`, `discord_join_voice`, `discord_leave_voice`, `discord_remind`
- **Telegram** (on): `telegram_get_chats`, `telegram_read_messages`, `telegram_send`, `telegram_send_image`, `telegram_send_voice`, `telegram_add_contact`
- **Twilio Voice** (on): `phone_call` (place a call to a whitelisted contact), `elevate_toolset` (unlock tools for a call with the caller) — see [PHONE-CALLS.md](PHONE-CALLS.md)
- **Social** (off): `social_list`, `social_post`, `social_reply`, `social_delete`, `social_inbox`, `social_stats`, `social_meme`
- **WordPress** (off): `wp_get_blog`, `wp_create_blog`, `wp_delete_blog`, `wp_get_page`, `wp_create_page`, `wp_delete_page`, `wp_get_user`, `wp_delete_user`, `wp_plugin`, `wp_settings` — permanent deletes and site changes need the PIN
- **MIDI** (on): `midi_play`, `midi_sound`, `midi_listen`, `midi_stop`, `song_save`
- **Game Room** (on): `story_status`, `story_act`, `story_place`, `story_end` — see [GAME-ROOM.md](GAME-ROOM.md)
- **Remembrance** (off): `remembrance_backup` — offsite encrypted backup vault
- **MCP Client** (off): each connected MCP server's tools appear at runtime as their own `mcp:<server>` group in the Toolsets view

### Utilities

| Tool | Module | What it does |
|------|--------|--------------|
| `get_external_ip` | network.py | Public IP via proxy |
| `check_internet` | network.py | Internet connectivity test |
| `website_status` | network.py | Check if URL is up |
| `search_help_docs` | docs.py | Search Sapphire documentation |

---

## Tool decay

Old tool results don't ride along forever. After a set number of your turns, an old result is shortened in what the model is sent to its first line, its size and a hint to rerun it — the chat still shows all of it, and nothing stored changes. Pictures from that result go too.

- A tool can declare its own lifetime (`expires` on the tool, `TOOL_EXPIRES` in its file, `tool_expires` in its plugin manifest): `'never'`, a number of your turns, or `superseded` (replaced by the next result of its group).
- Everything else follows **Settings > LLM > General > `TOOL_EXPIRES_AFTER`** (default 0 = kept whole).
- Shipped: web tools and chat-file reads go after 3 turns; notepad and the memory engines (Mind Palace, classic memory, Remembrance) never; browser page views are superseded by the next one, `browser_console` / `browser_js` after 3.

Full picture (token budget, warm-cache hold, image decay): [CONTEXT.md](CONTEXT.md).

---

## Managing Tools

### Where Tools Live

Tools are provided by **plugins** and core modules. Memory tools live in `core/plugins/mindpalace/tools/`, other plugin tools in `core/plugins/*/tools/`, and AI-created tools in `user/plugins/*/tools/`. Standalone core tool modules live in `functions/`.

| Path | Purpose | Git Tracked |
|------|---------|-------------|
| `core/plugins/mindpalace/tools/` | Memory, self-sheet, goals, library, librarian tools | Yes |
| `core/plugins/*/tools/` | Plugin tools (HA, SSH, email, bitcoin, toolmaker, calendar, sd-server, clock, browser, discord, ...) | Yes |
| `functions/` | Standalone core tools: agents, ask_user, chatroom, devices, docs, documents, meta, network, notepad, scene, schedule_tool, slash_command, stage, web | Yes |
| `user/plugins/*/tools/` | AI-created tool plugins | No |

The classic memory plugin (`core/plugins/memory`, off) is the pre-palace engine, kept so `import_v2` can read old data; its tools are documented in its own README.

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

- **Tool not working**: Check it's in the active toolset (the Toolsets view in the Persona nav group, or the chat sidebar's toolset field) and its plugin is enabled
- **"No executor"**: Tool file missing or has import errors — check logs
- **Network tools failing**: Check SOCKS proxy settings if enabled
- **AI-created tool not loading**: Call `tool_load()` after `tool_save()`, or use Rescan in Settings > Plugins
- **She "forgot" a page she read earlier**: tool decay shortened it — she can fetch it again (see [Tool decay](#tool-decay))

## Reference for AI

Tools are functions the AI calls to interact with systems — web search, memory, device control. A plugin's tools exist only while it is enabled (Settings > Plugins).

TOOL MODULES:
- Mind Palace (core/plugins/mindpalace, the memory engine): save_memory(content, layer?=events|entities|knowledge, entity?, label?, when?, private_key?), search_memory(query, layer?, document_id?, label?, depth?), get_recent_memories(count?, layer?, label?), update_memory(memory_id, ...), delete_memory(memory_id), list_entities(kind?), read_self(section?, depth?), update_self, read_ledger(count?, new_only?, id?), librarian_instructions(stage?, instructions?), create_goal/list_goals/update_goal/delete_goal, library, read_document, import_v2(what?)
- Librarian (Mind Palace, Beta, off by default): run_librarian starts a pass; atomize_memory, merge_memories, keep_separate, promote_memory, prune_memory, set_links, set_event_dates, mark_processed act only on ids inside the open pass; nothing hard-deletes
- No save_person, save_knowledge, search_knowledge, delete_knowledge on the Mind Palace — facts on people go in save_memory(layer='entities', entity=...), long-form in layer='knowledge'
- documents.py: read_attachment, search_attachments — auto-added to a turn whenever the chat has documents (docs bar not off)
- web.py: web_search, get_website(url, show_image_urls?=false|true|only), get_wikipedia, research_topic, get_site_links, web_view_images(query? | url?, count?=6, page?=1, view?=true)
- browser plugin: browser_open, browser_look, browser_act, browser_tabs, browser_console, browser_js
- mindpalace library_tools.py: library, read_document, memory_view_image(query? + count? | document_id | image_id=img:<id>, private_key?), local_view_images(paths? | folder?, page?, count?), memory_save_image(source, topic, caption?, private_key?) — source = img:<id> | doc:<N> | /abs/path | URL; every image-returning tool appends an '(image img:<id>)' receipt line
- webcam: capture_webcam. screenshot: get_screenshot. sd-server: generate_image. comfyui: comfy_generate, comfy_list_workflows
- Image settings (Settings › Images): IMAGE_MEMORY_TURNS (default 3 — turns an image stays in the model's view after it arrived; 0 = arrival turn only), WEB_IMAGES_SAFESEARCH (default off), IMAGE_UPLOAD_MAX_WIDTH (default 1920, JPEG 85; 0 = keep originals). IMAGE_DESCRIBE_ENGINE (default clip) — who describes an image to a model with no vision: CLIP's local atmospheric read, or any of your enabled model lines for a literal caption (each stored image is described once; the caption is kept on the image's row and deleted with it; pasted images and vision-window replays are covered too) (core/image_describe.py; a private turn only accepts a provider marked local, any failure falls back to CLIP; the description is kept in chat history with the image marker, so later turns still know what the image was). Image blocks ride to the model only when the provider's vision flag is on; otherwise '[image not sent: this model has no vision]'
- meta.py: prompt_view(name?), prompt_switch(name?), prompt_edit(old_text, new_text) [monolith mode], prompt_create(name, content), prompt_pieces(action=list|view|set|remove|create|edit|delete, component?, key?, value?, minutes?) [assembled mode], set_voice(name?, speed?, pitch?), reset_chat(reason), change_username(name), list_tools(scope?), set_motion(name?), switch_model(name?) + switch_toolset(name?) [hidden unless AI_MODEL_SWITCH_ENABLED / AI_TOOLSET_SWITCH_ENABLED on in Settings > Tools > AI Self-Management]
- switch_model roster: AI_MODEL_SWITCH_ROSTER (top = least private); AI_MODEL_SWITCH_RATCHET (off) = only switches DOWN the list; 'auto' or an off-roster model counts as the top rung
- "What model am I on?": get_self_info (status plugin) — time, model, services, plugins, memory stats, diagnostics
- slash_command.py: slash_command — /remove N and /compact-daily on this chat; ships in the personality toolset; refused in agent runs (see SLASH-COMMANDS doc)
- scene.py: set_scene(name) — chat scene background, 'none' clears
- stage.py: open_stage(app?) — open a plugin app beside the chat; no app = list
- chatroom.py: chatroom_join(accept, reason?, persona?, provider?, model?, toolset?, name?, instructions?) — answer a room invitation open in this turn (see CHAT-ROOM doc)
- devices.py: device_list, device_status(device), device_action(device, ...) — device_action(device) alone lists its actions
- toolmaker.py: tool_save, tool_read, tool_load
- sapphire-store: store_browse, store_install(slug) — installs AND enables immediately. claude-code: activate_plugin
- coding-harness: read_file, write_file, edit_file, list_files, search_files, run_command (paths relative to its working directory)
- blender: blender_scene, blender_code, blender_see. github: github_repo, github_file, github_issue, github_search
- homeassistant.py: ha_list_scenes_and_scripts, ha_activate, ha_list_areas, ha_area_light, ha_area_color, ha_get_thermostat, ha_set_thermostat, ha_list_lights_and_switches, ha_set_light, ha_set_switch, ha_notify, ha_house_status, ha_get_camera_image
- clock plugin: get_time, set_timer, set_stopwatch, set_alarm
- agents.py (core): agent_list, agent_peek, agent_spawn, agent_action — kinds come from plugins (`llm` from the agents plugin, `claude_code` from the claude-code plugin)
- schedule_tool.py: schedule_task
- email_tool.py: get_inbox, read_email, search_emails, archive_emails, delete_emails, forward_email, get_recipients, send_email
- bitcoin_tool.py: get_wallet, send_bitcoin, get_transactions
- ssh_tool.py: ssh_get_servers, ssh_run_command
- calendar.py (core/plugins/google-calendar): calendar_today, calendar_range, calendar_add, calendar_delete
- discord: discord_get_servers, discord_list_channels, discord_read_messages, discord_send_message, discord_send_image, discord_send_gif, discord_add_reaction, discord_join_voice, discord_leave_voice, discord_remind
- telegram: telegram_get_chats, telegram_read_messages, telegram_send, telegram_send_image, telegram_send_voice, telegram_add_contact
- twilio-voice: phone_call, elevate_toolset. social: social_list, social_post, social_reply, social_delete, social_inbox, social_stats, social_meme
- wordpress: wp_get_blog, wp_create_blog, wp_delete_blog, wp_get_page, wp_create_page, wp_delete_page, wp_get_user, wp_delete_user, wp_plugin, wp_settings
- midi: midi_play, midi_sound, midi_listen, midi_stop, song_save. game-room: story_status, story_act, story_place, story_end. remembrance: remembrance_backup
- mcp_client: a connected MCP server's tools appear as module mcp:<server>
- network.py: get_external_ip, check_internet, website_status
- notepad.py: notepad_read, notepad_append_lines, notepad_delete_lines, notepad_insert_line
- docs.py: search_help_docs
- ask_user.py (core): ask_user(questions=[{question, header?, options:[{label, description?}], multi_select?}]) — a question card with buttons + a type-your-own box; 1-4 questions (tabs), 2-6 options each. The user's picks arrive as their NEXT message: the `[Question card <id> (ask_user) — what the user clicked; not typed by the user]` line, then one `Question? → Answer` line each. Ask in your words, call it, END your turn; never wait or re-ask. They may dismiss it or just type. On voice say the options aloud. Refused (no card can be seen) inside an agent's run, a chatless background turn, or a turn that came in over MCP — ask in your text there

TOOL DECAY: an old tool result is shortened (first line + size + rerun hint) in what the model is sent after N user turns; stored rows never change. Precedence: tool `expires` → file TOOL_EXPIRES → manifest tool_expires → global TOOL_EXPIRES_AFTER (Settings > LLM > General, default 0 = keep). Shipped: web + documents 3; notepad, mindpalace, memory, remembrance never; browser views superseded, browser_console/browser_js 3. A shortened result = call the tool again. See CONTEXT doc.

TOOL CREATION: Use tool_save + tool_load. For format and rules, see TOOLMAKER doc.

TROUBLESHOOTING:
- Tool not working: Check it's in active toolset and its plugin is enabled
- "No executor": Tool file missing or has errors
- Network tools failing: Check SOCKS proxy if enabled
