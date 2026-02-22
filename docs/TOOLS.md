# Tools

Tools are functions the AI can call to interact with the world - search the web, save memories, control devices, etc. Unlike plugins (which YOU trigger with keywords), the AI decides when to use tools based on context.

**Terminology:** In Sapphire, "tools", "functions", and "abilities" are used interchangeably. They all mean the same thing: capabilities the AI can invoke.

## What Are Tools?

When you ask the AI something like "search for news about SpaceX", the AI recognizes it needs the `web_search` tool and calls it automatically. You don't say a magic keyword - the AI figures it out from your request.

**Tools vs Plugins:**
- **Tools**: The AI decides to call them. Contextual, flexible.
- **Plugins**: YOU trigger them with keywords. Deterministic, predictable.

## Using Tools

<img src="screenshots/tool-results.png" alt="Tool results in Sapphire" width="100%">

### Toolsets

Tools are grouped into **toolsets** - named collections you can switch between. Each persona can have its own custom set of tools you choose. See [TOOLSETS.md](TOOLSETS.md).

---

## Included Tools

Sapphire ships with 15 tool modules containing 74+ functions:

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
| `get_time` | meta.py | Get current date/time |

### Tool Creation

| Tool | Module | What it does |
|------|--------|--------------|
| `tool_save` | toolmaker.py | Create/update custom tool (validated) |
| `tool_read` | toolmaker.py | Read custom tool source code |
| `tool_activate` | toolmaker.py | Restart app to load new tools |

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

### Utilities

| Tool | Module | What it does |
|------|--------|--------------|
| `get_external_ip` | network.py | Public IP via proxy |
| `check_internet` | network.py | Internet connectivity test |
| `website_status` | network.py | Check if URL is up |
| `search_help_docs` | docs.py | Search Sapphire documentation |

---

## Managing Tools

### Locations

| Path | Purpose | Git Tracked |
|------|---------|-------------|
| `functions/` | Core tools | Yes |
| `user/functions/` | Your custom tools | No |

### Enable/Disable

Each tool file has `ENABLED = True/False` at the top. Set to `False` to disable without deleting.

## Custom Toolsets

Use the **Toolset Manager** in the web UI. See [TOOLSETS.md](TOOLSETS.md).

## AI Self-Creating Tools (Tool Maker)

Sapphire can create her own tools using the **Tool Maker** (`tool_save`, `tool_read`, `tool_activate`). The AI writes a tool module, validates it, saves to `user/functions/`, and restarts to load it. No manual file editing needed.

**Validation strictness** is a user setting (`TOOL_MAKER_VALIDATION`):
- `strict` — Only allowlisted imports (json, re, datetime, math, requests, etc.)
- `moderate` — Blocks dangerous operations (subprocess, shutil, eval, os.system, etc.)
- `trust` — Syntax check only

New tools appear in the **All** toolset automatically. Add them to other toolsets via the Toolset Manager.

## Creating Tools Manually

You can also create tools by hand or with an external AI:

> Create a Sapphire tool that [describe what you want].
>
> **Tool file requirements:**
> - Location: `functions/{name}.py` or `user/functions/{name}.py`
> - Must export: `ENABLED`, `AVAILABLE_FUNCTIONS`, `TOOLS`, and `execute()` function
> - `execute()` returns tuple: `(result_string, success_bool)`
> - Tool definitions use OpenAI function calling schema
>
> **Example tool file:**
> ```python
> import logging
>
> logger = logging.getLogger(__name__)
>
> ENABLED = True
>
> AVAILABLE_FUNCTIONS = ['my_tool']
>
> TOOLS = [
>     {
>         "type": "function",
>         "function": {
>             "name": "my_tool",
>             "description": "What this does and WHEN to use it",
>             "parameters": {
>                 "type": "object",
>                 "properties": {
>                     "query": {
>                         "type": "string",
>                         "description": "The search query"
>                     }
>                 },
>                 "required": ["query"]
>             }
>         }
>     }
> ]
>
> def execute(function_name, arguments, config):
>     try:
>         if function_name == "my_tool":
>             query = arguments.get('query')
>             if not query:
>                 return "I need a query.", False
>
>             # Do the work here
>             result = f"Processed: {query}"
>             return result, True
>
>         return f"Unknown function: {function_name}", False
>     except Exception as e:
>         logger.error(f"{function_name} error: {e}")
>         return f"Error: {str(e)}", False
> ```
>
> Give me the complete file, ready to drop in.

After the AI gives you the file:
1. Save to `user/functions/your_tool.py`
2. Add to a toolset (UI or JSON)
3. Restart Sapphire

## Technical Reference

Condensed reference for developers and AI assistants.

### File Structure

```python
# functions/example.py

import logging

logger = logging.getLogger(__name__)

ENABLED = True                          # Set False to disable

AVAILABLE_FUNCTIONS = ['func_name']     # List of function names

TOOLS = [...]                           # OpenAI schema (see below)

def execute(function_name, arguments, config):
    """Returns (result_string, success_bool)"""
    ...
```

### Tool Definition (OpenAI Schema)

```python
{
    "type": "function",
    "network": True,  # Optional: marks tool as using network (highlighted in UI)
    "function": {
        "name": "function_name",
        "description": "What it does and WHEN to use it",
        "parameters": {
            "type": "object",
            "properties": {
                "param_name": {
                    "type": "string",       # string, integer, number, boolean, array, object
                    "description": "What this is for"
                }
            },
            "required": ["param_name"]
        }
    }
}
```

### Network Flag

Add `"network": True` to tool definitions that access external services (web, APIs, cloud). These tools are highlighted orange in the UI so users know data may leave the machine. SOCKS proxy routing also uses this flag.

**No parameters:**
```python
"parameters": {"type": "object", "properties": {}, "required": []}
```

### execute() Function

```python
def execute(function_name, arguments, config):
    """
    Args:
        function_name: Which tool was called
        arguments: Dict of arguments from the AI
        config: Sapphire config module

    Returns:
        (result_string, success_bool)
    """
    if function_name == "my_tool":
        query = arguments.get('query')
        if not query:
            return "I need a query.", False
        return f"Result: {query}", True

    return f"Unknown: {function_name}", False
```

**Return values:**
- `return "Success message", True` - Worked
- `return "Error message", False` - Failed (AI sees this)
- `return "No results for X", True` - Empty result (not an error)

### Tool Settings (Optional)

Tools can declare settings that appear in the Settings page under Custom Tools:

```python
SETTINGS = {
    'MYTOOL_API_KEY': '',          # string -> text input
    'MYTOOL_TIMEOUT': 30,          # number -> number input
    'MYTOOL_ENABLED': True,        # bool -> toggle
}
SETTINGS_HELP = {
    'MYTOOL_API_KEY': 'API key for the external service',
    'MYTOOL_TIMEOUT': 'Request timeout in seconds',
}
```

- Prefix keys with tool name (e.g. `MYTOOL_`) to avoid collisions
- Access in `execute()` via `config.MYTOOL_API_KEY`
- Types inferred from default values

### Best Practices

**Descriptions matter:** The AI uses descriptions to decide WHEN to call tools.
```python
# Good - tells AI when to use it
"description": "Search the web for URLs. Use get_website to read content."

# Bad - doesn't help AI decide
"description": "Searches the web"
```

**Lazy imports:** For heavy dependencies, import inside execute():
```python
def execute(function_name, arguments, config):
    if function_name == "heavy_tool":
        import heavy_library  # Only loaded when called
```

### Files Reference

| Path | Purpose |
|------|---------|
| `functions/` | Core tools |
| `user/functions/` | Your custom tools |
| `core/modules/system/toolsets/toolsets.json` | Default toolsets |
| `user/toolsets/toolsets.json` | Your toolset overrides |
| `core/chat/function_manager.py` | Tool loading system |

## Reference for AI

Tools are functions the AI calls to interact with systems - web search, memory, device control.

TOOL MODULES (15 total, 74+ functions):
- memory.py: save_memory, search_memory, get_recent_memories, delete_memory
- knowledge.py: save_person, save_knowledge, search_knowledge, delete_knowledge
- goals.py: create_goal, list_goals, update_goal, delete_goal
- web.py: web_search, get_website, get_wikipedia, research_topic, get_site_links, get_images
- ai.py: ask_claude
- meta.py: view_prompt, switch_prompt, edit_prompt, set_piece, remove_piece, create_piece, list_pieces, reset_chat, change_username, set_tts_voice, list_tools, get_time
- toolmaker.py: tool_save, tool_read, tool_activate
- homeassistant.py: 12 HA control functions
- image.py: generate_scene_image
- email_tool.py: get_inbox, read_email, archive_emails, get_recipients, send_email
- bitcoin_tool.py: get_wallet, send_bitcoin, get_transactions
- ssh_tool.py: ssh_get_servers, ssh_run_command
- network.py: get_external_ip, check_internet, website_status
- notepad.py: notepad_read, notepad_append_lines, notepad_delete_lines, notepad_insert_line
- docs.py: search_help_docs

TOOL FILE FORMAT:
```python
ENABLED = True
AVAILABLE_FUNCTIONS = ['my_func']
TOOLS = [{"type": "function", "function": {"name": "my_func", "description": "...", "parameters": {...}}}]
def execute(function_name, arguments, config):
    return "result", True  # (string, bool) tuple
```

TOOL FORMAT RULES:
- function.description: critical — this is how AI decides WHEN to call
- execute() returns (result_string, success_bool) tuple
- "is_local": True = offline, False = network, "endpoint" = conditional
- "network": True = highlighted in UI, routed through SOCKS
- Optional: EMOJI, MODE_FILTER, SETTINGS, SETTINGS_HELP

TOOL SETTINGS:
- SETTINGS dict: str=text, int/float=number, bool=toggle
- SETTINGS_HELP dict: descriptions shown under fields
- Access via config.SETTING_NAME in execute()

VALIDATION (tool_save):
- strict: allowlisted imports only
- moderate: blocks dangerous ops
- trust: syntax check only

TROUBLESHOOTING:
- Tool not working: Check it's in active toolset
- "No executor": Tool file missing or has errors
- Network tools failing: Check SOCKS proxy if enabled
