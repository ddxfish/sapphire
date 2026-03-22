# Costs & Token Management

Cloud LLMs charge per token. Sapphire can burn through tokens fast — tool calling loops, long system prompts, extended thinking, and frequent chats all add up. This guide explains how caching works, what breaks it, and how to reduce costs across all providers.

## How Prompt Caching Works

Every message you send to an LLM includes the **system prompt** (your persona, instructions, spice, etc.) plus the **conversation history**. The system prompt is nearly identical every turn — it only changes when you modify it.

**Prompt caching** tells the provider: "I already sent you this text — don't charge me full price for it again." The provider stores a hash of the cached content, and if your next request starts with the same text, you pay a fraction of the cost.

**The catch:** If *anything* in the cached portion changes — even one character — the cache misses and you pay full price. This is why features that modify the system prompt every turn are cache killers.

## What Breaks Cache

These features change the system prompt on every message, causing cache misses:

| Feature | Why it breaks cache | Setting to disable |
|---------|--------------------|--------------------|
| **Spice** | Injects a random snippet each turn | Chat Settings → Spice off |
| **Datetime injection** | Adds current time to prompt each turn | Chat Settings → Inject Date off |
| **State vars in prompt** | Story engine state changes on actions | Story settings → State in prompt off |

**These are safe** (don't break cache):
- Changing conversation history (that's after the cached portion)
- Story-in-prompt (only changes on scene advance, not every turn)
- Switching tools mid-chat (tool definitions are separate from system prompt on most providers)

---

## Cost Savings — All Providers

These apply regardless of which LLM you're using:

### 1. Disable Spice for Utility Chats

Spice is great for stories but murders cache. For daily-driver chats where you just want answers, turn spice off.

Chat Settings → Spice → Off

### 2. Disable Datetime Injection

Unless the AI needs to know the current time, turn this off. It changes the system prompt every minute.

Chat Settings → Inject Date → Off

### 3. Use Smaller Toolsets

Every tool definition gets sent in the request. A toolset with 40 tools costs more tokens than one with 10. Build focused toolsets — "research" with web tools, "home" with HA tools — instead of one mega-set.

### 4. Shorter Prompts

Your assembled prompt gets sent every single turn. A 500-word prompt costs 5x more over a conversation than a 100-word one. Be concise — the AI doesn't need a novel to know who it is.

### 5. Use Local Models for Casual Chat

LM Studio costs nothing. Use local models for everyday conversation and save cloud for complex tasks. Set up per-chat LLM overrides so your story persona uses local while your coding persona uses Claude.

### 6. Limit Extended Thinking

Thinking tokens add up fast. Default budget is 10,000 tokens — that's 10K extra tokens per response. Only enable thinking for tasks that benefit from step-by-step reasoning (coding, analysis), not casual chat.

### 7. Use Scopes Wisely

Large memory/knowledge stores get embedded in context. If a chat doesn't need access to your 5,000-entry knowledge base, set the scope to "none".

---

## Claude (Anthropic)

### Prompt Caching

Claude's prompt caching can save **up to 90%** on input costs. This is the single biggest cost saver.

**Enable:** Settings → LLM → Claude → Enable Prompt Caching

**Cache TTL options:**

| TTL | Best for | Setting |
|-----|----------|---------|
| **5 minutes** (default) | Quick Q&A, short sessions | `5m` |
| **1 hour** | Long conversations, story sessions | `1h` |

The 1-hour TTL keeps the cache alive between messages even if you take breaks. Use it for sessions where you're actively chatting.

**Cache hit vs miss:**
- **Cache write** (first message): Costs 25% more than normal (one-time investment)
- **Cache hit** (subsequent): Costs 90% less than normal
- **Cache miss** (prompt changed): Full price — you lost the cache

The Dashboard (Settings → Dashboard) shows cache hit/miss rates so you can see if your caching is working.

### Claude-Specific Tips

- **Disable spice + datetime** = consistent system prompt = cache hits every turn
- **Use assembled prompts** — they're more stable than monolith prompts the AI rewrites constantly
- **Avoid frequent meta-tool edits** — every time the AI edits its own prompt, the cache breaks
- **1-hour TTL for stories** — you might pause between scenes, 5m TTL would expire

---

## OpenAI (GPT)

OpenAI automatically caches prompts longer than 1024 tokens. You don't need to enable anything — it just works.

**OpenAI caching gives 50% discount** on cached input tokens (less than Claude's 90%, but automatic).

**Tips:**
- Same rules apply — stable system prompt = more cache hits
- Longer prompts benefit more (must exceed 1024 token threshold to cache)
- No TTL control — OpenAI manages cache lifetime internally

---

## Fireworks

Fireworks uses **session affinity** for caching — requests with the same session ID route to the same replica, which keeps the model's KV cache warm.

Sapphire handles this automatically (sends a stable session ID per chat). No configuration needed.

**Tips:**
- Fireworks caching is replica-level, so it's less predictable than Claude/OpenAI
- Still benefits from stable system prompts
- Open models on Fireworks are generally cheaper per token than Claude/OpenAI

---

## Monitoring Usage

Sapphire tracks all token usage locally.

**Dashboard:** Settings → Dashboard → Token Metrics
- Total calls, prompt/completion/thinking tokens over 30 days
- Per-model breakdown with cache hit percentages
- Daily usage trend charts

**Enable metrics:** Toggle in the Dashboard. All data stays local in `user/metrics/token_usage.db`.

Watch the cache hit percentage — if it's low, something is changing your system prompt every turn. Check spice and datetime injection first.

---

## Quick Reference: Cost Optimization Checklist

| Action | Savings | Effort |
|--------|---------|--------|
| Disable spice on utility chats | High (enables caching) | 1 click |
| Disable datetime injection | High (enables caching) | 1 click |
| Enable Claude prompt caching | Up to 90% on input | 1 click |
| Use local LLM for casual chat | 100% (free) | Per-chat LLM setting |
| Smaller toolsets | Moderate | Build focused sets |
| Shorter prompts | Moderate | Edit prompt |
| Limit thinking budget | High per-message | Settings toggle |
| 1h cache TTL for long sessions | Moderate | Settings dropdown |

---

## Reference for AI

Help users understand and reduce LLM token costs.

WHAT BREAKS CACHE:
- Spice: random snippet each turn → disable in Chat Settings
- Datetime injection: timestamp each turn → disable in Chat Settings
- State vars in prompt: story state changes → disable in story settings
- Meta-tool prompt edits: AI editing its own prompt

COST SAVINGS (ALL PROVIDERS):
- Disable spice + datetime for utility chats
- Smaller toolsets (fewer tool definitions = fewer tokens)
- Shorter prompts (sent every turn)
- Local LLM for casual chat (free)
- Limit extended thinking budget
- Scope to "none" when not needed

CLAUDE CACHING:
- Enable: Settings → LLM → Claude → Prompt Caching
- TTL: 5m (default, quick chats) or 1h (long sessions)
- Cache write: +25% first message
- Cache hit: -90% subsequent messages
- Cache miss: full price (prompt changed)

OPENAI CACHING:
- Automatic for prompts >1024 tokens
- 50% discount on cached input
- No configuration needed

FIREWORKS CACHING:
- Session affinity (automatic, per-chat session ID)
- Replica-level KV cache, less predictable
- Generally cheaper per-token than Claude/OpenAI

MONITORING:
- Settings → Dashboard → Token Metrics
- Shows cache hit/miss rates, per-model breakdown
- Data in user/metrics/token_usage.db (local only)
