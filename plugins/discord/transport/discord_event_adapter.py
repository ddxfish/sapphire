from __future__ import annotations

import time
from dataclasses import asdict

from plugins.discord.conversation.ignored_channels import is_channel_ignored
from plugins.discord.models.observations import TextMessageObservation, TypingObservation


class DiscordEventAdapter:
    def __init__(
        self,
        *,
        message_repository,
        channel_repository=None,
        trace_repository=None,
        media_service=None,
        settings_store=None,
        mention_map_service=None,
        llm_debug_service=None,
    ):
        self.message_repository = message_repository
        self.channel_repository = channel_repository
        self.trace_repository = trace_repository
        self.media_service = media_service
        self.settings_store = settings_store
        self.mention_map_service = mention_map_service
        self.llm_debug_service = llm_debug_service

    def adapt_message_event(
        self,
        account_name: str,
        self_user_id: int | str | None,
        message,
        *,
        interpret_media: bool = True,
    ) -> TextMessageObservation | None:
        author_id = str(getattr(message.author, 'id', ''))
        if self_user_id is not None and author_id == str(self_user_id):
            return None

        guild = getattr(message, 'guild', None)
        channel = message.channel
        attachments = [
            {
                'url': getattr(item, 'url', ''),
                'filename': getattr(item, 'filename', ''),
                'content_type': getattr(item, 'content_type', ''),
            }
            for item in getattr(message, 'attachments', [])
        ]
        mention_user_ids = [str(getattr(item, 'id', '')) for item in getattr(message, 'mentions', []) if getattr(item, 'id', None)]
        self_id = str(self_user_id) if self_user_id is not None else ''
        mentioned = bool(self_id and self_id in mention_user_ids)
        reference = getattr(message, 'reference', None)
        observation = TextMessageObservation(
            observation_id=f'message:{account_name}:{message.id}',
            account_name=account_name,
            guild_id=str(getattr(guild, 'id', '') or ''),
            guild_name=getattr(guild, 'name', '') or 'DM',
            channel_id=str(channel.id),
            channel_name=getattr(channel, 'name', 'DM') or 'DM',
            author_id=author_id,
            username=getattr(message.author, 'name', '') or '',
            display_name=getattr(message.author, 'display_name', '') or getattr(message.author, 'name', ''),
            message_id=str(message.id),
            content=getattr(message, 'content', '') or '',
            clean_content=getattr(message, 'clean_content', '') or getattr(message, 'content', '') or '',
            created_at=time.time(),
            is_dm=guild is None,
            mentioned=mentioned,
            author_is_bot=bool(getattr(message.author, 'bot', False)),
            mention_user_ids=mention_user_ids,
            attachments=attachments,
            reply_to_message_id=str(getattr(reference, 'message_id', '') or ''),
        )
        # Ignored channels are dropped BEFORE anything observes them (broadsword
        # H6): this check used to sit at the END, so an ignored channel was
        # still stored and counted as activity. The doc says "fully ignored…
        # dropped before batching".
        if self.settings_store:
            settings = self.settings_store.resolve(
                guild_id=observation.guild_id,
                channel_id=observation.channel_id,
                dm_id=observation.channel_id if observation.is_dm else None,
            )
            if is_channel_ignored(observation.account_name, observation.channel_id, settings):
                if self.trace_repository:
                    self.trace_repository.record_trace('event_dropped', 'Ignored channel', {
                        'message_id': observation.message_id,
                        'channel_id': observation.channel_id,
                        'account_name': observation.account_name,
                    })
                return None
        if self.channel_repository:
            # The guild / channel / user caches the pickers and mention map read.
            if observation.guild_id:
                self.channel_repository.upsert_guild(observation.guild_id, observation.guild_name)
            self.channel_repository.upsert_channel(observation.channel_id, observation.guild_id, observation.channel_name)
            self.channel_repository.upsert_user(observation.author_id, observation.username, observation.display_name)
        if self.message_repository:
            self.message_repository.save_message(observation)
        if interpret_media:
            self.interpret_media(observation)
        if self.settings_store:
            settings = self.settings_store.resolve(
                guild_id=observation.guild_id,
                channel_id=observation.channel_id,
                dm_id=observation.channel_id if observation.is_dm else None,
            )
        if self.mention_map_service:
            self.mention_map_service.update_from_discord_message(account_name, message, observation)
        if self.trace_repository:
            self.trace_repository.record_trace('message_observed', 'Observed Discord message', {
                'message_id': observation.message_id,
                'channel_id': observation.channel_id,
                'is_dm': observation.is_dm,
            })
        # Companion seam: other plugins (e.g. a cognition sidecar) can observe
        # every processed inbound message without touching this pipeline.
        try:
            from core.event_bus import publish
            publish('discord_message_observed', {
                'account': account_name,
                'guild_id': observation.guild_id,
                'guild_name': observation.guild_name,
                'channel_id': observation.channel_id,
                'channel_name': observation.channel_name,
                'author_id': observation.author_id,
                'username': observation.username,
                'display_name': observation.display_name,
                'message_id': observation.message_id,
                'content': observation.clean_content,
                'is_dm': observation.is_dm,
                'mentioned': observation.mentioned,
                'author_is_bot': getattr(observation, 'author_is_bot', False),
            })
        except Exception:
            pass
        return observation

    async def adapt_typing_event(self, account_name: str, self_user_id: int | str | None, channel, user, when=None) -> TypingObservation | None:
        author_id = str(getattr(user, 'id', ''))
        if self_user_id is not None and author_id == str(self_user_id):
            return None
        guild = getattr(channel, 'guild', None)
        observation = TypingObservation(
            observation_id=f'typing:{account_name}:{author_id}:{getattr(channel, "id", "")}',
            account_name=account_name,
            guild_id=str(getattr(guild, 'id', '') or ''),
            guild_name=getattr(guild, 'name', '') or 'DM',
            channel_id=str(getattr(channel, 'id', '')),
            channel_name=getattr(channel, 'name', 'DM') or 'DM',
            author_id=author_id,
            username=getattr(user, 'name', '') or '',
            display_name=getattr(user, 'display_name', '') or getattr(user, 'name', ''),
            created_at=time.time(),
            is_dm=guild is None,
        )
        if self.trace_repository:
            self.trace_repository.record_trace('typing_observed', 'Observed typing signal', asdict(observation))
        return observation

    def interpret_media(self, observation: TextMessageObservation) -> None:
        """Detect, describe, and store the message's attachments.

        Blocking: fetches the attachment (up to 3 tries with sleeps) and
        calls the vision model. It used to run inline in on_message on the
        daemon loop — every account, voice feed, and the flush loop froze
        for the duration (hunt 2026-09-12, C2). The transport now calls it
        on a worker thread after adapt_message_event(interpret_media=False);
        the inline default keeps single-threaded callers (tests) unchanged.
        """
        if self.media_service and observation.attachments:
            media_settings = None
            media_enabled = True
            image_understanding_enabled = True
            if self.settings_store:
                media_settings = self.settings_store.resolve(
                    guild_id=observation.guild_id,
                    channel_id=observation.channel_id,
                    dm_id=observation.channel_id if observation.is_dm else None,
                )
                media_config = getattr(media_settings, 'media', None)
                media_enabled = bool(getattr(media_config, 'enabled', True))
                image_understanding_enabled = bool(getattr(media_config, 'image_understanding_enabled', True))
            if media_enabled:
                for artifact in self.media_service.detect_artifacts(
                    observation.message_id,
                    observation.channel_id,
                    observation.account_name,
                    observation.attachments,
                ):
                    if self.trace_repository:
                        self.trace_repository.record_trace('media_detected', 'Detected media attachment', {
                            'message_id': observation.message_id,
                            'channel_id': observation.channel_id,
                            'media_kind': artifact.media_kind,
                            'filename': artifact.filename,
                        })
                    stored = self.media_service.store_and_interpret(
                        artifact,
                        settings=media_settings,
                        image_understanding_enabled=image_understanding_enabled,
                    )
                    interpretation = stored.interpretation or {}
                    source = interpretation.get('source', '')
                    fallback = interpretation.get('fallback') or {}
                    if self.trace_repository and source in {'fallback', 'metadata'}:
                        self.trace_repository.record_trace('media_fallback_used', 'Used fallback media interpretation', {
                            'message_id': observation.message_id,
                            'channel_id': observation.channel_id,
                            'media_kind': stored.media_kind,
                            'source': source,
                            'reason': fallback.get('reason') or ('image_understanding_disabled' if not image_understanding_enabled else 'metadata_only'),
                        })
                    if self.trace_repository and fallback.get('error_type'):
                        self.trace_repository.record_trace('media_interpretation_failed', 'Media interpretation failed', {
                            'message_id': observation.message_id,
                            'channel_id': observation.channel_id,
                            'media_kind': stored.media_kind,
                            'error_type': fallback.get('error_type'),
                            'error_message': fallback.get('error_message', ''),
                        })

