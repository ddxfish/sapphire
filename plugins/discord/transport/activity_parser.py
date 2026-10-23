"""Parse Discord activity strings into API activity types."""

from __future__ import annotations

_ACTIVITY_PREFIXES = {
    'custom': 'custom',
    'playing': 'playing',
    'listening': 'listening',
    'watching': 'watching',
    'competing': 'competing',
}


def parse_activity_entry(text: str) -> tuple[str | None, str | None]:
    """Return (activity_type_name, activity_name) or (None, None) when cleared."""
    if not text:
        return None, None
    lower = text.lower()
    for prefix, kind in _ACTIVITY_PREFIXES.items():
        needle = f'{prefix}:'
        if lower.startswith(needle):
            name = text[len(needle):].strip()
            return kind, (name[:128] if name else None)
    return 'custom', text[:128]
