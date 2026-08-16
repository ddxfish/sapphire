# Spice

Spice prevents stories from going stale and helps avoid loops or repetitive formatting. Spices are random prompt snippets delivered to the AI each turn — as a note beside your message, or woven into the system prompt — changing each round (or however often you set). This keeps conversations fresh and unpredictable.

## How It Works

1. Create spices in categories via the Spice Manager
2. Enable/disable categories with checkboxes (applies globally)
3. Enable spice for a chat in Chat Settings
4. Each message, one random snippet reaches the AI — by default on the **ghost-message rail**, a labeled note inserted just before your input, visible to the AI but not to you
5. Rotates every X messages based on your settings

**Two ways to deliver it.** Settings → LLM → General → **Spice Delivery** picks the rail. It applies immediately, no restart:

- **Ghost message** (default) — the snippet rides a labeled per-turn note (`Spice: ...`) just before your input. The system prompt never changes, so prompt caching on cloud models stays intact, and the line lands right before generation where models weight it most (recency effect). Trade-off: the note is labeled as coming from Sapphire's own app, so the AI knows it was injected and may say so out loud.
- **System prompt** — the snippet is appended to the system prompt unattributed, so the AI wears it as its own inclination instead of an instruction handed to it. Feels the most natural. Trade-off: the prompt changes on every rotation, which re-tokenizes the cached prefix on cache-billed cloud models (free on local models).

Rule of thumb: cloud model with long chats → ghost message. Local model, or you want the spice to feel self-chosen → system prompt. Rotation, categories, and the per-chat toggle work the same either way.

<img width="50%" alt="sapphire-spices" src="https://github.com/user-attachments/assets/f5563bed-7c5d-490a-9d18-c7f87339d9ef" />


## Quick Toggle

The Spice dropdown in the Chat Settings gives quick access to spice:

- **Hover** — Shows the current spice for last message
- **Click** — Toggle spice on/off for this chat only

## Category Control

Use the checkboxes next to each category to enable or disable entire categories globally. This affects all chats that have spice enabled.

- ✅ Checked categories contribute to the spice pool
- ⬜ Unchecked categories are excluded

## Example Spices

```json
{
  "storytelling": [
    "Something unexpected is about to happen.",
    "Reference a new character.",
    "The weather shifts dramatically.",
    "An old memory surfaces.",
    "Someone is not who they seem."
  ],
  "formatting": [
    "Use 2 paragraphs for this reply.",
    "Use 4 paragraphs for this reply.",
    "Include inner thoughts."
  ]
}
```

## Tips

- Keep snippets vague enough to fit any scene
- Short phrases work better than long sentences
- Use categories to organize by purpose (storytelling, formatting, tone)

## Reference for AI

Spice injects random prompt snippets to prevent repetitive outputs.

SETUP:
1. Open Spice Manager (sidebar)
2. Add snippets to categories
3. Enable/disable categories with checkboxes (global)
4. Enable spice in Chat Settings (per-chat)
5. Set rotation interval

QUICK ACCESS:
- Spice dropdown input area
- Hover: shows current spice
- Click: toggle spice for this chat

HOW IT WORKS:
- One random snippet per interval; only enabled categories contribute to pool
- Delivery picked by SPICE_DELIVERY (Settings > LLM > General, hot-reload, default 'ghost'):
  - 'ghost' — rides the ghost-message rail as a labeled line (since 2.6.4); system prompt stays cached
  - 'system' — appended to the system prompt unattributed; prompt changes each rotation, so cache-billed cloud models re-tokenize (free on local models)
- Stored in user/prompts/prompt_spices.json

GOOD SPICES:
- "Something unexpected happens" (vague, fits any scene)
- "Use 3 paragraphs" (format control)
- "An old memory surfaces" (story catalyst)

BAD SPICES:
- "The dragon attacks" (too specific)
- Long paragraphs (bloats prompt)
