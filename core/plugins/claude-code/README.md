# Claude Code

The `claude_code` agent kind: a Claude Code coding session she spawns, talks
to and answers, on the Claude Agent SDK. It is a cloud kind, so a private chat
refuses it. The full guide is `docs/AGENTS.md` (section Claude Code).

```
agent_spawn("claude_code", "build a weather plugin", {"mode": "plugin", "name": "weather"})
agent_action("Forge", "say", "now add a test")
```

At a fork the session asks with a question card in the chat; she (or you)
answers with `agent_action(agent, "answer", ...)`. Permissions are never
routed to her: each tool call is approved or denied by Claude Code's own
`auto` classifier, or allowed outright in bypass mode.

## Modes

| `mode` | Works in |
|---|---|
| `project` (default) | `<Workspace Directory>/<name>`, outside Sapphire |
| `plugin` | `user/plugins/<name>`, with the plugin-author docs injected (`capabilities` picks which: tools, hooks, daemon, routes, settings, providers, schedule) |
| `core` | Sapphire's own root |

`effort`: `low` · `medium` · `high` · `xhigh` · `max` (empty = the model's default).

## Tool

`activate_plugin(name)`: once a plugin-mode session reports done, validates
the plugin in `user/plugins/<name>`, rescans and enables it. A plugin built
this way has no signature, so it loads only with unsigned plugins allowed
(the switch on the Settings > Plugins banner).

## Settings

Settings > Plugins > **Claude Code**:

| Setting | Default | |
|---|---|---|
| `permission_mode` | `auto` | `auto` (classifier) or `bypassPermissions` |
| `workspace_dir` | `~/claude-workspaces` | Where project-mode sessions live |
| `model` | empty | `fable`, `opus`, `sonnet`, `haiku` or a model id; empty = the account default |
| `effort` | empty | Default effort |
| `max_budget_usd` | 20 | A session ends on its own past this |
| `idle_timeout_min` | 60 | An idle session rests; the next `say` resumes it |
| `claude_binary` | empty | Empty = the CLI bundled with the SDK |

Plus four instruction boxes: Base (all modes), Project, Plugin, Core.

## Install

Click **Install** on the plugin card: it adds `claude-agent-sdk` (about 250 MB,
it bundles the Claude Code CLI). The rest of Sapphire runs without it.

Sessions run on your Claude Code sign-in (Pro or Max), never an API key. Sign
in once, as the user Sapphire runs as: `claude auth login`. With no sign-in a
run does nothing, bills nothing, and its report names that command.
