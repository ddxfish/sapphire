## Technical Reference

For developers and those who want to understand the internals.

### Configuration Layers

```
core/settings_defaults.json    ← System defaults (don't edit)
        ↓ merged with
user/settings.json             ← Your overrides
        ↓ equals
Runtime config
```

On first run, `user/settings.example.json` is created as a template. Copy to `user/settings.json` and add only settings you want to override.

Settings can be changed via:
- **UI** - Gear icon (recommended)
- **File** - Edit `user/settings.json` (auto-detected within ~2 seconds)
- **API** - `PUT /api/settings/<key>`

### Reload Tiers

| Tier | When | Examples |
|------|------|----------|
| Hot | Immediate | Names, TTS voice/speed/pitch, generation params |
| Component | Restart needed | TTS/STT enabled, server URLs, module toggles |
| Restart | Restart needed | Ports, paths, model configs |

The UI indicates the tier for each setting.

### Key File Locations

| File | Purpose |
|------|---------|
| `core/settings_defaults.json` | System defaults (read-only) |
| `core/settings_help.json` | Help text for UI |
| `user/settings.json` | Your overrides |
| `user/settings/chat_defaults.json` | Defaults for new chats |
| `~/.config/sapphire/secret_key` | Password hash |

### Authentication

The bcrypt hash serves as password, API key, and session secret. Delete to reset (triggers setup wizard).

- **Linux/Mac:** `~/.config/sapphire/secret_key`
- **Windows:** `%APPDATA%\sapphire\secret_key`

### Default Ports

These are the original defaults (adjustable in Settings soon):

- Internal API: `127.0.0.1:8071`
- Web UI: `0.0.0.0:8073` (HTTPS self-signed)
- TTS Server: `5012`
- STT Server: `5050`

### SOCKS Proxy Authentication

Two options for proxy credentials:

**Option 1 - Config file** (recommended): Create `user/.socks_config` with username on line 1, password on line 2.

**Option 2 - Environment variables:**
- `SAPPHIRE_SOCKS_USERNAME`
- `SAPPHIRE_SOCKS_PASSWORD`

### Troubleshooting

**Settings not working?** Restart Sapphire, verify JSON syntax, check logs.

**Reset to defaults?** Delete `user/settings.json` or the whole user/ dir if you want.

**Can't log in?** Delete `~/.config/sapphire/secret_key` and restart.