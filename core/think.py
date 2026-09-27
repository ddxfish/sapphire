# core/think.py
"""ONE place that knows what a thinking block looks like (2026-09-27).

Reasoning models wrap their scratch work in tags: <think>, <seed:think>,
<thinking>, <redacted_thinking>, <reasoning>. Sapphire has to tell that apart
from the answer in a dozen lanes (chat history, the web UI, TTS, Discord,
Telegram, email, tasks, agents, the image describer). Until today each lane
carried its own regex and they disagreed. This module is the one reader; the
web UI's twin is interfaces/web/static/shared/think.js (same four rules).

The rules, in order, walking the reply left to right:

  1. A tag in backticks, or inside a fenced code block of the answer, is a
     WORD. A model explaining `<think>` tags is not thinking. (The Discord
     plugin's rule; typed tags used to cut replies in half.)
  2. An opener with a closer after it is a thinking block.
  3. A closer with nothing open means what came before it was thinking: the
     provider ate the opener, or the model closed early and kept going
     (<think>A</think>B</think>C, a GLM habit). Greedy, but FENCED: it reaches
     back to the last block, never across one. A reply with several think
     blocks (one per tool round) keeps the prose between them. The old
     first-opener-to-last-closer sweep ate it.
  4. An opener that never closes is thinking to the end only where a block can
     start: the start of the reply, the start of a line, right after a
     sentence or a tag. Mid-sentence it is a word.

Never raises. Text in, text out.

    from core import think
    think.strip(text)      -> the answer
    think.split(text)      -> (answer, thinking)
    think.segments(text)   -> [('text' | 'think', text, tag_name), ...] in order
    think.wrap(thinking, answer)  -> the one serialized form
    think.only(text)       -> thinking only, for a tool-calling turn's stored row
    think.unfinished(buf)  -> chars of thinking still open at the end of a stream
    think.PAIRS            -> [(opener, closer), ...] literals for stream machines
    think.BLOCK_RE         -> one complete block, for a stream machine's piece cleaner
"""
from __future__ import annotations

import re

OPEN_NAMES = ('think', 'seed:think', 'thinking', 'redacted_thinking', 'reasoning')
# seed models close a budget note inside their thinking; it ends a block too.
CLOSE_NAMES = OPEN_NAMES + ('seed:cot_budget_reflect',)
OPEN, CLOSE = '<think>', '</think>'
PAIRS = [(f'<{name}>', f'</{name}>') for name in OPEN_NAMES]

_ALT = '|'.join(re.escape(n) for n in sorted(CLOSE_NAMES, key=len, reverse=True))
_TAG_RE = re.compile(r'<(/?)(' + _ALT + r')(?:\s[^<>]*)?\s*>', re.IGNORECASE)
_FENCE_RE = re.compile(r'^[ \t]*(?:`{3,}|~{3,})', re.MULTILINE)
# A complete block, any tag name. For stream machines that clean one safe piece
# at a time and only need whole blocks gone; everyone else uses strip().
BLOCK_RE = re.compile(r'<(?:' + '|'.join(re.escape(n) for n in sorted(OPEN_NAMES, key=len, reverse=True))
                      + r')(?:\s[^<>]*)?\s*>.*?</(?:' + _ALT + r')\s*>', re.DOTALL | re.IGNORECASE)
_BLOCK_START = '\n\r.!?>'


def _backticked(text: str, at: int) -> bool:
    return at > 0 and text[at - 1] == '`'


def _in_fence(chunk: str) -> bool:
    """An odd number of fence lines in the answer text so far = inside a code block."""
    return len(_FENCE_RE.findall(chunk)) % 2 == 1


def _closer(text: str, start: int):
    """The next real closer at or after `start` (a backticked one is a word)."""
    for m in _TAG_RE.finditer(text, start):
        if m.group(1) and not _backticked(text, m.start()):
            return m
    return None


def _block_position(text: str, at: int) -> bool:
    head = text[:at].rstrip(' \t')
    return not head or head[-1] in _BLOCK_START


def _after(text: str, at: int) -> int:
    """Past the whitespace that follows a closer."""
    n = len(text)
    while at < n and text[at] in ' \t\r\n':
        at += 1
    return at


def segments(text) -> list:
    """The reply in order: [(kind, text, tag_name)], kind 'text' or 'think'.
    tag_name is the block's own tag ('think', 'seed:think', ...), '' for text."""
    text = '' if text is None else str(text)
    out: list = []

    def add(kind, chunk, name=''):
        if chunk and (kind == 'text' or chunk.strip()):
            out.append((kind, chunk, name))

    pos = i = 0
    while True:
        m = _TAG_RE.search(text, i)
        if m is None:
            break
        closing, name = bool(m.group(1)), m.group(2).lower()
        chunk = text[pos:m.start()]
        if _backticked(text, m.start()) or _in_fence(chunk) or (not closing and name not in OPEN_NAMES):
            i = m.end()                                   # rule 1: a word
            continue
        if closing:                                       # rule 3: nothing open
            add('think', chunk, name)
            pos = i = _after(text, m.end())
            continue
        close = _closer(text, m.end())
        if close is None:                                 # rule 4: never closes
            if _block_position(text, m.start()):
                add('text', chunk)
                add('think', text[m.end():], name)
                pos = len(text)
                break
            i = m.end()
            continue
        add('text', chunk)                                # rule 2: a block
        add('think', text[m.end():close.start()], name)
        pos = i = _after(text, close.end())
    add('text', text[pos:])
    return out


def split(text):
    """(answer, thinking). Empty or None comes back as it came, with '' thinking."""
    if not text:
        return text, ''
    parts = segments(text)
    answer = ''.join(chunk for kind, chunk, _ in parts if kind == 'text').strip()
    thinking = '\n\n'.join(chunk.strip() for kind, chunk, _ in parts if kind == 'think').strip()
    return answer, thinking


def strip(text) -> str:
    """The answer: what a person would see of the reply."""
    return split(text)[0] or ''


def thinking(text) -> str:
    return split(text)[1]


def has(text) -> bool:
    """Does the reply carry any thinking at all?"""
    return any(kind == 'think' for kind, _, _ in segments(text))


def wrap(thinking_text, answer='') -> str:
    """The one serialized form: a think block, a blank line, the answer."""
    thinking_text, answer = str(thinking_text or ''), str(answer or '')
    if not thinking_text:
        return answer
    block = f'{OPEN}{thinking_text}{CLOSE}'
    return f'{block}\n\n{answer}' if answer else block


def only(text) -> str:
    """Thinking only: the stored form of an assistant turn that called a tool
    (its prose is a promise the tool result will repeat). A reply with no
    thinking is wrapped whole, never cut."""
    if not text:
        return ''
    _, found = split(text)
    return wrap(found) if found else wrap(str(text).strip())


def unfinished(buf) -> int:
    """Chars of thinking still open at the end of a stream buffer (0 = none)."""
    text = '' if buf is None else str(buf)
    open_at = None
    for m in _TAG_RE.finditer(text):
        if _backticked(text, m.start()):
            continue
        if m.group(1):
            open_at = None
        elif m.group(2).lower() in OPEN_NAMES:
            open_at = m.end()
    return 0 if open_at is None else len(text) - open_at


__all__ = ['OPEN_NAMES', 'CLOSE_NAMES', 'OPEN', 'CLOSE', 'PAIRS', 'BLOCK_RE', 'segments', 'split', 'strip',
           'thinking', 'has', 'wrap', 'only', 'unfinished']
