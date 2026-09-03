# Tool Maker

Tool Maker lets the AI create custom tools at runtime. The AI writes a Python tool file, validates it, saves it as a plugin, and loads it live — no restart needed.

This is the guide for **AI-created tools** — simple tool plugins. For full plugin development (hooks, voice commands, schedules, web UIs), see the [Plugin Author Guide](plugin-author/README.md).

---

## How It Works

1. AI calls `tool_save(name, code)` — validates and saves as `user/plugins/{name}/`
2. AI calls `tool_load()` — discovers and activates the new plugin live
3. The tool appears in the current toolset immediately — no restart

Tool Maker auto-generates a `plugin.json` manifest from the code. The AI only writes the Python file.

---

## Simple Tool Format

The minimum needed to create a working tool. This is what `tool_save` expects.

```python
ENABLED = True
AVAILABLE_FUNCTIONS = ['my_func']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "my_func",
            "description": "What this tool does and when to use it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

def execute(function_name, arguments, config, plugin_settings=None):
    if function_name == 'my_func':
        query = arguments.get('query', '')
        return f"Result: {query}", True
    return f"Unknown function: {function_name}", False
```

### Required Exports

| Export | Type | Purpose |
|--------|------|---------|
| `ENABLED` | `bool` | Must be `True` |
| `AVAILABLE_FUNCTIONS` | `list[str]` | Function names this file provides |
| `TOOLS` | `list[dict]` | OpenAI-compatible function schemas |
| `execute()` | function | Dispatches calls, returns `(str, bool)` |

### execute() Contract

```python
def execute(function_name, arguments, config, plugin_settings=None):
    """
    Args:
        function_name: Which tool was called (matches TOOLS[].function.name)
        arguments: Dict of parameters from the AI
        config: Sapphire config module (system settings)
        plugin_settings: Dict of this plugin's settings from the Settings UI (or None)

    Returns:
        (result_string, success_bool) — AI sees the result string
    """
```

Return values:
- `return "Success message", True` — worked
- `return "Error: something broke", False` — failed, AI sees the error
- `return "No results found", True` — empty result (not an error)

### Tool Description Tips

The `description` field in TOOLS is how the AI decides **when** to call the tool. Make it clear.

```python
# Good — tells AI when to use it
"description": "Convert between temperature units. Use when asked about Fahrenheit/Celsius."

# Bad — doesn't help AI decide
"description": "Temperature converter"
```

### Parameter Types

Standard JSON Schema types: `string`, `integer`, `number`, `boolean`, `array`, `object`.

No parameters:
```python
"parameters": {"type": "object", "properties": {}, "required": []}
```

---

## Tool with Settings

Tools can declare settings that appear in the web UI Settings page. Users configure them in the browser, the tool reads them at runtime.

```python
ENABLED = True
AVAILABLE_FUNCTIONS = ['weather_get']

TOOLS = [
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "weather_get",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }
        }
    }
]

# Settings — auto-converted to UI fields in the Settings page.
# Types inferred from defaults: str→text input, int/float→number, bool→toggle.
SETTINGS = {
    'WEATHER_API_KEY': '',
    'WEATHER_UNITS': 'metric',
    'WEATHER_CACHE_MIN': 15,
}
SETTINGS_HELP = {
    'WEATHER_API_KEY': 'API key from openweathermap.org',
    'WEATHER_UNITS': 'metric or imperial',
    'WEATHER_CACHE_MIN': 'Cache duration in minutes',
}

def execute(function_name, arguments, config, plugin_settings=None):
    if function_name == 'weather_get':
        settings = plugin_settings or {}
        api_key = settings.get('WEATHER_API_KEY', '')
        if not api_key:
            return "Weather API key not configured. Set it in Settings > Weather.", False

        city = arguments.get('city', '')
        units = settings.get('WEATHER_UNITS', 'metric')

        import requests
        resp = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": units},
            timeout=10
        )
        if resp.status_code != 200:
            return f"Weather API error: {resp.status_code}", False

        data = resp.json()
        temp = data["main"]["temp"]
        desc = data["weather"][0]["description"]
        unit = "°C" if units == "metric" else "°F"
        return f"{city}: {temp}{unit}, {desc}", True

    return f"Unknown function: {function_name}", False
```

### How Settings Work

1. `SETTINGS` dict in your code → Tool Maker auto-converts to manifest `capabilities.settings`
2. Settings appear in web UI under the plugin's settings tab (auto-rendered, no JavaScript needed)
3. Read at runtime via the `plugin_settings` parameter in `execute()` — no imports needed
4. `SETTINGS_HELP` dict (optional) adds descriptions below each field

Type inference from defaults:
- `str` → text input
- `int` or `float` → number spinner
- `bool` → toggle switch

### Settings Key Naming

**Prefix SETTINGS keys with your plugin name** for clarity and to avoid confusion if multiple plugins have similar settings.

```python
# OK but unclear which plugin owns it
SETTINGS = {'API_KEY': '', 'ZIP_CODE': '10001'}

# Better — clear ownership
SETTINGS = {'WEATHER_API_KEY': '', 'WEATHER_ZIP_CODE': '10001'}
```

Settings are stored per-plugin (`user/webui/plugins/{name}.json`) so they can't technically collide, but prefixing keeps things readable in logs and the Settings UI.

---

## Multiple Functions

A single tool file can provide multiple functions:

```python
ENABLED = True
AVAILABLE_FUNCTIONS = ['convert_temp', 'convert_length']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "convert_temp",
            "description": "Convert between Celsius and Fahrenheit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "Temperature value"},
                    "to": {"type": "string", "description": "'celsius' or 'fahrenheit'"}
                },
                "required": ["value", "to"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "convert_length",
            "description": "Convert between meters and feet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "Length value"},
                    "to": {"type": "string", "description": "'meters' or 'feet'"}
                },
                "required": ["value", "to"]
            }
        }
    }
]

def execute(function_name, arguments, config):
    if function_name == 'convert_temp':
        value = arguments.get('value', 0)
        to = arguments.get('to', 'celsius')
        if to == 'celsius':
            return f"{(value - 32) * 5/9:.1f}°C", True
        return f"{value * 9/5 + 32:.1f}°F", True

    if function_name == 'convert_length':
        value = arguments.get('value', 0)
        to = arguments.get('to', 'meters')
        if to == 'meters':
            return f"{value * 0.3048:.2f}m", True
        return f"{value / 0.3048:.2f}ft", True

    return f"Unknown function: {function_name}", False
```

---

## Optional Flags

| Export | Type | Default | Purpose |
|--------|------|---------|---------|
| `EMOJI` | `str` | — | Display icon (e.g. `'🌤️'`) |
| `is_local` | `bool` or `str` | unset | `True` = fully local, `False` = needs network, `"endpoint"` = calls external API. Unset counts as non-local: private chats refuse the tool (fail-closed) |
| `network` | `bool` | `False` | Mark as network-dependent — a UI badge only; it does not affect routing or the privacy gate |

```python
EMOJI = '🌤️'

TOOLS = [{
    "type": "function",
    "is_local": False,       # uses network
    "network": True,          # network badge in the UI
    "function": { ... }
}]
```

### Lazy Imports

For heavy dependencies, import inside execute() so they only load when called:

```python
def execute(function_name, arguments, config):
    if function_name == 'analyze':
        import pandas as pd  # only loaded when tool is called
        ...
```

---

## Validation Levels

Tool Maker validates code before saving. The level is set in Settings > Tool Maker.

| Level | What's checked |
|-------|----------------|
| **Strict** (default) | ~60 allowlisted imports: requests, json, numpy, openai, anthropic, PIL, datetime, math, re, csv, bs4, hashlib, ssl, etc. Covers most tools without needing to change level. |
| **Moderate** | Everything in strict + subprocess + any import not explicitly dangerous (shutil, ctypes, multiprocessing, socket, signal, importlib still blocked) |
| **System Killer** | Syntax check only — no import or call restrictions. AI code can do anything on your system. |

---

## Tool Maker Commands

| Tool | What it does |
|------|-------------|
| `tool_save(name, code)` | Validate and save a tool plugin |
| `tool_load()` | Discover and activate new plugins (live, no restart) |
| `tool_read(name?)` | Read source code, or list all AI-created plugins |

### Workflow

1. Call `tool_save("weather", code)` — validates, creates `user/plugins/weather/`
2. Call `tool_load()` — rescan picks it up, tool is immediately available
3. The tool shows up in the plugin list and can be enabled/disabled like any plugin

### Name Rules

- **Short and descriptive** — 1-2 words (e.g. `weather`, `unit_converter`, `stock_price`)
- The name becomes the plugin title in the UI (underscores → spaces, title cased)
- Alphanumeric and underscores only
- Cannot match core tool names (memory, knowledge, goals, web, meta, etc.)
- Cannot match system plugin names (ssh, bitcoin, email, homeassistant, toolmaker, etc.)
- Cannot start with underscore

---

## What Tool Maker Does NOT Create

Tool Maker creates **tool plugins** — Python files the AI can call. For anything beyond that, a developer creates a full plugin manually. See the [Plugin Author Guide](plugin-author/README.md).

Not supported by Tool Maker:
- **Hooks** (pre_chat, prompt_inject, post_chat, etc.)
- **Voice commands** (keyword triggers that bypass the LLM)
- **Scheduled tasks** (cron jobs)
- **Web settings UI** (custom JavaScript — though SETTINGS dict auto-renders without JS)

## Reference for AI

TOOL MAKER — creates custom tool plugins at runtime.

WORKFLOW: tool_save(name, code) → tool_load() → tool is live

TEMPLATE:
```python
ENABLED = True
EMOJI = '🔧'  # Optional — plugin icon in the UI
AVAILABLE_FUNCTIONS = ['my_func']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "my_func",
            "description": "What this tool does and when to use it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The input"}
                },
                "required": ["query"]
            }
        }
    }
]

def execute(function_name, arguments, config, plugin_settings=None):
    if function_name == 'my_func':
        query = arguments.get('query', '')
        return f"Result: {query}", True
    return f"Unknown function: {function_name}", False
```

RULES:
- execute() returns (string, bool) — (result_text, success)
- plugin_settings = dict of THIS plugin's settings from Settings UI
- description field is critical — AI uses it to decide WHEN to call
- EMOJI = optional plugin icon (skipping it just means no icon); pick one that fits the tool's purpose
- is_local: True=fully local; False/"endpoint"=leaves the machine; UNSET=treated non-local (private chats refuse, fail-closed). network flag = UI badge only, never routing
- network: True = network badge in the UI only (routing is the net facade's job, not this flag's)
- Lazy imports for heavy deps (import inside execute, not at top)
- No parameters: `"properties": {}, "required": []`

SETTINGS (optional — adds fields to Settings UI):
```python
SETTINGS = {'MYPLUGIN_API_KEY': '', 'MYPLUGIN_TIMEOUT': 30, 'MYPLUGIN_ENABLED': True}
SETTINGS_HELP = {'MYPLUGIN_API_KEY': 'Your API key', 'MYPLUGIN_TIMEOUT': 'Timeout in seconds'}

# Read in execute():
settings = plugin_settings or {}
api_key = settings.get('MYPLUGIN_API_KEY', '')
```
- Prefix keys with plugin name for clarity
- Types inferred from defaults: str→text, int/float→number, bool→toggle

MULTIPLE FUNCTIONS: Add entries to both TOOLS and AVAILABLE_FUNCTIONS, branch in execute().

ALLOWED IMPORTS (strict mode):
requests, json, re, datetime, math, random, csv, base64, hashlib, uuid,
numpy, PIL, bs4, openai, anthropic, tiktoken, pypdf, croniter,
urllib, urllib3, http, ssl, collections, itertools, functools,
typing, enum, dataclasses, copy, os, io, pathlib, logging, time,
gzip, zlib, and more (~60 total)

NAME RULES: short (weather, stock_price), alphanumeric + underscores, no core/system names

AFTER SAVE: always call tool_load() to activate
