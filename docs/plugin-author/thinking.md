# Thinking Blocks

Reasoning models write their scratch work before the answer, wrapped in tags:
`<think>`, `<seed:think>`, `<thinking>`, `<redacted_thinking>`, `<reasoning>`. If
your plugin sends an LLM reply anywhere a person will read or hear it (a chat
channel, an email, a text message, a speaker, a JSON parser), the thinking has to
come out first.

Sapphire has **one** reader for those tags: `core/think.py`. Use it. Do not write
a regex. Before this module existed the app carried fourteen of them and they
disagreed about what a thinking block was.

## Use it

```python
from core import think

answer = think.strip(reply)                 # what a person should see
answer, thinking = think.split(reply)       # both halves
```

That is the whole job for most plugins. `strip()` never raises, takes `None`, and
returns a plain string with the surrounding whitespace trimmed.

```python
def _reply_handler(task, event_data, response_text):
    from core import think
    clean = think.strip(response_text)
    if not clean:
        logger.warning("[MYPLUGIN] reply was thinking only, nothing sent")
        return
    send(clean)
```

An empty result is normal: a model can spend its whole budget reasoning. Check for
it and say so in the log, as above.

## The API

| Call | Returns | Use it for |
|---|---|---|
| `think.strip(text)` | the answer | anything you send, speak, parse, or store as "what she said" |
| `think.split(text)` | `(answer, thinking)` | showing or measuring both halves |
| `think.thinking(text)` | the thinking | a debug view |
| `think.has(text)` | `bool` | "did this reply reason at all?" |
| `think.segments(text)` | `[(kind, text, tag_name), ...]` in order, `kind` is `'text'` or `'think'` | rendering a reply in place, block by block |
| `think.wrap(thinking, answer)` | `<think>…</think>` + blank line + answer | writing the one serialized form |
| `think.only(text)` | thinking only, wrapped | the stored row of a turn that called a tool |
| `think.unfinished(buf)` | chars of thinking still open at the end | a stream watchdog ("she has been thinking for 6000 chars") |
| `think.PAIRS` | `[(opener, closer), ...]` | a stream machine that holds text back until a block closes |
| `think.BLOCK_RE` | compiled regex, one complete block | a stream machine cleaning one safe piece |
| `think.OPEN_NAMES`, `think.CLOSE_NAMES`, `think.OPEN`, `think.CLOSE` | the tag names and the default pair | anything that needs the list |

## The four rules

The reader walks the reply left to right.

1. **A typed tag is a word.** A tag in backticks, or inside a fenced code block of
   the answer, is never a block. A model explaining `` `<think>` `` tags is not
   thinking.
2. **An opener with a closer after it is a thinking block.**
3. **A closer with nothing open means what came before it was thinking.** The
   provider ate the opener, or the model closed early and kept going
   (`<think>A</think>B</think>C`). This is greedy, but fenced: it reaches back to
   the last block and never across one, so a reply with a think block per tool
   round keeps the prose between them.
4. **An opener that never closes** is thinking to the end only where a block can
   start: the start of the reply, the start of a line, right after a sentence or a
   tag. In the middle of a sentence it is a word.

| Reply | `think.strip()` gives |
|---|---|
| `<think>plan</think>Hello` | `Hello` |
| `<think>a</think>One. <think>b</think>Two.` | `One. Two.` |
| `leftover</think>Answer.` | `Answer.` |
| `<think>A</think>B</think>C` | `C` |
| `<think>cut off mid-thought` | *(empty)* |
| ``Models emit `<think>` tags.`` | unchanged |
| `You wrap reasoning in <think> tags.` | unchanged |

Two shapes the rules cannot tell apart, by design. A bare `</think>` typed in
prose with no backticks reads as a lost opener, and a bare typed pair
(`<think> and </think>`) reads as a block. Models nearly always backtick a tag
they are explaining, and reading either the other way would leak real thinking
into a channel.

## In the web UI

A plugin's web page uses the twin, same names and same rules:

```javascript
import { stripThink, splitThink, thinkSegments, hasThink } from '/static/shared/think.js';

const answer = stripThink(reply);
const { answer, thinking } = splitThink(reply);
for (const seg of thinkSegments(reply)) { /* seg.type: 'text' | 'think', seg.text, seg.name */ }
```

Render thinking as **plain text** (`element.textContent = seg.text`). It is
scratch paper: markdown, image markers, and code blocks inside it must not come
alive. The chat page does the same.

`tests/test_think_js_twin.py` runs both readers on the same cases. If you change a
rule, change both files and add the case there.

## Streams

Text that arrives a piece at a time cannot wait for the whole reply. Keep your own
small state machine and take the tags from the module:

- Hold text back while a block is open: `think.PAIRS` gives the literal openers
  and closers (`core/tts/streaming.py` does this).
- Watch for a model that never stops thinking: `think.unfinished(buffer)`
  (`core/cadence.py` cancels the turn past a budget).
- When the stream ends, run `think.strip()` on the whole text. The full-text
  reader is the judge; a stream machine is a best guess until then.

## Asking a model not to think

Stripping is the safety net. If a side job does not need reasoning (a caption, a
summary, a classifier), ask for it off in the generation params and strip anyway:

```python
params = {**get_generation_params(key, model, providers_config()), 'disable_thinking': True}
response = provider.chat_completion(messages, None, params)
if getattr(response, 'content_is_reasoning', False):
    text = ''                       # the reply was ALL reasoning: treat as empty
else:
    text = think.strip(response.content)
```

`content_is_reasoning` is set when a provider returned reasoning and no answer at
all. Without the check the reasoning would be used as the answer.

## Gotchas

- **Never write your own pattern.** `tests/test_think_js_twin.py` fails the suite
  if a think-tag pattern appears in any Python file under `core/`, `plugins/` or
  `functions/` other than `core/think.py`, or in any web file other than
  `shared/think.js`.
- **Strip before you parse.** A model asked for JSON may reason first; run
  `think.strip()` before `json.loads()`.
- **Strip before you store a caption, a summary, a note.** Anything written back
  into the chat will be read by the next model as fact.
- **Do not strip user text.** These rules are for model output. A user may type
  a tag for any reason.
- **Sign after every edit** (`python tools/sign_plugin.py <your-plugin>`).

## Reference for AI

THINKING BLOCKS (`core/think.py`, the one reader; web twin `interfaces/web/static/shared/think.js`):
- Tag names: openers `think`, `seed:think`, `thinking`, `redacted_thinking`, `reasoning`; closers add `seed:cot_budget_reflect`. Case-insensitive; attributes allowed after whitespace.
- `from core import think` — `think.strip(text) -> str` (answer; None-safe; trimmed), `think.split(text) -> (answer, thinking)`, `think.thinking(text) -> str`, `think.has(text) -> bool`, `think.segments(text) -> [(kind, text, tag_name)]` with kind `'text'|'think'`, `think.wrap(thinking, answer='') -> str`, `think.only(text) -> str` (thinking only, wrapped; a reply with no thinking is wrapped whole), `think.unfinished(buf) -> int`, `think.PAIRS`, `think.BLOCK_RE`, `think.OPEN`, `think.CLOSE`.
- Rules, left to right: (1) a tag preceded by a backtick or inside a fenced code block of the answer is a word; (2) opener + later closer = block; (3) a closer with nothing open folds the text since the last block (or the start) into thinking, never reaching across a block; (4) an unclosed opener is thinking to the end only at a block start (start of reply, start of line, after `. ! ? >`), otherwise a word.
- JS: `import { stripThink, splitThink, thinkSegments, hasThink, nextThinkOpener, nextThinkCloser, isSeedThink } from '/static/shared/think.js'`. Render thinking with `textContent`, never `innerHTML`.
- Side jobs: pass `'disable_thinking': True` in generation params, treat `response.content_is_reasoning` as an empty reply, and still `think.strip()` the content.
- Never write a think-tag regex: `tests/test_think_js_twin.py` fails on any outside the two reader files.
