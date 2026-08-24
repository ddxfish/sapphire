"""Post-STT addressing filter for Discord voice conversation."""

from __future__ import annotations

import difflib
import re

# Obvious directed-at-bot phrases when STT misses the bot's name.
_DIRECTED_VOICE_PATTERNS = (
    r'\bcan you hear me\b',
    r'\bare you there\b',
    r'\bdo you copy\b',
    r'\bis (?:this|that) working\b',
    r'\banyone there\b',
    r'\bhello\??\s*$',
    r'\bhey\??\s*$',
    r'\btesting\b.*\b(?:one|1)\b.*\b(?:two|2)\b.*\b(?:three|3)\b',
)


def directed_voice_phrase(text: str) -> bool:
    haystack = str(text or '').strip().lower()
    if not haystack:
        return False
    return any(re.search(pattern, haystack) for pattern in _DIRECTED_VOICE_PATTERNS)


_STOP_COMMAND_PATTERNS = (
    r'\bstop\b',
    r'\bshut up\b',
    r'\bbe quiet\b',
    r'\bquiet down\b',
    r'\bsilence\b',
    r'\bstfu\b',
)


def is_stop_command(text: str) -> bool:
    haystack = str(text or '').strip().lower()
    if not haystack:
        return False
    return any(re.search(pattern, haystack) for pattern in _STOP_COMMAND_PATTERNS)


def should_address_bot(text: str, bot_names: list[str], *, addressing_mode: str = 'bot_name') -> bool:
    mode = str(addressing_mode or 'bot_name').strip().lower()
    if mode == 'always':
        return True
    return mentions_bot(text, bot_names)


def _token_matches_name(token: str, name: str, *, fuzzy_threshold: float = 0.82) -> bool:
    token_n = token.lower().strip()
    name_n = name.lower().strip()
    if not token_n or not name_n:
        return False
    if token_n == name_n or token_n in name_n or name_n in token_n:
        return True
    if len(token_n) >= 3 and len(name_n) >= 3 and token_n[:3] == name_n[:3]:
        if abs(len(token_n) - len(name_n)) <= 2:
            return True
    if len(token_n) >= 3 and difflib.SequenceMatcher(None, token_n, name_n).ratio() >= fuzzy_threshold:
        return True
    return False


def mentions_bot(text: str, bot_names: list[str], *, case_sensitive: bool = False) -> bool:
    """True when transcript appears directed at the bot by display name or alias."""
    raw = str(text or '').strip()
    if not raw:
        return False
    names = [str(name or '').strip() for name in (bot_names or []) if str(name or '').strip()]
    if not names:
        return False
    haystack = raw if case_sensitive else raw.lower()
    tokens = re.findall(r'\w+', haystack if case_sensitive else raw, re.UNICODE)
    for name in names:
        needle = name if case_sensitive else name.lower()
        if not needle:
            continue
        if needle in haystack:
            return True
        if re.search(rf'\b{re.escape(needle)}\b', haystack if case_sensitive else haystack, re.I if not case_sensitive else 0):
            return True
        for token in tokens:
            if _token_matches_name(token, needle):
                return True
    return False


def resolve_bot_names(*, settings=None, transport=None, account_name: str = '') -> list[str]:
    names: list[str] = []
    if settings is not None:
        aliases = getattr(getattr(settings, 'voice', None), 'addressing_aliases', None) or []
        names.extend(str(alias).strip() for alias in aliases if str(alias).strip())
    if transport is not None and account_name:
        try:
            state = transport._accounts.get(account_name) or {}
            client = state.get('client')
            user = getattr(client, 'user', None) if client else None
            for attr in ('display_name', 'global_name', 'name'):
                value = str(getattr(user, attr, '') or '').strip()
                if value and value not in names:
                    names.append(value)
        except Exception:
            pass
    if account_name:
        account = str(account_name).strip()
        if account and account not in names:
            names.append(account)
    return names
