# Agents

The `llm` agent kind: a background LLM with its own tool loop that works a
mission while she keeps talking, then reports into the chat that spawned it.
It runs on any provider, local ones included. This plugin also holds the
roster of models agents may use.

The four agent tools are core, not this plugin's: `agent_list`,
`agent_spawn(kind, mission?, options?)`, `agent_peek(agent, what?)`,
`agent_action(agent, action?, value?)`. The full guide is `docs/AGENTS.md`.

## Spawn options (`llm`)

| Option | What it does |
|---|---|
| `toolset` | A saved toolset, a tool category (`web`, `files`, ...) or a plugin name. A comma list unions: `'web, files'`. Empty = the Default Toolset |
| `prompt` | `agent` (default): a lean worker with no data scopes. `self`: this chat's persona, her memory and nothing else. A persona name: that persona in full, scopes included |
| `memory` | `prompt='self'` only. `read-only` (default) = search and read; `full` = save, update and delete too; `false` = identity alone |
| `model` | A roster name, a provider key, or `provider:model`. A string no provider matches is refused at spawn |

Common to every kind: `name`, `context`. Each agent gets 10 tool rounds and up
to 3 tools at once. An agent spawned from a private chat runs private: cloud
providers and network tools are off for it.

## Settings

Settings > Plugins > **Agents**:

| Setting | Default | |
|---|---|---|
| `max_concurrent` | 3 | Running agents at once, 1-5 |
| `default_toolset` | `default` | Toolset when the spawn names none |
| `roster` | empty | Friendly names → provider and model |

## Where in the UI

**Apps > Agents**: your agents by chat, questions waiting for an answer,
follow-ups, transcripts, and a form to spawn more.
