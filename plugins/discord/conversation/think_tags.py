"""Think-tag stripping for LLM replies sent to Discord."""

from __future__ import annotations

import re

_TAG = r'(?:redacted_thinking|thinking|(?:seed:)?think|seed:cot_budget_reflect)'
# A complete block anywhere. `(?<!`)`: a tag the model TYPED while explaining
# reasoning models sits in backticks; a real thinking block never does.
_THINK_CLOSE_RE = re.compile(r'(?<!`)<' + _TAG + r'[^>]*>[\s\S]*?</' + _TAG + r'>', re.IGNORECASE)
# An unclosed opener only when the reply STARTS with it (a reasoning model cut
# off mid-thought). A mid-prose `<think>` used to eat the rest of the answer.
_THINK_OPEN_RE = re.compile(r'^\s*<' + _TAG + r'[^>]*>.*$', re.DOTALL | re.IGNORECASE)
# A closer with no opener left = the provider ate the opener; the head is thinking.
_THINK_LEAD_RE = re.compile(r'^[\s\S]*?(?<!`)</' + _TAG + r'>', re.IGNORECASE)
_OPENER_RE = re.compile(r'(?<!`)<' + _TAG + r'[^>]*>', re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    """Remove thinking blocks from LLM output before sending to Discord.

    Only the shapes a reasoning model actually emits: a complete block anywhere,
    an unclosed block that OPENS the reply, a closer with no opener (the head of
    the reply was thinking). A literal tag typed in prose — the help channel's
    exact subject — no longer deletes half the answer (hunt 2.13.0, row 16).
    Residual: a bare prose `</think>` with no backticks still reads as a lost
    opener; models nearly always backtick a tag they are explaining.
    """
    text = _THINK_CLOSE_RE.sub('', text or '')
    text = _THINK_OPEN_RE.sub('', text)
    if not _OPENER_RE.search(text):
        text = _THINK_LEAD_RE.sub('', text)
    return text.strip()
