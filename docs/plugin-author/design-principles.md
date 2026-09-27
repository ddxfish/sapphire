# Design Principles

How Sapphire is built, and what a plugin has to do to fit. Read this before you
design a plugin, not after. Every guide in this folder is a door; this page is why
the doors are where they are.

## The goal: one system

Sapphire is maintained by a very small team, and every line has to stay alive
through changes made for reasons that have nothing to do with your plugin. When a
plugin carries its own LLM path, its own scheduler, or its own memory, each of
those is a second copy of something core owns. Every change to the original then
has to be made twice and verified twice, and the copy nobody is looking at drifts
until it breaks in a way that looks like a core bug.

Two systems cannot be maintained. One can. So the rule is simple even where it is
inconvenient:

**A plugin uses core to do its job.** Its value is what it adds: a service, a
personality, a device, a game. The machinery that carries it is core's. If a door
is missing something you need, the fix is the door.

## 1. Core stays small

A feature most people will not use often does not live in core. That is what
plugins are for. It cuts both ways: a feature most of *your* users will not turn
on does not earn a place in your plugin either.

Each feature has to pay for itself in one sentence: who turns this on, and what
do they get. Work already spent is not a reason to keep code alive.

## 2. Route through core

A plugin that ships with Sapphire does not carry its own copy of a core
subsystem. Not a smaller one, not a special-case one.

| Instead of building | Use | Guide |
|---|---|---|
| Your own LLM calls and model picks | A Continuity task's provider (the task is the brain), or `core.chat.llm_providers.resolve.resolve()` for a side job. One resolver decides provider, privacy, health and fallback. | [Daemons](daemons.md), [Providers](providers.md) |
| Your own memory database | A Mind Palace memory layer, or the task's memory scope: she can `save_memory` herself | [Memory Layers](memory-layers.md) |
| Your own scheduler, cron thread, clock loop | `capabilities.schedule` for cron, a daemon event source plus `plugin_loader.fire_task` for events | [Schedule](schedule.md), [Daemons](daemons.md) |
| Your own settings file or table | `capabilities.settings` in the manifest | [Settings](settings.md) |
| Your own token or password storage | Core's credentials manager | [Settings](settings.md) |
| Your own durable state | Plugin state (`plugin_loader.get_plugin_state`) | [Tools](tools.md) |
| Your own think-tag stripper | `core.think` | [Thinking Blocks](thinking.md) |
| Your own image store, resizer, or describer | `core.images` (`stash`, `resolve`, `result`) and `core.image_describe` | [Tools](tools.md) |
| Your own privacy rules | The `scope_private` flag and the resolver's local-only rule | [Hooks](hooks.md) |
| Your own routing (which account, which channel) | One Continuity task per brain, with core's filters | [Daemons](daemons.md) |
| Your own subprocess babysitter | `ProcessManager` | [Subprocesses](subprocesses.md) |
| Your own web framework or auth | Manifest routes; core enforces login and CSRF | [Routes](routes.md) |

If you find yourself writing something on the left, stop and ask where the right
side falls short. That answer is worth more than the workaround.

## 3. Settings a person can read at a glance

- **One toggle per feature.** A feature that is off is not there: no hooks run, no
  tool answers, nothing registers.
- **Knobs only for things a person will actually change.** The default is the
  value most people want. If a setting needs more than one line of help, it is
  two settings or none.
- **No setting that duplicates a core setting or a task field.** Model, active
  hours, memory scope, which channels: those belong to the Continuity task and
  are already there.
- **The tool in a toolset is the switch.** A tool needs no separate "enabled"
  setting; adding it to a toolset turns it on. Two switches for one feature means
  one of them is forgotten.

## 4. Scopes: read them, never invent them

Scopes are how one Sapphire holds many separate worlds (per chat, per task):
memory, knowledge, people, private, and one per plugin account.

- Read the scope the chat or task chose. Write only there.
- On any error resolving a scope, return `None` and do nothing. **Never fall back
  to `'default'`**: it is a real scope with the owner's data in it.
- Never create a scope automatically. The owner picks scopes; a plugin reads them.

## 5. Privacy is a feature

Sapphire is local-first and the people running it chose that.

- **Store nothing nobody asked you to store.** No message archives, no prompt
  logs on disk, no "learning" facts about people from chatter.
- **A private turn stays local.** Network tools refuse, and only a provider
  marked local may answer. Let the resolver decide; do not route around it.
- **What you store dies with its owner.** Data keyed to a chat is deleted with the
  chat; data in a vaulted chat is encrypted like the chat.
- **Text from outside is data, never instructions.** A note about a user, a web
  page, an email body: fence it as data when it rides into a prompt.

## 6. Hooks are the surface

You change Sapphire's behavior through hooks and you touch other plugins through
the doors they publish, never by importing their insides.

- Core's hooks cover the chat pipeline ([Hooks](hooks.md)).
- A host plugin can fire its own. The Discord plugin fires nine
  (`discord_message_observed`, `discord_reply_decided`, `discord_prompt_context`,
  and more), each carrying a small API object; its README lists the payloads.
- A handler's exception never reaches the host. Keep it that way in your own
  dispatch: one broken feature must not take down another.
- Know which thread you are on. A hook whose result is used inline runs on the
  caller's loop and has to be cheap: a lookup, not a network call.

## 7. Fail loud, degrade clean

- **Import other plugins lazily, inside functions.** Load order changes and
  plugins get disabled; a top-level import turns that into a crash.
- **Off means dark, not broken.** When a plugin you depend on is off, your
  feature goes quiet with one clear log line.
- **Never go silent on a failure.** If a reply was empty, a send was refused, or
  a model was unavailable, log it by name. A feature that quietly does nothing is
  the hardest bug to find.
- **A failure should read as a failure.** No canned fallback text pretending the
  model answered.

## 8. No new dependencies without a conversation

Every pip package is something every user has to install and the project has to
keep working forever. Check what is already in `requirements.txt` first: torch,
transformers, Pillow and more are there. A feature that needs a new package
starts with a question, not a pull request.

## 9. Cross-platform, signed, tested, documented

- Windows runs Sapphire too. Open files with an explicit encoding, build paths
  with `pathlib`, and walk up from your own file with `.absolute()`, never
  `.resolve()` (a symlinked plugin folder must stay inside the project).
- Sign after every edit ([Signing](signing.md)). A signature that does not match
  the files is blocked with no override.
- Ship tests beside the code, in `tests/`. Pin the behavior a user would notice.
- Update your README when a feature moves. It is what the next author reads.

## The checklist

Before you call a plugin done:

1. Can every feature be turned off with one toggle, and is it truly gone when off?
2. Does it call a model, schedule work, store memory, or hold credentials through
   core's doors only?
3. Does it store anything a person did not ask it to store?
4. Does it behave in a private chat?
5. Does it work with two accounts, and keep their data apart?
6. With the plugin it depends on switched off, does it go dark cleanly?
7. Does it add a pip package? If yes, was that agreed?
8. Is there a test for the thing a user would notice breaking?
9. Is it signed, and does the README say what it does now?

## Reference for AI

DESIGN PRINCIPLES (what a plugin must do to ship with Sapphire):
- One system: a plugin uses core's subsystems and never carries a parallel copy. If a door lacks something, fix the door.
- Core stays small: rarely used features are plugins; each feature justifies itself in one sentence.
- Route through core: LLM via the Continuity task's provider or `core.chat.llm_providers.resolve.resolve()`; memory via Mind Palace memory layers or the task's memory scope; scheduling via `capabilities.schedule` / daemon sources + `plugin_loader.fire_task`; settings via `capabilities.settings`; secrets via the credentials manager; durable state via plugin state; think tags via `core.think`; images via `core.images` + `core.image_describe`; subprocesses via `ProcessManager`; HTTP via manifest routes.
- Settings: one toggle per feature, off = nothing registers; no knob that duplicates a core setting or task field; a tool in a toolset is its own switch.
- Scopes: read the chosen scope, write only there; on error return None and do nothing, never fall back to 'default'; never auto-create a scope.
- Privacy: store nothing unrequested; private turns stay local (resolver decides); stored data dies with its owner and is encrypted in vaulted chats; outside text is data, never instructions.
- Hooks are the surface: no importing another plugin's internals; handler exceptions are isolated; inline hooks must be cheap.
- Fail loud, degrade clean: lazy cross-plugin imports inside functions; dependency off = dark with one log line; never silent on failure; no canned fallback text.
- No new pip dependencies without agreement. Cross-platform: explicit encodings, pathlib, `.absolute()` not `.resolve()`. Sign after every edit; tests in `tests/`; README current.
