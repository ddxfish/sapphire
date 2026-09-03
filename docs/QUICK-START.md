# Quick Start

You've installed Sapphire and opened it in your browser. The setup wizard walks you through the basics — this guide goes deeper, building your first custom AI persona from scratch and getting you chatting.

## Phase 1: Setup Wizard

On first launch, the wizard walks four steps:

1. **Voice** — This is where you turn voice on. Pick a speech-to-text provider (local Faster Whisper or cloud), a text-to-speech provider (local Kokoro or ElevenLabs), and enable the wake word if you want hands-free "hey sapphire". Leave everything Disabled for a text-only install. Full details: [VOICE.md](VOICE.md).
2. **Audio** — Choose and test your microphone and speakers.
3. **AI Brain** — Choose an LLM (local or cloud), enter its key or URL, and hit **Test Connection** to verify it responds.
4. **Identity** — Your name and timezone, so the AI knows who it's talking to and when.

If you skip the wizard, everything is reachable later: LLM in Settings → LLM, voice in Settings → TTS / STT / Wakeword (see [VOICE.md](VOICE.md)).

### LLM Options

**Common options:**

| Option | Privacy | Needs | Best For |
|--------|---------|-------|----------|
| **LM Studio** (local) | Full privacy | GPU, 16GB+ RAM | Private use, stories |
| **Ollama** (local) | Full privacy | GPU, 8GB+ RAM | Easy local setup |
| **Claude** (cloud) | Conversations sent to Anthropic | API key | Complex reasoning, coding |
| **OpenAI** (cloud) | Conversations sent to OpenAI | API key | General purpose |
| **Gemini** (cloud) | Conversations sent to Google | API key | Fast, multimodal |
| **Fireworks** (cloud) | Conversations sent to Fireworks | API key | Fast, open models |

Claude, OpenAI, and Gemini are built in. LM Studio, Ollama, Fireworks, and many more are one click away as curated presets in Settings → LLM.

Local: Install [LM Studio](https://lmstudio.ai/) or [Ollama](https://ollama.com/), load a model, enable the API. Sapphire connects automatically.

Cloud: Get an API key from the provider and enter it in the provider's **API Key** field in Settings → LLM.

**Custom providers:** Any endpoint that speaks the OpenAI, Anthropic, or Responses API spec works — add it as a custom provider in Settings → LLM. This covers most local and cloud services (vLLM, text-generation-webui, Together, Groq, etc.). Want cloud traffic routed through a proxy? See [NETWORK.md](NETWORK.md).

**Plugin providers:** If a future LLM doesn't fit any of these specs, plugins can register entirely custom LLM backends via the provider system. See [Providers](plugin-author/providers.md).

---

## Phase 2: Create a Prompt

Your prompt is who the AI is. Sapphire has two types — start with **Assembled** for full control.

1. Open **Prompts** in the nav rail (under Persona group)
2. Click **+** to create a new prompt
3. Choose **Assembled** and give it a name
4. Build it from sections:

| Section | What to write | Example |
|---------|--------------|---------|
| **Character** | Who the AI is | "You are Nova, a sharp-witted AI who loves science and dry humor." |
| **Relationship** | Who you are to it | "I am Alex, your creator and friend." |
| **Location** | Where you are | "We're in a cozy apartment with rain on the windows." |
| **Goals** | What it should do | "Be helpful, honest, and keep conversations interesting." |
| **Format** | How it should respond | "Keep responses conversational, 2-3 paragraphs max." |

**Tips:**
- Write in first/second person — "You are..." for the AI, "I am..." for you
- Use `{ai_name}` and `{user_name}` as variables so they update if you change names
- Extras and Emotions sections allow multiple — swap them dynamically
- The AI can edit its own assembled prompt sections at runtime (with meta tools)

---

## Phase 3: Create a Toolset

Tools are the AI's abilities — memory, web search, knowledge, and more. A toolset is a named group of tools.

1. Open **Toolsets** in the nav rail (under Persona group)
2. Click **+** to create a new toolset
3. Name it something meaningful (e.g., "daily-driver", "research", "storytelling")
4. Check the tools you want:

| Tool category | Good for |
|---------------|----------|
| **Memory** (save/search/recall) | Persistent conversations |
| **Knowledge** (save/search/list) | Organized facts and topics |
| **Web search + research** | Answering current questions |
| **Meta tools** (prompt editing) | Self-modifying AI personality |
| **Goals** | Tracking progress on things |

5. Save — it's available immediately

Start lean. You can always add tools later. A toolset with just memory and web search covers most daily use.

---

## Phase 4: Pick Your Spices

Spice injects random prompt snippets to keep conversations fresh and avoid repetition. Great for stories, optional for utility chats.

1. Open **Spices** in the nav rail (under Persona group)
2. Pick a **spice set** on the left — a set is a named selection of categories
3. Toggle categories in or out of the set — storytelling, mood shifts, conversation starters
4. Optionally add your own categories and spice entries

Each chat picks its own spice set, and spice rotates every few turns (the "spice turns" setting, per chat). If you don't want randomness in a chat, just switch spice off there — it's per-chat. See [SPICE.md](SPICE.md) for the full system.

---

## Phase 5: Build Your Persona

Now combine everything into a persona — one-click personality switching.

1. Open **Personas** in the nav rail
2. Click **+** to create a new persona
3. Fill in the fields:
   - **Prompt** — select the one you made in Phase 2
   - **Toolset** — select the one from Phase 3
   - **Spice** — select a spice set from Phase 4 (or "none")
   - **Voice** — pick a TTS voice (try Heart, Sky, or Isabella)
   - **Pitch/Speed** — fine-tune the voice
   - **LLM Provider** — Auto (uses default) or force a specific one
4. Give it an avatar and tagline
5. Save

You now have a complete AI persona. Or skip building your own and use one of the built-in personas — Sapphire, Cobalt, Anita, Alfred, and more. See [PERSONAS.md](PERSONAS.md) for everything a persona can carry.

---

## Phase 6: Chat

1. Open **Chat** in the nav rail
2. Activate your persona from the sidebar persona grid (or just start typing)
3. Talk

**Things to try:**
- "Remember that I prefer dark roast coffee" — tests memory
- "Search the web for today's news" — tests web tools
- "What do you know about me?" — tests recall
- "Change your location to a beach" — tests self-modification (needs meta tools)

### Per-Chat Settings

Each chat has its own settings, and the chat sidebar *is* the settings panel — what you see there is what this chat uses:
- Prompt, toolset, spice set, LLM provider — dropdowns, all independent per chat
- **Mind** accordion — scopes that isolate memory, knowledge, entities per chat
- **Documents** accordion — attach files for the AI to reference (RAG)
- **TTS (Voice)** accordion — voice, pitch, and speed for this chat

Creating, renaming, importing, and organizing chats is covered in [CHATS.md](CHATS.md). The 🎨 button in the sidebar header opens Appearance — themes, scenes, and motion ([APPEARANCE.md](APPEARANCE.md)).

---

## Mind Scopes

Scopes isolate data per-chat. Your work AI doesn't need to see your personal memories, and your storytelling chat doesn't need your email contacts.

Set scopes in the chat sidebar's **Mind** accordion (or they come bundled with a persona).

| Scope | What it isolates | Sees global? |
|-------|-----------------|-------------|
| **Memory** | Long-term memories | Yes — own + global |
| **Goals** | Goal tracking | Yes |
| **Knowledge** | Knowledge tabs and entries | Yes |
| **People** | Contacts | Yes |
| **Email** | Email account | No |
| **Bitcoin** | Wallet | No |
| **Google Cal** | Calendar account | No |
| **Telegram** | Telegram account | No |
| **Discord** | Discord account | No |
| **RAG** | Per-chat documents | No (strict) |

**Private chats** aren't a scope dropdown — unlock the vault with the padlock 🔒 in the chat sidebar, and any chat you talk in while it's open becomes private: locked to local models and local tools, encrypted on disk. See [PRIVACY.md](PRIVACY.md).

**How global overlay works:** Memory, goals, knowledge, and people scopes see their own data AND anything in the "global" scope. So shared info (your name, your preferences) lives in global and every scope sees it, while specialized data stays isolated.

**Set to "none"** to disable a system entirely for that chat (e.g., no memory for throwaway chats).

**Create new scopes** with the **+** button next to any dropdown. Name it anything — "work", "personal", "story-world". Plugins can register scopes of their own, so your list may show more than the table above.

---

## Extended Thinking

Some LLMs can think through problems step-by-step before answering. This improves reasoning but costs more tokens.

| Provider | Feature | How to enable |
|----------|---------|---------------|
| **Claude** | Adaptive Thinking | Settings → LLM → Claude → Adaptive Thinking toggle + Effort (Low to Max) |
| **GPT-5.x** | Reasoning Effort + Summaries | Reasoning Effort (low/medium/high) and Reasoning Summary on the provider |
| **Gemini** | Reasoning Effort | Reasoning Effort on thinking-enabled models |

Good for complex tasks, overkill for casual chat — higher effort means more thinking tokens per reply. Custom providers get a universal "Disable thinking" toggle and a 🧠 Thinking probe to see what a model actually does.

See [COSTS.md](COSTS.md) for how to manage token usage and caching.

---
---

# Optional: Integrations

Everything above gets you a working AI companion. Below are optional integrations for connecting Sapphire to external services.

---

## Telegram

Connect Sapphire to your Telegram account.

1. Install Telegram on your phone if you haven't — you need it to receive the login code
2. Go to [my.telegram.org](https://my.telegram.org) → log in → click **"API development tools"** (not "Bot API") → get **API ID** and **API Hash**
3. In Sapphire: Settings → expand Plugin Settings → Telegram
4. Enter API ID and Hash → click **Save Settings** (must save before adding account)
5. Scroll down → **+ Add Account** → enter phone → enter code Telegram sends you
6. Enable Telegram tools in your toolset

Now the AI can read your chats and send messages. See the Telegram plugin docs in Help → Plugins for full details.

---

## Discord

Connect a Discord bot to your server.

1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Enable **Message Content Intent** in the Bot tab
3. Invite the bot to your server via OAuth2 URL
4. In Sapphire: Settings → expand Plugin Settings → Discord → paste bot token
5. Enable Discord tools in your toolset

The AI can read channels and send messages. See the Discord plugin docs in Help → Plugins for full details.

---

## Email

Connect your email inbox.

1. In Sapphire: Settings → expand Plugin Settings → Email → "Add Account"
2. Enter IMAP/SMTP server, email address, and password
   - Gmail users: use an [App Password](https://myaccount.google.com/apppasswords), not your regular password
3. Enable Email tools in your toolset
4. Add contacts in **Mind → Entities** to whitelist who the AI can email

The AI can read your inbox and send emails — but only to people you've added to contacts. See the Email plugin docs in Help → Plugins for full details.

---

## Linking Daemons to Integrations

Once you've connected Telegram, Discord, or Email, you can set up **daemons** — background listeners that trigger the AI when something happens.

1. Open **Triggers** in the nav rail → **Daemons**
2. Click **+ Daemon**
3. Pick a **Daemon Source**:
   - **Discord Message** — reacts to messages in your server
   - **Telegram Message** — reacts to incoming Telegram chats
   - **New Email** — reacts to incoming emails
4. Add **filter** rows to narrow what triggers it (channel name, sender, keywords) — the event must match every row, comma means "any of these"
5. Configure the AI: instructions, prompt, toolset, voice, scopes

Daemons always reply back to their source — a Discord daemon answers in Discord, an email daemon answers by email.

### Example: Discord Helper Bot

```
Source: Discord Message
Filter: mentioned = true
Prompt: your-helper-prompt
Toolset: your-toolset
```

The AI responds whenever someone @mentions the bot.

### Example: Email Auto-Responder

```
Source: New Email
Filter: to_address = support@mysite.com
Prompt: support-agent
Email scope: support
```

### Tips

- Use **filters** — without them, the daemon fires on every event
- Use **scopes** to keep daemon memory separate from your personal chats
- Use a **named chat** to see daemon conversations in the chat list — and to review what the AI has been saying on your behalf

See [DAEMONS-WEBHOOKS.md](DAEMONS-WEBHOOKS.md) for webhooks, advanced filters, and more examples.

---

## What's Next?

Now that you're set up, explore:

- [VOICE.md](VOICE.md) — STT, TTS, wake word, and conversation mode
- [CHATS.md](CHATS.md) — Everything about creating and managing chats
- [APPEARANCE.md](APPEARANCE.md) — Themes, scenes, fonts, and motion
- [CONTINUITY.md](CONTINUITY.md) — Scheduled tasks (morning greetings, dream mode)
- [KNOWLEDGE.md](KNOWLEDGE.md) — Organized knowledge base
- [MIND-PALACE.md](MIND-PALACE.md) — The opt-in next-generation memory engine
- [AGENTS.md](AGENTS.md) — Spawn background AI workers
- [TOOLMAKER.md](TOOLMAKER.md) — Let the AI create its own tools
- [PLUGINS.md](PLUGINS.md) — Extend Sapphire with plugins
- [GAME-ROOM.md](GAME-ROOM.md) — Games and interactive stories with your AI
- [NETWORK.md](NETWORK.md) — Proxy routing and what phones home
- [BACKUPS.md](BACKUPS.md) — Automatic backups and one-click restore

Or follow the whole ladder in order: [MASTERY-GUIDE.md](MASTERY-GUIDE.md).

---

## Reference for AI

Guide users through initial Sapphire setup and first persona creation.

SETUP ORDER:
1. Setup wizard — four steps: Voice (STT/TTS/wake word — voice on/off lives here, docs/VOICE.md), Audio (mic + speaker devices), AI Brain (LLM + Test Connection), Identity (user name + timezone)
2. Create assembled prompt (Prompts view → + → Assembled)
3. Create toolset (Toolsets view → + → check functions)
4. Pick a spice set + its categories (Spices view)
5. Create persona (Personas view → + → assign prompt/toolset/spice/voice)
6. Chat

ASSEMBLED PROMPT SECTIONS:
- character: who the AI is ("You are...") — first section, named "character" not "persona"
- relationship: who the user is ("I am...")
- location: setting/environment
- goals: what the AI should do
- format: response style
- scenario: current situation
- extras: additional rules (multiple allowed)
- emotions: current mood (multiple allowed)
- Variables: {ai_name}, {user_name}

TOOLSET CREATION:
- Toolsets view → + → name it → check tools → save
- Starter set: memory (save/search/recall) + web search
- Per-chat: each chat stores its own active toolset

PERSONA CREATION:
- Personas view → + → assign prompt, toolset, spice, voice, LLM
- Quick switch: chat sidebar persona faces strip
- Set as Default: star (applies to new chats)

SCOPES:
- memory, goal, knowledge, people: global overlay (sees own + global)
- email, bitcoin, gcal, telegram, discord: no overlay (strict per-scope)
- rag: strict per-chat isolation
- plugins can register additional scopes
- private: boolean — private chat (local models + local tools only); not a dropdown, vault padlock in chat sidebar, see docs/PRIVACY.md
- Set per-chat in chat sidebar → Mind accordion
- "none" disables a system for that chat
- Create new scopes with + button

THINKING/REASONING:
- Claude: Adaptive Thinking toggle + Effort (low/medium/high/xhigh/max) in Settings → LLM
- GPT-5.x: Reasoning Effort (low/medium/high) + Reasoning Summary
- Gemini: Reasoning Effort on thinking-enabled models
- Custom providers: universal "Disable thinking" toggle + thinking probe button

OPTIONAL INTEGRATIONS:
- Telegram: my.telegram.org API ID/Hash → Settings → Plugin Settings → Telegram
- Discord: Developer Portal bot token → Settings → Plugin Settings → Discord
- Email: IMAP/SMTP + password (Gmail: App Password) → Settings → Plugin Settings → Email; whitelist recipients in Mind → Entities
- All require enabling respective tools in active toolset

DAEMONS:
- Triggers nav group → Daemons → + Daemon
- Sources: discord_message, telegram_message, email_message (labels: Discord Message, Telegram Message, New Email)
- Filters: rows, all AND'd; comma = any-of; key suffixes _not and _contains
- Replies are implicit: daemons always respond back to their source platform
- Use scopes to isolate daemon memory from personal chats
- Full trigger surface: Triggers → Heartbeat / Scheduled / Daemons / Realtime / Webhooks (docs/CONTINUITY.md, docs/DAEMONS-WEBHOOKS.md)
