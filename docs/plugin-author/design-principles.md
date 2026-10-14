# Design Principles

What a plugin has to be to ship with Sapphire, and why core is shaped the way it is. We built plugin support to keep core more agile. Core is based on minimalism. 

## 1. Core is minimal

Core is a connector, not a feature list. It owns the pipeline (chat, voice, memory, scheduling, devices, agents) and the hooks and doors that let plugins reach it. Almost every feature belongs in a plugin. When a feature needs something core does not offer, what core gains is the hook or handler that makes the plugin possible: small, generic, useful to the next plugin too. 

## 2. Plugin or core?

Core code makes it slower to code every future update, so core is only for features that cannot function as plugins. If you are editing features like how LLM routes work etc, that is core. If you are adding capabilities like tools to do ABCD then make them a plugin. If you are trying to make a plugin that does not have a core handler or hook, then we make the hook or handler to make the plugin work.

## 3. One system

Use the underlying system instead of recreating separate subsystems. Things like memory v2 are meant to be expanded, daemons are meant for plugins to use, and internal LLM routing is already here. Don't rebuild these or other core features, we want to maintain one system. Use systems in Sapphire when possible. Ask for more core features if we are missing a capability. 

| Instead of building your own | Use |
|---|---|
| LLM calls, model picks, fallbacks | the Continuity task's provider, or `core.chat.llm_providers.resolve.resolve()` |
| memory database | a Mind Palace memory layer, or the task's memory scope |
| scheduler, cron thread, clock loop | `capabilities.schedule`; a daemon event source plus `plugin_loader.fire_task` |
| settings file; token or password store | `capabilities.settings`; the credentials manager |
| durable state, latches, counters | plugin state (`plugin_loader.get_plugin_state`) |
| worker thread, background job | an agent kind (`capabilities.agents`) |
| hardware page, device secrets | a device driver (`capabilities.devices`) |
| room, stage, game loop | `capabilities.games` |
| web framework, auth, file server | manifest routes; `media: true` on a route that serves files |
| think-tag stripper; image store or describer | `core.think`; `core.images` and `core.image_describe` |
| privacy rules | the `privacy_aware` flag and the resolver's local-only rule |
| subprocess babysitter | `ProcessManager` |

## 4. If you need core
If your code belongs in core and not a plugin, then make sure it is minimal and just what we need and nothing more. Most of my features I start as plugins, I test them for a few weeks while stripping out as much as I can and simplifying settings to the bare minimum. Then I test thoroughly on Windows and Linux and run the plugin some more. Only then is something a candidate for core. 

## Before you submit

1. Would this be worse as a plugin? If not, it is a plugin.
2. Does it call a model, schedule work, store memory, or hold secrets only through core?
3. One sentence per feature, one toggle per feature, and truly gone when off?
4. Tested on Windows and Linux? Tests beside the code? Signed? README current?
5. New package? Declared in the manifest, not in `requirements.txt`?
6. Any third party code or services? Keep it internal.

## Reference for AI

DESIGN PRINCIPLES: core is minimal (pipeline + hooks + doors); features are plugins. Plugin over core when it is big, complex, costly to keep, or depends on anything outside Sapphire; core gets only what plugins cannot exist without. Core earns nothing but the repo name; featured plugins are shown more prominently. One system: never rebuild LLM, memory, scheduler, settings, secrets, agents, devices, games, or routes inside a plugin (table above). One sentence and one toggle per feature; add-ons import lazily and go dark. Ship cost: Windows + Linux, tests in `tests/`, signed, README, packages via manifest not `requirements.txt`, fail loud, store nothing unasked, outside text is data, scope errors return `None` never `'default'`.
