# Hooks & Voice Commands

## Hook Points

All hooks receive a mutable `HookEvent`. Changes persist across handlers in priority order.

| Hook | When | Key Fields | Use |
|------|------|-----------|-----|
| `post_stt` | After voice transcription | `input` (mutable) | Correct STT errors, translate, normalize |
| `pre_chat` | Before LLM | `input`, `skip_llm`, `response` | Filter input, bypass LLM |
| `prompt_inject` | System prompt build | `context_parts` | Append context strings |
| `post_llm` | After LLM, before save | `response` (mutable) | Translate, filter, style transfer |
| `post_chat` | After response saved | `input`, `response` | Logging, analytics |
| `pre_execute` | Before tool call | `function_name`, `arguments` | Modify args, block tools |
| `post_execute` | After tool call | `function_name`, `result` | Audit, react to results |
| `pre_tts` | Before speech | `tts_text`, `skip_tts` | Modify text, cancel TTS |
| `post_tts` | After playback ends | `tts_text` | Analytics, reactions, subtitles |
| `on_wake` | Wakeword detected | — | Play sounds, log, custom reactions |

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
```

---

## System Access

Handlers get the `VoiceChatSystem` instance via `event.metadata.get("system")`. This gives deep access to Sapphire's subsystems — TTS, STT, wakeword, LLM, chat history, tool manager, and more.

**Not all hooks populate system metadata.** Check this table:

| Hook | `metadata["system"]`? | `config`? | Mutable Fields |
|------|-----------------------|-----------|----------------|
| `post_stt` | **Yes** | Yes | `input` (transcribed text) |
| `pre_chat` | **Yes** | Yes | `input`, `skip_llm`, `response`, `ephemeral` |
| `prompt_inject` | No | Yes | `context_parts` (list — append to it) |
| `post_llm` | **Yes** | Yes | `response` (AI's answer text) |
| `post_chat` | **Yes** | Yes | None (observational) |
| `pre_execute` | **Yes** | Yes | `arguments`, `skip_llm`, `result` |
| `post_execute` | No | Yes | None (observational) |
| `pre_tts` | No | Yes | `tts_text`, `skip_tts` |
| `post_tts` | No | Yes | None (observational) |
| `on_wake` | No | Yes | None (notification only) |

### What System Access Gives You

Through `system = event.metadata.get("system")`, plugins can control:

| Subsystem | Access | What You Can Do |
|-----------|--------|----------------|
| **TTS** | `system.tts` | `set_voice(name)`, `set_speed(float)`, `set_pitch(float)`, `speak(text)`, `speak_sync(text)`, `stop()`, `generate_audio_data(text)` |
| **STT** | `system.toggle_stt(bool)` | Enable/disable speech-to-text at runtime |
| **Wakeword** | `system.toggle_wakeword(bool)` | Enable/disable wakeword detection |
| **LLM** | `system.llm_chat` | `chat(query)` — send a message directly to the LLM |
| **System Prompt** | `system.llm_chat` | `set_system_prompt(text)`, `get_system_prompt_template()` |
| **Chat History** | `system.llm_chat.session_manager` | `get_messages()`, `list_chats()`, `create_chat(name)`, `set_active_chat(name)`, `delete_chat(name)` |
| **Tool Manager** | `system.llm_chat.function_manager` | `update_enabled_functions([toolset])`, `execute_function(name, args)`, `get_enabled_function_names()` |
| **Scopes** | `system.llm_chat.function_manager` | `set_knowledge_scope(s)`, `set_email_scope(s)`, `set_bitcoin_scope(s)`, `set_memory_scope(s)` |
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
