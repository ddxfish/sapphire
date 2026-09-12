"""Score competing social intentions: reply, react-only, or stay silent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScoredIntention:
    kind: str  # reply | react | silent
    score: float
    reason: str


def competition_enabled(settings) -> bool:
    cognitive = getattr(settings, 'cognitive', None) if settings else None
    return bool(getattr(cognitive, 'intention_competition_enabled', False))


def choose_social_intention(
    *,
    settings,
    addressed: bool,
    is_dm: bool,
    reply_mode: str,
    situation=None,
    relationship: dict | None = None,
    organic_base_chance: float = 15.0,
    organic_multiplier: float = 1.0,
    reaction_base_chance: float = 10.0,
    reaction_multiplier: float = 1.0,
    rng=None,
) -> ScoredIntention:
    """Pick one social outcome for an unforced channel message.

    Addressed messages / DMs / reply_mode=all are forced to reply by the caller.
    """
    import random
    roll = rng if rng is not None else random.random

    if addressed or is_dm or reply_mode == 'all':
        return ScoredIntention('reply', 1.0, 'forced_address')

    if not competition_enabled(settings):
        # Legacy path: organic roll only; caller handles react-on-miss.
        effective = max(0.0, min(100.0, float(organic_base_chance) * float(organic_multiplier)))
        if effective > 0 and roll() < (effective / 100.0):
            return ScoredIntention('reply', effective / 100.0, 'organic_chance')
        return ScoredIntention('silent', 0.0, 'organic_miss')

    vibe = getattr(situation, 'vibe', 'calm') if situation is not None else 'calm'
    heat = float(getattr(situation, 'heat', 0.0) or 0.0) if situation is not None else 0.0
    silence = float(getattr(situation, 'silence_seconds', 0.0) or 0.0) if situation is not None else 0.0
    familiarity = float((relationship or {}).get('familiarity') or 0.0)

    reply_score = (float(organic_base_chance) / 100.0) * float(organic_multiplier)
    react_score = (float(reaction_base_chance) / 100.0) * float(reaction_multiplier) * 0.9
    silent_score = 0.55

    if vibe == 'heated':
        reply_score *= 0.25
        react_score *= 0.6
        silent_score += 0.35
    elif vibe == 'quiet' and silence >= 1800:
        reply_score *= 1.2
        silent_score -= 0.1
    elif vibe == 'playful':
        react_score *= 1.25
        reply_score *= 1.05
    elif vibe == 'lively':
        reply_score *= 0.8
        react_score *= 1.1

    reply_score += familiarity * 0.15
    if heat > 0.6:
        silent_score += 0.2

    candidates = [
        ScoredIntention('reply', max(0.0, reply_score), 'scored_reply'),
        ScoredIntention('react', max(0.0, react_score), 'scored_react'),
        ScoredIntention('silent', max(0.0, silent_score), 'scored_silent'),
    ]
    # Softmax-ish discrete pick: weight by score^2 with floor
    weights = [max(0.01, c.score) ** 2 for c in candidates]
    total = sum(weights)
    pick = roll() * total
    upto = 0.0
    winner = candidates[-1]
    for candidate, weight in zip(candidates, weights):
        upto += weight
        if pick <= upto:
            winner = candidate
            break
    return winner
