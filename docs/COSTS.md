# Costs & Token Management

Cloud LLMs charge per token. Sapphire can burn through tokens fast — tool calling loops, long histories, extended thinking, and frequent chats all add up. This guide explains how caching works, what affects it, and how to reduce costs across all providers.

## How Prompt Caching Works

Every message you send to an LLM includes the **system prompt** (your persona, instructions), the **tool definitions**, the **conversation history**, and your **new input**. With caching, the provider remembers the prefix from a prior request and only charges full price for what's new.

**Sapphire automatically caches the heavy stuff:**
- System prompt
- Tool definitions
- Full conversation history (since 2.6.4)

**Each new turn, the only fresh tokens are:**
- A small per-turn ghost message (spice, current time, etc.)
- Your new user message

This is mostly automatic — you don't need to fiddle with settings to get the cache to work. Long active conversations on Claude can save **70-80% on input costs** with caching enabled (default).

## How Per-Turn Variations Stay Cache-Friendly

A few things change every turn (spice rotation, current time, plugin context). Pre-2.6.4, these lived inside the system prompt — and changing the system prompt every turn defeated caching entirely.

In 2.6.4 these moved to the **ghost-message rail**: a separate per-turn message inserted *after* the cached prefix, just before your new input. Spice and datetime no longer break cache. They also hit the model with stronger compliance because they're closer to the moment of generation (recency effect).

**The only thing that still breaks system-prompt caching:** plugins that register a `prompt_inject` hook (e.g. RAG/context injectors, Vanta-class plugins). When such a plugin is active, system-prompt caching auto-disables on Claude to avoid the 1.25× cache-write penalty for guaranteed misses. Tools and history still cache.

If you don't have any `prompt_inject` plugins active and you're not sure why your hit rate is low, check Settings → Dashboard → Token Metrics for per-model breakdown.

---

## Cost Savings — All Providers

These apply regardless of which LLM you're using:

### 1. Use Smaller Toolsets

Every tool definition gets sent in the request. With caching, tool definitions are cached too — but only the first turn pays the write cost. The bigger your toolset, the bigger the one-time write tax. A toolset with 158 tools racks up tens of thousands of tokens just for schemas. Build focused toolsets — "research" with web tools, "home" with HA tools — instead of one mega-set.

### 2. Shorter Prompts

Your assembled prompt gets sent every turn (cached after the first), but the first turn always pays full + 25% write tax. A 5,000-token prompt costs ~$0.019 to write to cache vs $0.0004 for a 500-token prompt. Be concise — the AI doesn't need a novel to know who it is.

### 3. Use Local Models for Casual Chat

LM Studio costs nothing. Use local models for everyday conversation and save cloud for complex tasks. Set up per-chat LLM overrides so your story persona uses local while your coding persona uses Claude.

### 4. Limit Extended Thinking

Thinking tokens add up fast: they're extra **output** tokens on every response (not cached). Claude models think adaptively, steered by the provider card's effort setting (default `high`); lower effort, or thinking off, costs less. Haiku-class models use a fixed budget per effort level instead. With thinking on, the reply's Max Tokens is raised to at least 16,000. Only enable thinking for tasks that benefit from step-by-step reasoning (coding, analysis), not casual chat.

### 4b. Keep the context lean

Every turn resends the whole request, so its size is the bill. Old tool results decay into short stubs, pictures drop out after a few turns, and the oldest history goes out of view when the window fills. `/context` shows what's costing the most. See [CONTEXT.md](CONTEXT.md), and `/compact` or `/compact-daily` ([SLASH-COMMANDS.md](SLASH-COMMANDS.md)) to summarize a long chat.

### 5. Use Scopes Wisely

Large memory/knowledge stores get embedded in tool results when AI searches them. If a chat doesn't need access to your 5,000-entry knowledge base, set the scope to "none".

### 6. Stay in the Cache TTL Window

Cache survives 5 minutes by default (1 hour optionally). Coming back to a chat after a 6-minute break = next turn pays cache-write penalty for the entire prefix. For long active sessions or chats you return to throughout the day, switch the TTL to 1h on Claude.

---

## Claude (Anthropic)

### Prompt Caching

Claude's prompt caching saves **up to 90%** on cached input — the single biggest cost saver. Sapphire enables this by default.

**Setting:** Settings → LLM → Claude → Enable Prompt Caching (on by default)

**Cache TTL options:**

| TTL | Best for | Setting |
|-----|----------|---------|
| **5 minutes** (default) | Quick Q&A, continuous active sessions | `5m` |
| **1 hour** | Long conversations with idle gaps, story sessions | `1h` |

The 1-hour TTL keeps the cache alive between messages even if you walk away briefly. Use it for sessions where you're chatting in bursts.

**Cache hit vs miss vs write:**
- **Cache write** (cache fill on miss): 1.25× normal price (one-time investment)
- **Cache hit** (subsequent same prefix): 0.10× normal price (90% off)
- **Cache miss** (prefix changed or TTL expired): full price + 1.25× write tax to refill

The Dashboard (Settings → Dashboard) shows cache hit/miss rates per model.

### Claude-Specific Tips

- **Caching is on by default and covers system + tools + history** — you usually don't need to do anything.
- **Avoid `prompt_inject` plugins** if you want maximum caching. Those mutate the system prompt per turn and disqualify it from caching. Most plugins use the safer `ghost_inject` hook (no caching impact).
- **Avoid AI self-prompt-editing** — meta-tools that rewrite the system prompt break the cache for the next request.
- **1-hour TTL for sessions with breaks** — you might pause between turns, 5m would expire.

---

## OpenAI (GPT)

OpenAI automatically caches prompts longer than 1024 tokens. You don't need to enable anything — it just works.

**OpenAI caching gives 50% discount** on cached input tokens (less than Claude's 90%, but automatic).

**Tips:**
- Same rules apply — stable system prompt = more cache hits
- Longer prompts benefit more (must exceed 1024 token threshold to cache)
- No TTL control — OpenAI manages cache lifetime internally

---

## Gemini (Google)

Gemini uses automatic **context caching** for prompts. Google manages caching internally — no configuration needed from Sapphire's side.

**Tips:**
- Same cache-friendly rules apply — stable system prompts help
- Gemini models are generally cost-competitive, especially Gemini Flash
- Thinking-enabled models (Gemini 2.5 Flash) use `reasoning_effort` to control thinking cost

---

## Monitoring Usage

Sapphire tracks all token usage locally.

**Dashboard:** Settings → Dashboard → Token Metrics
- Total calls, prompt/completion/thinking tokens over 30 days
- Per-model breakdown with cache hit percentages
- Daily usage trend charts

**Enable metrics:** Toggle in the Dashboard, or Settings → Privacy (`METRICS_ENABLED`). All data stays local in `user/metrics/token_usage.db`.

Watch the cache hit percentage — if it's low, something is changing your system prompt every turn. Check spice and datetime injection first.

---

## Quick Reference: Cost Optimization Checklist

| Action | Savings | Effort |
|--------|---------|--------|
| Enable Claude prompt caching | Up to 90% on cached input (default ON) | 1 click |
| 1h cache TTL for chats with idle gaps | Saves cache-write tax on re-engagement | Settings dropdown |
| Use local LLM for casual chat | 100% (free) | Per-chat LLM setting |
| Smaller toolsets | Moderate (smaller cache writes) | Build focused sets |
| Shorter prompts | Moderate (smaller cache writes) | Edit prompt |
| Limit thinking budget | High per-message (output, not cached) | Settings toggle |
| Avoid `prompt_inject` plugins on Claude | Reactivates system-prompt caching | Disable plugin |

---

## Reference for AI

Help users understand and reduce LLM token costs.

CACHE FRIENDLY (don't break cache):
- Spice rotation (lives on ghost rail, post-cache)
- Datetime injection (same)
- Most plugins (use ghost_inject hook)
- AI tool calls (results don't mutate prefix)
- New user messages (always fresh by design)

CACHE BREAKERS (still real):
- prompt_inject plugin hooks: mutate system prompt → auto-disables system-prompt caching on Claude
- AI self-prompt-editing via meta-tools: rewrites the system prompt, invalidates cache
- TTL expiry (5m default, 1h paid option): forces cache rewrite

COST SAVINGS (ALL PROVIDERS):
- Smaller toolsets (cheaper cache writes)
- Shorter system prompts (cheaper cache writes)
- Lean context: tool decay (TOOL_EXPIRES_AFTER), image memory (IMAGE_MEMORY_TURNS), /context to see the biggest buckets, /compact or /compact-daily (CONTEXT.md, SLASH-COMMANDS.md)
- Claude thinking is adaptive by effort (default high; lower effort = cheaper); Haiku-class uses a budget per effort; thinking floors max_tokens at 16000
- Local LLM for casual chat (free)
- Limit extended thinking budget (output tokens, not cached)
- Scope to "none" when not needed

CLAUDE CACHING (default ON, system + tools + history all cached since 2.6.4):
- Setting: Settings → LLM → Claude → Prompt Caching
- TTL: 5m (default) or 1h (long sessions / idle gaps)
- Cache write: +25% first request (one-time)
- Cache hit: -90% subsequent requests
- Cache miss: full price + write tax to refill

OPENAI CACHING:
- Automatic for prompts >1024 tokens
- 50% discount on cached input
- No configuration needed

GEMINI CACHING:
- Automatic context caching (no configuration)
- Cost-competitive, especially Gemini Flash
- Thinking via reasoning_effort param

MONITORING:
- Settings → Dashboard → Token Metrics
- Shows cache hit/miss rates, per-model breakdown
- Data in user/metrics/token_usage.db (local only)
