"""Resolve proactive greeting and goodnight message text."""

from __future__ import annotations

import logging

from plugins.discord.proactive.birthday_llm import generate_birthday_wish, generate_bulk_birthday_wish
from plugins.discord.proactive.goodnight_llm import generate_goodnight
from plugins.discord.proactive.greeting_llm import generate_greeting
from plugins.discord.proactive.proactive_history import format_proactive_history
from plugins.discord.sapphire.llm_settings import proactive_llm_from_settings

logger = logging.getLogger(__name__)


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

    def build_greeting(self, account_name: str, channel_id: str, settings) -> str:
        proactive = settings.proactive
        provider_key, model_name = proactive_llm_from_settings(settings, kind='greeting')
        return self._build_message(
            account_name,
            channel_id,
            settings,
            kind='greeting',
            use_llm=bool(proactive.greeting_use_llm),
            instructions=str(proactive.greeting_message or '').strip(),
            fallback=str(proactive.greeting_fallback or 'Good morning!').strip(),
            provider_key=provider_key,
            model_name=model_name,
            max_tokens=int(proactive.greeting_max_tokens or 180),
        )

    def build_goodnight(self, account_name: str, channel_id: str, settings) -> str:
        proactive = settings.proactive
        provider_key, model_name = proactive_llm_from_settings(settings, kind='goodnight')
        max_tokens = int(proactive.goodnight_max_tokens or proactive.greeting_max_tokens or 180)
        return self._build_message(
            account_name,
            channel_id,
            settings,
            kind='goodnight',
            use_llm=bool(proactive.goodnight_use_llm),
            instructions=str(proactive.goodnight_message or '').strip(),
            fallback=str(proactive.goodnight_fallback or 'Goodnight everyone!').strip(),
            provider_key=provider_key,
            model_name=model_name,
            max_tokens=max_tokens,
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
        proactive = settings.proactive
        provider_key, model_name = proactive_llm_from_settings(settings, kind='greeting')
        base_tokens = int(proactive.greeting_max_tokens or 180)
        recipient_count = len(recipients or []) if recipients else 1
        max_tokens = min(500, base_tokens + max(0, recipient_count - 1) * 40)
        use_llm = bool(proactive.birthday_use_llm)
        fallback = str(proactive.birthday_wish_fallback or 'Happy birthday! 🎂').strip()
        bulk = bool(recipients and len(recipients) > 1)

        if use_llm:
            system = self._get_system()
            if system:
                guild_name, channel_name = self._resolve_names(channel_id)
                recent_chat = self._recent_chat(account_name, channel_id)
                if bulk:
                    text = generate_bulk_birthday_wish(
                        system,
                        account=account_name,
                        guild_name=guild_name,
                        channel_name=channel_name,
                        recipients=recipients,
                        recent_chat=recent_chat,
                        provider_key=provider_key,
                        model_name=model_name,
                        max_tokens=max_tokens,
                        transport=self.transport,
                        account_repository=self.account_repository,
                    )
                else:
                    text = generate_birthday_wish(
                        system,
                        account=account_name,
                        guild_name=guild_name,
                        channel_name=channel_name,
                        display_name=display_name,
                        mention=mention,
                        recent_chat=recent_chat,
                        provider_key=provider_key,
                        model_name=model_name,
                        max_tokens=max_tokens,
                        transport=self.transport,
                        account_repository=self.account_repository,
                    )
                if text:
                    return text
                self._record_fallback('birthday', account_name, channel_id, reason='llm_empty')
            else:
                self._record_fallback('birthday', account_name, channel_id, reason='no_system')

        if bulk and recipients:
            mentions = ' '.join(str(item.get('mention') or '').strip() for item in recipients if item.get('mention'))
            return f'{fallback} {mentions}'.strip()
        if mention:
            return f'{fallback} {mention}'.strip()
        return fallback

    def _build_message(
        self,
        account_name: str,
        channel_id: str,
        settings,
        *,
        kind: str,
        use_llm: bool,
        instructions: str,
        fallback: str,
        provider_key: str,
        model_name: str,
        max_tokens: int,
    ) -> str:
        if use_llm:
            system = self._get_system()
            if system:
                guild_name, channel_name = self._resolve_names(channel_id)
                recent_chat = self._recent_chat(account_name, channel_id)
                generator = generate_greeting if kind == 'greeting' else generate_goodnight
                text = generator(
                    system,
                    account=account_name,
                    guild_name=guild_name,
                    channel_name=channel_name,
                    instructions=instructions,
                    recent_chat=recent_chat,
                    provider_key=provider_key,
                    model_name=model_name,
                    max_tokens=max_tokens,
                    transport=self.transport,
                    account_repository=self.account_repository,
                )
                if text:
                    return text
                self._record_fallback(kind, account_name, channel_id, reason='llm_empty')
            else:
                self._record_fallback(kind, account_name, channel_id, reason='no_system')

        if instructions and not use_llm:
            return instructions
        return fallback or ('Good morning!' if kind == 'greeting' else 'Goodnight!')

    def _get_system(self):
        try:
            from core.api_fastapi import get_system

            return get_system()
        except Exception as exc:
            logger.warning('Proactive message could not reach Sapphire system: %s', exc)
            return None

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

    def _record_fallback(self, kind: str, account_name: str, channel_id: str, *, reason: str) -> None:
        if not self.trace_repository:
            return
        self.trace_repository.record_trace(
            'proactive_llm_fallback',
            f'Used {kind} fallback text',
            {
                'kind': kind,
                'account_name': account_name,
                'channel_id': channel_id,
                'reason': reason,
            },
        )
