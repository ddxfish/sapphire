"""Lightweight sentiment helpers for silent reactions."""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

_vader_analyser = None
_vader_lock = threading.Lock()

_QUESTION_RE = re.compile(r'\?')

_SENTIMENT_EMOJIS = {
    'very_positive': [
        '🎉', '🥳', '💯', '🔥', '✨', '⭐', '🌟', '❤️', '💕', '😍', '🤩', '🥰', '😊', '👏', '🙌', '👍',
    ],
    'positive': [
        '👍', '👏', '🙌', '🤝', '❤️', '💛', '💚', '💙', '💜', '💯', '🔥', '✨', '😊', '🙂', '😉', '🥰',
    ],
    'curious': [
        '👀', '🧐', '🤔', '💭', '🫡', '❓', '⁉️', '💡', '🧠', '📚',
    ],
    'negative': [
        '😢', '😿', '😞', '😔', '🙁', '☹️', '😕', '😣', '😫', '😤', '😠', '💔',
    ],
    'very_negative': [
        '😢', '💔', '😭', '🥺', '😞', '😔', '😟', '🙁', '☹️', '🫂', '😰',
    ],
}

_DEFAULT_BLOCKED = {
    'negative': ['👍', '😂', '🤣'],
    'very_negative': ['👍', '😂', '🤣', '💀'],
}

_TECH_CHANNEL_RE = re.compile(
    r'\b(dev|code|coding|tech|programming|python|javascript|typescript|rust|golang|'
    r'engineering|software|hardware|linux|server|ops|sre|api|debug|helpdesk|'
    r'support|infra|network|security|data|ml|ai)\b',
    re.I,
)
_TECH_PREFERRED = frozenset({'👀', '🧐', '💡', '🧠', '📚', '🤔', '💭', '❓', '🫡', '⁉️'})
_SOFT_EMOJIS = frozenset({'💕', '💞', '💓', '💗', '💖', '💘', '💝', '🥰', '😍', '😘', '💌', '🫶'})


def _get_vader():
    global _vader_analyser
    if _vader_analyser is not None:
        return _vader_analyser
    with _vader_lock:
        if _vader_analyser is not None:
            return _vader_analyser
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader_analyser = SentimentIntensityAnalyzer()
            logger.info('[DISCORD] VADER sentiment analyser loaded')
        except ImportError:
            logger.warning(
                '[DISCORD] vaderSentiment not installed — silent reactions use heuristics only. '
                'Run: pip install vaderSentiment'
            )
            _vader_analyser = False
    return _vader_analyser


def sentiment_tier(content: str, *, context_text: str = '') -> str:
    """Return sentiment bucket for emoji selection, or empty string."""
    scored = f'{context_text} {content}'.strip() if context_text else (content or '').strip()
    if not scored:
        return ''

    vader = _get_vader()
    if vader:
        compound = vader.polarity_scores(scored[:512])['compound']
        if compound >= 0.5:
            return 'very_positive'
        if compound >= 0.1:
            return 'positive'
        if compound <= -0.5:
            return 'very_negative'
        if compound <= -0.1:
            return 'negative'

    lowered = (content or '').lower()
    if _QUESTION_RE.search(content or ''):
        return 'curious'
    if any(token in lowered for token in ('love', 'amazing', 'awesome', 'great', 'thanks', 'thank you', 'yay', 'nice')):
        return 'positive'
    if any(token in lowered for token in ('hate', 'awful', 'terrible', 'sad', 'angry', 'upset')):
        return 'negative'
    if '!' in (content or ''):
        return 'positive'
    return ''


def is_tech_channel(channel_name: str) -> bool:
    return bool(channel_name and _TECH_CHANNEL_RE.search(channel_name))


def pick_reaction_emoji(
    content: str,
    *,
    context_text: str = '',
    channel_name: str = '',
    blocked_rules: dict | None = None,
) -> str:
    """Pick a Unicode reaction emoji for message content."""
    import random

    tier = sentiment_tier(content, context_text=context_text)
    if not tier:
        return ''
    rules = blocked_rules if isinstance(blocked_rules, dict) else _DEFAULT_BLOCKED
    blocked = set(rules.get(tier, []) or [])
    candidates = [emoji for emoji in _SENTIMENT_EMOJIS.get(tier, []) if emoji not in blocked]
    if is_tech_channel(channel_name):
        filtered = [emoji for emoji in candidates if emoji not in _SOFT_EMOJIS]
        if filtered:
            candidates = filtered
        tech = [emoji for emoji in candidates if emoji in _TECH_PREFERRED]
        if tech and random.random() < 0.65:
            candidates = tech
    return random.choice(candidates) if candidates else ''
