# Plugins

<img width="66%" alt="image" src="https://github.com/user-attachments/assets/a4e4c033-08a4-499e-8453-183969039900" />


This documentation has been reorganized. See the **[Plugin Author Guide](plugin-author/README.md)**.

| Guide | What's Inside |
|-------|--------------|
| [Overview & Quick Start](plugin-author/README.md) | What plugins are, tools vs plugins, quick start, complete example |
| [Manifest](plugin-author/manifest.md) | `plugin.json` reference — fields, priority bands, directory structure |
| [Hooks](plugin-author/hooks.md) | All hook points (incl. `ghost_inject`, streaming-TTS, chat lifecycle), HookEvent fields, system access, the `privacy_aware` gate for private chats, examples |
| [Examples](plugin-author/examples.md) | Copy-paste samples for every capability type |
| [Daemons](plugin-author/daemons.md) | Event sources, reply handlers, realtime gates, task fields |
| [Subprocesses](plugin-author/subprocesses.md) | ProcessManager and declared services |
| [Apps](plugin-author/APPS.md) | Full-page plugin UIs promoted into the nav rail |
| [Themes](plugin-author/THEMES.md) | Plugin themes and motions — CSS, settings, teardown |
| [Memory Layers](plugin-author/memory-layers.md) | Plugins adding layers to the Mind Palace |
| [Prompt Packs](plugin-author/prompts.md) | Shipping prompts with a plugin |
| [Voice Commands](plugin-author/voice-commands.md) | Keyword triggers that bypass the LLM — match modes, handlers, macros |
| [Tools](plugin-author/tools.md) | Tool file format, schema flags, scopes, reading settings, plugin + chat-scoped state, privacy patterns |
| [Routes](plugin-author/routes.md) | Custom HTTP endpoints — path params, auth enforcement, handler signature |
| [Schedule](plugin-author/schedule.md) | Cron tasks — manifest fields, handler contract, examples |
| [Widgets](plugin-author/widgets.md) | Dashboard panels — manifest, render contract, settings schema, sample plugin |
| [Games](plugin-author/games.md) | Game Room games — engine contract, sealed seat, board modules, sessions-are-chats |
| [Stories](plugin-author/stories.md) | Story packs — rooms, referee, dice, sealed blanks, identity modes, backdrops |
| [Providers](plugin-author/providers.md) | Custom TTS, STT, Embedding, LLM backends — base classes, manifest, lifecycle |
| [Settings](plugin-author/settings.md) | Manifest-declared settings, custom web UI, settings API, danger confirms |
| [Web UI](plugin-author/web-ui.md) | Shared JS modules, CSS variables, modals, CSRF, style injection |
| [Signing](plugin-author/signing.md) | Verification states, sideloading, signing your own plugins |
| [Lifecycle](plugin-author/lifecycle.md) | Startup, live toggle, hot reload, rescan, error isolation, chat lifecycle, full API reference |
| [Publishing](plugin-author/publishing.md) | How to structure your repo and submit to the Sapphire Store |
| [AI Reference](plugin-author/ai-reference.md) | Compact reference for Sapphire's own use |

For simple tool creation (no hooks/schedules needed), see [TOOLMAKER.md](TOOLMAKER.md).
