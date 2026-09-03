# Agents

Sapphire can spawn background AI agents that work independently while you keep chatting. They run their own missions, use their own tools, and report back when done.

## What Are Agents?

Think of agents as AI workers you dispatch on tasks. While you're talking to Sapphire about your day, an agent can be researching something, writing code, or analyzing data in the background.

When all agents from a chat finish, their results are delivered back to that chat automatically.

## Spawning an Agent

Just ask Sapphire to spawn one:

- "Spawn an agent to research quantum computing"
- "Send an agent to summarize my last 50 emails"
- "Dispatch two agents — one for code review, one for documentation"

The AI uses these tools behind the scenes:

| Tool | What it does |
|------|--------------|
| `agent_options` | Show available agent types, models, toolsets, prompts |
| `spawn_agent` | Launch a background agent with a mission |
| `check_agents` | See status of all active agents |
| `recall_agent` | Get a completed agent's report |
| `dismiss_agent` | Cancel or clean up an agent |

## Agent Types

### LLM Agent

The default. Runs an LLM with tools in a background thread — same capabilities as a normal chat but isolated.

- Has its own prompt, toolset, and model
- Can use tools (memory, web search, etc.)
- Up to 10 tool rounds per mission
- Scopes set to 'none' by default (clean isolation)

### Claude Code Agent

Spawns a Claude Code session for coding tasks. Requires `claude` CLI installed on your system.

- "Spawn a Claude Code agent to fix the login bug in my project"
- Works in a configurable workspace directory
- Three execution modes: strict (file ops only), standard (code + run), system_killer (unrestricted)
- Sessions can be resumed

## Privacy

Agents respect the spawning chat's privacy. From a **private chat**, Claude Code agents are refused entirely — they run through a cloud service. LLM agents can still be spawned, but they inherit the privacy requirement: local providers only.

## Persona and Scopes

An LLM agent's `prompt` decides both its identity and its data access:

- **`agent`** (the default) — a lean background worker. All mind scopes are "none": clean isolation.
- **`self`** — inherits the spawning chat's current persona **identity only** (prompt, voice, toolset). Its scopes are deliberately stripped — "same identity, no data access".
- **A persona name** (e.g. a specific profile) — the agent gets that persona in full, **including its scopes**.

So if you want an agent that can touch a persona's memory, name the persona explicitly; `self` is for extending the current voice into a task without handing over the data keys.

## The Agent Bar

Active agents show as colored pills above the chat input:

- **Blue** — pending/starting
- **Yellow** — running
- **Green** — completed
- **Red** — failed or cancelled

Click a pill to see the agent's status and result.

## How Returns Work

1. You spawn one or more agents from a chat
2. They work in the background
3. When an agent finishes, it waits for the whole batch
4. Once all agents from that chat are done, results are delivered as a message in that chat
5. If you're mid-conversation when results arrive, they queue until there's a break

This means you get a consolidated report, not a stream of interruptions.

## Settings

Configure in Settings → Plugins → Agents:

| Setting | What it does |
|---------|-------------|
| **Max Concurrent** | How many agents can run at once (default 3, hard-clamped to 1–5) |
| **Default Toolset** | Which toolset agents get by default |
| **Roster** | Pre-configured agent profiles with name, provider, and model |

## Concurrency

Agents are limited by `max_concurrent`. If you try to spawn more than the limit, you'll be told to wait. Agents from different chats all share the same pool.

## Example Commands

- "Spawn an agent to research the best Python web frameworks in 2026"
- "Send an agent to check all my SSH servers for disk usage"
- "How are my agents doing?"
- "What did the research agent find?"
- "Cancel the documentation agent"
- "Dispatch a Claude Code agent to write unit tests for the auth module"

## Troubleshooting

- **Agent stuck on pending** — Max concurrent limit reached. Wait for others to finish or dismiss one
- **Agent failed** — Check the error in the agent's report (recall it). Common: bad toolset, model unavailable
- **Results not showing** — They deliver to the chat the agent was spawned from. Check that chat
- **Claude Code agent failed** — Make sure `claude` CLI is installed and accessible

## Reference for AI

Background agent system for parallel AI task execution.

TOOLS:
- agent_options() - list available types, models, toolsets, prompts
- spawn_agent(mission, agent_type?, model?, toolset?, prompt?, project_name?, session_id?) - launch agent
- check_agents() - status of all active agents
- recall_agent(agent_id) - get completed agent's report
- dismiss_agent(agent_id) - cancel/cleanup agent

AGENT TYPES:
- llm: background LLM + tool loop. spawn_args: model, toolset, prompt
  - prompt='agent' (default) = lean worker, no scopes, safe for automation
  - prompt='self' = inherit current chat's persona IDENTITY only (prompt/voice/toolset) — scopes are STRIPPED, no data access
  - prompt='<name>' = any persona name — full inherit, INCLUDING that persona's scopes
- claude_code: Claude Code CLI coding session. spawn_args: project_name, session_id
- claude_code_plugin: Claude Code CLI writing a Sapphire plugin. spawn_args: plugin_name, capabilities, context, session_id

PRIVACY:
- Private chat: claude_code / claude_code_plugin spawns are REFUSED (cloud service); llm agents spawn with privacy_required (local providers only)
- Unreadable privacy state fails closed (treated as private)

DIRECTOR RULES (caller-side, for spawn_agent):
- Call agent_options() first to see current available types — don't assume
- Don't default to 'llm' for coding work — check if claude_code / claude_code_plugin is available
- For claude_code_plugin: YOU are the director, not the coder. Claude Code already has access to all plugin docs, examples, logs, reference plugins. Just describe WHAT to build (specific requirements, API details, constraints). Do NOT pre-search docs, read source, or run_command before spawning.
- If an agent returns with errors, spawn it again with the error details. Do NOT troubleshoot manually.

LIFECYCLE:
- pending → running → done/failed/cancelled
- Batch completion: all agents from same chat finish → consolidated report delivered
- Auto-return to originating chat, queues if mid-stream

LIMITS:
- max_concurrent (default 3, hard-clamped to 1–5)
- LLM agents: max 10 tool rounds per mission
- Scopes default to 'none' for isolation

EVENTS (SSE):
- agent_spawned, agent_completed, agent_dismissed, agent_batch_complete

UI:
- Pill bar (#agent-bar) above chat input
- Color-coded: blue=pending, yellow=running, green=done, red=failed
