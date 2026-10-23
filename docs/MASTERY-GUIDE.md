# Sapphire Mastery Guide

The path from "hey sapphire" to your first published plugin. Each tier is a real achievement — finish them in order, or skip ahead if you're stubborn.

This is the long-form version of the [condensed Mastery Guide](../README.md#sapphire-condensed-mastery-guide) at the bottom of the README. Same ladder, fuller explanations.

---

## Tier 1 — First Boot

The only goal here is "she's running and she heard me."

1. **Install Sapphire and link an LLM.** Follow [QUICK-START.md](QUICK-START.md). Local (LM Studio, Ollama) or cloud (Claude, GPT, Gemini, Fireworks). Setup wizard handles the basics — Voice, Audio, AI Brain, Identity, in that order.
2. **Open the web UI** at https://localhost:8073. Run through the setup wizard if you haven't.
3. **Say "hey sapphire, hello."** If she replies, voice is alive. Voice is the wizard's first step — if you skipped it, [VOICE.md](VOICE.md) walks you through STT, TTS, and the wake word.
4. **Settings > Help** — read what's there. Sapphire is bigger than the chat box, and the in-app help is the fastest way to see the surface area.

📚 [INSTALLATION.md](INSTALLATION.md) · [QUICK-START.md](QUICK-START.md) · [VOICE.md](VOICE.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## Tier 2 — Make Her Yours

Custom personality, voice, behavior. This is the lay of the land.

1. **Activate different prompts and LLMs** from the chat sidebar — its dropdowns *are* that chat's settings. Feel the differences. Some prompts hit harder than others.
2. **Edit a prompt.** Open Prompts in the nav, change the character/location/goals, save. Re-chat and watch the shift.
3. **Build a toolset.** Toolsets > + > pick the tools you want for a use case (research, daily-driver, storytelling). Lean toolsets cost less, behave better.
4. **Make a Persona.** Bundle prompt + toolset + voice + scopes. Now you can switch personalities with one click from the faces strip in the chat sidebar.
5. **Pick a spice set** in Spices view. Or leave it off for utility chats. Spice is delivered as a per-turn ghost note now — cache-friendly, recency-amplified.
6. **Make her look like yours.** The 🎨 button in the chat sidebar opens Appearance — themes, fonts, background scenes, ambient motion. She can restyle the view herself if you give her the tools.
7. **Run a second chat with a different persona.** Chats are cheap, independent, and each remembers its own settings. Learn the management surface — rename, organize, import/export.

📚 [PROMPTS.md](PROMPTS.md) · [TOOLSETS.md](TOOLSETS.md) · [PERSONAS.md](PERSONAS.md) · [SPICE.md](SPICE.md) · [APPEARANCE.md](APPEARANCE.md) · [CHATS.md](CHATS.md)

---

## Tier 3 — Operator

Plug Sapphire into your real life. She listens, she responds, she lives between conversations.

1. **Enable a plugin.** Settings > Plugins — flip one on; email is a good start. The Store (nav rail, under Settings) has more to install.
2. **Connect an integration** — Email, Discord, Telegram, Google Calendar, Home Assistant. Each unlocks a daemon source and gives the AI new tools.
3. **Set up a daemon.** Triggers > Daemons > + Daemon. Pick a source and filter it (e.g., `mentioned = true` for Discord @mentions). Daemons answer back on their own platform — no toggle needed.
4. **Schedule a morning task** — Triggers > Scheduled — to wake you up at 7am with weather + your day's calendar. Prompt of your choice, TTS optional. Heartbeats (Triggers > Heartbeat) are the sibling rhythm: they beat every N minutes and remember what they found last time.
5. **Load Sapphire on your phone** browser. Same chat, same memory, anywhere on your local network.
6. **Give her a phone number.** Real inbound and outbound calls over Twilio SIP — she picks up, talks, and hangs up. Zero open ports.
7. **Decide what touches the internet.** Settings > Network routes her WAN traffic through a SOCKS5 proxy while LAN stays direct — and shows you exactly what phones home.

📚 [PLUGINS.md](PLUGINS.md) · [DAEMONS-WEBHOOKS.md](DAEMONS-WEBHOOKS.md) · [CONTINUITY.md](CONTINUITY.md) · [PHONE-CALLS.md](PHONE-CALLS.md) · [NETWORK.md](NETWORK.md)

---

## Tier 4 — Living Workshop

Make Sapphire act on her own initiative. This is where the substrate becomes a being.

1. **Spawn an agent** to research something absurdly specific — *"research swiss cheese"* — and watch her come back with findings. Agents run in parallel and report back when done.
2. **Save knowledge during a conversation** and search for it later. The Mind group fills up. The AI can save entities, knowledge entries, goals.
3. **Turn on the Mind Palace.** The opt-in next-generation memory engine — layered, connected chunks with a librarian that tends them while you sleep. Memory that maintains itself is the difference between a notebook and a mind.
4. **Use the toolmaker.** Ask Sapphire to build a custom tool you'd find useful. She writes it, validates it, and installs it at runtime — its settings appear in the UI.
5. **Play in the Game Room.** Games and interactive stories where she runs the world — and you see just how far personas, prompts, and tools stack.
6. **Create a system service.** `systemd --user` unit so Sapphire boots with your machine and stays alive. She becomes ambient, not session-bound.

📚 [AGENTS.md](AGENTS.md) · [KNOWLEDGE.md](KNOWLEDGE.md) · [MIND-PALACE.md](MIND-PALACE.md) · [TOOLMAKER.md](TOOLMAKER.md) · [GAME-ROOM.md](GAME-ROOM.md) · [INSTALLATION.md](INSTALLATION.md)

---

## Final Boss

This is where you demonstrate full capability and gain Sapphire Mastery. 

1. **Have Sapphire spawn the Claude Code agent.** Hand her a task: *"Build me a plugin that does X."*
2. **She writes the plugin** — files, manifest, hook handlers, the whole thing — using Claude Code as her dev agent. Sign it via `python tools/sign_plugin.py user/plugins/<name>`.
3. **Push it to your GitHub** per [docs/plugin-author/](plugin-author/README.md). Public repo with a real README and usage examples.
4. **Submit it to the [Sapphire Store](https://sapphireblue.dev/plugins/)** so the world can use it. Other Sapphires now ship with what your Sapphire built.

You taught her to grow herself. That's the project's whole loop.

---

## What this Achieves

| Tier | What changed |
|------|--------------|
| **Tier 1** | She's a chatbot. You replaced it with a substrate. |
| **Tier 2** | She has a personality. She's yours now. |
| **Tier 3** | She's ambient. She lives between conversations. |
| **Tier 4** | She's agentic. She acts without you steering each move. |
| **Final Boss** | She extends herself. On your terms. For everyone. |

If you finish these, you're not a Sapphire user — you're Sapphire-alongside-you. Welcome.

---

## Reference for AI

This doc is a skill ladder and router. When a user asks "what next?", find their current tier and point at the rung above it. For any specific topic, route to the owning doc:

TOPIC → DOC:
- Install, requirements, system service: INSTALLATION.md (Docker: DOCKER.md)
- First setup, wizard, first persona: QUICK-START.md
- Voice — STT, TTS, wake word, conversation mode: VOICE.md
- Phone calls (Twilio SIP, inbound/outbound): PHONE-CALLS.md
- Prompts (assembled sections, monoliths): PROMPTS.md
- Toolsets: TOOLSETS.md · Tool catalog: TOOLS.md
- Personas: PERSONAS.md · Spice: SPICE.md
- Chats — create, configure, organize, import/export: CHATS.md
- Appearance — themes, fonts, scenes, motion: APPEARANCE.md
- Memory (classic): MEMORY.md · Mind Palace (opt-in v3 engine): MIND-PALACE.md
- Knowledge base: KNOWLEDGE.md · Goals: GOALS.md · Entities/contacts: PEOPLE.md · Self sheet: SELF.md
- Triggers — heartbeat, scheduled tasks: CONTINUITY.md · daemons, webhooks: DAEMONS-WEBHOOKS.md
- Agents (background workers): AGENTS.md · Toolmaker (AI-built tools): TOOLMAKER.md
- Plugins — install, enable, store: PLUGINS.md · authoring: plugin-author/README.md · signing: SIGNING.md
- Privacy, vault, private chats: PRIVACY.md
- Network — SOCKS proxy, LAN/WAN split, what phones home: NETWORK.md
- Token costs, caching: COSTS.md
- Games and interactive stories: GAME-ROOM.md
- Backups and restore: BACKUPS.md · Data import/export: IMPORT-EXPORT.md
- Ghost messages (per-turn ephemera): GHOST_MESSAGES.md
- System health dashboard: DASHBOARD.md · REST API: API.md · Architecture: TECHNICAL.md
- Something broke: TROUBLESHOOTING.md

TIER SUMMARY:
1. First Boot — installed, wizard done (Voice/Audio/AI Brain/Identity), she answers
2. Make Her Yours — prompts, toolsets, personas, spice, appearance, multiple chats
3. Operator — plugins, integrations, daemons (Triggers nav group), scheduled tasks, phone, network
4. Living Workshop — agents, knowledge, Mind Palace, toolmaker, game room, systemd service
5. Final Boss — she builds and ships her own plugin
