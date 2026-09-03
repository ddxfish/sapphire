# Spice

Spice prevents stories from going stale and helps avoid loops or repetitive formatting. Spices are random prompt snippets delivered to the AI each turn — as a note beside your message, or woven into the system prompt — changing each round (or however often you set). This keeps conversations fresh and unpredictable.

## How It Works

1. Create spices in categories via the **Spices** view (in the Persona group)
2. Group categories into **spice sets** — each chat picks one set, and only that set's categories feed the pool
3. Enable spice for a chat with the **Spice** toggle in the chat sidebar
4. Each message, one random snippet reaches the AI — by default on the **ghost-message rail**, a labeled note inserted just before your input, visible to the AI but not to you
5. Rotates every X messages based on your settings

**Two ways to deliver it.** Settings → LLM → General → **Spice Delivery** picks the rail. It applies immediately, no restart:

- **Ghost message** (default) — the snippet rides a labeled per-turn note (`Spice: ...`) just before your input. The system prompt never changes, so prompt caching on cloud models stays intact, and the line lands right before generation where models weight it most (recency effect). Trade-off: the note is labeled as coming from Sapphire's own app, so the AI knows it was injected and may say so out loud.
- **System prompt** — the snippet is appended to the system prompt unattributed, so the AI wears it as its own inclination instead of an instruction handed to it. Feels the most natural. Trade-off: the prompt changes on every rotation, which re-tokenizes the cached prefix on cache-billed cloud models (free on local models).

Rule of thumb: cloud model with long chats → ghost message. Local model, or you want the spice to feel self-chosen → system prompt. Rotation, categories, and the per-chat toggle work the same either way.

<img width="50%" alt="sapphire-spices" src="https://github.com/user-attachments/assets/f5563bed-7c5d-490a-9d18-c7f87339d9ef" />


## Spice Sets

A **spice set** is a named selection of categories — the Spices view is built around them. The left rail lists your sets; the right panel shows every category in the pool with a checkbox for whether it's **in the selected set**.

- ✅ Checked categories are part of this set and contribute to its spice pool
- ⬜ Unchecked categories are excluded from this set (they still exist in the pool for other sets)

Checkbox changes save to the selected set automatically. Sapphire seeds starter sets — **default** 🌶️, **companion** 💜, and **professional** 💼 — and they're yours to edit or delete.

**Working with sets:**

- **Create** — the **+** button saves the current checkbox selection as a new set
- **Delete** — removes the set (the categories and their spices stay in the pool)
- **Emoji** — click the set's emoji (or *add emoji*) to pick a badge; it shows in dropdowns everywhere
- **Export / Import** — sets travel as JSON files that carry their categories *with* the spices inside, so a shared set works on another install
- **Activate** — stamps the set onto your current chat

**Each chat picks its own set.** The chat sidebar has a **spice set** dropdown — switching chats applies that chat's set, so a storytelling chat and a work chat can run completely different pools. Personas can carry a spice set too, so activating a persona brings its flavor along.

## Quick Toggle

The **Spice** pill in the chat sidebar toggles spice on/off for the current chat only. Its label shows the rotation interval (e.g. `Spice · 3`), which you can change under the sidebar's System Prompt section.

## Category Management

Categories and their spices are managed inline in the Spices view: **+ Category** creates one, and each category expands to add, edit, or delete individual spices. Categories can have their own emoji, and **Reload Pool** re-reads the spice pool from disk if you've edited the file by hand.

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

Spice injects random prompt snippets to prevent repetitive outputs. Spice SETS group categories; each chat picks a set.

SETUP:
1. Open the Spices view (Persona nav group)
2. Add snippets to categories (+ Category, then + Spice inside it)
3. Check categories into the selected spice set (checkbox = membership in that set, saves automatically)
4. Pick the set per chat via the sidebar "spice set" dropdown (personas can carry a spice_set)
5. Enable spice per chat with the sidebar Spice toggle; set rotation via spice turns

SPICE SETS:
- Set = named list of categories + optional emoji; built-in starters: default, companion, professional
- Sets rail: + saves current checkboxes as a new set; delete removes the set only (pool keeps categories); export/import JSON carries categories with their spices
- Activate stamps the set onto the current chat; switching chats applies that chat's set — the enabled pool follows the active chat
- No global category toggle exists anymore — membership is always per-set

QUICK ACCESS:
- Sidebar "Spice · N" pill: click toggles spice for this chat (N = rotation turns)
- Sidebar "spice set" dropdown: which set this chat uses (↗ opens the Spices view)

HOW IT WORKS:
- One random snippet per interval; only the active chat's set categories contribute to the pool
- Delivery picked by SPICE_DELIVERY (Settings > LLM > General, hot-reload, default 'ghost'):
  - 'ghost' — rides the ghost-message rail as a labeled line (since 2.6.4); system prompt stays cached
  - 'system' — appended to the system prompt unattributed; prompt changes each rotation, so cache-billed cloud models re-tokenize (free on local models)
- Spice pool stored in user/prompts/prompt_spices.json; sets in user/spice_sets/spice_sets.json (seeded from core defaults on first run, user file authoritative after)

GOOD SPICES:
- "Something unexpected happens" (vague, fits any scene)
- "Use 3 paragraphs" (format control)
- "An old memory surfaces" (story catalyst)

BAD SPICES:
- "The dragon attacks" (too specific)
- Long paragraphs (bloats prompt)
