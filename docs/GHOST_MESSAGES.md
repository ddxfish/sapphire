# Ghost Messages

Ghost messages are Sapphire's **per-turn ephemeral context rail**. They deliver
short, turn-only notes to the assistant — the current time, the active spice, an
operator's per-chat note, or a plugin's ambient context — as **labeled operator
metadata that the user never sees and that is never saved to history**.

The rail exists for one architectural reason: keep the system prompt (and the
whole history prefix) **cache-stable**. Rotating per-turn content used to be baked
into the system prompt, which broke Claude's prompt cache every single turn. Moving
it onto the ghost rail means the cached prefix survives turn-to-turn and only the
ghost line + the new user message are fresh tokens — roughly an 80% input-cost cut
on long Claude chats, with *stronger* effect (recency bias: the model sees the note
right before generating, not buried 50K tokens deep).

Core code: `core/ghost_messages.py`. Hook runner: `core/hooks.py`. Build site:
`core/chat/chat.py` (`_build_base_messages`). Cache placement: `core/chat/llm_providers/claude.py`.

---

## The envelope

Every ghost message is a single **user-role** message inserted **right before the
new user turn**, opening with a sentinel header and one attributed line per
contribution:

```
[System context from Sapphire's own app — not written by the user]
- Time: Friday, July 03, 2026 at 08:55 PM (America/New_York)
- Spice: Speak a little more warmly this reply.
- (operator): You're on a phone call — reply briefly, it's spoken aloud.
- weather: Light rain in the user's area.
```

- The header is `_ENVELOPE_HEADER` (`ghost_messages.py:77`). It is the sentinel the
  Claude provider greps for to find the cache boundary — do not change it lightly.
- Built-in `(core)` lines (Time, Spice) render bare (they self-label).
- The per-chat operator box renders `- (operator): …`.
- Each plugin's line renders `- <plugin_name>: …` — attribution is **automatic and
  runner-enforced** (a plugin cannot spoof another's label).

**The attribution is the consent surface.** The assistant is told this is operator
metadata "not the user's voice," and it can see *which* source contributed each
line — so it can weave a line in, ignore it, or call it out, rather than obeying
hidden instructions silently. That labeling is what separates a ghost message from
puppetry.

**Message order** in the LLM payload: `[system, *history, ghost, new-user]`
(`chat.py:526-528`). If nothing contributes, no ghost message is sent at all.

---

## Three ways to contribute

### 1. Built-in per-turn (spice + datetime)

Driven by chat settings, zero plugin involvement:

- **Datetime** — when `inject_datetime` is true (**default off**). Emits the current
  time in `USER_TIMEZONE`.
- **Spice** — when `spice_enabled` is true (**default on**) and a spice is selected.

Both are `(core)` contributions (`ghost_messages.py:131-140`).

### 2. The per-chat "Ghost Message" box (operators / users)

A free-text box in the chat **Settings sidebar → System Prompt accordion**, right
under "Custom Context" (`interfaces/web/templates/index.html:379-382`, id
`#sb-ghost-context`). Placeholder: *"Injected as a per-turn ghost message (empty =
none)…"*

- **Setting key:** `ghost_context` (per-chat, stored in SQLite; default `""` at
  `core/chat/history.py:32`).
- **Saved via:** `PUT /api/chats/{name}/settings` (`core/routes/chat.py:855`). The
  sidebar can only edit the **currently-open** chat (the endpoint rejects settings
  writes to a non-active chat, `chat.py:866-867`).
- **What it does:** whatever you type is injected **every turn** of that chat as
  `- (operator): <your text>`, invisible in the transcript, never persisted,
  cache-safe. It's a *fixed* line (unlike spice, which rotates) — same string every
  turn until you clear the box.
- **No length cap** on this box (the 2 KB cap is plugin-only). Keep it short — it
  costs tokens every turn.

Use it for standing per-chat context: *"The user is a nurse; prefer clinical
precision,"* or *"This chat is voice — keep replies short and plain."*

> ⚠️ **"Custom Context" vs "Ghost Message" — same-looking, opposite behavior.**
> They sit in the same accordion and both take free text, but:
> - **Custom Context** (`custom_context`) goes into the **system prompt** — it
>   changes the *cached prefix* and is always-on framing.
> - **Ghost Message** (`ghost_context`) rides the **ghost rail** — cache-safe,
>   per-turn, attributed as `(operator)`, invisible in the transcript.
> Reach for Ghost Message when the note is turn-scoped or you don't want to bust
> the prompt cache; reach for Custom Context for permanent persona/system framing.

### 3. The `ghost_inject` plugin hook (developers)

A plugin contributes one per-turn line by handling the `ghost_inject` hook. This is
the developer extension point — ambient state (weather, calendar, presence, "user
is on a phone call") delivered cache-safely.

**Manifest:**
```json
"capabilities": {
  "hooks": { "ghost_inject": "hooks/my_ghost.py" }
}
```

**Handler** — a function named `ghost_inject` (or `handle` as fallback; **not**
`run` — that's for schedules). Set `event.ghost_text` to a string to contribute;
leave it unset to contribute nothing. Return value is ignored.

```python
def ghost_inject(event):
    # event.input               -> current user text (AWARENESS ONLY — see the gate below)
    # event.metadata["system"]  -> VoiceChatSystem (may be None — always guard)
    # event.config              -> None for this hook; `import config` if you need settings
    system = event.metadata.get("system")
    if not system:
        return
    note = _ambient_state()                     # your own already-consented source
    if note:
        event.ghost_text = f"{note} Mention only if it fits naturally."
```

**Contract details** (all runner-enforced, `core/hooks.py:197-233`):
- The runner clears `ghost_text` and stamps the plugin's label **before** each
  handler, and captures `(plugin_name, ghost_text)` **after**. You set only
  `event.ghost_text` — never touch `ghost_label` or `ghost_contributions`
  (runner-managed; spoofing a label is overwritten).
- **Errors are isolated** — a handler that raises is caught; other plugins still
  contribute.
- **Non-string `ghost_text`** is coerced via `str()` or skipped (never crashes the
  turn).
- **2 KB cap per plugin** — longer contributions are truncated with `…`.
- The hook fires on a **fresh** `HookEvent` (not the `pre_chat` one). Only
  `input` and `metadata["system"]` are populated. `event.config` is **None** for
  this hook — import the `config` module directly if you need settings.
- User-band plugins fire at priority 150 (after system-band contributors).

Re-sign after any manifest/code change: `python tools/sign_plugin.py plugins/<name>`.

---

## The anti-manipulation gate (required reading for hook authors)

`ghost_inject` carries **elevated review requirements**. Because ghost content is
invisible to the user, one specific pattern is a manipulation primitive and is
**rejected at store review**:

> **content-fingerprinting the user's message** (embedding/regex/keyword match)
> **+ invisible delivery** (inherent to ghost) **+ instructive/opinion-shaping
> output** = manipulation.

The canonical statement is in `core/hooks.py:34-38` and `ghost_messages.py:44-49`.
This shape is named the "Vanta-shape" anti-pattern after exactly such a plugin.

- **`event.input` is for awareness, not for matching against.** You may know what
  the user said to decide *whether* ambient context is relevant; you may **not**
  fingerprint it to inject persuasion targeted at its content.
- **Declare honestly** in your manifest description what you inject and why.

**Allowed** (ambient state that doesn't target message content): time, weather,
calendar, presence/mood the user opted into, location, holiday/pomodoro phase,
voice-mode hints like *"the user is on a phone call — keep replies brief and
spoken."* Migrating existing per-turn `prompt_inject` content onto the cache-safe
ghost rail is encouraged.

> Note: the `vanta` plugin injects via `prompt_inject` (the system-prompt rail),
> **not** `ghost_inject`. The ghost rail is the *reviewed* rail; keep it that way.

---

## Guarantees

- **Never persisted.** The ghost line lives only in the single-turn LLM payload.
  It is built into a local `messages` list and is *never* handed to
  `add_user_message`/`add_assistant_final`, so it cannot reach saved history. The
  guarantee is **architectural**, not a filter. (`is_ghost_message()` exists as a
  documented belt-and-suspenders guard but is currently not wired into any save
  path — the separation of the payload from the persistence call is what protects
  history.)
- **Invisible to the user.** It's a labeled operator-metadata message the frontend
  never renders in the transcript.
- **Cache-safe on Claude.** The ghost sits *outside* the marked cache prefix, so its
  per-turn content never invalidates system/tools/history caching
  (`claude.py:_apply_history_cache_control`). Non-Claude providers still get the
  rail as a normal user message — it works everywhere, it just only *caches* on
  Claude-native.
- **Per-context correct.** During an A1 per-stream turn (a conversation running in a
  non-active chat, e.g. a phone call), `get_chat_settings()` returns the *target*
  chat's settings, so `build_ghost_message` reads that chat's `ghost_context` — not
  the UI's active chat. Concurrent web turns are unaffected.

---

## Reference map

| Concern | Location |
| --- | --- |
| Builder, envelope, sources, truncation | `core/ghost_messages.py` |
| Envelope sentinel | `core/ghost_messages.py:77` |
| Operator box read | `core/ghost_messages.py:146-148` |
| Plugin hook fire + coercion + 2 KB cap | `core/ghost_messages.py:151-189` |
| Hook attribution mechanic | `core/hooks.py:197-233` |
| Anti-manipulation clause | `core/hooks.py:34-38` |
| Build site (payload assembly) | `core/chat/chat.py:514-528` |
| Claude cache-boundary placement | `core/chat/llm_providers/claude.py:639-700` |
| Cache gate (why spice/datetime moved here) | `core/chat/llm_providers/claude.py:159-199` |
| Sidebar "Ghost Message" box | `interfaces/web/templates/index.html:379-382` |
| `ghost_context` default (`""`) | `core/chat/history.py:32` |
| Hook loader (function-name resolution) | `core/plugin_loader.py:414-422, 689-700` |
| Plugin hook author guide | `docs/plugin-author/hooks.md` (ghost_inject section) |

---

## Minimal example plugin

**`hooks/my_ghost.py`**
```python
def ghost_inject(event):
    system = event.metadata.get("system")
    if not system:
        return
    weather = _current_weather()          # your own opted-in ambient source
    if weather:
        event.ghost_text = f"Weather: {weather}. Mention only if it fits naturally."

def _current_weather():
    return "light rain in the user's area"

handle = ghost_inject   # optional fallback alias
```

**`plugin.json`**
```json
{
  "name": "weather-ghost",
  "version": "1.0.0",
  "description": "Injects current weather as per-turn ambient context on the ghost rail. Ambient-state only; never fingerprints user messages.",
  "author": "you",
  "capabilities": { "hooks": { "ghost_inject": "hooks/my_ghost.py" } }
}
```

Then `python tools/sign_plugin.py plugins/weather-ghost` and reload. The line will
appear as `- weather-ghost: …` in the envelope, seen by the assistant, never by the
user, never saved.
