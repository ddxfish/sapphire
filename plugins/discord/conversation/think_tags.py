"""Think-tag stripping for LLM replies sent to Discord."""

from __future__ import annotations

import re

_THINK_CLOSE_RE = re.compile(
    r'<(?:redacted_thinking|thinking|(?:seed:)?think|seed:cot_budget_reflect)[^>]*>'
    r'[\s\S]*?</(?:redacted_thinking|thinking|(?:seed:)?think|seed:cot_budget_reflect)>',
    re.IGNORECASE,
)
_THINK_OPEN_RE = re.compile(
    r'<(?:redacted_thinking|thinking|(?:seed:)?think|seed:cot_budget_reflect)[^>]*>.*$',
    re.DOTALL | re.IGNORECASE,
)
_THINK_LEAD_RE = re.compile(
    r'^[\s\S]*</(?:redacted_thinking|thinking|(?:seed:)?think|seed:cot_budget_reflect)>',
    re.IGNORECASE,
)


def strip_think_tags(text: str) -> str:
    """Remove thinking blocks from LLM output before sending to Discord."""
    text = _THINK_CLOSE_RE.sub('', text or '')
    text = _THINK_OPEN_RE.sub('', text)
    text = _THINK_LEAD_RE.sub('', text)
    return text.strip()
