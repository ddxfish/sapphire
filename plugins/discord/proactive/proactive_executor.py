"""Execute proactive intentions via transport and record traces."""

from __future__ import annotations

from plugins.discord.models.intentions import (
    BirthdayWishIntention,
    GoodnightIntention,
    GreetChannelIntention,
    OutreachIntention,
    ReplyMessageIntention,
    SendGifIntention,
    SendMemeIntention,
    UpdatePresenceIntention,
)


class ProactiveExecutor:
    def __init__(self, *, transport=None, greeting_service=None, outreach_service=None, sleep_service=None, presence_service=None, presence_repository=None, world_model_service=None, gif_service=None, settings_store=None, trace_repository=None, event_bridge=None, proactive_message_service=None, channel_repository=None, mention_map_service=None, birthday_service=None):
        self.transport = transport
        self.greeting_service = greeting_service
        self.outreach_service = outreach_service
        self.sleep_service = sleep_service
        self.presence_service = presence_service
        self.presence_repository = presence_repository
        self.world_model_service = world_model_service
        self.gif_service = gif_service
        self.settings_store = settings_store
        self.trace_repository = trace_repository
        self.event_bridge = event_bridge
        self.proactive_message_service = proactive_message_service
        self.channel_repository = channel_repository
        self.mention_map_service = mention_map_service
        self.birthday_service = birthday_service

    def execute(self, intention) -> dict:
        if isinstance(intention, GreetChannelIntention):
            return self._send_text(intention, marker='greeting', on_sent=self.greeting_service.mark_sent if self.greeting_service else None, account_name=intention.account_name)
        if isinstance(intention, BirthdayWishIntention):
            return self._send_text(
                intention,
                marker='birthday_wish',
                on_sent=self.birthday_service.mark_wished if self.birthday_service else None,
                account_name=intention.account_name,
            )
        if isinstance(intention, OutreachIntention):
            return self._send_text(intention, marker='outreach', on_sent=self.outreach_service.mark_sent if self.outreach_service else None, account_name=intention.account_name)
        if isinstance(intention, GoodnightIntention):
            result = self._send_text(intention, marker='goodnight', account_name=intention.account_name)
            if self.sleep_service and result.get('status') == 'sent':
                self.sleep_service.mark_goodnight_sent(intention)
            return result
        if isinstance(intention, ReplyMessageIntention):
            return self._send_text(intention, marker='wake_reply', reply_to=intention.message_id or None)
        if isinstance(intention, SendGifIntention):
            return self._send_gif(intention)
        if isinstance(intention, SendMemeIntention):
            return self._send_meme(intention)
        if isinstance(intention, UpdatePresenceIntention):
            return self._update_presence(intention)
        return {'status': 'unsupported', 'intention_type': getattr(intention, 'intention_type', '')}

    async def execute_async(self, intention) -> dict:
        if isinstance(intention, GreetChannelIntention):
            return await self._send_text_async(
                intention,
                marker='greeting',
                on_sent=self.greeting_service.mark_sent if self.greeting_service else None,
                account_name=intention.account_name,
            )
        if isinstance(intention, BirthdayWishIntention):
            return await self._send_text_async(
                intention,
                marker='birthday_wish',
                on_sent=self.birthday_service.mark_wished if self.birthday_service else None,
                account_name=intention.account_name,
            )
        if isinstance(intention, OutreachIntention):
            return await self._send_text_async(
                intention,
                marker='outreach',
                on_sent=self.outreach_service.mark_sent if self.outreach_service else None,
                account_name=intention.account_name,
            )
        if isinstance(intention, GoodnightIntention):
            result = await self._send_text_async(
                intention,
                marker='goodnight',
                account_name=intention.account_name,
            )
            if self.sleep_service and result.get('status') == 'sent':
                self.sleep_service.mark_goodnight_sent(intention)
            return result
        if isinstance(intention, ReplyMessageIntention):
            return await self._send_text_async(
                intention,
                marker='wake_reply',
                reply_to=intention.message_id or None,
            )
        if isinstance(intention, SendGifIntention):
            return await self._send_gif_async(intention)
        if isinstance(intention, SendMemeIntention):
            return await self._send_meme_async(intention)
        if isinstance(intention, UpdatePresenceIntention):
            return await self.execute_presence_async(intention)
        return {'status': 'unsupported', 'intention_type': getattr(intention, 'intention_type', '')}

    async def execute_presence_async(self, intention: UpdatePresenceIntention) -> dict:
        return await self._update_presence_async(intention)

    def _send_text(self, intention, *, marker: str, on_sent=None, reply_to=None, account_name=None) -> dict:
        if not self.transport and not self.event_bridge:
            return {'status': 'skipped', 'reason': 'no_transport'}
        if isinstance(intention, ReplyMessageIntention) and intention.metadata.get('use_llm'):
            return self._send_via_llm(intention, marker=marker)
        if not self.transport:
            return {'status': 'skipped', 'reason': 'no_transport'}
        account_name = account_name or getattr(intention, 'account_name', None) or None
        guild_id = self._guild_id_for_channel(intention.channel_id)
        message_text = self._resolve_message_text(intention, account_name=account_name)
        result = self.transport.send_message_sync(
            intention.channel_id,
            message_text,
            reply_to_message_id=reply_to,
            account_name=account_name,
            guild_id=guild_id or None,
        )
        if result.get('status') != 'sent':
            return {'status': 'error', 'reason': result.get('error', 'send_failed'), 'transport': result}
        if on_sent:
            on_sent(intention)
        if self.trace_repository:
            self.trace_repository.record_trace('proactive_sent', f'Sent {marker}', {'channel_id': intention.channel_id, 'reason': intention.reason})
        return {'status': 'sent', 'transport': result}

    async def _send_text_async(self, intention, *, marker: str, on_sent=None, reply_to=None, account_name=None) -> dict:
        if not self.transport and not self.event_bridge:
            return {'status': 'skipped', 'reason': 'no_transport'}
        if isinstance(intention, ReplyMessageIntention) and intention.metadata.get('use_llm'):
            return await self._send_via_llm_async(intention, marker=marker)
        if not self.transport:
            return {'status': 'skipped', 'reason': 'no_transport'}
        account_name = account_name or getattr(intention, 'account_name', None) or None
        guild_id = self._guild_id_for_channel(intention.channel_id)
        message_text = self._resolve_message_text(intention, account_name=account_name)
        result = await self.transport.send_message_async(
            intention.channel_id,
            message_text,
            reply_to_message_id=reply_to,
            account_name=account_name,
            guild_id=guild_id or None,
        )
        if result.get('status') != 'sent':
            return {'status': 'error', 'reason': result.get('error', 'send_failed'), 'transport': result}
        if on_sent:
            on_sent(intention)
        if self.trace_repository:
            self.trace_repository.record_trace('proactive_sent', f'Sent {marker}', {'channel_id': intention.channel_id, 'reason': intention.reason})
        return {'status': 'sent', 'transport': result}

    def _send_via_llm(self, intention: ReplyMessageIntention, *, marker: str) -> dict:
        payload = dict(intention.metadata.get('event_payload') or {})
        payload.setdefault('account', intention.account_name)
        payload.setdefault('channel_id', intention.channel_id)
        payload.setdefault('message_id', f"task-followup-{intention.metadata.get('task_id', '0')}")
        payload.setdefault('content', intention.prompt)
        payload.setdefault('task_id', str(intention.metadata.get('task_id') or ''))
        if self.settings_store:
            settings = self.settings_store.resolve(
                guild_id=str(payload.get('guild_id') or ''),
                channel_id=str(payload.get('channel_id') or ''),
                dm_id=str(payload.get('channel_id') or '') if str(payload.get('is_dm', '')).lower() in {'true', '1'} else None,
            )
            from plugins.discord.sapphire.llm_settings import llm_event_fields

            payload.update(llm_event_fields(settings))
        self._prime_mention_map(payload)
        if not self.event_bridge:
            return self._send_task_follow_up_direct(intention, payload, marker=marker, reason='no_event_bridge')
        accepted = self.event_bridge.emit_discord_message(payload)
        if accepted:
            task_id = intention.metadata.get('task_id')
            if task_id and self.world_model_service:
                self.world_model_service.task_repository.update_task_status(int(task_id), 'processing')
            if self.trace_repository:
                self.trace_repository.record_trace('proactive_sent', f'Queued {marker} via LLM', {'channel_id': intention.channel_id, 'reason': intention.reason, 'task_id': intention.metadata.get('task_id')})
            return {'status': 'queued', 'accepted': True}
        if self.trace_repository:
            self.trace_repository.record_trace('proactive_skipped', 'No Sapphire task accepted follow-up event', {'channel_id': intention.channel_id, 'reason': intention.reason, 'task_id': intention.metadata.get('task_id')})
        return self._send_task_follow_up_direct(intention, payload, marker=marker, reason='event_not_accepted')

    async def _send_via_llm_async(self, intention: ReplyMessageIntention, *, marker: str) -> dict:
        payload = dict(intention.metadata.get('event_payload') or {})
        payload.setdefault('account', intention.account_name)
        payload.setdefault('channel_id', intention.channel_id)
        payload.setdefault('message_id', f"task-followup-{intention.metadata.get('task_id', '0')}")
        payload.setdefault('content', intention.prompt)
        payload.setdefault('task_id', str(intention.metadata.get('task_id') or ''))
        if self.settings_store:
            settings = self.settings_store.resolve(
                guild_id=str(payload.get('guild_id') or ''),
                channel_id=str(payload.get('channel_id') or ''),
                dm_id=str(payload.get('channel_id') or '') if str(payload.get('is_dm', '')).lower() in {'true', '1'} else None,
            )
            from plugins.discord.sapphire.llm_settings import llm_event_fields

            payload.update(llm_event_fields(settings))
        self._prime_mention_map(payload)
        if not self.event_bridge:
            return await self._send_task_follow_up_direct_async(intention, payload, marker=marker, reason='no_event_bridge')
        accepted = self.event_bridge.emit_discord_message(payload)
        if accepted:
            task_id = intention.metadata.get('task_id')
            if task_id and self.world_model_service:
                self.world_model_service.task_repository.update_task_status(int(task_id), 'processing')
            if self.trace_repository:
                self.trace_repository.record_trace('proactive_sent', f'Queued {marker} via LLM', {'channel_id': intention.channel_id, 'reason': intention.reason, 'task_id': intention.metadata.get('task_id')})
            return {'status': 'queued', 'accepted': True}
        if self.trace_repository:
            self.trace_repository.record_trace('proactive_skipped', 'No Sapphire task accepted follow-up event', {'channel_id': intention.channel_id, 'reason': intention.reason, 'task_id': intention.metadata.get('task_id')})
        return await self._send_task_follow_up_direct_async(intention, payload, marker=marker, reason='event_not_accepted')

    def _send_task_follow_up_direct(self, intention: ReplyMessageIntention, payload: dict, *, marker: str, reason: str) -> dict:
        if not self.transport:
            return {'status': 'skipped', 'reason': reason, 'accepted': False}
        author_id = str(payload.get('author_id') or '')
        mention = f'<@{author_id}>' if author_id else ''
        reminder = str(payload.get('reminder') or '').strip()
        when_label = str(payload.get('when_label') or '').strip()
        if reminder:
            prefix = f'{mention} ' if mention else ''
            timing = f' ({when_label})' if when_label else ''
            text = f'{prefix}Reminder{timing}: {reminder}'
        else:
            text = str(payload.get('reply_instructions') or payload.get('content') or intention.prompt)
            if mention and mention not in text:
                text = f'{mention} {text}'
        result = self.transport.send_message_sync(
            intention.channel_id,
            text,
            account_name=intention.account_name or None,
            guild_id=str(payload.get('guild_id') or self._guild_id_for_channel(intention.channel_id) or '') or None,
        )
        if self.trace_repository:
            self.trace_repository.record_trace('proactive_sent', f'Sent {marker} via direct transport', {'channel_id': intention.channel_id, 'reason': intention.reason, 'task_id': intention.metadata.get('task_id'), 'fallback': reason})
        return {'status': 'sent', 'transport': result, 'fallback': reason}

    async def _send_task_follow_up_direct_async(self, intention: ReplyMessageIntention, payload: dict, *, marker: str, reason: str) -> dict:
        if not self.transport:
            return {'status': 'skipped', 'reason': reason, 'accepted': False}
        author_id = str(payload.get('author_id') or '')
        mention = f'<@{author_id}>' if author_id else ''
        reminder = str(payload.get('reminder') or '').strip()
        when_label = str(payload.get('when_label') or '').strip()
        if reminder:
            prefix = f'{mention} ' if mention else ''
            timing = f' ({when_label})' if when_label else ''
            text = f'{prefix}Reminder{timing}: {reminder}'
        else:
            text = str(payload.get('reply_instructions') or payload.get('content') or intention.prompt)
            if mention and mention not in text:
                text = f'{mention} {text}'
        result = await self.transport.send_message_async(
            intention.channel_id,
            text,
            account_name=intention.account_name or None,
            guild_id=str(payload.get('guild_id') or self._guild_id_for_channel(intention.channel_id) or '') or None,
        )
        if self.trace_repository:
            self.trace_repository.record_trace('proactive_sent', f'Sent {marker} via direct transport', {'channel_id': intention.channel_id, 'reason': intention.reason, 'task_id': intention.metadata.get('task_id'), 'fallback': reason})
        return {'status': 'sent', 'transport': result, 'fallback': reason}

    def _send_gif(self, intention: SendGifIntention) -> dict:
        if not self.transport or not self.gif_service:
            return {'status': 'skipped'}
        settings = self.settings_store.resolve() if self.settings_store else None
        url = self.gif_service.search_gif_url(intention.query, settings=settings)
        if not url:
            return {'status': 'skipped', 'reason': 'no_gif'}
        result = self.transport.send_gif_sync(intention.channel_id, url)
        self.gif_service.mark_sent(intention.account_name, intention.channel_id)
        return {'status': 'sent', 'transport': result}

    async def _send_gif_async(self, intention: SendGifIntention) -> dict:
        if not self.transport or not self.gif_service:
            return {'status': 'skipped'}
        settings = self.settings_store.resolve() if self.settings_store else None
        url = self.gif_service.search_gif_url(intention.query, settings=settings)
        if not url:
            return {'status': 'skipped', 'reason': 'no_gif'}
        result = await self.transport.send_gif_async(intention.channel_id, url)
        self.gif_service.mark_sent(intention.account_name, intention.channel_id)
        return {'status': 'sent', 'transport': result}

    def _send_meme(self, intention: SendMemeIntention) -> dict:
        if not self.transport:
            return {'status': 'skipped'}
        result = self.transport.send_gif_sync(intention.channel_id, intention.meme_url)
        return {'status': 'sent', 'transport': result}

    async def _send_meme_async(self, intention: SendMemeIntention) -> dict:
        if not self.transport:
            return {'status': 'skipped'}
        result = await self.transport.send_gif_async(intention.channel_id, intention.meme_url)
        return {'status': 'sent', 'transport': result}

    def _update_presence(self, intention: UpdatePresenceIntention) -> dict:
        if self.presence_repository:
            self.presence_repository.save_presence(intention.account_name, intention.status, intention.activity)
        transport_result = None
        if self.transport:
            transport_result = self.transport.change_presence_sync(intention.account_name, status=intention.status, activity=intention.activity)
        self._after_presence_update(intention, transport_result)
        return {'status': 'updated', 'transport': transport_result}

    async def _update_presence_async(self, intention: UpdatePresenceIntention) -> dict:
        if self.presence_repository:
            self.presence_repository.save_presence(intention.account_name, intention.status, intention.activity)
        transport_result = None
        if self.transport:
            transport_result = await self.transport.change_presence_async(intention.account_name, status=intention.status, activity=intention.activity)
        self._after_presence_update(intention, transport_result)
        return {'status': 'updated', 'transport': transport_result}

    def _after_presence_update(self, intention: UpdatePresenceIntention, transport_result: dict | None) -> None:
        mode = str(intention.metadata.get('mode') or '')
        if self.transport and (not transport_result or transport_result.get('status') != 'updated'):
            return
        if self.presence_service and mode:
            self.presence_service.mark_updated(intention.account_name, mode)
        if self.world_model_service:
            self.world_model_service.record_presence_update(intention.account_name, status=intention.status, activity=intention.activity, reason=intention.reason, mode=mode)

    def _resolve_message_text(self, intention, *, account_name: str | None = None) -> str:
        account_name = account_name or getattr(intention, 'account_name', None) or ''
        channel_id = getattr(intention, 'channel_id', '') or ''
        settings = self.settings_store.resolve(channel_id=channel_id) if self.settings_store else None
        if self.proactive_message_service and settings:
            if isinstance(intention, GreetChannelIntention):
                return self.proactive_message_service.build_greeting(account_name, channel_id, settings)
            if isinstance(intention, BirthdayWishIntention):
                metadata = intention.metadata or {}
                recipients = metadata.get('recipients') or []
                if metadata.get('bulk') and recipients:
                    return self.proactive_message_service.build_birthday_wish(
                        account_name,
                        channel_id,
                        settings,
                        recipients=recipients,
                    )
                return self.proactive_message_service.build_birthday_wish(
                    account_name,
                    channel_id,
                    settings,
                    display_name=str(metadata.get('display_name') or '').strip(),
                    mention=str(metadata.get('mention') or '').strip(),
                )
            if isinstance(intention, GoodnightIntention):
                return self.proactive_message_service.build_goodnight(account_name, channel_id, settings)
        prompt = str(getattr(intention, 'prompt', '') or '').strip()
        if prompt:
            return prompt
        proactive = settings.proactive if settings else None
        if isinstance(intention, GreetChannelIntention) and proactive:
            return str(proactive.greeting_fallback or 'Good morning!').strip()
        if isinstance(intention, GoodnightIntention) and proactive:
            return str(proactive.goodnight_fallback or 'Goodnight everyone!').strip()
        return prompt

    def _guild_id_for_channel(self, channel_id: str) -> str:
        if not self.channel_repository or not channel_id:
            return ''
        channel = self.channel_repository.get_channel(str(channel_id)) or {}
        return str(channel.get('guild_id') or '').strip()

    def _prime_mention_map(self, payload: dict) -> None:
        if not self.mention_map_service:
            return
        account_name = str(payload.get('account') or '').strip()
        channel_id = str(payload.get('channel_id') or '').strip()
        if not account_name or not channel_id:
            return
        mention_map = self.mention_map_service.build_for_channel(
            account_name,
            channel_id,
            author_id=str(payload.get('author_id') or ''),
            username=str(payload.get('username') or ''),
            display_name=str(payload.get('display_name') or ''),
        )
        payload['mention_map'] = mention_map
        hint = self.mention_map_service.mention_format_hint()
        existing = str(payload.get('reply_instructions') or '').strip()
        if hint not in existing:
            payload['reply_instructions'] = f'{existing}\n\n{hint}'.strip() if existing else hint
