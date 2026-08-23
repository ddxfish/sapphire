# Hooks & Voice Commands

## Hook Points

All hooks receive a mutable `HookEvent`. Changes persist across handlers in priority order.

| Hook | When | Key Fields | Use |
|------|------|-----------|-----|
| `post_stt` | After voice transcription | `input` (mutable) | Correct STT errors, translate, normalize |
| `pre_chat` | Before LLM | `input`, `skip_llm`, `response` | Filter input, bypass LLM |
| `prompt_inject` | System prompt build | `context_parts` | Append context strings (system prompt — long-lived, breaks cache) |
| `ghost_inject` | Per-turn ephemeral context | `ghost_text` | Add operator-metadata visible to assistant only, cache-friendly. **See dedicated section below — this hook has elevated review requirements.** |
| `post_llm` | After LLM, before save | `response` (mutable) | Translate, filter, style transfer |
| `post_chat` | After response saved | `input`, `response` | Logging, analytics |
| `pre_execute` | Before tool call | `function_name`, `arguments` | Modify args, block tools |
| `post_execute` | After tool call | `function_name`, `result` | Audit, react to results |
| `pre_tts` | Before speech (legacy/whole-blob) | `tts_text`, `skip_tts` | Modify text, cancel TTS |
| `post_tts` | After playback ends (legacy/whole-blob) | `tts_text` | Analytics, reactions, subtitles |
| `tts_stream_start` | Streaming TTS: turn begins | `skip_tts` | Disable the whole turn's streaming speech |
| `tts_chunk_text` | Streaming TTS: per chunk, before synth | `tts_text`, `skip_tts` | Rewrite or mute a chunk before it's voiced |
| `tts_chunk_audio` | Streaming TTS: per chunk, after synth | `metadata['audio']` carrier | Transform/replace chunk audio bytes |
| `tts_stream_end` | Streaming TTS: turn ends | — | Finalize captions/recording |
| `on_wake` | Wakeword detected | — | Play sounds, log, custom reactions |
| `provider_switched` | After a TTS/STT/embed provider hot-swap | metadata `kind`, `provider` | Warm caches / reset state (observational) |
| `chat_renamed` | After a chat rename succeeds | metadata `old`, `new` | Re-key chat-keyed data your plugin holds *outside* the chat-scoped store |
| `chat_deleted` | After a chat delete succeeds (every path) | metadata `name` | Drop chat-keyed data your plugin holds *outside* the chat-scoped store |
| `chat_vaulted` | After a chat is encrypted into the vault | metadata `name` | Scrub plaintext deposits keyed by that name. **`privacy_aware` plugins only — see [Private chats](#private-chats--privacy_aware)** |
| `plugins_ready` | Once per plugin scan, after every enabled plugin registered | metadata `loaded`, `reason` | Boot work that needs other plugins present |

Data kept in the [chat-scoped store](tools.md#chat-scoped-state) needs neither
rename nor delete handling — core carries those rows through the rename and
drops them inside the delete transaction. Reach for `chat_renamed` /
`chat_deleted` only for chat-keyed things that live somewhere else: files,
your own database, an external service. (The Game Room used to carry its
playthroughs by hand this way; it doesn't any more.)

### Manifest Declaration

```json
"capabilities": {
  "hooks": {
    "post_stt": "hooks/stt_cleanup.py",
    "pre_chat": "hooks/filter.py",
    "prompt_inject": "hooks/context.py",
    "post_llm": "hooks/response_filter.py",
    "post_chat": "hooks/log.py",
    "pre_execute": "hooks/guard.py",
    "post_execute": "hooks/audit.py",
    "pre_tts": "hooks/tts_filter.py",
    "post_tts": "hooks/tts_done.py",
    "on_wake": "hooks/wake_react.py"
  }
}
```

Each handler exports a function matching the hook name (e.g. `def pre_chat(event):`), or `def handle(event):` as fallback. Do **not** use `def run(event):` — that's for schedule handlers only and will silently fail to register as a hook.

---

## HookEvent Fields

```python
@dataclass
class HookEvent:
    input: str = ""                          # User message
    skip_llm: bool = False                   # Bypass LLM / skip execution
    response: Optional[str] = None           # Direct response (with skip_llm)
    ephemeral: bool = False                  # Don't save to history (with skip_llm)
    context_parts: List[str] = []            # System prompt injections
    stop_propagation: bool = False           # Halt lower-priority hooks
    config: Any = None                       # System config (read-only)
    metadata: Dict[str, Any] = {}            # Free-form data (system, etc.)
    function_name: Optional[str] = None      # Tool name (execute hooks)
    arguments: Optional[dict] = None         # Tool args (mutable in pre_execute)
    result: Optional[str] = None             # Tool result (post_execute)
    tts_text: Optional[str] = None           # TTS text (mutable in pre_tts)
    skip_tts: bool = False                   # Cancel TTS
    chat_name: Optional[str] = None          # This turn's chat (runner-stamped)
    chat_private: Optional[bool] = None      # True = private chat (runner-stamped)
    surface: Optional[str] = None            # 'chat' | 'game' — the chat's surface (fire-site-stamped)
```

`chat_name` / `chat_private` are stamped by the runner, not by you — read them, don't set them. See [Private chats](#private-chats--privacy_aware). `surface` is stamped by the fire site for the presence hooks — see [Surfaces](#surfaces--where-presence-hooks-fire).

---

## System Access

Handlers get the `VoiceChatSystem` instance via `event.metadata.get("system")`. This gives deep access to Sapphire's subsystems — TTS, STT, wakeword, LLM, chat history, tool manager, and more.

**Not all hooks populate system metadata.** Check this table:

| Hook | `metadata["system"]`? | `config`? | Mutable Fields |
|------|-----------------------|-----------|----------------|
| `post_stt` | **Yes** | Yes | `input` (transcribed text) |
| `pre_chat` | **Yes** | Yes | `input`, `skip_llm`, `response`, `ephemeral` |
| `prompt_inject` | No | Yes | `context_parts` (list — append to it) |
| `ghost_inject` | **Yes** | **No** (`event.config` is `None` — `import config` if needed) | `ghost_text` (set to a string to contribute one ghost line) |
| `post_llm` | **Yes** | Yes | `response` (AI's answer text) |
| `post_chat` | **Yes** | Yes | None (observational) |
| `pre_execute` | **Yes** | Yes | `arguments`, `skip_llm`, `result` |
| `post_execute` | No | Yes | None (observational) |
| `pre_tts` | No | Yes | `tts_text`, `skip_tts` |
| `post_tts` | No | Yes | None (observational) |
| `tts_stream_start` | **Yes** | Yes | `skip_tts` |
| `tts_chunk_text` | **Yes** | Yes | `tts_text`, `skip_tts` |
| `tts_chunk_audio` | **Yes** | Yes | `metadata['audio']['audio_bytes']`, `['content_type']` |
| `tts_stream_end` | **Yes** | Yes | None (observational) |
| `on_wake` | No | Yes | None (notification only) |
| `provider_switched` | No | No | None (observational; metadata `kind`, `provider`) |
| `chat_renamed` | No | No | None (observational; metadata `old`, `new`) |
| `chat_deleted` | No | No | None (observational; metadata `name`) |
| `chat_vaulted` | No | No | None (observational; metadata `name`) |
| `plugins_ready` | No | No | None (observational; metadata `loaded`, `reason`) |

### What System Access Gives You

Through `system = event.metadata.get("system")`, plugins can control:

| Subsystem | Access | What You Can Do |
|-----------|--------|----------------|
| **TTS** | `system.tts` | `set_voice(name)`, `set_speed(float)`, `set_pitch(float)`, `speak(text)`, `speak_sync(text)`, `stop()`, `generate_audio_data(text)` |
| **STT** | `system.toggle_stt(bool)` | Enable/disable speech-to-text at runtime |
| **Wakeword** | `system.toggle_wakeword(bool)` | Enable/disable wakeword detection |
| **LLM** | `system.llm_chat` | `chat(query)` — send a message directly to the LLM |
| **System Prompt** | `system.llm_chat` | `set_system_prompt(text)`, `get_system_prompt_template()` |
| **Chat History** | `system.llm_chat.session_manager` | `get_messages()`, `list_chats()`, `create_chat(name)`, `set_active_chat(name)` (also applies that chat's prompt/toolset/voice to the live runtime), `delete_chat(name)` |
| **Tool Manager** | `system.llm_chat.function_manager` | `update_enabled_functions([toolset])`, `execute_function(name, args)`, `get_enabled_function_names()` |
| **Scopes** | `system.llm_chat.function_manager` | `set_rag_scope(s)`, `set_private_chat(bool)` — the only core scope setters. Plugin scopes (memory/knowledge/people/email/etc.) apply from chat settings via `apply_scopes_from_settings()`; there are no per-scope setter methods |
| **Generation** | `system` | `cancel_generation()` — cancel in-progress LLM streaming |
| **Event Bus** | `from core.event_bus import publish, Events` | Broadcast events system-wide |

Always guard access with `hasattr()` checks — subsystems may be None if disabled:

```python
def pre_chat(event):
    system = event.metadata.get("system")
    if not system:
        return
    if hasattr(system, "tts") and system.tts:
        system.tts.set_voice("af_sky")
```

---

## Surfaces — where presence hooks fire

A chat is shown on a **surface**: `chat` (the chat view, with plugin sidebar accordions) or `game` (the Game Room — stories and games, where core sections render but plugin accordions don't). The story engine stamps the chat setting `surface: "game"` while a story is active and hands it back at the end.

If your plugin injects text *because something is on screen* — an avatar, a scene, a soundscape — declare where that something actually lives:

```json
{ "surfaces": ["chat"] }
```

Core then withholds **`prompt_inject` and `ghost_inject`** (only those two — the presence hooks) from your plugin on every other surface. No declaration = fire everywhere, exactly as before. The user can change your default per plugin from **Settings › Plugins** ("Show in: ☑ Chat ☐ Game Room") — the override lives in your plugin state under `surfaces` and wins over the manifest. Unknown surface ids are dropped. `GET /api/chats/{name}/prompt-preview` shows the assembled prompt for a chat *after* this filtering — the honest way to check what she gets.

---

## Private chats & `privacy_aware`

A chat can be marked private (the 👁 toggle; with the prompt vault set up, its
history is also encrypted at rest). On a turn in a private chat, the hook runner
**withholds every hook from plugins that haven't declared themselves
privacy-aware**. Your handler simply isn't called — the skip is logged at debug
level and nothing else happens. Core withholds by default because a plugin with
no privacy signal might persist or transmit content the user just locked up.

Declare it **top-level in `plugin.json`** (next to `name`, not inside `capabilities`):

```json
{
  "name": "my-plugin",
  "privacy_aware": true,
  "capabilities": {
    "hooks": { "post_chat": "hooks/log.py" }
  }
}
```

**Rules the runner follows:**

- Privacy is resolved **once per fire**. The event arrives at your handler with
  `chat_name` and `chat_private` already stamped.
- **Fail-closed**: if privacy can't be resolved (resolver error), the turn is
  treated as private and non-aware plugins are withheld.
- Some hooks **always deliver**, private or not: `chat_renamed`, `chat_deleted`,
  `plugins_ready`, `provider_switched`, `on_wake`. Rename/delete are housekeeping —
  withholding them would strand a private chat's plugin-side data under a stale
  name, which is the exact plaintext orphan the gate exists to prevent. The rest
  carry no chat content and no chat identity.
- Awareness is dropped when the plugin unloads, and re-read from the manifest on
  the next load.

**When to declare it.** Only when your plugin is correct on private content: it
doesn't write chat text or chat names into plaintext stores, doesn't ship them
off-box, and doesn't log them. If your plugin mirrors conversation anywhere,
leave the flag off — being withheld in private chats is the safe default, not a
bug. Bundled examples that declare it: `plugins/game-room`, `plugins/mindpalace`,
`plugins/captioning`.

### `chat_vaulted` — a chat just went private

Fires **after** the chat is sealed (encryption already committed), with
`event.metadata["name"]` set to the chat that flipped. Delivered **only to
privacy_aware plugins** — the event itself says "this name is now a secret."

Use it to scrub plaintext deposits your plugin left while the chat was public:
rows keyed by that chat name, exports, caches. Errors are isolated and best-effort
(a failure here doesn't unwind the vaulting), so make the handler idempotent.
Scrubbing is **one-way** — unvaulting the chat later cannot restore what you
removed, and shouldn't.

```python
# hooks/chat_vaulted.py
def chat_vaulted(event):
    name = (event.metadata or {}).get("name")
    if not name:
        return
    my_store.rekey_chat(name, "__private__")   # or delete outright
    # Don't log the name — it just became a secret.
```

```json
"privacy_aware": true,
"capabilities": {
  "hooks": { "chat_vaulted": "hooks/chat_vaulted.py" }
}
```

---

## Streaming TTS Hooks

When streaming TTS is enabled (`TTS_STREAMING_ENABLED`) and the active provider
supports it (Kokoro), the brain synthesizes speech in chunks *while the LLM is
still generating*. Four hooks let a plugin observe or transform that stream —
enough to build live captions, an audio recorder, or a lipsync driver.

These fire **only** for streaming-capable providers. Legacy/whole-blob providers
use `pre_tts` / `post_tts` instead.

### Lifecycle & order (per turn)

```
tts_stream_start             ── once, before any synth
  tts_chunk_text   (chunk 0) ── before synth; mutate or skip this chunk
  tts_chunk_audio  (chunk 0) ── after synth; transform/replace the audio
  tts_chunk_text   (chunk 1)
  tts_chunk_audio  (chunk 1)
  ...
tts_stream_end               ── once, after the last chunk
```

`tts_stream_end` fires **exactly once per turn on every exit path** — normal
completion, forced-final, user Stop, plugin skip-at-start, and connection
error. Use it to finalize any state you opened in `tts_stream_start`. (Note:
per-handler error isolation means `tts_stream_end` still fires even if your
`tts_stream_start` handler raised — don't assume start succeeded.)

### Per-hook contract

| Hook | Mutable | Key metadata |
|------|---------|--------------|
| `tts_stream_start` | `skip_tts` (True disables the whole turn's speech) | `voice`, `speed`, `stream_id`, `system` |
| `tts_chunk_text` | `tts_text` (rewrite; `""`/whitespace mutes this chunk; `skip_tts` skips it) | `chunk_index`, `boundary`, `pause_after_ms`, `stream_id`, `system` |
| `tts_chunk_audio` | `metadata['audio']['audio_bytes']` and `['content_type']` (the carrier dict; falsy bytes drops the chunk) | `chunk_index`, `chunk_text`, `boundary`, `pause_after_ms`, `stream_id`, `system` |
| `tts_stream_end` | None (observational) | `chunk_count`, `total_chars`, `interrupted`, `stream_id`, `system` |

`stream_id` is stable across all four hooks for the same turn — use it to
correlate chunks.

> **Count asymmetry:** `tts_chunk_text` fires for every chunk, but a chunk can
> be dropped before audio (emoji/punctuation-only, plugin-emptied text, or a
> synth failure). So `tts_chunk_audio` may fire fewer times than
> `tts_chunk_text`. Reconcile against `tts_stream_end`'s `chunk_count` (the
> number actually voiced), not the text-hook count.

### Manifest declaration

```json
"capabilities": {
  "hooks": {
    "tts_stream_start": "hooks/captions.py",
    "tts_chunk_text": "hooks/captions.py",
    "tts_stream_end": "hooks/captions.py"
  }
}
```

### Example — live caption log

```python
# hooks/captions.py
from core.plugin_loader import plugin_loader

def tts_stream_start(event):
    plugin_loader.get_plugin_state("captioning").save("buffer", "")

def tts_chunk_text(event):
    # event.tts_text is the chunk about to be voiced (mutable). metadata
    # carries chunk_index + boundary if you want to time-align.
    state = plugin_loader.get_plugin_state("captioning")
    buf = state.get("buffer", "") + (event.tts_text or "") + " "
    state.save("buffer", buf)

def tts_stream_end(event):
    meta = event.metadata
    print(f"[captions] {meta.get('chunk_count')} chunks voiced, "
          f"interrupted={meta.get('interrupted')}")
```

### Transforming chunk audio

`tts_chunk_audio` hands you a mutable carrier at `event.metadata['audio']`:

```python
def tts_chunk_audio(event):
    carrier = event.metadata["audio"]   # {'audio_bytes': ..., 'content_type': ...}
    carrier["audio_bytes"] = transcode_ogg_to_wav(carrier["audio_bytes"])
    carrier["content_type"] = "audio/wav"
    # Set carrier['audio_bytes'] to b"" / None to drop this chunk entirely.
```

The browser honors a per-chunk `content_type`, so you can re-encode on the fly.

---

## Voice Commands

Voice commands are pre_chat hooks with keyword trigger matching. See the dedicated [Voice Commands](voice-commands.md) guide for match modes, handler patterns, and examples.

---

## Examples

**post_stt — correct transcription:**
```python
def post_stt(event):
    fixes = {"creme": "Krem", "saphire": "Sapphire", "hey i": "AI"}
    text = event.input
    for wrong, right in fixes.items():
        text = text.replace(wrong, right)
    event.input = text
```

**pre_chat — block input:**
```python
def pre_chat(event):
    if "password" in event.input.lower():
        event.skip_llm = True
        event.response = "I can't help with that."
        event.stop_propagation = True
```

**prompt_inject — add context:**
```python
def prompt_inject(event):
    event.context_parts.append("The user's timezone is UTC+3.")
```

**post_llm — add spice to responses:**
```python
import random
SPICE = ["hell yeah", "damn right", "no kidding"]

def post_llm(event):
    if event.response and random.random() < 0.3:
        event.response = event.response + f" ...{random.choice(SPICE)}."
```

**post_llm — clean mode (strip profanity):**
```python
SWEARS = {"damn": "darn", "hell": "heck", "ass": "butt"}

def post_llm(event):
    text = event.response or ""
    for word, clean in SWEARS.items():
        text = text.replace(word, clean)
    event.response = text
```

**pre_execute — block a tool:**
```python
def pre_execute(event):
    if event.function_name == "delete_memory":
        event.skip_llm = True
        event.result = "Deletion blocked by plugin."
```

**pre_execute — tool guardrails:**
```python
MAX_BTC_SEND = 0.01

def pre_execute(event):
    if event.function_name == "send_bitcoin":
        amount = event.arguments.get("amount", 0)
        if amount > MAX_BTC_SEND:
            event.skip_llm = True
            event.result = f"Blocked: {amount} BTC exceeds limit of {MAX_BTC_SEND}"
            return

    if event.function_name == "ssh_execute":
        cmd = event.arguments.get("command", "")
        if "sudo" in cmd:
            event.arguments["command"] = cmd.replace("sudo ", "")
```

**pre_tts — fix pronunciation:**
```python
def pre_tts(event):
    event.tts_text = event.tts_text.replace("API", "A.P.I.")
```

**Dynamic voice switcher (post_chat):**
```python
def post_chat(event):
    system = event.metadata.get("system")
    if not system or not system.tts:
        return
    text = event.response or ""
    if any(w in text.lower() for w in ["sorry", "understand", "feel"]):
        system.tts.set_speed(0.9)
        system.tts.set_voice("af_heart")
    elif "!" in text:
        system.tts.set_speed(1.3)
```

**Ambient context injection (prompt_inject):**
```python
import requests, time

_cache = {"data": "", "ts": 0}

def prompt_inject(event):
    now = time.time()
    if now - _cache["ts"] > 300:  # refresh every 5 min
        try:
            weather = requests.get("http://ha-server/api/states/weather.home",
                                   headers={"Authorization": "Bearer ..."}).json()
            _cache["data"] = f"Weather: {weather['state']}, {weather['attributes']['temperature']}F"
            _cache["ts"] = now
        except Exception:
            pass
    if _cache["data"]:
        event.context_parts.append(_cache["data"])
```

**Voice macro — goodnight:**
```python
import requests

def pre_chat(event):
    system = event.metadata.get("system")
    if system and system.tts:
        system.tts.set_voice("af_heart")
        system.tts.set_speed(0.8)

    try:
        requests.post("http://ha:8123/api/services/scene/turn_on",
                       json={"entity_id": "scene.goodnight"},
                       headers={"Authorization": "Bearer ..."})
    except Exception:
        pass

    event.skip_llm = True
    event.ephemeral = True
    event.response = "Goodnight. Lights dimmed, quiet mode on."
    event.stop_propagation = True
```

**Privacy filter — strip PII from speech (pre_tts):**
```python
import re

PII_PATTERNS = [
    (r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[phone]'),
    (r'\b[\w.+-]+@[\w-]+\.[\w.]+\b', '[email]'),
    (r'\b\d{1,5}\s+[\w\s]+(?:St|Ave|Blvd|Dr|Ln)\b', '[address]'),
]

def pre_tts(event):
    text = event.tts_text or ""
    for pattern, replacement in PII_PATTERNS:
        text = re.sub(pattern, replacement, text)
    event.tts_text = text
```

**Conversation summarizer (post_chat):**
```python
from core.plugin_loader import plugin_loader

def post_chat(event):
    system = event.metadata.get("system")
    if not system:
        return
    state = plugin_loader.get_plugin_state("summarizer")
    turn_count = (state.get("turns", 0) + 1)
    state.save("turns", turn_count)

    if turn_count % 10 == 0:
        messages = system.llm_chat.session_manager.get_messages()
        last_10 = [m["content"] for m in messages[-20:] if isinstance(m.get("content"), str)]
        summary_prompt = f"Summarize this conversation excerpt:\n\n{'\\n'.join(last_10)}"
        summary = system.llm_chat.chat(summary_prompt)
        state.save("last_summary", summary)
```

---

## `ghost_inject` — per-turn ephemeral context (cache-friendly)

`ghost_inject` is for content that should reach the assistant on a single turn but **NOT** persist into chat history and **NOT** mutate the cached system prompt. Cache hit rates on Claude jump from ~0% to ~95% when per-turn content is delivered through this rail instead of `prompt_inject`. The assistant sees the contribution as labeled operator metadata; the user does not see it at all.

### When to use it (vs `prompt_inject`)

| Use this hook | If your content is | Examples |
|---|---|---|
| `ghost_inject` | **Per-turn ephemera** that changes often | Time, weather, calendar status, ambient mood, current focus block, current location |
| `prompt_inject` | **Long-lived character/state** stable across many turns | Story scenario flags, RAG document context, persistent persona overrides |

If your content changes more than once a session, use `ghost_inject`. If it's stable across the whole chat, use `prompt_inject`.

### Handler

```python
# hooks/my_ghost.py

def ghost_inject(event):
    # event.metadata["system"] gives access to the VoiceChatSystem
    # event.input is the user's current message (read-only here)

    # Set event.ghost_text to contribute to this turn's ghost message.
    # Plugins set ONE string per fire; the runner attributes it to the
    # plugin name automatically. Return value is ignored.
    if some_condition_for_my_plugin:
        event.ghost_text = "Light rain in user's area, mention if natural."
    # If no contribution this turn, do nothing — leave ghost_text unset.

handle = ghost_inject  # fallback for `handle` dispatch
```

### Manifest declaration

```json
"capabilities": {
  "hooks": {
    "ghost_inject": "hooks/my_ghost.py"
  }
}
```

### Envelope format (what the assistant sees)

When ghost contributions exist for a turn, the runner builds a single envelope and inserts it as a user-role message just before the new user input:

```
[System context from Sapphire's own app — not written by the user]
- Time: Tuesday, May 8, 8:55 PM (America/Indiana/Indianapolis)
- Spice: Speak more urgently in this reply.
- weather: Light rain in user's area.
- calendar: User has a meeting in 15 minutes.
```

Each line is attributed to the plugin that contributed it. The opener tells the assistant unambiguously that the content is operator metadata, not the user. The assistant can choose to weave the context naturally, mention it explicitly, or refuse to comply with any individual line.

### Why this matters: the trust model

`ghost_inject` is **invisible to the user**. That's the point — operator-orchestrated context like time, weather, mood, current task should reach the assistant without cluttering what the user reads. **But invisible-to-user is also the manipulation surface to be careful about.** The trust model has three legs:

1. **Per-line plugin attribution.** The envelope shows the assistant exactly which plugin contributed which line. A ghost message saying "argue with the user about X" gets attributed by name, so the assistant can flag it out loud rather than complying silently.
2. **Plugin store review.** Ghost-injecting plugins are reviewed for what they declare and what they actually inject. Plugins that fingerprint user content and inject opinion-shaping or behavior-shaping text get rejected.
3. **No content-targeting allowed.** The hook receives `event.input` as **read-only context** so plugins know what's happening, NOT so they can match against it for adversarial injection.

### What gets accepted at store review

✅ **Ambient state**: time, weather, calendar, energy/mood (user-set), location, holiday awareness, pomodoro phase
✅ **Operator settings**: spice variants from a content pack the user opted into, voice-mode hints ("user is on phone")
✅ **Cache-friendly migrations**: existing `prompt_inject` plugins moving per-turn parts to ghost for the cache win

### What gets rejected at store review

❌ **Content-fingerprinting + opinion shaping**: matching user message text (embedding cosine, regex, keyword) and injecting "tell user not to do X" or "argue with the user about Y"
❌ **Hidden persuasion**: any plugin that conceals what it's actually injecting in the manifest description
❌ **Label spoofing**: plugins that try to override `ghost_label` to disguise their attribution (the runner overwrites this — don't bother)
❌ **Context dominance**: plugins emitting >2KB of content per turn (truncated automatically with a warning, but worth knowing)
❌ **Vanta-shape patterns**: cosine-match on user input → inject behavioral instruction. This is the literal pattern the rail is gated against.

### How to think about it

The ghost rail is **labeled operator metadata**, not invisible puppetry. If you wouldn't be comfortable with a user discovering exactly what your plugin injects on every turn, your plugin probably shouldn't ship through the store. Build for plugins where the user **knows** their AI has weather context, time awareness, or calendar awareness — they just don't need it cluttering what they read.

