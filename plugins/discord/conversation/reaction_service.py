"""Sentiment-driven silent reactions and LLM-tag reaction passthrough."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from plugins.discord.conversation.sentiment import pick_reaction_emoji
from plugins.discord.models.intentions import AddReactionIntention

logger = logging.getLogger(__name__)

REACTION_DELAY_MIN = 1.0
REACTION_DELAY_MAX = 5.0
READ_ONLY_REACT_CHANCE = 0.05


class ReactionService:
    def __init__(self, *, message_repository=None, trace_repository=None):
        self.message_repository = message_repository
        self.trace_repository = trace_repository
        self._last_reaction_at: dict[tuple[str, str, str], float] = {}
        self._reacted_messages: set[tuple[str, str, str]] = set()

    def maybe_react(self, parsed_reply) -> str:
        """Pass through explicit LLM [react:] tags."""
        return str(getattr(parsed_reply, 'reaction', '') or '').strip()

    def evaluate_silent(
        self,
        trigger,
        *,
        settings,
        world_state: dict | None = None,
        reply_planned: bool = False,
        read_only: bool = False,
    ) -> AddReactionIntention | None:
        reaction = getattr(settings, 'reaction', None) if settings else None
        if not reaction or not reaction.enabled or not reaction.silent_enabled:
            return None
        if not (trigger.clean_content or '').strip():
            return None
        if self._on_cooldown(trigger, reaction):
            return None
        if self._already_reacted(trigger):
            return None

        world_state = world_state or {}
        respond_trigger = bool(world_state.get('respond_trigger'))
        if reply_planned and not reaction.react_on_reply_path:
            return None

        if read_only:
            if respond_trigger or not reaction.read_only_enabled:
                return None
            if random.random() >= READ_ONLY_REACT_CHANCE:
                return None
        else:
            chance = max(0.0, min(100.0, float(reaction.reaction_chance)))
            if chance <= 0:
                return None
            if random.random() >= (chance / 100.0):
                return None

        context_text = self._recent_context_text(trigger)
        emoji = pick_reaction_emoji(
            trigger.clean_content,
            context_text=context_text,
            channel_name=trigger.channel_name,
        )
        if not emoji:
            return None

        confidence = 0.45 if read_only else 0.55
        if respond_trigger:
            confidence = min(0.85, confidence + 0.15)

        return AddReactionIntention(
            intention_type='add_reaction',
            account_name=trigger.account_name,
            channel_id=trigger.channel_id,
            message_id=trigger.message_id,
            reason='read_only_react' if read_only else 'silent_sentiment',
            emoji=emoji,
            confidence=confidence,
            urgency=0.2,
            cost=0.05,
            metadata={
                'read_only': read_only,
                'guild_id': trigger.guild_id,
                'author_id': trigger.author_id,
            },
        )

    def execute_silent(
        self,
        intention: AddReactionIntention,
        *,
        transport,
        settings=None,
    ) -> dict:
        if not transport:
            return {'status': 'skipped', 'reason': 'no_transport'}
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        transport_loop = getattr(transport, 'loop', None)
        if running is not None and transport_loop is running:
            asyncio.create_task(
                self.execute_silent_async(intention, transport=transport, settings=settings),
                name='discord-silent-reaction',
            )
            return {'status': 'scheduled'}
        return self._execute_silent_blocking(intention, transport=transport, settings=settings)

    async def execute_silent_async(
        self,
        intention: AddReactionIntention,
        *,
        transport,
        settings=None,
    ) -> dict:
        if not transport:
            return {'status': 'skipped', 'reason': 'no_transport'}
        delay = random.uniform(REACTION_DELAY_MIN, REACTION_DELAY_MAX)
        await asyncio.sleep(delay)
        add_reaction = getattr(transport, 'add_reaction_async', None)
        if add_reaction:
            result = await add_reaction(
                intention.channel_id,
                intention.message_id,
                intention.emoji,
                account_name=intention.account_name or None,
            )
        else:
            result = transport.add_reaction_sync(
                intention.channel_id,
                intention.message_id,
                intention.emoji,
                account_name=intention.account_name or None,
            )
        return self._record_silent_reaction(intention, result=result, delay=delay)

    def _execute_silent_blocking(
        self,
        intention: AddReactionIntention,
        *,
        transport,
        settings=None,
    ) -> dict:
        delay = random.uniform(REACTION_DELAY_MIN, REACTION_DELAY_MAX)
        time.sleep(delay)
        result = transport.add_reaction_sync(
            intention.channel_id,
            intention.message_id,
            intention.emoji,
            account_name=intention.account_name or None,
        )
        return self._record_silent_reaction(intention, result=result, delay=delay)

    def _record_silent_reaction(self, intention: AddReactionIntention, *, result: dict, delay: float) -> dict:
        key = (intention.account_name, intention.channel_id, intention.message_id)
        self._reacted_messages.add(key)
        self._last_reaction_at[(intention.account_name, intention.channel_id, 'channel')] = time.time()
        if self.trace_repository:
            self.trace_repository.record_trace('silent_reaction', 'Added sentiment reaction', {
                'channel_id': intention.channel_id,
                'message_id': intention.message_id,
                'emoji': intention.emoji,
                'reason': intention.reason,
            })
        return {'status': 'reacted', 'emoji': intention.emoji, 'transport': result, 'delay': delay}

    def _recent_context_text(self, trigger) -> str:
        if not self.message_repository:
            return ''
        rows = self.message_repository.get_recent_messages(
            trigger.account_name,
            trigger.channel_id,
            limit=4,
        )
        parts = []
        for row in rows:
            if str(row.get('message_id')) == str(trigger.message_id):
                continue
            parts.append(str(row.get('content') or ''))
        return ' '.join(parts).strip()

    def _on_cooldown(self, trigger, reaction) -> bool:
        cooldown = max(0, int(getattr(reaction, 'reaction_cooldown_seconds', 0) or 0))
        if cooldown <= 0:
            return False
        key = (trigger.account_name, trigger.channel_id, 'channel')
        last = self._last_reaction_at.get(key, 0.0)
        return (time.time() - last) < cooldown

    def _already_reacted(self, trigger) -> bool:
        key = (trigger.account_name, trigger.channel_id, trigger.message_id)
        return key in self._reacted_messages
