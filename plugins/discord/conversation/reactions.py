"""Silent emoji reactions — a host built-in, rewritten small (S4, 2026-09-22).

A message she reads gets one roll at `reaction.reaction_chance`; on a hit a
lexicon picks the emoji from the message's tone (no sentiment model, no
network). One reaction per message, a per-channel cooldown, a 1-5 s human
delay before it lands. The LLM's own `[react:]` tags are a separate lane
(ConversationService.deliver_tags) gated by `reaction.enabled`.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import OrderedDict

from plugins.discord.models.intentions import AddReactionIntention

logger = logging.getLogger(__name__)

REACTION_DELAY_MIN = 1.0
REACTION_DELAY_MAX = 5.0
REACTED_MESSAGES_CAP = 2000

_EMOJIS = {
    'very_positive': ['🎉', '🥳', '💯', '🔥', '✨', '🌟', '🤩', '😊', '👏', '🙌'],
    'positive': ['👍', '👏', '🙌', '❤️', '💯', '🔥', '✨', '😊', '🙂', '😉'],
    'curious': ['👀', '🧐', '🤔', '💭', '💡', '🧠'],
    'funny': ['😂', '🤣', '💀', '😆', '😹'],
    'negative': ['😢', '😞', '😔', '🙁', '😕', '😣', '💔'],
    'very_negative': ['😢', '💔', '😭', '🥺', '🫂', '😰'],
}
_LEXICON = {
    'very_positive': ('amazing', 'awesome', 'incredible', 'fantastic', 'congrats', 'congratulations', 'hooray',
                      'yay', 'woohoo', 'love it', 'love this', 'best day', 'nailed it', 'we did it', 'shipped'),
    'positive': ('great', 'nice', 'good', 'thanks', 'thank you', 'cool', 'sweet', 'glad', 'happy', 'love',
                 'well done', 'perfect', 'works', 'fixed', 'finally', 'cheers', 'appreciate'),
    'funny': ('lol', 'lmao', 'rofl', 'haha', 'hehe', '😂', '🤣', 'hilarious', 'dead', 'im crying'),
    'very_negative': ('heartbroken', 'devastated', 'passed away', 'died', 'funeral', 'hospital', 'crying',
                      'depressed', 'miserable', 'terrible news', 'worst day'),
    'negative': ('sad', 'upset', 'angry', 'hate', 'awful', 'terrible', 'ugh', 'annoying', 'frustrated', 'broken',
                 'sucks', 'sorry', 'tired', 'sick', 'lost', 'failed'),
}
_TIER_ORDER = ('very_negative', 'very_positive', 'funny', 'negative', 'positive')
_WORD = re.compile(r"[a-z']+")


def tone(text: str) -> str:
    """Rough tone of one message: a tier name or '' (nothing worth reacting to)."""
    lowered = ' '.join(str(text or '').lower().split())
    if not lowered:
        return ''
    words = set(_WORD.findall(lowered))
    for tier in _TIER_ORDER:
        for cue in _LEXICON[tier]:
            if (cue in words) if ' ' not in cue and cue.isalpha() else (cue in lowered):
                return tier
    if '?' in lowered:
        return 'curious'
    if lowered.endswith('!') and len(lowered) > 3:
        return 'positive'
    return ''


def pick_emoji(text: str, rng=random) -> str:
    tier = tone(text)
    return rng.choice(_EMOJIS[tier]) if tier else ''


class Reactions:
    def __init__(self):
        self._last_reaction_at: dict[tuple[str, str], float] = {}
        self._reacted_messages: OrderedDict[tuple[str, str, str], bool] = OrderedDict()

    def evaluate_silent(self, trigger, *, settings):
        reaction = getattr(settings, 'reaction', None) if settings else None
        if not reaction or not getattr(reaction, 'silent_enabled', False):
            return None
        text = str(getattr(trigger, 'clean_content', '') or '')
        if not text.strip() or self._already_reacted(trigger) or self._on_cooldown(trigger, reaction):
            return None
        chance = max(0.0, min(100.0, float(getattr(reaction, 'reaction_chance', 0) or 0)))
        if chance <= 0 or random.random() >= chance / 100.0:
            return None
        emoji = pick_emoji(text)
        if not emoji:
            return None
        return AddReactionIntention(
            intention_type='add_reaction', account_name=trigger.account_name, channel_id=trigger.channel_id,
            message_id=trigger.message_id, reason='silent_reaction', emoji=emoji, confidence=0.5, urgency=0.2,
            cost=0.05, metadata={'guild_id': trigger.guild_id, 'author_id': trigger.author_id},
        )

    def execute_silent(self, intention, *, transport, settings=None) -> dict:
        if not transport:
            return {'status': 'skipped', 'reason': 'no_transport'}
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and getattr(transport, 'loop', None) is running:
            asyncio.create_task(self._execute_async(intention, transport), name='discord-silent-reaction')
            return {'status': 'scheduled'}
        delay = random.uniform(REACTION_DELAY_MIN, REACTION_DELAY_MAX)
        time.sleep(delay)
        result = transport.add_reaction_sync(intention.channel_id, intention.message_id, intention.emoji,
                                             account_name=intention.account_name or None)
        return self._record(intention, result=result, delay=delay)

    async def _execute_async(self, intention, transport) -> dict:
        delay = random.uniform(REACTION_DELAY_MIN, REACTION_DELAY_MAX)
        await asyncio.sleep(delay)
        add = getattr(transport, 'add_reaction_async', None)
        if add:
            result = await add(intention.channel_id, intention.message_id, intention.emoji,
                               account_name=intention.account_name or None)
        else:
            result = transport.add_reaction_sync(intention.channel_id, intention.message_id, intention.emoji,
                                                 account_name=intention.account_name or None)
        return self._record(intention, result=result, delay=delay)

    def _record(self, intention, *, result, delay: float) -> dict:
        self._reacted_messages[(intention.account_name, intention.channel_id, intention.message_id)] = True
        while len(self._reacted_messages) > REACTED_MESSAGES_CAP:
            self._reacted_messages.popitem(last=False)
        self._last_reaction_at[(intention.account_name, intention.channel_id)] = time.time()
        return {'status': 'reacted', 'emoji': intention.emoji, 'transport': result, 'delay': delay}

    def _on_cooldown(self, trigger, reaction) -> bool:
        cooldown = max(0, int(getattr(reaction, 'reaction_cooldown_seconds', 0) or 0))
        last = self._last_reaction_at.get((trigger.account_name, trigger.channel_id), 0.0)
        return cooldown > 0 and (time.time() - last) < cooldown

    def _already_reacted(self, trigger) -> bool:
        return (trigger.account_name, trigger.channel_id, trigger.message_id) in self._reacted_messages
