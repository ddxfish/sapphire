# Configuration

> **For individual setting descriptions:** Use the Settings UI (gear icon) which shows help text for each option, or see `core/settings_help.json` directly.

## How Configuration Works

Sapphire uses a layered configuration system:

```
core/settings_defaults.json    ← System defaults (don't edit)
        ↓ merged with
user/settings.json             ← Your overrides (safe to edit)
        ↓ equals
Runtime config                 ← What Sapphire actually uses
```

On first run, Sapphire creates `user/settings.example.json` as a template. Copy it to `user/settings.json` and customize. You only need to include settings you want to change - everything else uses defaults.

Settings can be changed three ways:
1. **Settings UI** - Gear icon in web interface (recommended)
2. **Edit user/settings.json** - Changes detected automatically within ~2 seconds
3. **API** - `PUT /api/settings/<key>` for programmatic changes

## Settings File Format

Settings are organized into categories but flattened at runtime:

```json
{
  "identity": {
    "DEFAULT_USERNAME": "Captain",
    "DEFAULT_AI_NAME": "Number One"
  },
  "tts": {
    "TTS_ENABLED": true,
    "TTS_SPEED": 1.3
  }
}
```

Categories in `settings_defaults.json`: identity, api, network, features, backups, wakeword, stt, recorder, tts, llm, tools, auth.

## Reload Tiers

Not all settings take effect immediately:

| Tier | Effect | Settings |
|------|--------|----------|
| **Hot** | Immediate | Names, TTS voice/speed/pitch, generation params, thinking mode |
| **Component** | Needs restart | TTS/STT enabled, server URLs, module/plugin/function toggles |
| **Restart** | Needs restart | Everything else (ports, paths, model configs, etc.) |

The Settings UI indicates which tier each setting belongs to. When in doubt, restart Sapphire.

## Special Configuration

### Authentication

Password hash stored outside the project at `~/.config/sapphire/secret_key`. This bcrypt hash serves as:
- Password verification for web login
- API key for internal requests (`X-API-Key` header)
- Flask session secret

Delete this file to reset password (triggers setup wizard on next visit).

### Environment Variables

Two optional env vars for SOCKS proxy authentication:
- `SAPPHIRE_SOCKS_USERNAME` - Proxy username
- `SAPPHIRE_SOCKS_PASSWORD` - Proxy password

### Chat Defaults

New chats inherit settings from `user/settings/chat_defaults.json`:

```json
{
  "prompt": "default",
  "ability": "default", 
  "voice": "af_heart",
  "pitch": 0.94,
  "speed": 1.3,
  "spice_enabled": true,
  "spice_turns": 3,
  "inject_datetime": false,
  "custom_context": ""
}
```

Set via Settings UI → "Save as Default" or edit directly.

### Themes

Theme selection stored in browser localStorage, not in settings.json. Themes are CSS files in `interfaces/web/static/themes/`.

### Hardcoded Values

These cannot be changed via configuration:
- Internal API: `127.0.0.1:8071`
- Web UI: `0.0.0.0:8073` (HTTPS)
- TTS Server: port `5012`
- STT Server: port `5050`

## File Locations Summary

| File | Purpose |
|------|---------|
| `core/settings_defaults.json` | System defaults (read-only) |
| `core/settings_help.json` | Help text for Settings UI |
| `user/settings.json` | Your configuration overrides |
| `user/settings.example.json` | Template (auto-generated) |
| `user/settings/chat_defaults.json` | Defaults for new chats |
| `~/.config/sapphire/secret_key` | Password hash |

## Troubleshooting

**Settings not taking effect?**
- Check the tier - may need restart
- Verify JSON syntax in user/settings.json
- Check logs for "Settings reloaded" or errors

**Reset to defaults?**
- Delete `user/settings.json` and restart
- Or use API: `POST /api/settings/reset`

**Can't log in after password change?**
- Delete `~/.config/sapphire/secret_key`
- Restart and complete setup wizard