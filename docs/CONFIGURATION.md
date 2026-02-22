# Configuration

Run the [Installation](INSTALLATION.md) and open Sapphire before you configure. The setup wizard handles LLM on first run — everything else is in the web UI.

---

## LLM

Your choice of LLM is the biggest factor in Sapphire's persona. Different models have different personalities, strengths, and quirks. Sapphire supports multiple providers with automatic fallback — if your primary LLM fails, it tries the next enabled provider.

### Local LLM (Private)

Run AI on your own hardware. Nothing leaves your machine.

**LM Studio** is the easiest option:
1. Download [LM Studio](https://lmstudio.ai/)
2. Load a model (Qwen3 8B for tools, QWQ 32B for stories)
3. Enable the API in Developer tab
4. Sapphire connects automatically to `http://127.0.0.1:1234/v1`

Local models vary wildly in personality and capability. Experiment to find what fits your persona.

### Cloud LLM (Not Private)

Use powerful cloud models. Your conversations go to external servers.

| Provider | Best For | Notes |
|----------|----------|-------|
| **Claude** (Anthropic) | Complex reasoning, conversation, coding | Most capable overall |
| **OpenAI** (GPT) | General purpose, well-documented | Widely supported |
| **Fireworks** | Fast inference, open models | Good price/performance |

Set API keys via environment variable or Settings → Credentials.

### Per-Chat Model Selection

Each chat can use a different LLM provider. In Chat Settings, the LLM dropdown offers:
- **Auto** — Uses fallback order (tries each enabled provider)
- **LM Studio** — Forces local only
- **Claude/OpenAI/Fireworks** — Forces specific cloud provider

This lets you make Einstein your coder on Claude, and Sapphire your storyteller on local LM Studio.

---

## Extended Thinking & Reasoning

Some LLM providers can show their reasoning process — thinking through problems step by step before answering.

| Provider | Feature | How to Enable |
|----------|---------|---------------|
| **Claude** | Extended Thinking | LLM Settings → Claude → Extended Thinking toggle |
| **GPT-5.x** | Reasoning Summaries | Uses Responses API, set `reasoning_effort` and `reasoning_summary` |
| **Fireworks** | Reasoning Effort | Use thinking-enabled models (Qwen3-Thinking, Kimi-K2-Thinking) |

**Claude Extended Thinking:** Set a budget (default 10,000 tokens). Thinking blocks are preserved across tool calls. Auto-disables during continue mode and tool cycles without thinking.

**Claude Prompt Caching (90% cost savings):**
- Enable in LLM Settings → Claude → Enable prompt caching
- **Disable Spice** — changes system prompt every turn, breaks cache
- **Disable Datetime injection** — same problem
- **Disable State vars in prompt** — changes on state updates
- "Story in prompt" is fine — only changes on scene advance
- Cache TTL: 5 minutes (default) or 1 hour for longer sessions

---

## Personas

Personas bundle everything about an AI personality — prompt, voice, tools, spice, model, and scopes — into one switchable package. Instead of manually configuring each setting, pick a persona and it all applies at once.

Sapphire ships with 11 built-in personas. See [PERSONAS.md](PERSONAS.md) for the full list and how to create your own.

**Quick switch:** Open the sidebar → persona grid at the top. Click one to activate.

**What a persona controls:** Prompt, toolset, spice set, voice/pitch/speed, LLM provider, mind scopes, story engine settings, trim color, and custom context.

---

## Make Your Persona

Each chat can have completely different personas, voices, and capabilities. Switch between them instantly.

### Make the Settings Yours
- Gear icon → App Settings
- Change names and avatars
- Enable TTS, STT, and Wakeword if desired
- Pick your wake word and raise Recorder Background Percentile if you have webcam mic

### Make the Prompt Yours
- Open the Prompt editor in the sidebar, click **+**
- Choose **Assembled** (more customizable) and name it
- Click **+** next to sections to create new ones:
  - **Persona** - Who the AI is. (You are William AI, a smart coder who...)
  - **Relationship** - Who you are to the AI (I am Jackie, your human boss that...)
  - **Location** - Story location (You are in a forest where...)
  - **Goals** - AI Goals (Your goals are to cheer up your user by...)
  - **Format** - Story Format (3 paragraphs of dialog, narration and inner thoughts...)
  - **Scenario** - World Events (Dinosaurs just invaded the mainland and...)
  - **Extras** - Optional, swap multiple in: sapphire-aware, your hobbies, uncensored
  - **Emotions** - Optional, multiple emotions: happy, curious, loved
- Save with the disk icon

Note: Write prompts in first person. Refer to yourself as "I", refer to your AI as "You".

### Set Up Your Default Chat Settings
- Open the default chat (upper left), click **... → Chat Settings**
- Select your preferred prompt
- Choose which LLM provider to use (Auto, local, or specific cloud)
- Choose which tools the AI can use
- Set TTS voice, pitch, speed (try: Heart, Sky, Isabella)
- **Spice** adds randomness to replies
- **Inject Date** lets the AI know the current date
- **Custom text** is always included in addition to system prompt
- Click **Set as Default** then **Save**

Note: Set as Default is for all future chats. Save is for this chat only. Each chat has its own settings.

---

## Mind Scopes

Scopes isolate data per-chat. Each chat can access its own memory, knowledge, people, goals, and more — or share them across chats.

Set scopes in the **Chat Settings sidebar → Mind Scopes** section.

| Scope | What It Isolates | Shared with Global? |
|-------|-----------------|---------------------|
| **Memory** | Long-term memories | Yes — sees own + global |
| **Goals** | Goal set and progress | Yes |
| **Knowledge** | Knowledge tabs and entries | Yes |
| **People** | Contacts | Yes |
| **Email** | Email account | No |
| **Bitcoin** | Wallet | No |
| **RAG** | Per-chat documents | No (strict per-chat) |

**Global overlay:** Memory, goals, knowledge, and people scopes see both their own data AND entries in the "global" scope. This lets you share common info across all chats while keeping specialized data isolated.

**Set to "none"** to disable a system for a chat entirely (e.g., no memory access for a throwaway chat).

**Create new scopes** with the **+** button next to any scope dropdown.

---

## Per-Chat Documents (RAG)

Attach documents directly to a chat for reference. These are separate from the Knowledge base — strictly scoped to that one conversation.

In **Chat Settings sidebar → Documents**:
- Upload files (auto-chunked and embedded for search)
- Set context level: Off, Light, Normal, Heavy
- View and remove attached documents

Useful for giving the AI reference material for a specific conversation without polluting the global knowledge base.

---

## Privacy Mode

Privacy Mode blocks all outbound cloud connections, keeping conversations local-only.

**What it does:**
- Blocks cloud LLM providers (Claude, OpenAI, Fireworks)
- Allows local providers (LM Studio on localhost)
- Blocks tool calls that require external network access
- Only allows endpoints on the whitelist (localhost, LAN IPs)
- Whitelist supports CIDR ranges (e.g., `192.168.0.0/16`)

**How to enable:**
- Settings → toggle Privacy Mode
- Or set `START_IN_PRIVACY_MODE: true` in `user/settings.json`

**Note:** Model downloads (wakeword, STT) still work on first launch even in privacy mode. Once downloaded, everything runs offline.

---

## Advanced Personalization

### Custom Plugins
Keyword-triggered extensions. Feed [PLUGINS.md](PLUGINS.md) to an AI and drop the output in `user/plugins/`. Can run on keywords, in background, or on schedule.

### Custom Tools
AI-callable functions. Simpler than plugins—they are one file in `user/functions/`. Control your devices, check services, simulate capabilities like email/text. Feed [TOOLS.md](TOOLS.md) to an AI to generate them.

### Custom Wake Word
Drop ONNX models in `user/wakeword/models/`. I trained "Hey Sapphire" in ~2 hours with synthetic data. [Community wakewords](https://github.com/fwartner/home-assistant-wakewords-collection) available.

### Custom Web UI Plugins
Extensible JavaScript plugins for the interface. See [WEB-PLUGINS.md](WEB-PLUGINS.md).

---

## Reference for AI

Help users configure Sapphire settings and personas.

SETTINGS LOCATION:
- Web UI: Gear icon → Settings (app-wide)
- Chat Settings: ... menu → Chat Settings (per-chat)
- Files: user/settings.json (use UI, not direct edit)

LLM CONFIGURATION:
- Local: LM Studio on port 1234 (private)
- Cloud: Claude, OpenAI, Fireworks (not private)
- Per-chat override: Chat Settings → LLM dropdown
- Auto mode uses fallback order through enabled providers

EXTENDED THINKING:
- Claude: Extended Thinking toggle in LLM settings, budget default 10,000 tokens
- GPT-5.x: Responses API with reasoning_effort (low/medium/high) and reasoning_summary
- Fireworks: Thinking-enabled models (Qwen3-Thinking, Kimi-K2-Thinking)
- Thinking blocks preserved across tool calls for Claude

PROMPT CACHING (Claude):
- Enable in LLM settings, saves ~90% cost
- Breaks if spice, datetime injection, or state vars change system prompt each turn
- Cache TTL: 5m (default) or 1h

PER-CHAT SETTINGS:
- Prompt: Which system prompt to use
- LLM: Auto, local, or specific cloud provider
- Toolset: Which tools AI can access
- Voice: TTS voice, pitch, speed
- Spice: Random prompt injection
- Mind Scopes: Memory, knowledge, people, goals, email, bitcoin, RAG isolation

PERSONAS:
- Bundle prompt + voice + tools + spice + model + scopes into one preset
- 11 built-in, fully customizable
- Quick switch via sidebar persona grid
- Set default persona for all new chats

SCOPES (7 types):
- memory, goal, knowledge, people: global overlay (sees own + global)
- email, bitcoin: no overlay
- rag: strict per-chat isolation
- Set per-chat in Chat Settings → Mind Scopes
- Set to "none" to disable a system for that chat

PER-CHAT DOCUMENTS (RAG):
- Upload files directly to a chat
- Auto-chunked and embedded for semantic search
- Context levels: Off, Light, Normal, Heavy
- Strict per-chat scope — not shared across chats

PRIVACY MODE:
- Blocks cloud LLMs (Claude, OpenAI, Fireworks)
- Allows local (LM Studio) and whitelisted endpoints
- Whitelist supports CIDR (e.g., 192.168.0.0/16)
- Toggle: Settings UI or START_IN_PRIVACY_MODE setting

COMMON TASKS:
- Change AI name: Settings → Identity
- Change voice: Chat Settings → Voice dropdown
- Change LLM: Settings → LLM tab, or Chat Settings for per-chat
- Enable wakeword: Settings → Wakeword → enable, restart
- Create persona: Personas view → + New Persona, or capture from chat sidebar
- Set scopes: Chat Settings → Mind Scopes section

FILES:
- user/settings.json - All settings
- user/prompts/ - Prompt definitions
- user/personas/ - Persona definitions and avatars
- user/toolsets/ - Custom toolsets
- ~/.config/sapphire/credentials.json - API keys (not in backups)
