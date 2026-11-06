<!-- AI_INCLUDE_FULL: Compact plugin-building reference + capability-to-guide index -->
# AI Reference

Compact reference for Sapphire's own use when building plugins. For simple tool creation (tool_save/tool_load), see the TOOLMAKER doc — this covers full plugin development.

When creating or modifying plugins:

- Plugin = folder in `user/plugins/{name}/` with `plugin.json` manifest (shipped ones live in `core/plugins/{name}/`; both import as `plugins.{name}`; a root-level `plugins/` is not scanned)
- Project root in plugin code: `from core.paths import PROJECT_ROOT` — never count `.parent`s
- `plugin.json` requires `name` field, everything else optional
- Loading a new plugin: Settings > Plugins → Rescan, turn on unsigned plugins (banner switch, `ALLOW_UNSIGNED_PLUGINS`), toggle on. Third-party plugins ship WITHOUT `plugin.sig`: a self-generated key = `failed` = always blocked. `tools/sign_plugin.py` is for keyholders only
- Route/hook/socket files are exec'd, one namespace per file: no relative imports, no module state shared across files — share via a named module in `sys.modules`
- Hooks = Python functions receiving mutable `HookEvent` object
- Tools = `TOOLS` list + `execute(function_name, arguments, config)` returning `(str, bool)` (optional 4th arg `plugin_settings`, 5th `credentials` — inspected by arity)
- Tool schema supports `is_local` (bool or `"endpoint"`) and `network: true` flags
- Optional `get_tools()` (returns `TOOLS`-shaped list from current settings) = dynamic descriptions; rebuilt in place on settings-save, no reload (name stays fixed)
- Voice commands = pre_chat hooks with trigger matching, `bypass_llm: true` for instant response
- Routes = custom HTTP endpoints at `/api/plugin/{name}/{path}`; handler is called with keyword args (each path param + `body`, `settings`, `credentials`, `query`, `request`) — END the signature with `**_` or it raises TypeError. Auth+CSRF+rate-limit enforced by framework
- Schedule = cron tasks calling `run(event)` handler, event has `system`, `config`, `task`, `plugin_state`
- Idle clock = `core.idle.minutes(chat=None, person=False)`: minutes since the last message in a chat (or any chat); one clock for plugins, her idle line, and the context budgeter
- Web settings = `web/index.js` using `registerPluginSettings()`, served at `/plugin-web/{name}/`. `settingsUI` is TOP-LEVEL (auto|plugin|manifest|core|none|null); there is no `capabilities.web`
- Manifest settings seed `user/webui/plugins/{name}.json` on first load only; stored values win after (a changed default never reaches existing users)
- Plugin scripts = `web/main.js` auto-loaded on app startup, listen for `sapphire:tool_start` DOM events
- Daemon = background thread with `start(plugin_loader, settings)` / `stop()`, emits events via `plugin_loader.emit_daemon_event(source_name, json_payload)`
- Daemon event sources declared in manifest `capabilities.daemon.event_sources[]` — `name`, `label`, `description`, `filter_fields`, `task_fields`, `realtime`. Optional daemon `on_settings_saved(settings)`
- Reply handlers: `plugin_loader.register_reply_handler(plugin_name, handler)` — routes LLM responses back to source platform
- Providers = register TTS/STT/Embedding/LLM backends via `capabilities.providers` — appear in settings dropdowns
- App = full-page plugin UI via `capabilities.app` — `label`, `icon`, `description`, `nav`, `places`. `nav`: absent = Apps grid tile; `true` = own rail icon (max 3); a group (`chat`|`personas`|`triggers`|`mind`|`settings`) = entry in that flyout; `"apps"` = start pinned on the Apps menu (shipped plugins only; users pin with 📌, `user/webui/apps_pins.json`). `places`: `["apps"]` default, add `"stage"` to open beside the chat (`open_stage` tool)
- Themes = custom CSS themes via `capabilities.themes[]` — `id`, `name`, `css`, `preview`, `font`, `bg`, `motion`, per-theme `settings` (`scripts` is deprecated — animate with a motion)
- Motions = animated chat backgrounds via `capabilities.motions[]` — `{id, name, description, script, settings?}`; module default-exports `{id, name, mount(host, settings), unmount()}`; picked in Settings > Visual or by `set_motion`
- State = `plugin_loader.get_plugin_state(name)` for persistent key-value storage
- Chat-scoped state = `plugin_loader.get_chat_state(name)` — `get/put/append/read_all/replace/delete/keys/get_all_chats/meta(chat, ...)`; rows live in the chat DB and rename/encrypt/delete with the chat. Hidden chat (private + sealed): reads empty, writes RAISE. Use it instead of chat-keyed files under `user/`
- System access = `event.metadata.get("system")` in `post_stt`, `pre_chat`, `ghost_inject`, `post_llm`, `post_chat`, `pre_execute`, and the four `tts_*` streaming hooks
- `prompt_inject`, `post_execute`, `pre_tts` do NOT get system metadata — only `config`
- System gives access to: `tts` (voice/speed/pitch/speak/stop), `toggle_stt(enabled: bool)`, `toggle_wakeword(enabled: bool)`, `llm_chat` (chat/history/prompt), `function_manager` (tools/scopes)
- LLM side call (summary, classify): `core.chat.llm_providers.resolve.resolve('auto'|key, model, private=...).provider.chat_completion(messages)` — NOT `llm_chat.chat()` (a full persisted turn). Chat list: `system.llm_chat.list_chats()`
- Enable/disable live via `PUT /api/webui/plugins/toggle/{name}`
- All hooks: `post_stt`, `pre_chat`, `prompt_inject`, `ghost_inject`, `post_llm`, `post_chat`, `pre_execute`, `post_execute`, `tools_filter`, `pre_tts`, `post_tts`, `on_wake`, `provider_switched`, `tts_stream_start`, `tts_chunk_text`, `tts_chunk_audio`, `tts_stream_end`, `chat_renamed`, `chat_deleted`, `chat_cleared`, `chat_vaulted`, `plugins_ready` (meta `loaded`, `reason`, `plugin`), `vault_unlocked`, `vault_locked` (re-derive / drop anything cached from vault contents)
- `pre_tts` metadata carries `tts_client`
- `tools_filter` = per-turn subtract-only tool fence (event.tools intersection; fail-open). `chat_cleared` = history wiped, chat + chat-scoped rows survive (metadata key `chat`)
- Private chats: hooks are withheld from any plugin that lacks top-level `"privacy_aware": true` in `plugin.json` (fail-closed if privacy can't be resolved). Runner stamps `event.chat_name` / `event.chat_private`. Always delivered regardless: `chat_renamed`, `chat_deleted`, `chat_cleared`, `plugins_ready`, `provider_switched`, `on_wake`
- `chat_vaulted` fires post-seal to privacy_aware plugins only — `metadata["name"]` is the chat that just went private; scrub plaintext deposits keyed by it, don't log the name
- `prompt_inject` mutates the system prompt (long-lived, breaks Claude cache). `ghost_inject` (since 2.6.4) injects per-turn ephemera as labeled operator metadata that doesn't break cache — prefer it for time-sensitive context, ambient state, weather, calendar, etc.
- `post_stt` fires only for voice input (after STT transcription, before chat pipeline)
- `post_llm` fires after LLM response, before history save + TTS — mutate `response` to filter/translate/style
- `post_tts` fires after playback completes or is stopped (daemon thread, observational)
- `on_wake` fires when wakeword detected, before recording starts (notification only, must return fast)
- Error isolation: exceptions logged and skipped, never crash pipeline
- Signing: ed25519 `plugin.sig`; `official` / `verified_author` load; `unsigned` loads only with unsigned plugins allowed; `failed` (tampered, or signed by an unknown/self-made key) ALWAYS blocked. Editing a signed plugin = failed: keyholder re-signs, else delete `plugin.sig`
- Settings stored at `user/webui/plugins/{name}.json`, read via `GET /api/webui/plugins/{name}/settings`
- Settings files are in `user/` (gitignored) — never tracked
- Multi-account tools use ContextVar scopes: `scope_memory`, `scope_email`, `scope_bitcoin`, etc. (import from `core.chat.function_manager`; on error return None, never 'default')
- Web UI modules available: `plugin-registry.js`, `plugins-api.js`, `notify.js` (rows + toasts), `toast.js` (legacy), `modal.js`, `danger-confirm.js`, `fetch.js` (+ `errWords`/`responseError` plain-words errors), `download.js` (THE file save), `llm-picker.js` (THE provider+model picker)
- Notifications from Python: `from core.notify import notify, flash, progress` (never chat content in a row)
- CSS variables for theming: `--bg`, `--text`, `--border`, `--trim`, `--success`, `--error`, etc.
- Always guard system access with `hasattr()` checks — subsystems may be None if disabled

## Thinking blocks (think tags)

ONE reader: `core/think.py` (web twin `interfaces/web/static/shared/think.js`). Never write a think-tag regex.
- `from core import think` — `think.strip(text)` = the answer (use before sending, speaking, parsing JSON, storing); `think.split(text)` = `(answer, thinking)`; `think.has`, `think.segments`, `think.wrap(thinking, answer)`, `think.only`, `think.unfinished(buf)`, `think.PAIRS`, `think.BLOCK_RE`.
- Tags: `think`, `seed:think`, `thinking`, `redacted_thinking`, `reasoning` (+ closer `seed:cot_budget_reflect`).
- Rules: a tag in backticks or in a fenced code block is a word; opener + later closer = block; a closer with nothing open folds the text since the last block into thinking (never across a block); an unclosed opener is thinking only at a block start.
- Side jobs: `disable_thinking: True` in generation params, treat `response.content_is_reasoning` as empty, strip anyway.
- Full guide: [thinking.md](thinking.md). Design rules for plugins: [design-principles.md](design-principles.md).

## Capability → guide

Manifest `capabilities` keys:

| Key | What | Guide |
|---|---|---|
| `hooks` | pipeline + lifecycle hooks | [hooks.md](hooks.md) |
| `voice_commands` | spoken triggers that skip the LLM | [voice-commands.md](voice-commands.md) |
| `commands` | `/name` slash commands in the composer | [commands.md](commands.md) |
| `tools` | functions she calls; `TOOL_EXPIRES`, `GROUP`, `MODE_FILTER`, `SETTINGS_GATED` | [tools.md](tools.md) |
| `scopes` | per-chat data partitions (ContextVar) | [manifest.md](manifest.md) |
| `routes` / `sockets` | HTTP endpoints / WebSockets | [routes.md](routes.md) |
| `schedule` | cron tasks | [schedule.md](schedule.md) |
| `settings` | auto-rendered settings form | [settings.md](settings.md) |
| `providers` | TTS / STT / embedding / LLM backends | [providers.md](providers.md) |
| `daemon` | background listener + event sources | [daemons.md](daemons.md) |
| `services` | supervised subprocess servers (+ `environment`) | [subprocesses.md](subprocesses.md) |
| `memory_layers` | plugin layers in the Mind Palace | [memory-layers.md](memory-layers.md) |
| `prompts` | ship monoliths / pieces | [prompts.md](prompts.md) |
| `games` | Game Room games | [games.md](games.md) |
| (`stories/` dir) | story packs, no capability key | [stories.md](stories.md) |
| `devices` | device drivers | [devices.md](devices.md) |
| `agents` | agent kinds she spawns | [agents.md](agents.md) |
| `social` | social media providers (read by the Social plugin) | [social.md](social.md) |
| `app` | full-page UI, nav, pins, stage | [APPS.md](APPS.md) |
| `widgets` | dashboard panels | [widgets.md](widgets.md) |
| `themes` / `motions` | CSS themes / animated backgrounds | [THEMES.md](THEMES.md) |
| `sidebar_accordion` | chat-sidebar panel | [manifest.md](manifest.md) |
| `cleanup_paths` | extra files removed on uninstall | [manifest.md](manifest.md) |

Top-level manifest fields: `privacy_aware`, `surfaces`, `essential`, `priority`, `default_enabled`, `settingsUI`, `requires_plugins` (add-on waits for its hosts), `tool_expires`, `pip_dependencies`, `extra`, `environment`, `platforms`, `min_core_version` — [manifest.md](manifest.md). Toggle/reload/rescan: [lifecycle.md](lifecycle.md). Signing: [signing.md](signing.md). Browser modules: [web-ui.md](web-ui.md). Store: [publishing.md](publishing.md). Voice elsewhere: [voice-routing.md](voice-routing.md). Worked examples: [examples.md](examples.md).
