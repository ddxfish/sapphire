# Agents

Sapphire can spawn background agents that work while you keep talking. An agent runs its own
mission with its own tools, **asks her a question when it hits a fork**, and **reports back into
the chat by itself** when it is done — with or without a browser open.

## What an agent is

A *kind* is a sort of agent a plugin teaches her — today two ship:

| kind | plugin | what it is | cloud? | follow-up turns? |
|---|---|---|---|---|
| `llm` | Agents | an isolated LLM + tool loop in the background | no (local-capable) | no |
| `claude_code` | Claude Code | a Claude Code coding session on the Claude Agent SDK | yes | yes — `say` continues it |

An *agent* is one running (or resting) instance of a kind, bound to the chat it was spawned from.
It has a name (Alpha, Spark, Forge…), a status, a transcript, and — when it asked something — a
pending question.

## Spawning one

Just ask Sapphire:

- "Spawn an agent to research quantum computing"
- "Have Claude Code fix the viewer test" (a `claude_code` agent in core mode)
- "Build me a plugin that…" (a `claude_code` agent in plugin mode)

Behind the scenes she has **four tools** — the same grammar as devices: list, look, start, do.

| tool | what it does |
|---|---|
| `agent_list()` | her agents in this chat (running / waiting / resting), the kinds she can spawn, what waits in the chat's inbox |
| `agent_peek(agent, what?)` | look in on one: status and progress, its pending question verbatim, the latest transcript lines, the last report (`what='report'` for all of it) |
| `agent_spawn(kind, mission, options?)` | start one. `agent_spawn(kind)` alone shows that kind's options |
| `agent_action(agent, action, value?)` | `answer` its pending question · `say` a follow-up turn · `stop` it |

Common spawn options: `name` (a workspace or session *directory* name — not what the agent is
called), `model`, `context` (extra material appended to the mission). Each kind adds its own —
`llm`: `toolset`, `prompt`; `claude_code`: `mode` (project / plugin / core), `capabilities`, `effort`.

## How she hears from them: the inbox

Reports and questions land in the chat through the **inbox** — a per-chat queue in front of the
turn engine. If she is mid-message, the item waits until her message truly ends (every tool round
included) and then runs as its own turn. Several reports that arrive within three seconds fold
into one message with a block each. Nothing is dropped and no browser needs to be open.

What you see in the chat:

```
[Agent Spark (claude_code) — asks; not typed by the user]
Fix: Delete the obsolete test, or rewrite it against the new route?
  (a) delete — drop it
  (b) rewrite — redo it against /api/agents
Answer with agent_action('Spark', 'answer', '<a letter, a label, or your words>') — or leave it while you ask the user; it waits.
```

She answers with `agent_action('Spark', 'answer', 'b')` — or asks you first and relays your
word. A fork question waits ten minutes; unanswered, the agent takes the safe default and goes on.

```
[Agent Spark (claude_code) — reports (6m02s in); not typed by the user]
Rewrote the test against /api/agents/… 3 passed.
(cost so far $0.41 · session 7f3a1c90)
```

> The chat's **toolset must include the four agent tools** for her to answer and steer. Toolsets
> that had the old agent tools were migrated automatically; add `agent_list`, `agent_peek`,
> `agent_spawn`, `agent_action` to any other toolset you want to run agents from.

## The Agents page and the pill bar

**Apps → Agents** is the page behind the pills: the chats that have agents down the side; for
the chosen chat, a card per agent — status colour, a progress line (`running 4m · 12 tool
calls · last: Edit core/x.py`), the pending question as lettered buttons (or your own words),
a Say box while it is idle, Stop, and the transcript; below, the agents that finished earlier
(a resting one wakes from its Say box); and a form to spawn a new agent of any loaded kind,
with that kind's own options.

Above the chat input, the **pill bar** shows the chat's agents: yellow running, **purple
pulsing waiting** — click it and answer right there — teal idle, green done (amber if the run
degraded), red failed. A finished pill lingers eight seconds. An **inbox chip** `⧗ N queued`
appears when turns are waiting for her to finish (yours, an agent's, a satellite's); click it to
see them, × takes one back out.

## Typing while she talks

Send while she is mid-message and the turn **waits its turn** — the bubble pulses until it
runs, with a × to take it back. Several messages typed while she talks **fold into one turn**:
one user message (your thoughts separated by blank lines), one reply, one set of tokens — they
were continuations of one thought. Only *your* typed turns fold with each other: an agent's
report, a satellite's question, a Discord message, a MIDI take each get her own reply.

## Statuses

`running` → `waiting` (a question is out) → `idle` (a conversational agent between turns) →
`resting` (idle past its timeout; it keeps its session id and `say` wakes it) → `done` / `failed`
/ `stopped` / `lost` (it was live when Sapphire restarted and could not be resumed).

The pill bar above the chat input shows them: yellow running, **purple pulsing waiting**, teal
idle, green done (amber if the run degraded to a placeholder), red failed. A finished pill
lingers eight seconds and goes.

## Claude Code

`claude_code` agents are Claude Code sessions. Three modes:

| mode | where it works | gets |
|---|---|---|
| `project` | `<Workspace Directory>/<name>` | a CLAUDE.md from the Base + Project instructions |
| `plugin` | `user/plugins/<name>` | the plugin-author docs injected, a structural validation pass after each turn, `activate_plugin(name)` when it passes |
| `core` | Sapphire's own root | the root's own CLAUDE.md plus the Core instructions |

**Permissions are never routed to Sapphire** — she is not asked per command. Sessions run in the
CLI's `auto` permission mode: a classifier approves or denies each tool call, and what it does not
approve is denied with a reason. Only `AskUserQuestion` forks reach her. (Settings → Plugins →
Claude Code lets you switch to bypass.)

A session stays alive between turns: `agent_action('Spark', 'say', '…')` sends the next one.
Idle past the timeout it rests; the next `say` wakes it with `--resume` — history intact. Costs
are cumulative per session and shown on every report.

Core mode can see what you can see: it runs at the Sapphire root with your Claude Code login and
settings. Project and plugin modes load only the workspace's own CLAUDE.md, no user-level settings
or MCP servers.

## Privacy

Every action that sends content — spawn, say, answer, waking a resting agent — re-checks privacy
at that moment: the current turn, the agent's chat **now**, and the chat it was spawned from. A
private turn refuses *cloud* kinds (`claude_code`); local kinds run with local providers and
network tools blocked. A kind that does not say whether it is cloud counts as cloud.

Agents are found only from the chat they belong to; another chat sees "no agent named X here".
`agent_list` may mention agents that finished recently in *other public* chats — name, kind and
status only, never from a private chat. Missions, questions and reports are stored under the
agent's chat (encrypted with it when vaulted, gone when it is deleted); the agents file on disk
holds metadata only.

## Settings

Settings → Plugins → **Agents**: Max Concurrent (1–5, running agents only; waiting and idle ones
don't count), Default Toolset for `llm` agents, Roster (friendly model names → provider/model).

Settings → Plugins → **Claude Code**: Permissions (auto / bypass), Workspace Directory, Model,
Effort, Budget Cap, Idle timeout, Claude Code binary (empty = the SDK's bundled one), and the four
instruction textareas (Base, Project, Plugin, Core).

## Troubleshooting

- **She says the agent tools aren't in her toolset** — add the four `agent_*` tools to the chat's toolset.
- **A cloud kind is refused** — the chat (or the turn) is private. That is the design.
- **`claude_code` refuses with "claude-agent-sdk is not installed"** — `python -m pip install claude-agent-sdk` in Sapphire's environment.
- **An agent shows `lost`** — Sapphire restarted while it was live and it had no session to resume. Spawn again.
- **"Agent limit reached"** — Max Concurrent counts running agents; stop one or raise the limit.

## Reference for AI

TOOLS (core, all chat-local):
- agent_list() — this chat's agents + kinds + inbox depth; recently finished elsewhere (public chats only)
- agent_peek(agent, what?) — status/progress, pending question verbatim, transcript tail, last report; what='report' | 'transcript'
- agent_spawn(kind, mission, options?) — start; agent_spawn(kind) = that kind's screen. options: name (directory, not the agent's name), model, context + the kind's own
- agent_action(agent, action?, value?) — answer <letter|label|text> · say <text> (idle/resting only) · stop; no action = what it takes now

KINDS: llm (local-capable; options toolset, prompt: 'agent' lean default | 'self' identity only | a persona name) · claude_code (cloud, conversational; options mode project|plugin|core, capabilities, effort)

RULES:
- A question arrives in chat as `[Agent X (kind) — asks; not typed by the user]` with lettered options. Answer it; or leave it pending while you ask the user — it waits 10 min.
- Reports arrive as `[Agent X (kind) — done in …]` / `— reports (… in)`. You need not reply unless there is something to do.
- say only works on an idle or resting agent; a running one says "still working".
- Private turn → cloud kinds refused. Agents are only visible from their own chat.
- Director rules: say WHAT to do, not how. Claude Code already has the docs, logs and reference plugins. If an agent returns with errors, say the errors back to it (agent_action say) instead of troubleshooting by hand.

EVENTS (SSE): agent_spawned, agent_waiting, agent_event, agent_completed, agent_dismissed (ids and status only — never content).
