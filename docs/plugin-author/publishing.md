# Publishing to the Sapphire Store

The Sapphire Store at [sapphireblue.dev](https://sapphireblue.dev) is where users discover and install community plugins. This guide covers how to structure your GitHub repo and submit your plugin for listing.

## How the Store Works

The store is a directory — it doesn't host any files. Each listing links to your GitHub repository. When a user installs your plugin, Sapphire downloads the zip from GitHub, extracts it, and loads it locally. You keep full control of your code.

---

## Step 1: Structure Your GitHub Repo

Your repository root should look exactly like a plugin folder. `plugin.json` must be at the **top level** of the repo.

```
my-plugin/                  ← GitHub repo root
  plugin.json               ← Required — must be at root
  tools/
    my_tool.py
  hooks/
    handler.py
  web/
    index.js
  README.md
```

**Do NOT** nest it inside a subfolder like `src/` or `plugin/`. The installer expects `plugin.json` at the repo root.

For a real-world example, see [ddxfish/sapphire-3rd-party-test-plugin](https://github.com/ddxfish/sapphire-3rd-party-test-plugin) — a minimal plugin with the correct layout:

```
sapphire-3rd-party-test-plugin/     ← repo root
  plugin.json                       ← manifest at root
  hooks/
    hello.py                        ← post_chat hook
  README.md                         ← required — usage instructions
```

### What gets installed

Everything in the repo root is copied into the user's `user/plugins/{name}/` directory. Keep it clean — don't include dev files, IDE configs, or unrelated assets. A `.gitignore` won't help here since the zip download includes everything tracked by git.

**Recommended `.gitignore`** (for your own dev workflow):
```
__pycache__/
*.pyc
.venv/
.env
```

### README.md is required

Every plugin submitted to the store **must** include a `README.md` at the repo root. This is the first thing users see on GitHub before they install. It should cover:

- **What the plugin does** — a clear summary
- **Setup instructions** — API keys, config steps, dependencies, anything the user needs to do after install
- **Usage** — how to use it (voice commands, tool names, what to ask Sapphire)
- **Requirements** — minimum Sapphire version, external services, etc.

A plugin without a README will not be approved for the store.

### Third-party code and licensing

If your plugin includes code from other projects (libraries, snippets, modules), you **must** respect the original license. Include the relevant license file or attribution in your repo. Common requirements:

- **MIT / BSD** — include the license text and copyright notice
- **Apache 2.0** — include license + NOTICE file if one exists
- **GPL** — your entire plugin must also be GPL (copyleft)
- **No license listed** — assume all rights reserved; don't use it without permission

When in doubt, include a `LICENSE` file in your repo and credit the original authors in your README.

### Plugin name = folder name

The `name` field in your `plugin.json` becomes the folder name on install. It must be:
- Lowercase
- Hyphen-separated (e.g. `my-cool-plugin`)
- Unique — can't collide with system plugin names

---

## Step 2: Write Your plugin.json

Minimum viable manifest:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "One-line summary of what it does",
  "author": "your-name",
  "capabilities": {
    "tools": ["tools/my_tool.py"]
  }
}
```

Recommended fields for a good store listing:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "One-line summary of what it does",
  "author": "your-name",
  "icon": "🔧",
  "url": "https://github.com/you/my-plugin",
  "priority": 50,
  "capabilities": {
    "tools": ["tools/my_tool.py"],
    "settings": [
      {
        "key": "api_key",
        "type": "string",
        "label": "API Key",
        "default": "",
        "widget": "password",
        "help": "Your API key for the service"
      }
    ]
  }
}
```

See the full [Manifest Reference](manifest.md) for all fields and capabilities.

---

## Step 3: Test Locally

Before submitting, test your plugin the way users will experience it:

1. Copy your plugin folder to `user/plugins/your-plugin-name/`
2. Open Settings > Plugins and enable it
3. Verify tools appear, hooks fire, settings render, etc.
4. Check the Sapphire logs for any load errors

Unless you're an authorized author signing with a key on the central key list (see [Signing](signing.md)), your plugin is unsigned — make sure `ALLOW_UNSIGNED_PLUGINS` is enabled in Settings > Plugins so it loads, the same way most of your users will run it.

---

## Step 4: Submit to the Store

1. **Create an account** at [sapphireblue.dev](https://sapphireblue.dev) if you don't have one
2. Go to [Submit Your Plugin](https://sapphireblue.dev/plugins/submit-your-plugin/)
3. Fill out the form:
   - **Plugin name** — human-readable name
   - **Version** — should match `plugin.json`
   - **GitHub URL** — link to your public repo (e.g. `https://github.com/you/my-plugin`)
   - **Description** — what it does, why someone would want it
   - **Category** — automation, finance, tools, security, entertainment, etc.
4. Submit — your plugin goes into the approval queue

A store admin reviews submissions before they go live.

### Updating Your Plugin

Push your changes to GitHub, then visit the same [Submit Your Plugin](https://sapphireblue.dev/plugins/submit-your-plugin/) page — your previous submissions are listed there. Edit the listing to update the version number and any changed details. Users who already installed it can reinstall to get the update.

---

## Step 5: Branch Naming

The installer tries to download from the `main` branch first, then falls back to `master`. If you use a different default branch name, it won't work. Stick with `main`.

---

## Trust Levels

| Level | Verification state | What It Means |
|-------|--------------------|---------------|
| **Official** | `official` | Built by the Sapphire team, signed with the baked-in official key |
| **Verified Author** | `verified_author` | Signed by an author whose public key is on the central authorized key list |
| **Community** | `unsigned` | No signature — loads only if sideloading is enabled |

Community plugins are unsigned. Users need `ALLOW_UNSIGNED_PLUGINS` enabled to load them (Settings > Plugins toggle). Verification states and the authorized-author path are covered in [Signing](signing.md).

### About Signing and plugin.sig

A `plugin.sig` verifies against either the baked-in official key or an authorized key from the central key list — the `verified_author` lane (see [Signing](signing.md)). What to ship depends on which side of that list you're on:

- **Authorized author?** Sign with your own key (`python tools/sign_plugin.py your-plugin/ --key your_key.pem`) and ship the resulting `plugin.sig`. Your plugin loads without sideloading and shows your author badge. Re-sign after every change.
- **Not on the key list?** Ship **no `plugin.sig` at all.** A signature that matches no trusted key shows as **TAMPERED**, which blocks the plugin from loading entirely — worse than unsigned. An unsigned plugin with sideloading enabled loads fine.
- **Have a `plugin.sig` you didn't generate** (say, from forking a signed plugin)? Delete it — your edits already invalidated it, and a stale signature also reads as tampered.

---

## Checklist

Before submitting, verify:

- [ ] `plugin.json` is at the **root** of your GitHub repo
- [ ] `name` field is lowercase, hyphen-separated, and unique
- [ ] `version` follows semver (e.g. `1.0.0`)
- [ ] `description` is a clear one-liner
- [ ] `author` is filled in
- [ ] GitHub repo is **public** (Sapphire downloads it as a zip — private repos won't work)
- [ ] Default branch is `main` (or `master`)
- [ ] `plugin.sig` either generated by you with an authorized key, or absent entirely — never a stale/foreign one
- [ ] Tested locally in `user/plugins/` with sideloading enabled
- [ ] No secrets, credentials, or API keys committed to the repo
- [ ] `README.md` included with description, setup instructions, and usage
- [ ] Third-party code properly licensed and attributed

---

## Example: Minimal Tool Plugin

A complete, store-ready plugin repo:

```
weather-plugin/
  plugin.json
  tools/weather.py
  README.md
```

**plugin.json**:
```json
{
  "name": "weather",
  "version": "1.0.0",
  "description": "Weather — get current weather and forecasts by city",
  "author": "your-name",
  "icon": "🌤️",
  "url": "https://github.com/you/weather-plugin",
  "priority": 50,
  "capabilities": {
    "tools": ["tools/weather.py"],
    "settings": [
      {
        "key": "api_key",
        "type": "string",
        "label": "OpenWeather API Key",
        "default": "",
        "widget": "password",
        "help": "Get one free at openweathermap.org"
      }
    ]
  }
}
```

**tools/weather.py**:
```python
"""Weather tool — current conditions and forecast."""
import logging
import requests

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🌤️'
AVAILABLE_FUNCTIONS = ['get_weather']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name (e.g. 'London' or 'Tokyo, JP')"
                    }
                },
                "required": ["city"]
            }
        }
    }
]

def execute(function_name, arguments, config, plugin_settings=None):
    if function_name != "get_weather":
        return "Unknown function", False

    city = arguments.get("city", "")
    api_key = (plugin_settings or {}).get("api_key", "")
    if not api_key:
        return "No API key configured. Set it in Settings > Plugins > Weather.", False

    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": api_key, "units": "metric"},
            timeout=10,
        )
        if r.status_code != 200:
            return f"Weather API error: {r.status_code}", False

        d = r.json()
        desc = d["weather"][0]["description"]
        temp = d["main"]["temp"]
        feels = d["main"]["feels_like"]
        humidity = d["main"]["humidity"]

        return f"{city}: {desc}, {temp}°C (feels like {feels}°C), humidity {humidity}%", True
    except Exception as e:
        return f"Weather lookup failed: {e}", False
```

**README.md**:
```markdown
# Weather Plugin for Sapphire

Get current weather by city. Uses OpenWeather API.

## Setup
1. Get a free API key at https://openweathermap.org/api
2. Enable the plugin in Settings > Plugins
3. Set your API key in the plugin settings

## Usage
Ask Sapphire: "What's the weather in Tokyo?"
```

## Reference for AI

- Store is a directory, not a host: listings link to public GitHub repos; install downloads the repo zip (`main` branch, `master` fallback — no other default branch works) and extracts to `user/plugins/{name}/`.
- `plugin.json` must be at the repo ROOT (no `src/` or `plugin/` nesting). Everything tracked in the repo gets installed.
- `name`: lowercase, hyphen-separated, unique (becomes the folder name; can't collide with system plugins). `version` semver. `README.md` at repo root is required for store approval (what it does, setup, usage, requirements).
- Trust levels = verification states: Official (`official`, baked-in key) / Verified Author (`verified_author`, central key list) / Community (`unsigned`, needs `ALLOW_UNSIGNED_PLUGINS`).
- `plugin.sig` rule: ship one only if it verifies — signed by you with a key on the central authorized list. A sig matching no trusted key, or any file edited after signing, shows as TAMPERED and always blocks (worse than unsigned). Forked a signed plugin: delete its `plugin.sig`.
- Test path: copy to `user/plugins/{name}/`, enable in Settings > Plugins, sideloading on unless verified-author signed.
- Updates: push to GitHub, then edit your existing store listing's version; users reinstall to update.
- Repos must be public; no secrets committed; third-party code needs license compliance (GPL is copyleft — the whole plugin becomes GPL).
