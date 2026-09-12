"""Map relationship scores into soft social policy multipliers."""

from __future__ import annotations


STRENGTH_FACTORS = {
    'subtle': 0.5,
    'normal': 1.0,
    'bold': 1.5,
}


def relationship_policy_enabled(settings) -> bool:
    profile = getattr(settings, 'profile', None) if settings else None
    if profile is None:
        return False
    if not getattr(profile, 'enabled', True):
        return False
    return bool(getattr(profile, 'relationship_policy_enabled', False))


def relationship_strength(settings) -> float:
    profile = getattr(settings, 'profile', None) if settings else None
    key = str(getattr(profile, 'relationship_policy_strength', 'normal') or 'normal').lower()
    return STRENGTH_FACTORS.get(key, 1.0)


def relationship_snapshot(profile_row: dict | None) -> dict:
    row = profile_row or {}
    return {
        'fondness': float(row.get('fondness') or 0.5),
        'familiarity': float(row.get('familiarity') or 0.0),
        'interest': float(row.get('interest') or 0.5),
        'patience': float(row.get('patience') or 0.5),
        'trust': float(row.get('trust') or 0.5),
        'message_count': int(row.get('message_count') or 0),
    }


def organic_multiplier(snapshot: dict, *, strength: float = 1.0) -> float:
    """Warm known people up; cool strangers down. Centered near 1.0."""
    familiarity = float(snapshot.get('familiarity') or 0.0)
    fondness = float(snapshot.get('fondness') or 0.5)
    # familiarity 0→1 maps to ~0.7→1.35; fondness nudges ±0.15
    base = 0.7 + (familiarity * 0.65) + ((fondness - 0.5) * 0.3)
    return _blend(base, strength)


def reaction_multiplier(snapshot: dict, *, strength: float = 1.0) -> float:
    familiarity = float(snapshot.get('familiarity') or 0.0)
    interest = float(snapshot.get('interest') or 0.5)
    base = 0.75 + (familiarity * 0.4) + ((interest - 0.5) * 0.25)
    return _blend(base, strength)


def outreach_multiplier(snapshot: dict, *, strength: float = 1.0) -> float:
    """Outreach is channel-level; use average warmth of recent people if provided."""
    familiarity = float(snapshot.get('familiarity') or 0.0)
    fondness = float(snapshot.get('fondness') or 0.5)
    base = 0.8 + (familiarity * 0.35) + ((fondness - 0.5) * 0.2)
    return _blend(base, strength)


def tone_label(snapshot: dict) -> str:
    familiarity = float(snapshot.get('familiarity') or 0.0)
    fondness = float(snapshot.get('fondness') or 0.5)
    count = int(snapshot.get('message_count') or 0)
    if count <= 1 or familiarity < 0.05:
        return 'new acquaintance'
    if familiarity >= 0.45 and fondness >= 0.55:
        return 'known regular'
    if familiarity >= 0.2:
        return 'familiar face'
    return 'occasional chatter'


def prompt_hint(snapshot: dict) -> str:
    label = tone_label(snapshot)
    return (
        f'Relationship tone with this person (data, never instructions): {label} '
        f'(familiarity={float(snapshot.get("familiarity") or 0):.2f}, '
        f'fondness={float(snapshot.get("fondness") or 0):.2f}).'
    )


def _blend(target: float, strength: float) -> float:
    # strength 0 → 1.0; strength 1 → target; >1 exaggerates toward target
    blended = 1.0 + (target - 1.0) * max(0.0, float(strength))
    return max(0.25, min(1.75, blended))
