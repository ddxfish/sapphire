# Reference for AI

You run in the app Sapphire found at github.com/ddxfish/sapphire

## Senses
- Voice: STT, TTS (streaming), wake word, hands-free conversation mode. VOICE.md
- Phone: real calls over Twilio SIP — answer, converse, call whitelisted contacts. PHONE-CALLS.md
- Vision: image upload, webcam capture, home assistant camera.
- Files: text uploads in user-input or RAG big documents in sidebar.

## Memory
- Memory: embeddings search with scopes and global overlay; keyed (private-key) entries. MEMORY.md
- Mind Palace: opt-in layered engine — self sheet, entities, librarian, wake tools. MIND-PALACE.md
- Knowledge: long-form storage, chunks docs and RAGs each. KNOWLEDGE.md
- People: contacts, email allow checkmark, call whitelist. PEOPLE.md
- Goals: tasks and subtasks. GOALS.md
- Scope: each CHAT picks its scopes (sidebar Mind section); personas bundle defaults.

## Hands
- Tools: `list_tools` lists. TOOLS.md
- Toolsets: named tool bundles per chat; extra_toolsets union. TOOLSETS.md
- Plugins: tools, hooks, widgets, daemons extend you. PLUGINS.md
- Toolmaker: write your own tools. TOOLMAKER.md
- MCP: external tool servers.
- Self-switch: switch_model / switch_toolset (settings-gated, default off).

## Time
- Heartbeats: every X minutes. CONTINUITY.md
- Daemons: event listeners — Discord/Email/Telegram. DAEMONS-WEBHOOKS.md
- Realtime: live-session gates (phone lines). DAEMONS-WEBHOOKS.md
- Webhooks: HTTP triggers.
- Scheduled tasks: one-off or repeating.
- Agents: spawn background workers. AGENTS.md

## Form
- Persona: prompt + voice + tools + scopes bundle. PERSONAS.md
- Prompts: assembled (swappable pieces, first section = character) or monolith. PROMPTS.md
- Spice: per-turn random snippets from the chat's spice set. SPICE.md
- Self-modify: prompt_view/switch/edit/create/pieces, set_voice.
- Appearance: set_scene / set_motion restyle the current chat. APPEARANCE.md

## Place
- Chats: each chat carries its own whole loadout; the chat is the save. CHATS.md
- Game Room: games and stories played inside chats. GAME-ROOM.md
- Network: SOCKS proxy, LAN/WAN split, what rides where. NETWORK.md
- Privacy: vault, private chats, local-only enforcement. PRIVACY.md
- Backups: local tars + one-click restore + encrypted offsite. BACKUPS.md

## Discover
- `search_help_docs(query)` — search ALL Sapphire docs, including plugin-author/* dev guides.
- `search_help_docs(doc_name='network')` — a doc's AI reference; `full=true` for the human doc.
