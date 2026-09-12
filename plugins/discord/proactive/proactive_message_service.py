"""Build proactive pipeline events and resolve static fallback text."""

from __future__ import annotations

import logging
import time

from plugins.discord.proactive.proactive_history import format_proactive_history
from plugins.discord.sapphire.llm_settings import cognitive_llm_from_settings, proactive_llm_from_settings

logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = {
    'greeting': (
        'Post a short, warm good-morning message for this Discord channel. '
        'One or two sentences. Vary your wording — do not repeat the same greeting each day.'
    ),
    'goodnight': (
        'Post a short goodnight message for this Discord channel before you head out for the night. '
        'One or two sentences. Vary your wording from night to night.'
    ),
    'outreach': (
        'This channel has been quiet for a while. Post a short, natural message to spark '
        'conversation — pick up on the recent chat if there is any, or start something fresh. '
        'Do not mention that the channel is quiet or that this post was scheduled.'
    ),
}


def _outreach_instructions(intention) -> str:
    base = DEFAULT_INSTRUCTIONS['outreach']
    metadata = getattr(intention, 'metadata', None) or {}
    hint = str(metadata.get('interest_hint') or '').strip()
    topics = [str(t).strip() for t in (metadata.get('interest_topics') or []) if str(t).strip()]
    if hint:
        return (
            f'{base} Shared interests from past chats here: {", ".join(topics) if topics else "various topics"}. '
            f'{hint} Keep it light — one or two sentences, not a quiz.'
        )
    return base


class ProactiveMessageService:
    def __init__(
        self,
        *,
        message_repository=None,
        channel_repository=None,
        transport=None,
        account_repository=None,
        trace_repository=None,
    ):
        self.message_repository = message_repository
        self.channel_repository = channel_repository
        self.transport = transport
        self.account_repository = account_repository
        self.trace_repository = trace_repository

    def build_event_payload(self, intention, settings, *, kind: str, account_name: str, guild_id: str = '') -> dict | None:
        """Continuity-pipeline payload for a proactive post, or None when this kind stays static."""
        proactive = settings.proactive
        channel_id = str(intention.channel_id or '')
        if kind == 'greeting':
            if not proactive.greeting_use_llm:
                return None
            content = str(proactive.greeting_message or '').strip() or DEFAULT_INSTRUCTIONS['greeting']
        elif kind == 'goodnight':
            if not proactive.goodnight_use_llm:
                return None
            content = str(proactive.goodnight_message or '').strip() or DEFAULT_INSTRUCTIONS['goodnight']
        elif kind == 'birthday':
            if not proactive.birthday_use_llm:
                return None
            content = self._birthday_instructions(intention)
        elif kind == 'outreach':
            content = _outreach_instructions(intention)
        else:
            return None

        guild_name, channel_name = self._resolve_names(channel_id)
        payload = {
            'account': account_name,
            'channel_id': channel_id,
            'guild_id': guild_id or '',
            'guild_name': guild_name,
            'channel_name': channel_name,
            'message_id': f'proactive-{kind}-{channel_id}-{int(time.time())}',
            'content': content,
            'recent_history': self._recent_chat(account_name, channel_id),
            'proactive_kind': kind,
        }
        payload.update(self._llm_fields(settings, kind))
        return payload

    def _llm_fields(self, settings, kind: str) -> dict:
        if kind in ('greeting', 'birthday'):
            provider, model = proactive_llm_from_settings(settings, kind='greeting')
        elif kind == 'goodnight':
            provider, model = proactive_llm_from_settings(settings, kind='goodnight')
        else:
            provider, model = cognitive_llm_from_settings(settings)
        if not provider or provider == 'auto':
            return {}
        fields = {'llm_primary': provider}
        if model:
            fields['llm_model'] = model
        return fields

    def _birthday_instructions(self, intention) -> str:
        metadata = intention.metadata or {}
        recipients = metadata.get('recipients') or []
        if metadata.get('bulk') and len(recipients) > 1:
            parts = []
            for item in recipients:
                name = str(item.get('display_name') or '').strip() or 'someone'
                mention = str(item.get('mention') or '').strip()
                parts.append(f'{name} ({mention})' if mention else name)
            names = ', '.join(parts)
            return (
                f'Today is the birthday of several people in this channel: {names}. '
                'Post one warm happy-birthday message that celebrates all of them, '
                'and @mention each person using the mention shown next to their name.'
            )
        display_name = str(metadata.get('display_name') or '').strip() or 'someone in this channel'
        mention = str(metadata.get('mention') or '').strip()
        text = f"Today is {display_name}'s birthday. Post a warm happy-birthday message for them."
        if mention:
            text += f' @mention them using {mention}.'
        return text

    # Static text — used when use_llm is off for the kind, or the pipeline rejected the event.

    def build_greeting(self, account_name: str, channel_id: str, settings) -> str:
        proactive = settings.proactive
        return self._static_text(
            use_llm=bool(proactive.greeting_use_llm),
            instructions=str(proactive.greeting_message or '').strip(),
            fallback=str(proactive.greeting_fallback or '').strip() or 'Good morning!',
        )

    def build_goodnight(self, account_name: str, channel_id: str, settings) -> str:
        proactive = settings.proactive
        return self._static_text(
            use_llm=bool(proactive.goodnight_use_llm),
            instructions=str(proactive.goodnight_message or '').strip(),
            fallback=str(proactive.goodnight_fallback or '').strip() or 'Goodnight everyone!',
        )

    def build_birthday_wish(
        self,
        account_name: str,
        channel_id: str,
        settings,
        *,
        display_name: str = '',
        mention: str = '',
        recipients: list[dict] | None = None,
    ) -> str:
        fallback = str(settings.proactive.birthday_wish_fallback or '').strip() or 'Happy birthday! 🎂'
        if recipients and len(recipients) > 1:
            mentions = ' '.join(str(item.get('mention') or '').strip() for item in recipients if item.get('mention'))
            return f'{fallback} {mentions}'.strip()
        if mention:
            return f'{fallback} {mention}'.strip()
        return fallback

    @staticmethod
    def _static_text(*, use_llm: bool, instructions: str, fallback: str) -> str:
        if instructions and not use_llm:
            return instructions
        return fallback

    def _resolve_names(self, channel_id: str) -> tuple[str, str]:
        channel_id = str(channel_id or '').strip()
        if not channel_id or not self.channel_repository:
            return '', ''
        channel = self.channel_repository.get_channel(channel_id) or {}
        channel_name = str(channel.get('name') or '').strip()
        guild_id = str(channel.get('guild_id') or '').strip()
        guild_name = ''
        if guild_id:
            guild_name = self.channel_repository.get_guild_name(guild_id)
        return guild_name, channel_name

    def _recent_chat(self, account_name: str, channel_id: str) -> list[str]:
        if not self.message_repository:
            return []
        rows = self.message_repository.get_recent_messages(account_name, channel_id, limit=20)
        if not rows:
            return []
        return format_proactive_history(
            rows,
            account_name=account_name,
            transport=self.transport,
            account_repository=self.account_repository,
        )
