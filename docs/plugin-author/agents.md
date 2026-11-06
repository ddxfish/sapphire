# Agent kinds

A plugin can teach Sapphire a new *kind* of agent — a background worker she spawns with a mission,
that can ask her questions while it works and reports back into her chat by itself. The engine
(`core/agents/`) owns spawning, rows, privacy, lookups and delivery; your plugin owns the work.

The two kinds that ship are the reference: `core/plugins/agents/agent_kind.py` (`llm` — an LLM + tool
loop through `ExecutionContext`) and `core/plugins/claude-code/agent_kind.py` (`claude_code` — a
conversational session on the Claude Agent SDK).

## 1. Declare it in `plugin.json`

```json
"capabilities": {
  "agents": [
    {
      "kind": "weather_watch",
      "label": "Weather watcher",
      "icon": "⛅",
      "description": "watches a forecast and reports when it changes",
      "module": "agent_kind.py",
      "cloud": true,
      "conversational": false,
      "spawn_schema": [
        {"key": "city", "type": "string", "label": "City", "help": "the city to watch"},
        {"key": "hours", "type": "number", "label": "Hours", "default": 6}
      ],
      "names": ["Cirrus", "Nimbus", "Stratus"]
    }
  ]
}
```

| field | meaning |
|---|---|
| `kind` | slug, `[a-z0-9][a-z0-9_-]{0,32}`, unique across plugins |
| `module` | plugin-relative `.py` that defines `class Agent(core.agents.base.Agent)` |
| `cloud` | **defaults to `true` when absent.** `true` = its work leaves the machine → refused from a private chat. Write `false` only if the kind honors `self.privacy` on every network call (local providers, no network tools) |
| `conversational` | `true` = the agent stays alive between turns and takes `say(text)` |
| `spawn_schema` | extra options (plugin-settings field shape). The common ones — `mission`, `name`, `model`, `context` — are the engine's; a schema may not redefine them |
| `names` | display names its agents take, in turn (the engine keeps live names unique) |

The engine registers kinds at plugin load and unregisters at unload; a running agent keeps its
already-imported class and finishes on its own.

## 2. Write the class

```python
# user/plugins/<your-plugin>/agent_kind.py
from core.agents.base import Agent as BaseAgent

class Agent(BaseAgent):
    def __init__(self, row, engine):
        super().__init__(row, engine)
        # self.mission, self.options (your spawn_schema keys + name/model/context),
        # self.chat, self.privacy (True = the spawning turn was private), self.resume_token
        self.city = self.options.get('city') or 'here'

    def run(self, mission):
        """The work. Runs on the agent's own thread, in a copy of the spawning
        turn's context, under the chat's stream-brain carrier (hooks and tools
        read THIS chat's settings and privacy)."""
        forecast = self._fetch(self.city)
        self.event('note', f'fetched {self.city}')                 # a transcript line, never the chat
        if forecast.ambiguous:
            choice = self.ask({'questions': [{
                'question': 'Two stations disagree — which one?',
                'header': 'Station',
                'options': [{'label': 'Airport', 'description': 'hourly, 3 km away'},
                            {'label': 'Harbor', 'description': 'every 10 min, 9 km away'}],
                'multiSelect': False}]})
            # choice = {'Two stations disagree — which one?': 'Airport'}  or None after 10 min
        if self.cancelled:
            return
        self.report(f"{self.city}: {forecast.summary}")            # lands in her chat via the inbox
```

The three doors, and what they mean:

| call | effect |
|---|---|
| `self.ask(question, timeout=600)` | **blocks this agent** until the director answers (`agent_action(..., 'answer', ...)`) or the timeout. `question` is `{'text': '…'}` or the AskUserQuestion shape (`questions` with lettered one-line `options`). Returns a `str`, a `{question: label \| [labels] \| text}` dict, or `None` on timeout. The question appears in the chat as `[Agent Cirrus (weather_watch) — asks; …]` |
| `self.event(kind, data)` | a transcript line (`text`, `tool_use` `{name, input}`, `tool_result`, `note`). A bounded in-memory ring the UI and `agent_peek` read. Never the chat; never logged |
| `self.report(text)` | the turn's result. Delivered into the chat through the inbox with a `[Agent Cirrus (weather_watch) — done in …]` header, kept on the agent's row under its chat |

Also available: `self.cancelled` (an `Event`-backed bool — check it between steps), `self.status`
(the engine sets `running`/`waiting`; a conversational kind sets `idle` between turns and
`resting` when its loop ends with a `resume_token`), `self.warning` (set it when the run completed
as a placeholder — the pill goes amber).

### Conversational kinds

Override `say(text)` to take a follow-up turn and `stop()` to interrupt whatever you hold (call
`super().stop()` — it voids the result and wakes a blocked `ask`). Keep `self.resume_token` set
to whatever lets you continue later: when your `run()` returns with status `resting`, the row
rests with that token, and the next `say` builds a fresh instance from the row **with the token
and the text as its mission** — `run()` should then resume rather than start over. See
`core/plugins/claude-code/agent_kind.py` for the asyncio-on-a-thread shape.

## 3. What the engine does for you

- **Privacy, in one place.** Spawn, say, answer and wake re-check: the current turn, the agent's
  chat *now*, the chat it was spawned from. Private + `cloud` → refused. `self.privacy` tells a
  local kind to stay local. Unreadable counts as private.
- **Chat-local lookups.** Her tools find your agent only from its own chat.
- **Rows.** `user/plugin_state/core-agents.json` keeps metadata (id, name, kind, chat, status, times,
  `resume_token`, privacy). Mission, pending question and last report go to `plugin_chat_data`
  under the chat (owner `core-agents` — core state, out of reach of any plugin card's Purge data) — encrypted when vaulted, deleted with the chat. Don't persist content yourself.
- **Delivery.** Reports and questions ride the per-chat inbox (`core/chat/inbox.py`): they wait
  for her current message to end, fold with neighbours that land within 3 s, and never need a
  browser.
- **Events.** `agent_spawned`, `agent_waiting`, `agent_event`, `agent_completed`,
  `agent_dismissed` — ids and status only. Don't publish content yourself.
- **Concurrency.** `max_concurrent` counts running agents; waiting and idle don't.

## 4. Don'ts

- Don't write to the chat, the history DB or the event bus from a kind — `report`/`ask`/`event` are the doors.
- Don't log mission or report text; logs can be mounted into other sessions.
- Don't `import` your plugin's other modules by file path — use `importlib.import_module('plugins.<name>.…')`; a hyphenated plugin name is fine.
- Don't accept a free-form "resume anything" option; resume only what your own rows recorded.

## 5. Test it

`tests/test_agents_engine.py` and the `agent_world` fixture in `tests/conftest.py` show the
harness: register your kind in the fake registry, give the engine a fake module, and drive
`spawn` → `ask`/`answer` → `report` with the inbox recorded.

## Reference for AI

- Manifest: `capabilities.agents` = [{kind ([a-z0-9][a-z0-9_-]{0,32}, unique), label, icon, description, module (plugin-relative .py defining `class Agent(core.agents.base.Agent)`), cloud (DEFAULT true = refused from private chats; false only if every network call honors self.privacy), conversational (bool), spawn_schema (plugin-settings field shape; may not redefine mission/name/model/context), names ([display names])}]. Registered at load, unregistered at unload.
- Class: `__init__(row, engine)` (self.mission, self.options, self.chat, self.privacy, self.resume_token); `run(mission)` on its own thread under the chat's context. Doors: `self.ask(question, timeout=600)` blocks until answered (str | {question: label(s)} | None on timeout); `self.event(kind, data)` transcript line (text|tool_use|tool_result|note), never the chat; `self.report(text)` result into the chat via the inbox. Also `self.cancelled`, `self.status` (running|waiting|idle|resting), `self.warning`.
- Conversational: override `say(text)` and `stop()` (call super); keep `self.resume_token`; status `resting` + token = next `say` rebuilds from the row and resumes.
- Engine owns: privacy re-checks on spawn/say/answer/wake (private + cloud = refused; unreadable = private), chat-local lookups, rows in `user/plugin_state/core-agents.json` (metadata) + `plugin_chat_data` owner `core-agents` (mission/question/report; vault-encrypted, deleted with the chat), inbox delivery, events (agent_spawned/waiting/event/completed/dismissed — ids only), `max_concurrent` (running only).
- Her core tools: `agent_spawn`, `agent_action` (answer | say | stop; agent alone = what it takes now), `agent_list`, `agent_peek`. Shipped kinds: `llm` (core/plugins/agents), `claude_code` (core/plugins/claude-code).
- Don't: write chat/history/event bus from a kind, log mission/report text, import sibling modules by path (use importlib.import_module('plugins.<name>....')), accept free-form resume.
