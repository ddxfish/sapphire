# Voice Commands

Instant spoken commands that skip the LLM: no model call, no wait. On by
default. No tools, no settings, no dependencies. Hidden in managed installs.

| Say | Does |
|---|---|
| `stop` · `halt` · `be quiet` · `shut up` | Stops her speech and cancels the reply being written. She answers "Stopped." |
| `reset` · `reset chat` · `clear chat` · `clear history` | Clears the current chat's history |

The match is exact: the whole message must be the phrase (any case). "Stop
talking about cats" goes to her as a normal message. A typed message that is
exactly one of these phrases works the same way.

On a phone call, a caller's "stop" stops only that call; your speakers and
browser keep going. A "stop" from you leaves live calls alone.

For plugin authors: this plugin is the reference for the `voice_commands`
block in `plugin.json` (triggers, match mode, `bypass_llm`, handler). See
`docs/plugin-author/voice-commands.md`.
