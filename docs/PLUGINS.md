# Plugins

<img width="66%" alt="image" src="https://github.com/user-attachments/assets/a4e4c033-08a4-499e-8453-183969039900" />


This documentation has been reorganized. See the **[Plugin Author Guide](plugin-author/README.md)**.

| Guide | What's Inside |
|-------|--------------|
| [Overview & Quick Start](plugin-author/README.md) | What plugins are, tools vs plugins, quick start, complete example |
| [Manifest](plugin-author/manifest.md) | `plugin.json` reference — fields, priority bands, directory structure |
| [Hooks](plugin-author/hooks.md) | All 16 hook points (incl. `ghost_inject`, streaming-TTS, `provider_switched`), HookEvent fields, system access, examples |
| [Voice Commands](plugin-author/voice-commands.md) | Keyword triggers that bypass the LLM — match modes, handlers, macros |
| [Tools](plugin-author/tools.md) | Tool file format, schema flags, scopes, reading settings, privacy patterns |
| [Routes](plugin-author/routes.md) | Custom HTTP endpoints — path params, auth enforcement, handler signature |
| [Schedule](plugin-author/schedule.md) | Cron tasks — manifest fields, handler contract, examples |
| [Widgets](plugin-author/widgets.md) | Dashboard panels — manifest, render contract, settings schema, sample plugin |
| [Games](plugin-author/games.md) | Game Room games — engine contract, sealed seat, board modules, sessions-are-chats |
| [Stories](plugin-author/stories.md) | Story packs — rooms, referee, dice, sealed blanks, identity modes, backdrops |
| [Providers](plugin-author/providers.md) | Custom TTS, STT, Embedding, LLM backends — base classes, manifest, lifecycle |
| [Settings](plugin-author/settings.md) | Manifest-declared settings, custom web UI, settings API, danger confirms |
| [Web UI](plugin-author/web-ui.md) | Shared JS modules, CSS variables, modals, CSRF, style injection |
| [Signing](plugin-author/signing.md) | Verification states, sideloading, signing your own plugins |
| [Lifecycle](plugin-author/lifecycle.md) | Startup, live toggle, hot reload, rescan, error isolation, full API reference |
| [Publishing](plugin-author/publishing.md) | How to structure your repo and submit to the Sapphire Store |
| [AI Reference](plugin-author/ai-reference.md) | Compact reference for Sapphire's own use |

For simple tool creation (no hooks/schedules needed), see [TOOLMAKER.md](TOOLMAKER.md).
