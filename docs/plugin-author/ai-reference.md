# AI Reference

Compact reference for Sapphire's own use when building plugins. For simple tool creation (tool_save/tool_load), see the TOOLMAKER doc — this covers full plugin development.

When creating or modifying plugins:

- Plugin = folder in `plugins/{name}/` with `plugin.json` manifest
- `plugin.json` requires `name` field, everything else optional
- Hooks = Python functions receiving mutable `HookEvent` object
- Tools = `TOOLS` list + `execute(function_name, arguments, config)` returning `(str, bool)` (optional 4th arg `plugin_settings`, 5th `credentials` — inspected by arity)
- Tool schema supports `is_local` (bool or `"endpoint"`) and `network: true` flags
- Optional `get_tools()` (returns `TOOLS`-shaped list from current settings) = dynamic descriptions; rebuilt in place on settings-save, no reload (name stays fixed)
- Voice commands = pre_chat hooks with trigger matching, `bypass_llm: true` for instant response
- Routes = custom HTTP endpoints at `/api/plugin/{name}/{path}`; handler is called with keyword args (each path param + `body`, `settings`, `credentials`, `query`, `request`) — END the signature with `**_` or it raises TypeError. Auth+CSRF+rate-limit enforced by framework
- Schedule = cron tasks calling `run(event)` handler, event has `system`, `config`, `task`, `plugin_state`
- Web settings = `web/index.js` using `registerPluginSettings()`, served at `/plugin-web/{name}/`
- Plugin scripts = `web/main.js` auto-loaded on app startup, listen for `sapphire:tool_start` DOM events
- Daemon = background thread with `start(plugin_loader, settings)` / `stop()`, emits events via `plugin_loader.emit_daemon_event(source_name, json_payload)`
- Daemon event sources declared in manifest `capabilities.daemon.event_sources[]` — `name`, `label`, `filter_fields`, `task_fields`
- Reply handlers: `plugin_loader.register_reply_handler(plugin_name, handler)` — routes LLM responses back to source platform
- Providers = register TTS/STT/Embedding/LLM backends via `capabilities.providers` — appear in settings dropdowns
- App = full-page plugin UI via `capabilities.app` — `label`, `icon`, optional `nav: true` for navrail promotion (max 3)
- Themes = custom CSS themes via `capabilities.themes[]` — `css`, `scripts`, `preview`, per-theme `settings`
- State = `plugin_loader.get_plugin_state(name)` for persistent key-value storage
- Chat-scoped state = `plugin_loader.get_chat_state(name)` — `get/put/append/read_all/replace/delete/keys/get_all_chats/meta(chat, ...)`; rows live in the chat DB and rename/encrypt/delete with the chat. Hidden chat (private + sealed): reads empty, writes RAISE. Use it instead of chat-keyed files under `user/`
- System access = `event.metadata.get("system")` in `post_stt`, `pre_chat`, `ghost_inject`, `post_llm`, `post_chat`, `pre_execute`, and the four `tts_*` streaming hooks
- `prompt_inject`, `post_execute`, `pre_tts` do NOT get system metadata — only `config`
- System gives access to: `tts` (voice/speed/pitch/speak/stop), `toggle_stt()`, `toggle_wakeword()`, `llm_chat` (chat/history/prompt), `function_manager` (tools/scopes)
- Enable/disable live via `PUT /api/webui/plugins/toggle/{name}`
- All 20 hooks: `post_stt`, `pre_chat`, `prompt_inject`, `ghost_inject`, `post_llm`, `post_chat`, `pre_execute`, `post_execute`, `pre_tts`, `post_tts`, `on_wake`, `provider_switched`, `tts_stream_start`, `tts_chunk_text`, `tts_chunk_audio`, `tts_stream_end`, `chat_renamed`, `chat_deleted`, `chat_vaulted`, `plugins_ready`
- Private chats: hooks are withheld from any plugin that lacks top-level `"privacy_aware": true` in `plugin.json` (fail-closed if privacy can't be resolved). Runner stamps `event.chat_name` / `event.chat_private`. Always delivered regardless: `chat_renamed`, `chat_deleted`, `plugins_ready`, `provider_switched`, `on_wake`
- `chat_vaulted` fires post-seal to privacy_aware plugins only — `metadata["name"]` is the chat that just went private; scrub plaintext deposits keyed by it, don't log the name
- `prompt_inject` mutates the system prompt (long-lived, breaks Claude cache). `ghost_inject` (since 2.6.4) injects per-turn ephemera as labeled operator metadata that doesn't break cache — prefer it for time-sensitive context, ambient state, weather, calendar, etc.
- `post_stt` fires only for voice input (after STT transcription, before chat pipeline)
- `post_llm` fires after LLM response, before history save + TTS — mutate `response` to filter/translate/style
- `post_tts` fires after playback completes or is stopped (daemon thread, observational)
- `on_wake` fires when wakeword detected, before recording starts (notification only, must return fast)
- Error isolation: exceptions logged and skipped, never crash pipeline
- Signing: ed25519 signatures in `plugin.sig`, tampered = always blocked, unsigned = blocked unless sideloading enabled
- Settings stored at `user/webui/plugins/{name}.json`, read via `GET /api/webui/plugins/{name}/settings`
- Settings files are in `user/` (gitignored) — never tracked
- Multi-account tools use ContextVar scopes: `scope_email`, `scope_bitcoin`, `scope_knowledge`, etc.
- Web UI modules available: `plugin-registry.js`, `plugins-api.js`, `toast.js`, `modal.js`, `danger-confirm.js`, `fetch.js`
- CSS variables for theming: `--bg`, `--text`, `--border`, `--trim`, `--success`, `--error`, etc.
- Always guard system access with `hasattr()` checks — subsystems may be None if disabled
