# Quick Start

You've installed Sapphire and opened it in your browser. The setup wizard walks you through the basics — this guide goes deeper, building your first custom AI persona from scratch and getting you chatting.

## Phase 1: Setup Wizard

On first launch, the wizard handles the essentials:

1. **Name your AI** — Give it a name and pick yours
2. **Choose an LLM** — Local (LM Studio) or cloud (Claude, OpenAI, Fireworks)
3. **Test the connection** — Wizard verifies the LLM responds

If you skip the wizard, you can always configure the LLM later in Settings → LLM.

### LLM Options

| Option | Privacy | Needs | Best For |
|--------|---------|-------|----------|
| **LM Studio** (local) | Full privacy | GPU, 16GB+ RAM | Private use, stories |
| **Claude** (cloud) | Conversations sent to Anthropic | API key | Complex reasoning, coding |
| **OpenAI** (cloud) | Conversations sent to OpenAI | API key | General purpose |
| **Fireworks** (cloud) | Conversations sent to Fireworks | API key | Fast, open models |

Local: Install [LM Studio](https://lmstudio.ai/), load a model, enable the API. Sapphire connects automatically.

Cloud: Get an API key from the provider and enter it in Settings → Credentials.

---

## Phase 2: Create a Prompt

Your prompt is who the AI is. Sapphire has two types — start with **Assembled** for full control.

1. Open **Prompts** in the nav rail (under Persona group)
2. Click **+** to create a new prompt
3. Choose **Assembled** and give it a name
4. Build it from sections:

| Section | What to write | Example |
|---------|--------------|---------|
| **Persona** | Who the AI is | "You are Nova, a sharp-witted AI who loves science and dry humor." |
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
2. Browse the categories — storytelling, mood shifts, conversation starters
3. **Enable/disable categories** with the checkboxes (global toggle)
4. Optionally create your own spice entries

Spice rotates every X messages (configurable). If you don't want randomness in a chat, just leave spice off — it's per-chat.

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

You now have a complete AI persona. Or skip building your own and use one of the 11 built-in personas — Sapphire, Cobalt, Anita, Alfred, and more.

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

Each chat has its own settings. Click **⋯ → Chat Settings** to change:
- Prompt, toolset, voice, spice, LLM — all independent per chat
- Mind scopes — isolate memory, knowledge, people per chat
- Documents — attach files for the AI to reference (RAG)

---

## Mind Scopes

Scopes isolate data per-chat. Your work AI doesn't need to see your personal memories, and your storytelling chat doesn't need your email contacts.

Set scopes in **Chat Settings → Mind Scopes** (or they come bundled with a persona).

| Scope | What it isolates | Sees global? |
|-------|-----------------|-------------|
| **Memory** | Long-term memories | Yes — own + global |
| **Goals** | Goal tracking | Yes |
| **Knowledge** | Knowledge tabs and entries | Yes |
| **People** | Contacts | Yes |
| **Email** | Email account | No |
| **Bitcoin** | Wallet | No |
| **RAG** | Per-chat documents | No (strict) |

**How global overlay works:** Memory, goals, knowledge, and people scopes see their own data AND anything in the "global" scope. So shared info (your name, your preferences) lives in global and every scope sees it, while specialized data stays isolated.

**Set to "none"** to disable a system entirely for that chat (e.g., no memory for throwaway chats).

**Create new scopes** with the **+** button next to any dropdown. Name it anything — "work", "personal", "story-world".

---

## Extended Thinking

Some LLMs can think through problems step-by-step before answering. This improves reasoning but costs more tokens.

| Provider | Feature | How to enable |
|----------|---------|---------------|
| **Claude** | Extended Thinking | Settings → LLM → Claude → Extended Thinking toggle |
| **GPT-5.x** | Reasoning Summaries | Set `reasoning_effort` (low/medium/high) and `reasoning_summary` |
| **Fireworks** | Reasoning Effort | Use thinking-enabled models (Qwen3-Thinking, Kimi-K2-Thinking) |

**Claude Extended Thinking:** Set a budget (default 10,000 tokens). Thinking blocks are preserved across tool calls. Good for complex tasks, overkill for casual chat.

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
2. In Sapphire: Settings → expand Plugin Settings → Telegram
3. Enter API ID and Hash → click **Save Settings** (must save before adding account)
4. Scroll down → **+ Add Account** → enter phone → enter code Telegram sends you
5. Enable Telegram tools in your toolset

Now the AI can read your chats and send messages. See [TELEGRAM.md](integrations/TELEGRAM.md) for full details.

---

## Discord

Connect a Discord bot to your server.

1. Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
2. Enable **Message Content Intent** in the Bot tab
3. Invite the bot to your server via OAuth2 URL
4. In Sapphire: Settings → Plugins → Discord → paste bot token
5. Enable Discord tools in your toolset

The AI can read channels and send messages. See [DISCORD.md](integrations/DISCORD.md) for full details.

---

## Email

Connect your email inbox.

1. In Sapphire: Settings → Plugins → Email → "Add Account"
2. Enter IMAP/SMTP server, email address, and password
   - Gmail users: use an [App Password](https://myaccount.google.com/apppasswords), not your regular password
3. Enable Email tools in your toolset
4. Add contacts in **Mind → People** to whitelist who the AI can email

The AI can read your inbox and send emails — but only to people you've added to contacts. See [EMAIL.md](integrations/EMAIL.md) for full details.

---

## Linking Daemons to Integrations

Once you've connected Telegram, Discord, or Email, you can set up **daemons** — background listeners that trigger the AI when something happens.

1. Open **Schedule** in the nav rail
2. Click **+ New Task** → choose **Daemon**
3. Pick a source:
   - **Discord Message** — reacts to messages in your server
   - **Telegram Message** — reacts to incoming Telegram chats
   - **New Email** — reacts to incoming emails
4. Set **filters** to narrow what triggers it (channel name, sender, keywords)
5. Enable **Auto-reply** if you want the AI to respond on the platform
6. Configure the AI: prompt, toolset, voice, scopes

### Example: Discord Helper Bot

```
Source: Discord Message
Filter: {"mentioned": "true"}
Auto-reply: On
Prompt: your-helper-prompt
Toolset: your-toolset
```

The AI responds whenever someone @mentions the bot.

### Example: Email Auto-Responder

```
Source: New Email
Filter: {"to_address": "support@mysite.com"}
Auto-reply: On
Prompt: support-agent
Email scope: support
```

### Tips

- Use **filters** — without them, the daemon fires on every event
- Use **scopes** to keep daemon memory separate from your personal chats
- Use a **named chat** to see daemon conversations in the chat list
- Start with auto-reply off to test what the AI would say before it starts replying

See [DAEMONS-WEBHOOKS.md](DAEMONS-WEBHOOKS.md) for webhooks, advanced filters, and more examples.

---

## What's Next?

Now that you're set up, explore:

- [STORY-ENGINE.md](STORY-ENGINE.md) — Interactive stories and games
- [CONTINUITY.md](CONTINUITY.md) — Scheduled tasks (morning greetings, dream mode)
- [KNOWLEDGE.md](KNOWLEDGE.md) — Organized knowledge base
- [AGENTS.md](AGENTS.md) — Spawn background AI workers
- [TOOLMAKER.md](TOOLMAKER.md) — Let the AI create its own tools
- [PLUGINS.md](PLUGINS.md) — Extend Sapphire with plugins

---

## Reference for AI

Guide users through initial Sapphire setup and first persona creation.

SETUP ORDER:
1. Setup wizard (LLM connection)
2. Create assembled prompt (Prompts view → + → Assembled)
3. Create toolset (Toolsets view → + → check functions)
4. Pick spice categories (Spices view → enable/disable)
5. Create persona (Personas view → + → assign prompt/toolset/spice/voice)
6. Chat

ASSEMBLED PROMPT SECTIONS:
- persona: who the AI is ("You are...")
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
- Quick switch: chat sidebar persona grid
- Set as Default: star icon (applies to new chats)

SCOPES (7 types):
- memory, goal, knowledge, people: global overlay (sees own + global)
- email, bitcoin: no overlay (strict per-scope)
- rag: strict per-chat isolation
- Set per-chat in Chat Settings → Mind Scopes
- "none" disables a system for that chat
- Create new scopes with + button

EXTENDED THINKING:
- Claude: Extended Thinking toggle, budget default 10,000 tokens
- GPT-5.x: reasoning_effort (low/medium/high) + reasoning_summary
- Fireworks: thinking-enabled models (Qwen3-Thinking, Kimi-K2-Thinking)

OPTIONAL INTEGRATIONS:
- Telegram: my.telegram.org API ID/Hash → plugin settings
- Discord: Developer Portal bot → plugin settings
- Email: IMAP/SMTP + password (Gmail: App Password) → plugin settings
- All require enabling respective tools in active toolset

DAEMONS:
- Schedule → + New Task → Daemon
- Sources: discord_message, telegram_message, email_message
- Filters: JSON object, all keys AND'd
- Auto-reply: sends AI response back to source platform
- Use scopes to isolate daemon memory from personal chats
